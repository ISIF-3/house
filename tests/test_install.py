import importlib.util
from pathlib import Path
import plistlib
import sys
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
