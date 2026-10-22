"""Synthetic bridge policy regressions; these do not certify client hook UIs."""

from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from house_engine import session_bridge as bridge, sessions
from house_engine.project import init_project
from house_engine.storage import HouseError, locked


HANDOFF = '''# 세션 인계
## 목표
설치한 연결의 경고 동작을 확인한다.
## 확정 결정
기존 연구 입력과 결과를 보존한다.
## 실행 중인 작업
합성 callback의 실행 근거를 검토했다.
## 다음 행동
실제 클라이언트 표시 여부를 별도로 확인한다.
'''


class SessionBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        project = self.base / 'research'
        project.mkdir()
        self.root = Path(init_project(project, self.base / 'common')['root'])
        self.transcript = self.base / 'rollout.jsonl'
        self.transcript.touch()

    def event(self, name='PostToolUse', endpoint='terminal', **fields):
        event = {'hook_event_name': name, 'session_id': 'actual-session',
                 'transcript_path': str(self.transcript)}
        event.update(fields)
        return bridge.handle_event(self.root, event, endpoint_id=endpoint)

    def state(self):
        values = bridge.observations(self.root)
        self.assertEqual(len(values), 1)
        return values[0]

    def append(self, value):
        with self.transcript.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(value) + '\n')

    def token(self, used, window=100, **info):
        info.update(last_token_usage={'total_tokens': used}, model_context_window=window)
        self.append({'type': 'event_msg', 'payload': {'type': 'token_count', 'info': info}})

    def compacted(self, number):
        self.append({'type': 'compacted', 'timestamp': 'compact-' + str(number), 'payload': {}})

    def auto(self, number):
        return self.event('PostCompact', trigger='auto', native_event_id='compact-' + str(number))

    def handoff(self, text=HANDOFF):
        Path(self.state()['handoff_path']).write_text(text, encoding='utf-8')

    def manifest(self, folder):
        return {str(path.relative_to(folder)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in folder.rglob('*') if path.is_file()}

    def test_prepare_is_stable_and_is_not_an_observed_callback(self):
        self.assertEqual(bridge.observations(self.root), [])
        first = bridge.prepare(self.root)
        self.assertEqual(first, bridge.prepare(self.root))
        self.assertEqual(set(first), {'project_id', 'execution_environment_id'})
        self.assertEqual(bridge.observations(self.root), [])
        self.assertFalse((self.root / bridge.RUNTIME / 'state.json').exists())

    def test_project_identity_has_a_valid_stable_owner_fingerprint(self):
        bridge.prepare(self.root)
        path = self.root / bridge.RUNTIME / 'project.json'
        original = path.read_bytes()
        identity = json.loads(original)
        self.assertEqual(identity['owner_fingerprint'], bridge._owner_fingerprint())
        self.assertRegex(identity['owner_fingerprint'], r'^[0-9a-f]{64}$')
        bridge.prepare(self.root)
        self.assertEqual(path.read_bytes(), original)

    def test_owner_mismatch_is_rejected_before_attempting_another_hosts_lock(self):
        self.event()
        self.handoff()
        key = self.state()['conversation_key']
        runtime = self.root / bridge.RUNTIME
        original = self.manifest(runtime)
        with patch.object(bridge.platform, 'node', return_value='different-owner-host'), \
                patch.object(bridge, 'locked', side_effect=AssertionError('must not touch foreign owner lock')):
            with self.assertRaisesRegex(HouseError, 'another OS/host'):
                bridge.prepare(self.root)
            output = self.event()
            self.assertEqual(set(output), {'systemMessage'})
            self.assertIn('WSL forwarding', output['systemMessage'])
            with self.assertRaisesRegex(HouseError, 'another OS/host'):
                bridge.observations(self.root)
            with self.assertRaisesRegex(HouseError, 'another OS/host'):
                bridge.checkpoint(self.root, key, boundary=True)
        self.assertEqual(self.manifest(runtime), original)

    def test_missing_or_invalid_owner_is_unknown_and_never_automatically_adopted(self):
        bridge.prepare(self.root)
        path = self.root / bridge.RUNTIME / 'project.json'
        identity = json.loads(path.read_text())
        for owner in (None, '', True, 'not-a-sha256', 'A' * 64):
            with self.subTest(owner=owner):
                altered = dict(identity)
                if owner is None:
                    altered.pop('owner_fingerprint')
                else:
                    altered['owner_fingerprint'] = owner
                path.write_text(json.dumps(altered))
                original = path.read_bytes()
                with self.assertRaisesRegex(HouseError, 'owner'):
                    bridge.prepare(self.root)
                self.assertIn('owner', self.event()['systemMessage'])
                self.assertEqual(path.read_bytes(), original)
        self.assertFalse((self.root / bridge.RUNTIME / 'state.json').exists())

    def test_missing_identity_with_existing_observations_is_not_recreated(self):
        self.event()
        runtime = self.root / bridge.RUNTIME
        identity = runtime / 'project.json'
        identity.unlink()
        original = self.manifest(runtime)
        with self.assertRaisesRegex(HouseError, 'identity metadata is missing'):
            bridge.prepare(self.root)
        self.assertIn('owner is unknown', self.event()['systemMessage'])
        self.assertFalse(identity.exists())
        self.assertEqual(self.manifest(runtime), original)

    def test_checkpoint_instructions_use_the_native_shell_command_formatter(self):
        self.event()
        state = self.state()
        root = self.base / '공유 프로젝트' / 'house'
        with patch.object(bridge, 'shell_command', return_value='NATIVE WINDOWS COMMAND') as render:
            value = bridge._instructions(root, state)
        render.assert_called_once_with(['house', 'connection', 'checkpoint', state['conversation_key'],
                                        '--project', str(root.parent)])
        self.assertIn('NATIVE WINDOWS COMMAND', value)

    def test_attach_on_first_tool_callback_uses_actual_identity_and_no_legacy_writes(self):
        protected = self.manifest(self.root)
        output = self.event()
        state = self.state()
        self.assertEqual(state['attachment_event'], 'PostToolUse')
        self.assertEqual(state['count_scope'], 'observed_since_attach')
        self.assertIsNone(state['native_thread_id'])
        self.assertEqual(state['native_session_id'], 'actual-session')
        self.assertEqual(state['identity_status'], 'provisional')
        self.assertIsNone(state['prior_compactions'])
        self.assertIn(state['handoff_path'], output['systemMessage'])
        self.assertIn('house connection checkpoint', output['systemMessage'])
        after = self.manifest(self.root)
        self.assertTrue(all(after[path] == digest for path, digest in protected.items()))
        self.assertFalse((self.root / 'main/sessions/actual-session').exists())

    def test_first_prompt_attaches_without_fabricating_session_start(self):
        output = self.event('UserPromptSubmit', native_thread_id='native-thread')
        state = self.state()
        self.assertEqual(state['attachment_event'], 'UserPromptSubmit')
        self.assertEqual(state['native_thread_id'], 'native-thread')
        self.assertEqual(output['hookSpecificOutput']['hookEventName'], 'UserPromptSubmit')
        self.assertIn(state['handoff_path'], output['hookSpecificOutput']['additionalContext'])

    def test_69_70_warning_once_per_context_epoch_and_endpoint(self):
        self.token(69)
        self.event()
        self.assertEqual(self.state()['context_percent'], 69)
        self.assertEqual(self.state()['alerts'], [])
        self.token(70)
        output = self.event()
        self.assertIn('70.0%', output['systemMessage'])
        state = self.state()
        self.assertEqual(len(state['alerts']), 1)
        alert_id = state['alerts'][0]['alert_id']
        self.assertEqual(self.event(), {})
        other = self.event(endpoint='editor')
        self.assertIn(alert_id, other['systemMessage'])
        alert = self.state()['alerts'][0]
        self.assertEqual(set(alert['deliveries']), {'terminal', 'editor'})
        self.assertEqual(alert['status'], 'pending')
        self.assertEqual(alert['deliveries']['editor']['display_confirmation'], 'unknown')
        self.assertEqual(len(alert['deliveries']['terminal']['attempts']), 1)

    def test_tool_warning_also_requests_a_chat_message_without_claiming_display(self):
        self.token(70)
        output = self.event()
        self.assertIn('70.0%', output['systemMessage'])
        specific = output['hookSpecificOutput']
        self.assertEqual(specific['hookEventName'], 'PostToolUse')
        self.assertIn('70.0%', specific['additionalContext'])
        self.assertIn('다음 사용자용 메시지', specific['additionalContext'])
        self.assertNotIn('decision', output)
        self.assertNotIn('continue', output)
        delivery = self.state()['alerts'][0]['deliveries']['terminal']
        self.assertTrue(delivery['additional_context_prepared_at'])
        self.assertEqual(delivery['chat_display_confirmation'], 'unknown')
        self.assertEqual(delivery['display_confirmation'], 'unknown')
        self.assertEqual(self.event(), {})

    def test_stop_warning_waits_for_next_supported_context_event(self):
        self.event()
        self.token(74)
        stopped = self.event('Stop')
        self.assertIn('74.0%', stopped['systemMessage'])
        self.assertNotIn('hookSpecificOutput', stopped)
        delivery = self.state()['alerts'][0]['deliveries']['terminal']
        self.assertNotIn('additional_context_prepared_at', delivery)
        resumed = self.event()
        self.assertIn('74.0%', resumed['hookSpecificOutput']['additionalContext'])
        self.assertNotIn('systemMessage', resumed)
        self.assertEqual(self.event(), {})

    def test_native_retry_does_not_repeat_already_prepared_chat_notice(self):
        self.token(73)
        self.event()
        initial = self.state()['alerts'][0]['deliveries']['terminal']['additional_context_prepared_at']
        with patch.object(bridge, '_now', return_value=bridge._now() + timedelta(minutes=10)):
            retried = self.event('UserPromptSubmit')
        self.assertIn('73.0%', retried['systemMessage'])
        self.assertNotIn('73.0%', retried['hookSpecificOutput']['additionalContext'])
        delivery = self.state()['alerts'][0]['deliveries']['terminal']
        self.assertEqual(len(delivery['attempts']), 2)
        self.assertEqual(delivery['additional_context_prepared_at'], initial)

    def test_resolved_stop_warning_is_not_later_forwarded_to_chat(self):
        self.event()
        self.token(78)
        self.event('Stop')
        self.auto(1)
        forwarded = self.event()
        self.assertNotIn('78.0%', forwarded.get('hookSpecificOutput', {}).get('additionalContext', ''))

    def test_attachment_instructions_alone_do_not_request_user_warning(self):
        output = self.event()
        self.assertIn('handoff.md', output['hookSpecificOutput']['additionalContext'])
        self.assertNotIn('다음 사용자용 메시지', output['hookSpecificOutput']['additionalContext'])

    def test_compaction_invalidates_usage_then_allows_a_new_warning(self):
        self.token(72)
        self.event()
        self.auto(1)
        self.assertIsNone(self.state()['context_percent'])
        self.event()
        self.assertIsNone(self.state()['context_percent'])
        self.token(75)
        output = self.event()
        self.assertIn('75.0%', output['systemMessage'])
        warnings = [alert for alert in self.state()['alerts'] if alert['kind'] == 'context_warning']
        self.assertEqual(len(warnings), 2)
        self.assertIsNotNone(warnings[0]['resolved_at'])
        self.assertNotEqual(warnings[0]['alert_id'], warnings[1]['alert_id'])

    def test_cumulative_counts_and_invalid_usage_never_drive_warning(self):
        for info in ({'total_token_usage': {'total_tokens': 999999}, 'model_context_window': 100},
                     {'last_token_usage': {'total_tokens': 70}},
                     {'last_token_usage': {'total_tokens': True}, 'model_context_window': 100},
                     {'last_token_usage': {'total_tokens': 70}, 'model_context_window': 0},
                     {'last_token_usage': {'total_tokens': 101}, 'model_context_window': 100}):
            self.append({'type': 'event_msg', 'payload': {'type': 'token_count', 'info': info}})
            self.event()
            self.assertIsNone(self.state()['context_percent'])
        self.assertEqual(self.state()['alerts'], [])

    def test_unchanged_usage_timestamp_is_not_refreshed_and_becomes_stale(self):
        self.token(69)
        self.event()
        initial = self.state()['context_usage']['observed_at']
        later = bridge._now() + timedelta(seconds=bridge.USAGE_MAX_AGE_SECONDS + 1)
        with patch.object(bridge, '_now', return_value=later):
            self.event()
        state = self.state()
        self.assertIsNone(state['context_percent'])
        self.assertEqual(state['context_usage']['status'], 'stale')
        self.assertEqual(state['context_usage']['at'], initial)

    def test_old_timestamped_record_is_stale_even_on_first_attach(self):
        old = (bridge._now() - timedelta(days=1)).isoformat()
        self.append({'timestamp': old, 'type': 'event_msg', 'payload': {'type': 'token_count',
                     'info': {'last_token_usage': {'total_tokens': 99}, 'model_context_window': 100}}})
        self.event()
        self.assertIsNone(self.state()['context_percent'])
        self.assertEqual(self.state()['context_usage']['status'], 'stale')
        self.assertEqual(self.state()['alerts'], [])

    def test_model_change_waits_for_new_usage(self):
        self.token(71)
        self.event(model='one')
        self.event(model='two')
        self.assertIsNone(self.state()['context_percent'])
        self.event(model='two')
        self.assertIsNone(self.state()['context_percent'])
        self.token(69)
        self.event(model='two')
        self.assertEqual(self.state()['context_percent'], 69)

    def test_replaced_transcript_requires_append_before_reusing_usage(self):
        self.token(71)
        self.event()
        replacement = self.base / 'replacement.jsonl'
        replacement.write_bytes(self.transcript.read_bytes())
        replacement.replace(self.transcript)
        self.event()
        self.assertIsNone(self.state()['context_percent'])
        self.token(69)
        self.event()
        self.assertEqual(self.state()['context_percent'], 69)

    def test_ten_confirmed_auto_need_valid_handoff_and_boundary_once(self):
        self.event()
        for number in range(9):
            self.auto(number)
        self.handoff()
        key = self.state()['conversation_key']
        self.assertFalse(bridge.checkpoint(self.root, key, boundary=True)['recommend_new_session'])
        self.auto(9)
        self.assertEqual(self.state()['auto_compactions'], 10)
        self.event('Stop')
        self.assertIsNone(self.state()['recommended_at'])
        self.assertFalse(bridge.checkpoint(self.root, key)['recommend_new_session'])
        result = bridge.checkpoint(self.root, key, boundary=True)
        self.assertTrue(result['recommend_new_session'])
        snapshot = Path(result['checkpoint']['path'])
        self.assertEqual(hashlib.sha256(snapshot.read_bytes()).hexdigest(), result['checkpoint']['hash'])
        self.assertFalse(bridge.checkpoint(self.root, key, boundary=True)['recommend_new_session'])
        alerts = [alert for alert in self.state()['alerts'] if alert['kind'] == 'new_session_recommendation']
        self.assertEqual(len(alerts), 1)
        self.assertIn(alerts[0]['alert_id'], self.event()['systemMessage'])

    def test_invalid_handoff_cannot_checkpoint_or_recommend(self):
        self.event()
        for number in range(10):
            self.auto(number)
        key = self.state()['conversation_key']
        for text in (sessions.INITIAL_HANDOFF, HANDOFF + 'a' * 8000,
                     HANDOFF.replace('## 다음 행동', '## 목표')):
            self.handoff(text)
            with self.assertRaises(HouseError):
                bridge.checkpoint(self.root, key, boundary=True)
        self.assertIsNone(self.state()['checkpoint'])
        self.assertIsNone(self.state()['recommended_at'])

    def test_manual_unknown_and_unidentified_auto_do_not_count_as_confirmed_auto(self):
        for number in range(10):
            self.event('PostCompact', trigger='manual', native_event_id='manual-' + str(number))
            self.event('contextCompaction', trigger='auto', native_event_id='unknown-' + str(number))
            self.event('PostCompact', trigger='auto', turn_id='not-an-event-identity')
        state = self.state()
        self.assertEqual(state['auto_compactions'], 0)
        self.assertEqual(state['manual_compactions'], 10)
        self.assertEqual(state['unknown_compactions'], 10)
        self.assertEqual(state['unidentified_auto_deliveries'], 10)
        self.handoff()
        self.assertFalse(bridge.checkpoint(self.root, state['conversation_key'], boundary=True)['recommend_new_session'])

    def test_native_event_replay_deduplicates_but_same_turn_can_have_two_compactions(self):
        self.auto('one')
        self.auto('one')
        self.event('PostCompact', trigger='auto', native_event_id='compact-two', turn_id='shared-turn')
        self.event('PostCompact', trigger='auto', native_event_id='compact-three', turn_id='shared-turn')
        self.assertEqual(self.state()['auto_compactions'], 3)
        # Module/process reloads use persisted evidence, not a Python cache.
        self.auto('one')
        self.assertEqual(self.state()['auto_compactions'], 3)

    def test_actual_compacted_record_deduplicates_across_native_adapters(self):
        self.compacted(1)
        self.auto(1)
        self.event('PostCompact', trigger='auto', endpoint='editor')
        self.assertEqual(self.state()['auto_compactions'], 1)
        self.compacted(2)
        self.event('PostCompact', trigger='auto')
        self.assertEqual(self.state()['auto_compactions'], 2)
        self.event('PostCompact', trigger='auto')
        self.assertEqual(self.state()['auto_compactions'], 2)

    def test_historical_or_copied_compacted_records_are_not_new_compactions(self):
        self.compacted('before-attach')
        self.event()
        self.event('PostCompact', trigger='auto')
        self.assertEqual(self.state()['auto_compactions'], 0)
        self.assertEqual(self.state()['unidentified_auto_deliveries'], 1)
        self.compacted('new')
        self.event('PostCompact', trigger='auto')
        self.assertEqual(self.state()['auto_compactions'], 1)
        replacement = self.base / 'copy.jsonl'
        replacement.write_bytes(self.transcript.read_bytes())
        replacement.replace(self.transcript)
        self.event('PostCompact', trigger='auto')
        self.assertEqual(self.state()['auto_compactions'], 1)

    def test_unknown_completed_item_requires_separate_verified_cause(self):
        self.event(item={'type': 'contextCompaction', 'id': 'item-one'})
        self.assertEqual(self.state()['unknown_compactions'], 1)
        self.assertEqual(self.state()['auto_compactions'], 0)
        self.event('PostCompact', trigger='auto', native_event_id='item-one')
        self.assertEqual(self.state()['unknown_compactions'], 0)
        self.assertEqual(self.state()['auto_compactions'], 1)

    def test_shared_session_id_forks_and_children_with_distinct_transcripts_are_isolated(self):
        self.auto(1)
        for name in ('fork', 'child'):
            path = self.base / (name + '.jsonl')
            path.touch()
            self.event('PostToolUse', transcript_path=str(path), agent_id=name,
                       parent_thread_id='actual-parent')
        states = bridge.observations(self.root)
        self.assertEqual(len(states), 3)
        self.assertEqual(sorted(state['auto_compactions'] for state in states), [0, 0, 1])
        self.assertTrue(all(state['native_thread_id'] is None for state in states))

    def test_native_threads_override_shared_session_and_resume_same_thread(self):
        self.event('PostCompact', trigger='auto', native_event_id='one', thread_id='parent')
        self.event(thread_id='child', parent_thread_id='parent', agent_id='worker')
        self.event('SessionStart', source='resume', native_thread_id='parent')
        states = bridge.observations(self.root)
        self.assertEqual(len(states), 2)
        parent = next(state for state in states if state['native_thread_id'] == 'parent')
        self.assertEqual(parent['auto_compactions'], 1)

    def test_new_native_identity_does_not_guess_merge_with_provisional_stream(self):
        self.auto(1)
        self.event(thread_id='newly-observed-thread')
        states = bridge.observations(self.root)
        self.assertEqual(len(states), 2)
        native = next(state for state in states if state['native_thread_id'])
        self.assertEqual(native['auto_compactions'], 0)
        self.assertEqual(native['legacy_history']['status'], 'not_imported')

    def test_legacy_session_checkpoint_and_recommendation_bytes_are_unchanged(self):
        value = {'hook_event_name': 'SessionStart', 'source': 'startup',
                 'session_id': 'actual-session', 'transcript_path': str(self.transcript)}
        sessions.handle_hook(self.root, 'main', value)
        legacy = sessions.workspace(self.root, 'main', 'actual-session')
        (legacy / 'handoff.md').write_text(HANDOFF, encoding='utf-8')
        sessions.checkpoint(self.root, 'main', 'actual-session', boundary=True)
        before = self.manifest(legacy)
        self.token(70)
        self.event()
        self.auto(1)
        self.handoff()
        bridge.checkpoint(self.root, self.state()['conversation_key'], boundary=True)
        self.assertEqual(before, self.manifest(legacy))

    def test_delivery_retry_is_bounded_and_reuses_alert_identity(self):
        self.token(70)
        self.event()
        self.assertEqual(self.event('UserPromptSubmit'), {})
        later = bridge._now() + timedelta(seconds=bridge.DELIVERY_LEASE_SECONDS + 1)
        with patch.object(bridge, '_now', return_value=later):
            output = self.event('UserPromptSubmit')
            self.assertIn(self.state()['alerts'][0]['alert_id'], output['systemMessage'])
            self.assertEqual(self.event('UserPromptSubmit'), {})
        self.assertEqual(len(self.state()['alerts'][0]['deliveries']['terminal']['attempts']), 2)

    def test_host_client_metadata_separates_endpoints_without_claiming_ui_ack(self):
        self.token(70)
        first = self.event(endpoint=None, client={'name': 'cli', 'version': '1'})
        self.assertIn('70.0%', first['systemMessage'])
        self.assertEqual(self.event(endpoint=None, client={'name': 'cli', 'version': '2'}), {})
        second = self.event(endpoint=None, client={'name': 'editor'})
        self.assertIn('70.0%', second['systemMessage'])
        alert = self.state()['alerts'][0]
        self.assertEqual(len(alert['deliveries']), 2)
        self.assertTrue(all(value['display_confirmation'] == 'unknown' for value in alert['deliveries'].values()))

    def test_concurrent_callbacks_do_not_lose_counts_or_duplicate_endpoint_attempt(self):
        bridge.prepare(self.root)
        self.token(70)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.event(), range(2)))
        self.assertEqual(sum('70.0%' in value.get('systemMessage', '') for value in results), 1)
        self.assertEqual(len(self.state()['alerts']), 1)
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(self.auto, [1, 2]))
        self.assertEqual(self.state()['auto_compactions'], 2)

    def test_lock_failure_and_invalid_payload_return_diagnostics_not_policy_decisions(self):
        bridge.prepare(self.root)
        with locked(self.root / bridge.RUNTIME / '.lock'):
            output = self.event()
        self.assertIn('in use', output['systemMessage'])
        self.assertEqual(set(output), {'systemMessage'})
        output = bridge.handle_event(self.root, [])
        self.assertEqual(set(output), {'systemMessage'})
        self.assertIn('JSON object', output['systemMessage'])
        self.assertEqual(bridge.observations(self.root), [])

    def test_missing_native_identity_and_mismatched_owner_are_unknown_not_guessed(self):
        result = self.event(session_id=None)
        self.assertIn('required', result['systemMessage'])
        self.assertEqual(bridge.observations(self.root), [])
        result = self.event(project_id='other-project')
        self.assertIn('owning runtime', result['systemMessage'])
        result = self.event(execution_environment_id='other-environment')
        self.assertIn('owning runtime', result['systemMessage'])
        self.assertEqual(bridge.observations(self.root), [])

    def test_arbitrary_prompt_and_tool_response_are_not_persisted(self):
        self.event(prompt='PRIVATE PROMPT', tool_response='PRIVATE TOOL RESPONSE')
        value = json.dumps(self.state(), ensure_ascii=False)
        self.assertNotIn('PRIVATE', value)

    def test_failed_atomic_write_reports_unavailable_and_can_retry_warning(self):
        bridge.prepare(self.root)
        self.token(70)
        with patch.object(bridge, 'atomic_json', side_effect=HouseError('disk write failed')):
            result = self.event()
        self.assertEqual(set(result), {'systemMessage'})
        self.assertIn('unavailable', result['systemMessage'])
        self.assertEqual(bridge.observations(self.root), [])
        self.assertIn('70.0%', self.event()['systemMessage'])
        self.assertEqual(len(self.state()['alerts']), 1)


if __name__ == '__main__':
    unittest.main()
