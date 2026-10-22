"""Registration and actual command-boundary tests; no model calls or trust bypass."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from house_engine import cli, connections
from house_engine.project import init_project
from house_engine.storage import HouseError

SCRIPT = Path(__file__).resolve().parents[1] / 'skill/scripts/house.py'


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.project = self.base / 'project 한글 with spaces'
        self.project.mkdir()
        self.home = self.base / 'shared'
        self.root = Path(init_project(self.project, self.home)['root'])
        self.config = self.project / '.codex/hooks.json'
        self.transcript = self.base / 'rollout.jsonl'
        self.transcript.touch()

    def write_json(self, value):
        self.config.parent.mkdir(exist_ok=True)
        self.config.write_text(json.dumps(value, indent=2) + '\n')

    def event(self, name='SessionStart', **fields):
        value = dict(hook_event_name=name, session_id='actual-test-id',
                     transcript_path=str(self.transcript), source='startup',
                     cwd=str(self.project))
        value.update(fields)
        return value

    def test_preview_and_doctor_do_not_initialize_or_change_any_files(self):
        before = {str(p):p.read_bytes() for p in self.base.rglob('*') if p.is_file()}
        plan = connections.connect(self.root)
        self.assertEqual(plan['status'], 'would_register')
        self.assertEqual(connections.doctor(self.root)['registration'], 'not_registered')
        self.assertEqual(before, {str(p):p.read_bytes() for p in self.base.rglob('*') if p.is_file()})

    def test_json_merge_idempotence_and_exact_restore_preserve_foreign_settings(self):
        original = {'foreign': {'keep': 7}, 'hooks': {'Stop': [{'matcher':'custom', 'hooks':[
            {'type':'command','command':'user-owned-command'}]}]}}
        self.write_json(original)
        old_bytes = self.config.read_bytes()
        first = connections.connect(self.root, apply=True)
        self.assertEqual(first['status'], 'registered')
        self.assertEqual(len(json.loads(self.config.read_text())['hooks']['Stop']), 2)
        second = connections.connect(self.root, apply=True)
        self.assertFalse(second['changed'])
        self.assertNotIn('original_text', second)
        self.assertEqual(len(json.loads(self.config.read_text())['hooks']['Stop']), 2)
        self.assertEqual(connections.disconnect(self.root)['status'], 'would_disconnect')
        self.assertTrue(connections.registered(self.root))
        connections.disconnect(self.root, apply=True)
        self.assertEqual(self.config.read_bytes(), old_bytes)
        self.assertEqual(connections.disconnect(self.root, apply=True)['status'], 'not_registered')

    def test_disconnect_keeps_user_changes_added_after_connection(self):
        connections.connect(self.root, apply=True)
        value = json.loads(self.config.read_text())
        value['new_user_setting'] = 'keep'
        value['hooks']['Stop'].append({'hooks':[{'type':'command','command':'new-user-hook'}]})
        self.config.write_text(json.dumps(value))
        connections.disconnect(self.root, apply=True)
        current = json.loads(self.config.read_text())
        self.assertEqual(current['new_user_setting'], 'keep')
        self.assertEqual(current['hooks']['Stop'], [{'hooks':[{'type':'command','command':'new-user-hook'}]}])

    def test_edited_owned_hook_is_reported_and_not_overwritten_or_removed(self):
        connections.connect(self.root, apply=True)
        value = json.loads(self.config.read_text())
        value['hooks']['Stop'][0]['hooks'][0]['timeout'] = 11
        self.config.write_text(json.dumps(value))
        changed = self.config.read_bytes()
        self.assertEqual(connections.doctor(self.root)['registration'], 'changed')
        for operation in (connections.connect, connections.disconnect):
            with self.assertRaises(HouseError):
                operation(self.root, apply=True)
        self.assertEqual(self.config.read_bytes(), changed)

    def test_inline_toml_preserves_existing_content_and_does_not_create_json_source(self):
        self.config.parent.mkdir()
        config = self.config.parent / 'config.toml'
        original = '# existing config\nmodel = "user-model"\n[[hooks.Stop]]\n[[hooks.Stop.hooks]]\ntype = "command"\ncommand = "user hook"\n'
        config.write_text(original)
        plan = connections.connect(self.root, apply=True)
        self.assertEqual(plan['format'], 'toml')
        self.assertFalse(self.config.exists())
        text = config.read_text()
        self.assertTrue(text.startswith(original))
        try:
            import tomllib
        except ImportError:
            tomllib = None
        if tomllib:
            value = tomllib.loads(text)
            self.assertEqual(len(value['hooks']['Stop']), 2)
            self.assertEqual(value['model'], 'user-model')
        connections.disconnect(self.root, apply=True)
        self.assertEqual(config.read_text(), original)

    def test_explicit_disable_corrupt_json_and_symlink_are_not_repaired_implicitly(self):
        self.config.parent.mkdir()
        config = self.config.parent / 'config.toml'
        config.write_text('[features]\nhooks = false\n')
        with self.assertRaises(HouseError):
            connections.connect(self.root, apply=True)
        self.assertFalse(connections.receipt_path(self.root).exists())
        config.unlink()
        self.config.write_text('{broken')
        with self.assertRaises(HouseError):
            connections.connect(self.root, apply=True)
        self.assertEqual(self.config.read_text(), '{broken')
        self.config.unlink()
        foreign = self.base / 'foreign.json'
        foreign.write_text('{}')
        try:
            self.config.symlink_to(foreign)
        except OSError:
            return  # Some Windows hosts prohibit unprivileged symlinks.
        with self.assertRaises(HouseError):
            connections.connect(self.root, apply=True)
        self.assertEqual(foreign.read_text(), '{}')

    def test_no_native_trust_granted_and_pin_and_research_records_preserved(self):
        paths = [self.root / 'hive.lock.json', self.root / 'research.json', self.root / 'main/handoff.md']
        original = {p:p.read_bytes() for p in paths}
        plan = connections.connect(self.root, apply=True)
        content = self.config.read_text()
        self.assertNotIn('trusted_hash', content)
        self.assertNotIn('bypass', content)
        self.assertEqual(plan['trust'], 'requires_native_review')
        self.assertEqual(original, {p:p.read_bytes() for p in paths})
        self.assertIn('/main/integration/', (self.root / '.gitignore').read_text())
        self.assertIn('/hooks.json', (self.config.parent / '.gitignore').read_text())

    def test_receipt_failure_restores_configuration(self):
        self.write_json({'foreign':'keep'})
        original = self.config.read_bytes()
        with mock.patch.object(connections, 'atomic_json', side_effect=HouseError('write failed')):
            with self.assertRaises(HouseError):
                connections.connect(self.root, apply=True)
        self.assertEqual(self.config.read_bytes(), original)

    def test_existing_house_launcher_does_not_duplicate_registered_lifecycle_hooks(self):
        connections.connect(self.root, apply=True)
        config = cli.session_config(cli.context(self.root, 'main'), self.home)
        for name in connections.EVENTS:
            flags = [flag for flag in config if flag.startswith('hooks.' + name + '=')]
            self.assertEqual(len(flags), 1)
            self.assertIn('--origin launcher', flags[0])
        self.assertTrue(any(flag.startswith('hooks.PreToolUse=') for flag in config))
        self.assertTrue(any(flag.startswith('hooks.SubagentStart=') for flag in config))

    def test_owned_launcher_keeps_fallback_and_project_callback_is_passive(self):
        plan = connections.connect(self.root, apply=True)
        with mock.patch.dict(os.environ, {'HOUSE_CONNECTION_ORIGIN':'launcher'}):
            self.assertEqual(connections.handle_event(self.root, self.event(), plan['connection_id']), {})
            self.assertEqual(connections.doctor(self.root)['event_observation'], 'not_observed')
            output = connections.handle_event(self.root, self.event(), plan['connection_id'], origin='launcher')
        self.assertIn('hookSpecificOutput', output)
        self.assertEqual(len(connections.doctor(self.root)['conversations']), 1)

    def test_connected_launcher_retains_real_parent_agent_policy_without_legacy_start(self):
        from house_engine import room_agents
        plan = connections.connect(self.root, apply=True)
        event = self.event()
        with mock.patch.dict(os.environ, {'HOUSE_CONNECTION_ORIGIN':'launcher'}):
            connections.handle_event(self.root,event,plan['connection_id'],origin='launcher')
        self.assertFalse((self.root/'main/sessions/actual-test-id/session.json').exists())
        spawn = dict(event,hook_event_name='PreToolUse',tool_name='spawn_agent',
                     tool_input={'agent_type':'literature'},tool_use_id='call-1')
        self.assertEqual(room_agents.handle_hook(self.root,'main',spawn),{})
        started = dict(event,hook_event_name='SubagentStart',agent_type='literature',agent_id='lit-agent')
        room_agents.handle_hook(self.root,'main',started)
        follow = dict(event,hook_event_name='PreToolUse',tool_name='send_input',tool_input={'id':'lit-agent'})
        self.assertEqual(room_agents.handle_hook(self.root,'main',follow),{})
        other = self.base/'child-transcript.jsonl'
        other.touch()
        self.assertEqual(room_agents.handle_hook(self.root,'main',dict(follow,transcript_path=str(other)))
                         ['hookSpecificOutput']['permissionDecision'],'deny')

    def test_disconnect_receipt_failure_restores_hook_configuration(self):
        connections.connect(self.root, apply=True)
        original = self.config.read_bytes()
        with mock.patch.object(connections, 'atomic_json', side_effect=HouseError('write failed')):
            with self.assertRaises(HouseError):
                connections.disconnect(self.root, apply=True)
        self.assertEqual(self.config.read_bytes(), original)

    def test_registered_command_attaches_existing_chat_and_emits_warning(self):
        plan = connections.connect(self.root, apply=True)
        payload = self.event('UserPromptSubmit', source='resume')
        record = {'type':'event_msg','payload':{'type':'token_count','info':{
            'last_token_usage':{'total_tokens':70},'model_context_window':100}}}
        self.transcript.write_text(json.dumps(record) + '\n')
        args = [sys.executable, str(SCRIPT), 'connection', 'hook', '--project', str(self.project),
                '--connection-id', plan['connection_id'], '--event', 'UserPromptSubmit']
        result = subprocess.run(args, input=json.dumps(payload), text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertIn('70', output['systemMessage'])
        status = connections.doctor(self.root)
        self.assertEqual(status['event_observation'], 'observed')
        self.assertEqual(status['screen_delivery'], 'unknown')
        self.assertEqual(status['native_trust'], 'unknown')
        self.assertEqual(len(status['conversations']), 1)

    def test_invalid_hook_payload_is_diagnostic_not_policy_approval(self):
        plan = connections.connect(self.root, apply=True)
        args = [sys.executable, str(SCRIPT), 'connection', 'hook', '--project', str(self.project),
                '--connection-id', plan['connection_id'], '--event', 'Stop']
        for payload in ('{broken', 'x' * (256 * 1024 + 1),
                        json.dumps(self.event('UserPromptSubmit'))):
            result = subprocess.run(args, input=payload, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0)
            value = json.loads(result.stdout)
            self.assertIn('systemMessage', value)
            self.assertNotIn('hookSpecificOutput', value)

    def test_binding_id_and_mismatched_transcript_are_not_guessed(self):
        connections.connect(self.root, apply=True)
        with self.assertRaises(HouseError):
            connections.handle_event(self.root, self.event(), 'different-id')

    def test_native_inspection_reports_untrusted_hooks_without_starting_a_turn(self):
        connections.connect(self.root, apply=True)
        expected = connections.commands(self.root)
        listing = {'data':[{'errors':[], 'hooks':[
            {'eventName':name[0].lower()+name[1:], 'command':command, 'enabled':True,
             'trustStatus':'untrusted', 'currentHash':'hash-'+name, 'key':'key-'+name}
            for name,command in expected.items()]}]}
        rpc = mock.Mock()
        rpc.request.return_value = listing
        with mock.patch('house_engine.codex_rpc.CodexRPC', return_value=rpc), \
                mock.patch.object(connections.shutil, 'which', return_value='codex'):
            value = connections.doctor(self.root, native=True)
        self.assertEqual(value['native_trust'], 'requires_review')
        self.assertEqual(value['host_support'], 'native_hooks_discovered')
        self.assertEqual(value['screen_delivery'], 'unknown')
        rpc.request.assert_called_once_with('hooks/list', {'cwds':[str(self.project)]}, timeout=15)
        rpc.close.assert_called_once()

    def test_old_observation_is_stale_rather_than_live(self):
        plan = connections.connect(self.root, apply=True)
        connections.handle_event(self.root, self.event(), plan['connection_id'])
        state_path = self.root / 'main/integration/v2/state.json'
        state = json.loads(state_path.read_text())
        for value in state['conversations'].values():
            value['last_observed_at'] = '2000-01-01T00:00:00+00:00'
        state_path.write_text(json.dumps(state))
        self.assertEqual(connections.doctor(self.root)['event_observation'], 'stale')

    def test_native_parser_warning_is_preserved_when_hooks_cannot_load(self):
        connections.connect(self.root, apply=True)
        rpc = mock.Mock()
        warning = 'failed to parse hooks config: unknown field in existing user config'
        rpc.request.return_value = {'data': [{'hooks': [], 'errors': [], 'warnings': [warning]}]}
        with mock.patch('house_engine.codex_rpc.CodexRPC', return_value=rpc), \
                mock.patch.object(connections.shutil, 'which', return_value='codex'):
            result = connections.doctor(self.root, native=True)
        self.assertEqual(result['host_support'], 'not_discovered_or_conflicting')
        self.assertEqual(result['native_probe']['warnings'], [warning])
        self.assertEqual(result['native_trust'], 'unknown')

    @unittest.skipIf(os.name == 'nt', 'WSL forwarding is configured by its POSIX owner')
    def test_windows_forward_command_uses_explicit_distribution_and_path_conversion(self):
        plan = connections.connect(self.root, apply=True, wsl_distro='Ubuntu-test')
        command = plan['groups']['SessionStart']['hooks'][0]['commandWindows']
        self.assertIn('wsl.exe', command)
        self.assertIn('Ubuntu-test', command)
        self.assertIn('--windows-paths', command)
        with mock.patch.object(connections.subprocess, 'run', return_value=mock.Mock(returncode=1,stdout='')):
            with self.assertRaises(HouseError):
                connections.handle_event(self.root, self.event(transcript_path=r'C:\missing\rollout.jsonl'),
                                         plan['connection_id'], windows_paths=True)


if __name__ == '__main__':
    unittest.main()
