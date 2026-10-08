import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skill/scripts'))
from house_engine import cli

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
        self.env = dict(os.environ, HOUSE_HOME=str(self.home))

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
        (self.home / 'shared/releases/0.1.0/rooms/main/ROOM.md').write_text('altered')
        result = self.run_house('context', 'main', '--project', str(self.project / 'house'))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('hash', result.stderr)


if __name__ == '__main__':
    unittest.main()
