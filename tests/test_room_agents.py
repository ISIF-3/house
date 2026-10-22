import json
from pathlib import Path
import tempfile
import unittest

from house_engine import room_agents
from house_engine.project import init_project
from house_engine.storage import HouseError


class RoomAgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.project = self.base / 'research'
        self.project.mkdir()
        self.home = self.base / 'common'
        self.root = Path(init_project(self.project, self.home)['root'])
        self.folder = self.root / 'main/sessions/chat-1'
        self.folder.mkdir(parents=True)
        self.transcript = self.base / 'root.jsonl'
        self.transcript.touch()
        (self.folder / 'session.json').write_text(json.dumps({'transcript_path': str(self.transcript)}))

    def event(self, name='PreToolUse', **fields):
        return dict(session_id='chat-1', transcript_path=str(self.transcript),
                    hook_event_name=name, tool_name='spawn_agent', turn_id='turn-1',
                    tool_use_id='call-1', tool_input={'agent_type': 'literature'}, **fields)

    def test_main_reserves_one_agent_per_room_and_reuses_its_id(self):
        first = self.event()
        self.assertEqual(room_agents.handle_hook(self.root, 'main', first), {})
        duplicate = dict(first, tool_use_id='call-2')
        self.assertEqual(room_agents.handle_hook(self.root, 'main', duplicate)['hookSpecificOutput']['permissionDecision'], 'deny')
        started = dict(session_id='chat-1', transcript_path=str(self.transcript),
                       hook_event_name='SubagentStart', agent_type='literature', agent_id='agent-1')
        reply = room_agents.handle_hook(self.root, 'main', started)
        self.assertIn('literature', reply['hookSpecificOutput']['additionalContext'])
        self.assertIn('agent-1', room_agents.handle_hook(self.root, 'main', duplicate)['hookSpecificOutput']['permissionDecisionReason'])
        self.assertTrue((self.root / 'literature/sessions/chat-1/agents/agent-1/work').is_dir())

    def test_default_task_specific_main_and_worker_spawning_are_denied(self):
        for role in ('default', 'worker', 'main', 'task_123'):
            event = self.event()
            event['tool_input'] = {'agent_type': role}
            self.assertEqual(room_agents.handle_hook(self.root, 'main', event)['hookSpecificOutput']['permissionDecision'], 'deny')
        self.assertEqual(room_agents.handle_hook(self.root, 'work', self.event())['hookSpecificOutput']['permissionDecision'], 'deny')

    def test_child_cannot_spawn_another_room_agent(self):
        event = self.event()
        event['transcript_path'] = str(self.base / 'child.jsonl')
        self.assertEqual(room_agents.handle_hook(self.root, 'main', event)['hookSpecificOutput']['permissionDecision'], 'deny')

    def test_failed_spawn_releases_only_its_own_reservation(self):
        event = self.event()
        room_agents.handle_hook(self.root, 'main', event)
        failure = dict(event, hook_event_name='PostToolUse', tool_response={'error': 'no free slot'})
        room_agents.handle_hook(self.root, 'main', failure)
        self.assertEqual(room_agents.handle_hook(self.root, 'main', dict(event, tool_use_id='call-2')), {})

    def test_post_tool_result_retains_agent_even_without_subagent_start(self):
        event = self.event()
        room_agents.handle_hook(self.root, 'main', event)
        room_agents.handle_hook(self.root, 'main', dict(event, hook_event_name='PostToolUse', tool_response='{"agent_id":"agent-2"}'))
        reply = room_agents.handle_hook(self.root, 'main', dict(event, tool_use_id='call-2'))
        self.assertIn('agent-2', reply['hookSpecificOutput']['permissionDecisionReason'])

    def test_failed_spawn_without_post_hook_recovers_only_from_its_native_result(self):
        event = self.event()
        room_agents.handle_hook(self.root, 'main', event)
        self.transcript.write_text(json.dumps({'type': 'response_item', 'payload': {
            'type': 'function_call_output', 'call_id': 'unrelated-call', 'output': '{"error":"failed"}'}}) + '\n')
        retry = dict(event, tool_use_id='call-2')
        self.assertEqual(room_agents.handle_hook(self.root, 'main', retry)['hookSpecificOutput']['permissionDecision'], 'deny')
        with self.transcript.open('a') as stream:
            stream.write(json.dumps({'type': 'response_item', 'payload': {
                'type': 'function_call_output', 'call_id': 'call-1', 'output': 'Provide one of: message or items'}}) + '\n')
        self.assertEqual(room_agents.handle_hook(self.root, 'main', retry), {})

    def test_native_spawn_failures_release_pending_without_a_post_hook(self):
        for error in ('Provide either message or items, but not both',
                      'collab spawn failed: transient error', '{"error":"failed"}'):
            event = self.event()
            room_agents.handle_hook(self.root, 'main', event)
            self.transcript.write_text(json.dumps({'type': 'response_item', 'payload': {
                'type': 'function_call_output', 'call_id': 'call-1', 'output': error}}) + '\n')
            self.assertEqual(room_agents.handle_hook(self.root, 'main', dict(event, tool_use_id='call-2')), {})
            (self.folder / 'room-agents.json').unlink()

    def test_successful_native_result_recovers_id_and_unknown_results_keep_reservation(self):
        event = self.event()
        room_agents.handle_hook(self.root, 'main', event)
        for output in ('still starting', '{"agent_id":"agent-3"}'):
            self.transcript.write_text(json.dumps({'type': 'response_item', 'payload': {
                'type': 'function_call_output', 'call_id': 'call-1', 'output': output}}) + '\n')
            reply = room_agents.handle_hook(self.root, 'main', dict(event, tool_use_id='call-2'))
            self.assertEqual(reply['hookSpecificOutput']['permissionDecision'], 'deny')
        self.assertIn('agent-3', reply['hookSpecificOutput']['permissionDecisionReason'])

    def test_missing_or_corrupt_parent_state_does_not_allow_spawn(self):
        (self.folder / 'session.json').write_text('{}')
        self.assertEqual(room_agents.handle_hook(self.root, 'main', self.event())['hookSpecificOutput']['permissionDecision'], 'deny')

    def test_agent_paths_cannot_escape_or_follow_symlinks(self):
        (self.folder / 'room-agents.json').symlink_to(self.base / 'external.json')
        with self.assertRaises(HouseError):
            room_agents.handle_hook(self.root, 'main', self.event())

    def test_worker_role_files_install_their_own_recursion_guard(self):
        contexts = {role: {'prompt': 'Read pinned ' + role} for role in room_agents.WORKER_ROOMS}
        flags = room_agents.role_overrides(self.root, self.home, contexts, write=True)
        for role in room_agents.WORKER_ROOMS:
            value = next(flag for flag in flags if flag.startswith('agents.' + role + '.config_file='))
            path = Path(json.loads(value.split('=', 1)[1]))
            text = path.read_text()
            self.assertIn('hooks.PreToolUse', text)
            self.assertIn('--room ' + role, text)
            self.assertNotIn('model =', text)


if __name__ == '__main__':
    unittest.main()
