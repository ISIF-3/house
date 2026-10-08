#!/usr/bin/env python3
"""Weekly discovery, with change-only local notifications and no model call."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skill/scripts'))
from house_engine.project import bootstrap
from house_engine.storage import HouseError, atomic_json, locked, read_json, safe_path


def notify(message):
    if sys.platform != 'darwin':
        return False
    script = 'on run argv\n display notification (item 1 of argv) with title "House"\nend run'
    result = subprocess.run(['/usr/bin/osascript', '-e', script, message],
                            capture_output=True, text=True, timeout=20)
    # This confirms OS submission, not whether the user saw a banner.
    return result.returncode == 0


def run(home, scan_fn=None, notify_fn=None):
    home = Path(home).resolve()
    bootstrap(home)
    if scan_fn is None:
        from house_engine.updates import scan
        scan_fn = scan
    notify_fn = notify_fn or notify
    with locked(safe_path(home, 'runtime/weekly.lock')):
        failed = False
        try:
            report = scan_fn(home)
            failed = bool(report.get('errors'))
            if failed:
                error = json.dumps(report['errors'], ensure_ascii=False, sort_keys=True)
                key = hashlib.sha256(error.encode()).hexdigest()[:20]
                atomic_json(safe_path(home, 'workshop/notifications/weekly-error-' + key + '.json'),
                            {'title': 'house #workshop scan error', 'errors': report['errors']})
                print(error, file=sys.stderr)
        except (HouseError, OSError, ValueError) as exc:
            failed = True
            error = str(exc)
            key = hashlib.sha256(error.encode()).hexdigest()[:20]
            atomic_json(safe_path(home, 'workshop/notifications/weekly-error-' + key + '.json'),
                        {'title': 'house #workshop scan error', 'error': error})
            print(error, file=sys.stderr)
        delivery_path = safe_path(home, 'runtime/notification-delivery.json')
        delivered = read_json(delivery_path).get('submitted_to_os', []) if delivery_path.exists() else []
        pending = []
        for path in sorted(safe_path(home, 'workshop/notifications').glob('*.json')):
            path = safe_path(home, 'workshop/notifications/' + path.name)
            if path.name not in delivered:
                pending.append((path.name, read_json(path)))
        if pending:
            title = pending[-1][1].get('title') or pending[-1][1].get('message') or 'house #workshop update'
            message = '{} · {}건 · Desktop/house/workshop에서 확인'.format(str(title)[:160], len(pending))
            try:
                submitted = notify_fn(message)
            except (OSError, subprocess.SubprocessError):
                submitted = False
            if submitted:
                delivered.extend(name for name, _ in pending)
                atomic_json(delivery_path, {'submitted_to_os': delivered})
            else:
                failed = True
                print('House: OS notification was not submitted; records remain in workshop/notifications', file=sys.stderr)
        return 1 if failed else 0


if __name__ == '__main__':
    try:
        raise SystemExit(run(Path(sys.argv[1])))
    except (HouseError, OSError, ValueError, IndexError) as exc:
        print('House weekly: ' + str(exc), file=sys.stderr)
        raise SystemExit(2)
