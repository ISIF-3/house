import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skill/scripts'))
from house_engine import cli
from house_engine import __version__ as VERSION

PACKAGE = Path(__file__).resolve().parents[1]
SCRIPT = PACKAGE / 'skill/scripts/house.py'


class CLITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.project = self.base / 'research with spaces'
        self.project.mkdir()
        self.home = self.base / 'common house'
        # Dry-run tests must also work on CI machines with no Codex installation.
        self.env = dict(os.environ, HOUSE_HOME=str(self.home), HOUSE_CODEX_BIN=sys.executable)

    def run_house(self, *args, input=None, cwd=None):
        return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=cwd or self.project,
                              env=self.env, input=input, capture_output=True, text=True)

    def test_init_preserves_existing_project_and_creates_five_rooms(self):
        note = self.project / 'my-notes.md'
        note.write_text('keep me')
        result = self.run_house('init', '--goal', 'Compare models', '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(Path(value['root']), self.project / 'house')
        self.assertEqual(note.read_text(), 'keep me')
        for room in ['main', 'literature', 'work', 'output', 'discussion']:
            self.assertTrue((self.project / 'house' / room).is_dir())

    def test_room_dry_run_passes_paths_as_arguments_without_executing(self):
        fake = self.base / 'fake codex'
        fake.write_text('#!/bin/sh\ntouch "' + str(self.base / 'executed') + '"\n')
        fake.chmod(0o755)
        result = self.run_house('room', 'literature', '--dry-run', '--json', '--codex', str(fake))
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        root = self.project / 'house'
        self.assertEqual(value['room'], 'literature')
        self.assertEqual(value['argv'][0], str(fake))
        self.assertEqual(value['argv'][value['argv'].index('--cd') + 1], str(root / 'literature'))
        self.assertIn(str(root / 'main/target.md'), value['prompt'])
        self.assertFalse((self.base / 'executed').exists())
        self.assertNotIn('--dangerously-bypass-approvals-and-sandbox', value['argv'])

    def test_menu_defaults_to_main_and_accepts_room_number(self):
        for answer, room in [('\n', 'main'), ('2\n', 'literature')]:
            result = self.run_house('--dry-run', '--json', input=answer)
            self.assertEqual(result.returncode, 0, result.stderr)
            value = json.loads(result.stdout)
            self.assertEqual(value['room'], room)

    def test_invalid_menu_choice_reprompts_and_exit_does_not_launch(self):
        result = self.run_house('--dry-run', input='99\n0\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('0', result.stderr)
        self.assertFalse(any((self.home / 'runtime/runs').glob('*.json')))

    def test_nested_room_resolves_existing_house(self):
        self.assertEqual(self.run_house('init').returncode, 0)
        root = self.project / 'house'
        result = self.run_house('room', 'discussion', '--dry-run', '--json', cwd=root / 'literature')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['root'], str(root))
        self.assertFalse((root / 'literature/house').exists())

    def test_context_stays_in_current_chat_and_never_launches_codex(self):
        fake = self.base / 'bad-codex'
        fake.write_text('#!/bin/sh\nexit 91\n')
        fake.chmod(0o755)
        self.assertEqual(self.run_house('init').returncode, 0)
        result = self.run_house('context', 'main', '--json', '--codex', str(fake))
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertIn('why.md', value['prompt'])
        self.assertIn('설계', value['prompt'])
        self.assertFalse(value['launched'])

    def test_status_in_uninitialized_project_does_not_create_files(self):
        result = self.run_house('status', '--json')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.project / 'house').exists())

    def test_start_goal_is_data_not_a_shell_command(self):
        marker = self.base / 'unsafe'
        goal = '$(touch ' + str(marker) + ') ; `id`'
        result = self.run_house('start', '--goal', goal, '--dry-run', '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(marker.exists())
        self.assertIn(goal, (self.project / 'house/main/target.md').read_text())

    def test_launch_creates_runtime_records_and_captures_child_exit(self):
        fake = self.base / 'fake codex'
        fake.write_text('#!/bin/sh\nexit 7\n')
        fake.chmod(0o755)
        self.assertEqual(self.run_house('init').returncode, 0)
        info = cli.context(self.project / 'house', 'main')
        with mock.patch.object(cli.sys.stdin, 'isatty', return_value=True):
            code = cli.launch(info, self.home, str(fake))
        self.assertEqual(code, 7)
        records = list((self.home / 'runtime/runs').glob('*.json'))
        self.assertEqual(len(records), 1)
        record = json.loads(records[0].read_text())
        self.assertEqual(record['status'], 'failed')
        self.assertEqual(record['exit_code'], 7)
        self.assertIn('ended_at', record)

    def test_context_rejects_modified_pinned_room_guide(self):
        self.assertEqual(self.run_house('init').returncode, 0)
        (self.home / f'shared/releases/{VERSION}/rooms/main/ROOM.md').write_text('altered')
        result = self.run_house('context', 'main', '--project', str(self.project / 'house'))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('hash', result.stderr)

    def test_room_launch_has_scoped_hooks_and_fixed_roles(self):
        result = self.run_house('start', '--dry-run', '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        info = json.loads(result.stdout)
        flags = [info['argv'][i + 1] for i, arg in enumerate(info['argv']) if arg == '-c']
        self.assertTrue(any('hooks.SessionStart=' in flag for flag in flags))
        self.assertTrue(any('hooks.PostCompact=' in flag for flag in flags))
        self.assertTrue(any('hooks.PreToolUse=' in flag for flag in flags))
        for role in ('literature', 'work', 'output', 'discussion'):
            self.assertTrue(any('agents.' + role + '.config_file=' in flag for flag in flags))
        self.assertIn('agents.max_threads=4', flags)
        self.assertIn('10', info['prompt'])
        self.assertIn('send_input', info['prompt'])
        self.assertFalse((self.project / '.codex').exists())
        self.assertFalse((self.project / 'house/main/sessions').exists())

    def test_web_dry_run_uses_native_app_server_without_starting_it(self):
        result = self.run_house('web', '--dry-run', '--json', '--port', '0', '--no-browser', '--goal', 'Compare A')
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value['mode'], 'web')
        self.assertEqual(value['host'], '127.0.0.1')
        self.assertEqual(value['argv'][1:4], ['app-server', '--listen', 'stdio://'])
        self.assertTrue(any('hooks.PreToolUse=' in flag for flag in value['argv']))
        self.assertFalse((self.project / 'house/main/sessions').exists())

    def test_web_rejects_invalid_port(self):
        result = self.run_house('web', '--dry-run', '--json', '--port', '99999')
        self.assertEqual(result.returncode, 2)
        self.assertIn('포트', result.stderr)

    def test_hook_creates_native_session_folder_and_listing_is_read_only(self):
        self.assertEqual(self.run_house('init').returncode, 0)
        event = {'hook_event_name': 'SessionStart', 'source': 'startup',
                 'session_id': 'native-1', 'transcript_path': str(self.base / 'transcript.jsonl')}
        result = self.run_house('session', 'hook', '--room', 'work', input=json.dumps(event))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('additionalContext', json.loads(result.stdout)['hookSpecificOutput'])
        folder = self.project / 'house/work/sessions/native-1'
        self.assertTrue((folder / 'work').is_dir())
        self.assertTrue((folder / 'handoff.md').is_file())
        before = {str(p): p.read_bytes() for p in folder.rglob('*') if p.is_file()}
        result = self.run_house('sessions', '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('native-1', result.stdout)
        self.assertEqual(before, {str(p): p.read_bytes() for p in folder.rglob('*') if p.is_file()})

    def test_checkpoint_requires_real_handoff_and_reports_ten_at_boundary(self):
        self.assertEqual(self.run_house('init').returncode, 0)
        event = {'hook_event_name': 'SessionStart', 'source': 'startup', 'session_id': 'native-2',
                 'transcript_path': str(self.base / 'transcript.jsonl')}
        self.assertEqual(self.run_house('session', 'hook', '--room', 'main', input=json.dumps(event)).returncode, 0)
        self.assertNotEqual(self.run_house('session', 'checkpoint', 'native-2', '--boundary').returncode, 0)
        folder = self.project / 'house/main/sessions/native-2'
        (folder / 'handoff.md').write_text('## 목표\nA 비교\n## 확정 결정\n설계 D1\n## 실행 중인 작업\n서버 job 17 상태 재확인\n## 다음 행동\njob 17 로그 확인\n')
        for i in range(10):
            payload = dict(event, hook_event_name='PostCompact', trigger='auto', turn_id=str(i))
            self.assertEqual(self.run_house('session', 'hook', '--room', 'main', input=json.dumps(payload)).returncode, 0)
        result = self.run_house('session', 'checkpoint', 'native-2', '--boundary', '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('새 채팅', result.stdout)

    def test_corrupt_session_cannot_bypass_room_agent_policy(self):
        self.assertEqual(self.run_house('init').returncode, 0)
        folder = self.project / 'house/main/sessions/native-3'
        folder.mkdir(parents=True)
        (folder / 'session.json').write_text('{broken')
        event = {'hook_event_name': 'PreToolUse', 'session_id': 'native-3', 'tool_name': 'spawn_agent',
                 'tool_input': {'agent_type': 'work'}, 'transcript_path': '/missing'}
        result = self.run_house('session', 'hook', '--room', 'main', input=json.dumps(event))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['hookSpecificOutput']['permissionDecision'], 'deny')

    def test_unreadable_spawn_payload_cannot_bypass_worker_guard(self):
        self.assertEqual(self.run_house('init').returncode, 0)
        for payload in ('{"hook_event_name":"PreToolUse","message":"' + 'x' * (256 * 1024) + '"}', '{broken'):
            result = self.run_house('session', 'hook', '--room', 'work', '--event', 'PreToolUse', input=payload)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['hookSpecificOutput']['permissionDecision'], 'deny')

    def test_foreground_fake_cli_runs_generated_hooks_and_finishes_session(self):
        fake = self.base / 'fake codex with hooks'
        fake.write_text('#!' + sys.executable + '\n' + textwrap.dedent(r'''
            import json
            from pathlib import Path
            import re
            import shlex
            import subprocess
            import sys
            argv = sys.argv[1:]
            flags = [argv[i + 1] for i, arg in enumerate(argv) if arg == '-c']
            flag = next(value for value in flags if value.startswith('hooks.SessionStart='))
            quoted = re.search(r'command=("(?:[^"\\]|\\.)*")', flag).group(1)
            command = shlex.split(json.loads(quoted))
            root = Path(command[command.index('--project') + 1])
            transcript = root / 'test-transcript.jsonl'
            event = {'session_id': 'fake-chat', 'transcript_path': str(transcript),
                     'hook_event_name': 'SessionStart', 'source': 'startup'}
            def call(value):
                flag = next(item for item in flags if item.startswith('hooks.' + value['hook_event_name'] + '='))
                quoted = re.search(r'command=("(?:[^"\\]|\\.)*")', flag).group(1)
                hook_command = shlex.split(json.loads(quoted))
                result = subprocess.run(hook_command, input=json.dumps(value), text=True, capture_output=True)
                assert result.returncode == 0, result.stderr
                return json.loads(result.stdout)
            assert 'additionalContext' in call(event)['hookSpecificOutput']
            folder = root / 'main/sessions/fake-chat'
            (folder / 'handoff.md').write_text('## 목표\nA\n## 확정 결정\nD1\n## 실행 중인 작업\njob 17 확인\n## 다음 행동\n로그 확인\n')
            for i in range(10):
                with transcript.open('a') as stream:
                    stream.write(json.dumps({'type': 'compacted', 'timestamp': str(i)}) + '\n')
                call(dict(event, hook_event_name='PostCompact', trigger='auto', turn_id=str(i)))
            spawn = dict(event, hook_event_name='PreToolUse', tool_name='spawn_agent',
                         tool_input={'agent_type': 'literature'}, tool_use_id='call-1')
            assert call(spawn) == {}
            call(dict(event, hook_event_name='SubagentStart', agent_type='literature', agent_id='agent-1'))
            assert call(dict(spawn, tool_use_id='call-2'))['hookSpecificOutput']['permissionDecision'] == 'deny'
            checkpoint = command[:command.index('hook')] + ['checkpoint', 'fake-chat', '--project', str(root), '--room', 'main', '--boundary', '--json']
            result = subprocess.run(checkpoint, text=True, capture_output=True)
            assert result.returncode == 0, result.stderr
            assert json.loads(result.stdout)['recommend_new_session']
            sys.exit(0)
        '''))
        fake.chmod(0o755)
        self.assertEqual(self.run_house('init').returncode, 0)
        root = self.project / 'house'
        with mock.patch.object(cli.sys.stdin, 'isatty', return_value=True):
            code = cli.launch(cli.context(root, 'main'), self.home, str(fake))
        self.assertEqual(code, 0)
        state = json.loads((root / 'main/sessions/fake-chat/session.json').read_text())
        self.assertEqual(state['auto_compactions'], 10)
        self.assertEqual(state['status'], 'cli_exited')
        self.assertTrue(state['recommended_at'])


if __name__ == '__main__':
    unittest.main()
