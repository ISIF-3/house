import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from house_engine.storage import HouseError, locked, windows_shell_command


class LockTests(unittest.TestCase):
    def test_windows_hook_quotes_no_space_metacharacters_and_trailing_slashes(self):
        result = windows_shell_command(['python.exe', 'repo&name/script.py', 'a|b', 'a^b', 'trailing\\'])
        self.assertEqual(result, '"python.exe" "repo&name/script.py" "a|b" "a^b" "trailing\\\\"')

    def test_windows_hook_rejects_expansion_and_ambiguous_arguments(self):
        for value in ('%PATH%', 'name!x!', 'a"b', 'a\rb', 'a\nb', 'a\0b'):
            with self.subTest(value=value), self.assertRaises(HouseError):
                windows_shell_command(['python.exe', value])

    @unittest.skipUnless(os.name == 'nt', 'Actual Windows cmd.exe required')
    def test_windows_native_hook_shell_preserves_metacharacter_arguments(self):
        import json
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / 'repo&name'
            folder.mkdir()
            script = folder / 'emit.py'
            script.write_text('import json,sys\nprint(json.dumps(sys.argv[1:]))\n')
            args = ['value&name', 'a|b', 'a^b', 'trailing\\', '한글']
            command = windows_shell_command([sys.executable, str(script), *args])
            result = subprocess.run(command, shell=True, capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), args)

    def test_real_os_lock_contends_across_processes_and_releases(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'resource.lock'
            script = """import sys
from pathlib import Path
from house_engine.storage import HouseError, locked
try:
    with locked(Path(sys.argv[1]), timeout=0.05):
        pass
except HouseError as exc:
    print(exc)
    sys.exit(7)
"""
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
            with locked(path):
                blocked = subprocess.run([sys.executable, '-c', script, str(path)],
                                         env=env, text=True, capture_output=True)
                self.assertEqual(blocked.returncode, 7, blocked.stderr)
                self.assertIn('resource is in use', blocked.stdout)
            released = subprocess.run([sys.executable, '-c', script, str(path)],
                                      env=env, text=True, capture_output=True)
            self.assertEqual(released.returncode, 0, released.stderr)
            self.assertTrue(path.exists())

    def test_invalid_wait_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            for timeout in (-1, 11, float('nan')):
                with self.subTest(timeout=timeout), self.assertRaises(HouseError):
                    with locked(Path(directory) / 'resource.lock', timeout=timeout):
                        pass
