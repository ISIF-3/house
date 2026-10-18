import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from house_engine.project import init_project
from house_engine.storage import HouseError, read_json

try:
    from house_engine import sessions
except ImportError:
    sessions = None


HANDOFF = '''# 세션 인계
## 목표
기준 모델의 검증 오차를 비교한다.
## 확정 결정
검증 데이터는 고정하고 데이터 누수를 검사하기로 했다.
## 실행 중인 작업
서버 job 101이 학습 중이다. logs/train.log에서 상태를 확인한다.
## 다음 행동
job 101의 종료 상태를 확인하고 검증 결과를 표로 기록한다.
'''


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(sessions, 'House session checkpoint module is required')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        project = self.base / 'research'
        project.mkdir()
        self.root = Path(init_project(project, self.base / 'common')['root'])
        self.transcript = self.base / 'rollout.jsonl'
        self.transcript.touch()
        self.session_id = 'native-session-1'

    def event(self, name, **fields):
        value = {'hook_event_name': name, 'session_id': self.session_id,
                 'transcript_path': str(self.transcript), 'cwd': str(self.root / 'main')}
        value.update(fields)
        return sessions.handle_hook(self.root, 'main', value)

    def start(self, source='startup'):
        return self.event('SessionStart', source=source)

    def directory(self):
        return sessions.workspace(self.root, 'main', self.session_id)

    def state(self):
        return read_json(self.directory() / 'session.json')

    def append(self, value):
        with self.transcript.open('a') as stream:
            stream.write(json.dumps(value) + '\n')

    def token(self, used, window=100, **info):
        info.update(last_token_usage={'total_tokens': used}, model_context_window=window)
        self.append({'type': 'event_msg', 'payload': {'type': 'token_count', 'info': info}})

    def valid_handoff(self):
        (self.directory() / 'handoff.md').write_text(HANDOFF)

    def auto(self):
        return self.event('PostCompact', trigger='auto')

    def test_start_creates_native_workspace_and_preserves_global_handoff(self):
        original = (self.root / 'main/handoff.md').read_bytes()
        with patch.dict(os.environ, {'HOUSE_LAUNCH_ID': 'launch-one'}):
            output = self.start()
        self.assertTrue((self.directory() / 'SESSION.md').is_file())
        self.assertTrue((self.directory() / 'handoff.md').is_file())
        self.assertTrue((self.directory() / 'work').is_dir())
        self.assertEqual(self.state()['launch_id'], 'launch-one')
        self.assertEqual(self.state()['auto_compactions'], 0)
        self.assertEqual(self.state()['count_scope'], 'session')
        self.assertEqual((self.root / 'main/handoff.md').read_bytes(), original)
        context = output['hookSpecificOutput']['additionalContext']
        self.assertIn(str(self.root / 'main/handoff.md'), context)
        self.assertEqual(output['hookSpecificOutput']['hookEventName'], 'SessionStart')

    def test_resume_and_compact_start_keep_counts_and_surface_review(self):
        self.start()
        self.auto()
        self.start(source='resume')
        output = self.start(source='compact')
        self.assertEqual(self.state()['auto_compactions'], 1)
        self.assertTrue(self.state()['review_required'])
        self.assertIn('검토', output['hookSpecificOutput']['additionalContext'])

    def test_unrecorded_resume_and_fork_count_only_observed_events(self):
        for source in ('resume', 'fork'):
            self.session_id = 'attached-' + source
            self.start(source=source)
            self.assertEqual(self.state()['count_scope'], 'observed_since_attach')
            self.auto()
            self.assertEqual(self.state()['auto_compactions'], 1)

    def test_clear_source_is_a_new_session_and_posttooluse_observes_tokens(self):
        self.start(source='clear')
        self.assertEqual(self.state()['count_scope'], 'session')
        self.token(72)
        self.assertIn('systemMessage', self.event('PostToolUse'))
        self.transcript.unlink()
        self.event('Stop')
        self.assertIsNone(self.state()['context_percent'])

    def test_compaction_dedup_history_has_a_bounded_size(self):
        self.start()
        state = self.state()
        state['compact_keys'] = ['prior-' + str(i) for i in range(256)]
        (self.directory() / 'session.json').write_text(json.dumps(state))
        self.append({'timestamp': '2026-10-19T00:00:01Z', 'type': 'compacted', 'payload': {}})
        self.auto()
        self.assertLessEqual(len(self.state()['compact_keys']), 256)

    def test_new_session_receives_latest_saved_snapshot_and_resets_counts(self):
        self.start()
        self.auto()
        self.valid_handoff()
        saved = sessions.checkpoint(self.root, 'main', self.session_id)
        self.session_id = 'new-native-id'
        output = self.start()
        self.assertEqual(self.state()['auto_compactions'], 0)
        self.assertIn(saved['checkpoint']['path'], output['hookSpecificOutput']['additionalContext'])

    def test_same_session_resume_reads_its_own_saved_checkpoint(self):
        self.start()
        self.valid_handoff()
        saved = sessions.checkpoint(self.root, 'main', self.session_id)
        output = self.start(source='resume')
        self.assertIn(saved['checkpoint']['path'], output['hookSpecificOutput']['additionalContext'])

    def test_external_snapshot_path_and_extreme_usage_numbers_are_rejected(self):
        self.start()
        self.token(10 ** 400, 10 ** 401)
        self.assertEqual(self.event('UserPromptSubmit'), {})
        self.assertIsNone(self.state()['context_percent'])
        self.valid_handoff()
        saved = sessions.checkpoint(self.root, 'main', self.session_id)
        state = self.state()
        state['checkpoint'] = dict(saved['checkpoint'], path=str(self.base / 'external.md'))
        (self.directory() / 'session.json').write_text(json.dumps(state))
        with self.assertRaises(HouseError):
            sessions.list_sessions(self.root)

    def test_manual_and_precompact_do_not_count_as_auto(self):
        self.start()
        self.event('PreCompact', trigger='auto')
        self.event('PostCompact', trigger='manual')
        self.assertEqual(self.state()['auto_compactions'], 0)
        self.assertEqual(self.state()['manual_compactions'], 1)
        self.assertFalse(self.state()['review_required'])
        output = self.auto()
        self.assertTrue(self.state()['review_required'])
        self.assertIn('systemMessage', output)
        self.assertNotIn('hookSpecificOutput', output)

    def test_same_turn_distinct_compactions_count_and_identifiable_replay_deduplicates(self):
        self.start()
        self.append({'timestamp': '2026-10-19T00:00:01Z', 'type': 'compacted', 'payload': {}})
        self.event('PostCompact', trigger='auto', turn_id='same-turn')
        self.event('PostCompact', trigger='auto', turn_id='same-turn')
        self.assertEqual(self.state()['auto_compactions'], 1)
        self.append({'timestamp': '2026-10-19T00:00:02Z', 'type': 'compacted', 'payload': {}})
        self.event('PostCompact', trigger='auto', turn_id='same-turn')
        self.assertEqual(self.state()['auto_compactions'], 2)

    def test_unidentifiable_postcompact_counts_each_completed_hook(self):
        self.start()
        self.event('PostCompact', trigger='auto', turn_id='same-turn')
        self.event('PostCompact', trigger='auto', turn_id='same-turn')
        self.assertEqual(self.state()['auto_compactions'], 2)

    def test_ten_auto_requires_explicit_saved_boundary_not_stop_or_plain_checkpoint(self):
        self.start()
        self.valid_handoff()
        for _ in range(9):
            self.auto()
        saved = sessions.checkpoint(self.root, 'main', self.session_id, boundary=True)
        self.assertFalse(saved['recommend_new_session'])
        self.auto()
        stop = self.event('Stop')
        self.assertNotIn('새 채팅', stop.get('systemMessage', ''))
        self.assertFalse(sessions.checkpoint(self.root, 'main', self.session_id)['recommend_new_session'])
        saved = sessions.checkpoint(self.root, 'main', self.session_id, boundary=True)
        self.assertTrue(saved['recommend_new_session'])
        self.assertIsNotNone(self.state()['recommended_at'])
        self.assertFalse(sessions.checkpoint(self.root, 'main', self.session_id, boundary=True)['recommend_new_session'])

    def test_checkpoint_verifies_handoff_and_keeps_immutable_hash_snapshot(self):
        self.start()
        self.auto()
        self.valid_handoff()
        saved = sessions.checkpoint(self.root, 'main', self.session_id)
        snapshot = Path(saved['checkpoint']['path'])
        self.assertEqual(snapshot.read_text(), HANDOFF)
        self.assertEqual(saved['checkpoint']['hash'], hashlib.sha256(HANDOFF.encode()).hexdigest())
        self.assertFalse(self.state()['review_required'])
        (self.directory() / 'handoff.md').write_text(HANDOFF + '\n새 관찰\n')
        sessions.checkpoint(self.root, 'main', self.session_id)
        self.assertEqual(snapshot.read_text(), HANDOFF)

    def test_checkpoint_rejects_initial_missing_empty_or_overlong_handoff(self):
        self.start()
        with self.assertRaises(HouseError):
            sessions.checkpoint(self.root, 'main', self.session_id)
        for text in (HANDOFF.replace('## 확정 결정', '## 다른 절'),
                     HANDOFF.replace('검증 데이터는 고정하고 데이터 누수를 검사하기로 했다.', ''),
                     HANDOFF.replace('기준 모델의 검증 오차를 비교한다.', '기록 필요'),
                     HANDOFF + '긴' * 8001):
            (self.directory() / 'handoff.md').write_text(text)
            with self.subTest(text=text[:40]), self.assertRaises(HouseError):
                sessions.checkpoint(self.root, 'main', self.session_id)

    def test_context_seventy_percent_warns_once_and_uses_last_not_cumulative_usage(self):
        self.start()
        self.token(69, total_token_usage={'total_tokens': 100000})
        self.assertEqual(self.event('UserPromptSubmit'), {})
        self.token(70)
        output = self.event('UserPromptSubmit')
        self.assertIn('70', output['systemMessage'])
        self.assertIn('인계', output['hookSpecificOutput']['additionalContext'])
        self.assertEqual(self.state()['context_percent'], 70)
        self.assertEqual(self.event('UserPromptSubmit'), {})

    def test_unknown_usage_never_uses_cumulative_tokens_or_invents_percentage(self):
        self.start()
        self.append({'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {
            'total_token_usage': {'total_tokens': 99000}, 'model_context_window': 100}}})
        self.assertEqual(self.event('UserPromptSubmit'), {})
        self.assertIsNone(self.state()['context_percent'])

    def test_saved_snapshot_tampering_is_rejected_before_next_session_handoff(self):
        self.start()
        self.valid_handoff()
        saved = sessions.checkpoint(self.root, 'main', self.session_id)
        Path(saved['checkpoint']['path']).write_text('changed after saving')
        self.session_id = 'new-native-id'
        with self.assertRaises(HouseError):
            self.start()

    def test_checkpoint_rejects_missing_handoff_and_duplicate_sections(self):
        self.start()
        handoff = self.directory() / 'handoff.md'
        handoff.unlink()
        with self.assertRaises(HouseError):
            sessions.checkpoint(self.root, 'main', self.session_id)
        handoff.write_text(HANDOFF + '\n## 목표\n다른 목표\n')
        with self.assertRaises(HouseError):
            sessions.checkpoint(self.root, 'main', self.session_id)

    def test_transcript_symlink_is_rejected_and_truncation_can_get_fresh_usage(self):
        self.start()
        self.token(75)
        self.event('UserPromptSubmit')
        self.auto()
        self.transcript.write_text('')
        self.event('UserPromptSubmit')
        self.token(72)
        self.assertIn('systemMessage', self.event('UserPromptSubmit'))
        self.transcript.unlink()
        outside = self.base / 'external.jsonl'
        outside.touch()
        self.transcript.symlink_to(outside)
        with self.assertRaises(HouseError):
            self.event('UserPromptSubmit')

    def test_malformed_state_fields_are_rejected_as_house_errors(self):
        self.start()
        original = self.state()
        state_path = self.directory() / 'session.json'
        for field, value in (('token_floor', {'identity': 'x', 'offset': 'bad'}),
                             ('context_percent', '70'), ('checkpoint', {'path': '/outside'}),
                             ('context_warning_emitted', 'false')):
            state_path.write_text(json.dumps(dict(original, **{field: value})))
            with self.subTest(field=field), self.assertRaises(HouseError):
                self.event('UserPromptSubmit')

    def test_invalid_numbers_and_unrelated_json_do_not_create_usage(self):
        self.start()
        for used, window in ((True, 100), (-1, 100), (101, 100), ('70', 100), (70, 0), (70, '100')):
            self.token(used, window)
            self.assertEqual(self.event('UserPromptSubmit'), {})
            self.assertIsNone(self.state()['context_percent'])

    def test_postcompact_invalidates_old_usage_until_fresh_record_and_warns_again(self):
        self.start()
        self.token(75)
        self.event('UserPromptSubmit')
        self.auto()
        self.assertIsNone(self.state()['context_percent'])
        self.assertEqual(self.event('UserPromptSubmit'), {})
        self.token(71)
        self.assertIn('systemMessage', self.event('UserPromptSubmit'))

    def test_replaced_transcript_does_not_revive_precompact_usage(self):
        self.start()
        self.token(75)
        self.event('UserPromptSubmit')
        self.auto()
        replacement = self.base / 'replacement.jsonl'
        replacement.write_bytes(self.transcript.read_bytes())
        os.replace(str(replacement), str(self.transcript))
        self.assertEqual(self.event('UserPromptSubmit'), {})
        self.assertIsNone(self.state()['context_percent'])
        self.token(71)
        self.assertIn('systemMessage', self.event('UserPromptSubmit'))

    def test_large_transcript_tail_ignores_partial_bad_and_oversized_lines(self):
        self.start()
        with self.transcript.open('ab') as stream:
            stream.write(b'x' * (3 * 1024 * 1024) + b'\n{bad json}\n')
        self.token(72)
        with self.transcript.open('ab') as stream:
            stream.write(b'{"unfinished":')
        self.assertIn('systemMessage', self.event('UserPromptSubmit'))
        self.assertEqual(self.state()['context_percent'], 72)

    def test_child_transcript_hook_cannot_change_parent_session(self):
        self.start()
        original = (self.directory() / 'session.json').read_bytes()
        child = self.base / 'child.jsonl'
        child.touch()
        self.assertEqual(self.event('PostCompact', trigger='auto', transcript_path=str(child)), {})
        self.assertEqual(self.event('SessionStart', source='compact', transcript_path=str(child)), {})
        self.assertEqual((self.directory() / 'session.json').read_bytes(), original)

    def test_list_sessions_is_read_only_and_ignores_no_rooms(self):
        before = set(self.root.rglob('*'))
        self.assertEqual(sessions.list_sessions(self.root), [])
        self.assertEqual(set(self.root.rglob('*')), before)
        self.start()
        records = sessions.list_sessions(self.root)
        self.assertEqual([r['session_id'] for r in records], [self.session_id])

    def test_finish_launch_updates_only_matching_launch_without_research_success(self):
        with patch.dict(os.environ, {'HOUSE_LAUNCH_ID': 'first-launch'}):
            self.start()
        self.session_id = 'other-session'
        with patch.dict(os.environ, {'HOUSE_LAUNCH_ID': 'second-launch'}):
            self.start()
        sessions.finish_launch(self.root, 'main', 'first-launch', 0)
        records = {r['session_id']: r for r in sessions.list_sessions(self.root)}
        self.assertEqual(records['native-session-1']['status'], 'cli_exited')
        self.assertEqual(records['other-session']['status'], 'active')
        sessions.finish_launch(self.root, 'main', 'second-launch', 7)
        self.assertEqual(self.state()['status'], 'cli_failed')

    def test_resume_rebinds_launch_id_without_resetting_native_session_counts(self):
        with patch.dict(os.environ, {'HOUSE_LAUNCH_ID': 'old-launch'}):
            self.start()
        self.auto()
        sessions.finish_launch(self.root, 'main', 'old-launch', 0)
        with patch.dict(os.environ, {'HOUSE_LAUNCH_ID': 'resumed-launch'}):
            self.start(source='resume')
        self.assertEqual(self.state()['launch_id'], 'resumed-launch')
        self.assertEqual(self.state()['status'], 'active')
        self.assertEqual(self.state()['auto_compactions'], 1)
        sessions.finish_launch(self.root, 'main', 'resumed-launch', 0)
        self.assertEqual(self.state()['status'], 'cli_exited')

    def test_rejects_traversal_room_symlink_state_symlink_and_corrupt_state(self):
        self.start()
        for bad in ('../escape', '/outside', 'a/b'):
            with self.assertRaises(HouseError):
                sessions.workspace(self.root, 'main', bad)
        with self.assertRaises(HouseError):
            sessions.workspace(self.root, 'invalid', 'okay')
        state_path = self.directory() / 'session.json'
        state_path.write_text('{bad')
        with self.assertRaises(HouseError):
            self.start()
        state_path.unlink()
        outside = self.base / 'outside.json'
        outside.write_text('{}')
        state_path.symlink_to(outside)
        with self.assertRaises(HouseError):
            self.start()
        state_path.unlink()
        room = self.root / 'work'
        import shutil
        shutil.rmtree(room)
        room.symlink_to(self.base, target_is_directory=True)
        with self.assertRaises(HouseError):
            sessions.workspace(self.root, 'work', 'okay')

    def test_metadata_log_never_copies_hook_prompt_or_tool_output(self):
        self.start()
        self.event('PostCompact', trigger='auto', prompt='PRIVATE USER DATA', tool_response='SECRET')
        log = (self.directory() / 'events.jsonl').read_text()
        self.assertNotIn('PRIVATE USER DATA', log)
        self.assertNotIn('SECRET', log)
        self.assertIn('PostCompact', log)


if __name__ == '__main__':
    unittest.main()
