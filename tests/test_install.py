import importlib.util
import json
import os
from pathlib import Path
import plistlib
import sys
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        spec = importlib.util.spec_from_file_location('house_install', ROOT / 'install.py')
        self.install = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.install)

    def apply(self):
        return self.install.install(ROOT, self.base / 'bin', self.base / 'skills', self.base / 'common')

    def test_install_idempotent_links_to_source_without_changing_foreign_skills(self):
        self.apply()
        (self.base / 'skills/foreign').mkdir()
        self.apply()
        self.assertEqual((self.base / 'bin/house').resolve(), ROOT / 'bin/house')
        self.assertEqual((self.base / 'skills/house').resolve(), ROOT / 'skill')
        self.assertTrue((self.base / 'skills/foreign').is_dir())

    def test_conflict_preflight_leaves_all_targets_unchanged(self):
        (self.base / 'skills/house').mkdir(parents=True)
        (self.base / 'skills/house/note').write_text('mine')
        with self.assertRaises(self.install.HouseError):
            self.apply()
        self.assertFalse((self.base / 'bin').exists())
        self.assertFalse((self.base / 'common').exists())
        self.assertEqual((self.base / 'skills/house/note').read_text(), 'mine')

    def previous_checkout(self):
        repo = self.base / 'old-checkout'
        (repo / 'bin').mkdir(parents=True)
        (repo / 'bin/house').write_text('old launcher')
        (repo / 'skill/scripts').mkdir(parents=True)
        (repo / 'skill/SKILL.md').write_text('old skill')
        (repo / 'skill/scripts/house.py').write_text('old engine')
        return repo

    def test_upgrade_replaces_only_explicit_previous_checkout_links(self):
        previous = self.previous_checkout()
        targets = (self.base / 'bin', self.base / 'skills', self.base / 'common')
        self.install.install(previous, *targets)
        with self.assertRaises(self.install.HouseError):
            self.install.install(ROOT, *targets)
        self.install.install(ROOT, *targets, previous_repo=previous)
        self.assertEqual((targets[0] / 'house').resolve(), ROOT / 'bin/house')
        self.assertEqual((targets[1] / 'house').resolve(), ROOT / 'skill')
        self.assertEqual((previous / 'skill/SKILL.md').read_text(), 'old skill')

    def test_upgrade_refuses_links_from_another_checkout(self):
        previous = self.previous_checkout()
        targets = (self.base / 'bin', self.base / 'skills', self.base / 'common')
        self.install.install(previous, *targets)
        with self.assertRaises(self.install.HouseError):
            self.install.install(ROOT, *targets, previous_repo=self.base / 'unrelated')
        self.assertEqual((targets[0] / 'house').resolve(), previous / 'bin/house')
        self.assertEqual((targets[1] / 'house').resolve(), previous / 'skill')

    def test_windows_upgrade_requires_explicit_previous_managed_copy(self):
        previous = self.previous_checkout()
        targets = (self.base / 'bin', self.base / 'skills', self.base / 'common')
        self.install.install(previous, *targets, windows=True)
        with self.assertRaises(self.install.HouseError):
            self.install.install(ROOT, *targets, windows=True)
        result = self.install.install(ROOT, *targets, windows=True, previous_repo=previous)
        marker = json.loads((Path(result['skill']) / '.house-install.json').read_text())
        self.assertEqual(marker['repository'], str(ROOT))
        self.assertEqual((Path(result['skill']) / 'SKILL.md').read_bytes(), (ROOT / 'skill/SKILL.md').read_bytes())
        self.assertEqual((previous / 'skill/SKILL.md').read_text(), 'old skill')

    def test_windows_copy_install_is_idempotent_without_symlink_privileges(self):
        args = (ROOT, self.base / 'bin', self.base / 'skills', self.base / 'common')
        first = self.install.install(*args, windows=True)
        self.assertTrue(Path(first['command']).is_file())
        self.assertFalse(Path(first['skill']).is_symlink())
        self.assertEqual(self.install.install(*args, windows=True), first)
        (Path(first['skill']) / 'user-note.md').write_text('keep')
        with self.assertRaises(self.install.HouseError):
            self.install.install(*args, windows=True)
        self.assertEqual((Path(first['skill']) / 'user-note.md').read_text(), 'keep')

    def test_windows_copy_conflict_leaves_shared_state_absent(self):
        skill = self.base / 'skills/house'
        skill.mkdir(parents=True)
        (skill / 'note').write_text('mine')
        with self.assertRaises(self.install.HouseError):
            self.install.install(ROOT, self.base / 'bin', self.base / 'skills', self.base / 'common', windows=True)
        self.assertFalse((self.base / 'common').exists())

    def test_windows_launcher_symlink_is_never_followed(self):
        args = (ROOT, self.base / 'bin', self.base / 'skills', self.base / 'common')
        installed = self.install.install(*args, windows=True)
        command = Path(installed['command'])
        original = command.read_text()
        foreign = self.base / 'foreign.cmd'
        foreign.write_text(original)
        command.unlink()
        try:
            command.symlink_to(foreign)
        except OSError:
            self.skipTest('Host disallows unprivileged symlinks')
        with self.assertRaises(self.install.HouseError):
            self.install.install(*args, windows=True)
        self.assertEqual(foreign.read_text(), original)

    def test_windows_launcher_replace_failure_preserves_previous_install(self):
        args = (ROOT, self.base / 'bin', self.base / 'skills', self.base / 'common')
        installed = self.install.install(*args, windows=True)
        command = Path(installed['command'])
        original = command.read_bytes()
        original_skill = (Path(installed['skill']) / 'SKILL.md').read_bytes()
        replace = self.install.os.replace
        def fail_command(source, target):
            if str(target) == str(command):
                raise OSError('simulated launcher replacement failure')
            return replace(source, target)
        with mock.patch.object(self.install.os, 'replace', side_effect=fail_command):
            with self.assertRaises(OSError):
                self.install.install(*args, windows=True)
        self.assertEqual(command.read_bytes(), original)
        self.assertEqual((Path(installed['skill']) / 'SKILL.md').read_bytes(), original_skill)

    @unittest.skipUnless(os.name == 'nt', 'Native cmd.exe execution requires Windows')
    def test_windows_native_launcher_handles_metacharacter_source_paths(self):
        repo = self.base / 'repo & 100% (한글)'
        (repo / 'skill/scripts').mkdir(parents=True)
        (repo / 'skill/SKILL.md').write_text('fixture', encoding='utf-8')
        (repo / 'skill/scripts/house.py').write_text(
            'import json,sys\nprint(json.dumps(sys.argv[1:]))\n', encoding='utf-8')
        installed = self.install.install(repo, self.base / 'bin', self.base / 'skills', self.base / 'common', windows=True)
        value = '한글 argument & value'
        result = subprocess.run(['cmd.exe','/d','/c',installed['command'],value],
                                capture_output=True,text=True,encoding='utf-8')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout),[value])

    def test_weekly_plist_has_exact_argv_and_no_run_at_load(self):
        self.apply()
        path = self.install.write_schedule(ROOT, self.base / 'common', self.base / 'launch agents')
        value = plistlib.loads(path.read_bytes())
        self.assertEqual(value['StartCalendarInterval'], {'Weekday': 1, 'Hour': 9, 'Minute': 0})
        self.assertNotIn('RunAtLoad', value)
        self.assertEqual(value['ProgramArguments'][-1], str(self.base / 'common'))
        runner_script = Path(value['ProgramArguments'][1])
        self.assertTrue(runner_script.is_file())
        self.assertIn(str(self.base / 'common/runtime/runners'), str(runner_script))
        self.assertEqual(value['WorkingDirectory'], str(self.base / 'common'))
        self.assertNotIn(str(ROOT), value['ProgramArguments'][1])
        self.assertFalse(value.get('KeepAlive', False))

    def test_main_expands_home_once_for_install_and_scheduler(self):
        with mock.patch.object(sys, 'argv', ['install.py', '--home', '~/house-test', '--weekly']), \
                mock.patch.object(self.install, 'install', return_value={}) as install, \
                mock.patch.object(self.install, 'write_schedule', return_value=self.base / 'job.plist') as write, \
                mock.patch.object(self.install, 'activate_schedule') as activate:
            self.assertEqual(self.install.main(), 0)
        expected = Path.home() / 'house-test'
        self.assertEqual(install.call_args.args[3], expected)
        self.assertEqual(write.call_args.args[1], expected)
        self.assertEqual(activate.call_args.args[1], expected)

    def test_views_point_at_shared_data_and_runner_is_reusable(self):
        repo = self.base / 'checkout'
        (repo / 'bin').mkdir(parents=True)
        (repo / 'bin/house').write_text('fixture')
        (repo / 'skill').mkdir()
        home = self.base / 'common'
        self.install.install(repo, self.base / 'bin', self.base / 'skills', home, views=True)
        self.assertEqual((repo / 'workshop').resolve(), home / 'workshop')
        first = self.install.prepare_runner(ROOT, home)
        self.assertEqual(self.install.prepare_runner(ROOT, home), first)
        (first / 'scripts/weekly_scan.py').write_text('modified')
        with self.assertRaises(self.install.HouseError):
            self.install.prepare_runner(ROOT, home)


if __name__ == '__main__':
    unittest.main()
