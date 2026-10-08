import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class WeeklyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name).resolve()
        spec = importlib.util.spec_from_file_location('house_weekly', ROOT / 'scripts/weekly_scan.py')
        self.weekly = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.weekly)

    def test_notifies_once_for_change_and_is_quiet_when_unchanged(self):
        notices = []
        def scan(home):
            p = home / 'workshop/notifications/change.json'
            p.write_text(json.dumps({'title': 'house v0.1.1 #literature skill update'}))
            return {'errors': []}
        self.assertEqual(self.weekly.run(self.home, scan, lambda message: notices.append(message) or True), 0)
        self.assertEqual(self.weekly.run(self.home, scan, lambda message: notices.append(message) or True), 0)
        self.assertEqual(len(notices), 1)
        self.assertIn('literature', notices[0])

    def test_notification_failure_does_not_claim_delivery_and_retries(self):
        def scan(home):
            (home / 'workshop/notifications/change.json').write_text('{"title":"candidate"}')
            return {'errors': []}
        self.assertEqual(self.weekly.run(self.home, scan, lambda message: False), 1)
        self.assertEqual(self.weekly.run(self.home, scan, lambda message: True), 0)

    def test_scan_error_is_recorded_and_notified_once(self):
        notices = []
        def scan(home):
            raise self.weekly.HouseError('HTTP 503 unavailable')
        for _ in range(2):
            self.assertEqual(self.weekly.run(self.home, scan, lambda message: notices.append(message) or True), 1)
        self.assertEqual(len(notices), 1)
        self.assertIn('error', notices[0])

    def test_returned_scan_errors_are_notified_and_deduplicated(self):
        notices = []
        def scan(home):
            return {'errors': [{'source': 'github_search', 'room': 'literature', 'error': 'HTTP 403'}]}
        for _ in range(2):
            self.assertEqual(self.weekly.run(self.home, scan, lambda message: notices.append(message) or True), 1)
        self.assertEqual(len(notices), 1)


if __name__ == '__main__':
    unittest.main()
