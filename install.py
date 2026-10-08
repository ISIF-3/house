#!/usr/bin/env python3
"""Link this checkout into the user's CLI and Codex skill directories."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'skill/scripts'))
from house_engine.project import bootstrap, default_home
from house_engine.storage import HouseError, atomic_json, read_json, safe_path

LABEL = 'research.house.weekly'


def check_link(path, target):
    if path.is_symlink() and path.resolve() == target.resolve():
        return
    if path.exists() or path.is_symlink():
        raise HouseError('Existing entry belongs to something else: ' + str(path))


def install(repo, bin_dir, skills_dir, home):
    repo = Path(repo).resolve()
    targets = [(Path(bin_dir) / 'house', repo / 'bin/house'),
               (Path(skills_dir) / 'house', repo / 'skill')]
    for path, target in targets:
        if not target.exists():
            raise HouseError('Incomplete source checkout: ' + str(target))
        check_link(path, target)
    bootstrap(Path(home))
    for path, target in targets:
        path.parent.mkdir(parents=True, exist_ok=True)
        check_link(path, target)
        if not path.is_symlink():
            path.symlink_to(target, target_is_directory=target.is_dir())
    return {'repository': str(repo), 'home': str(Path(home).resolve()),
            'command': str(targets[0][0]), 'skill': str(targets[1][0]),
            'custom_home_note': 'For a non-default home, set HOUSE_HOME to this home in every CLI/skill session.'}


def write_schedule(repo, home, directory):
    repo, home, directory = Path(repo).resolve(), Path(home).resolve(), Path(directory)
    path = safe_path(directory, LABEL + '.plist')
    if path.exists():
        try:
            old = plistlib.loads(path.read_bytes())
        except Exception as exc:
            raise HouseError('Unrecognized existing schedule: ' + str(path)) from exc
        if old.get('Label') != LABEL or old.get('HouseHome') != str(home):
            raise HouseError('Schedule belongs to a different House: ' + str(path))
    value = {
        'Label': LABEL,
        'HouseHome': str(home),
        'ProgramArguments': [str(Path(sys.executable).resolve()), str(repo / 'scripts/weekly_scan.py'), str(home)],
        'WorkingDirectory': str(repo),
        'StartCalendarInterval': {'Weekday': 1, 'Hour': 9, 'Minute': 0},
        'StandardOutPath': str(home / 'runtime/weekly.stdout.log'),
        'StandardErrorPath': str(home / 'runtime/weekly.stderr.log'),
        'ProcessType': 'Background',
    }
    directory.mkdir(parents=True, exist_ok=True)
    temporary = safe_path(directory, LABEL + '.plist.tmp')
    with temporary.open('xb') as stream:
        plistlib.dump(value, stream)
    try:
        os.replace(str(temporary), str(path))
    finally:
        if temporary.exists():
            temporary.unlink()
    return path


def activate_schedule(path, home):
    if sys.platform != 'darwin':
        raise HouseError('Automatic weekly installation currently supports macOS launchd only')
    domain = 'gui/' + str(os.getuid())
    # Reload only our specifically named job; never alter unrelated services.
    subprocess.run(['/bin/launchctl', 'bootout', domain + '/' + LABEL], capture_output=True)
    result = subprocess.run(['/bin/launchctl', 'bootstrap', domain, str(path)], capture_output=True, text=True)
    schedule_path = safe_path(home, 'workshop/schedule.json')
    schedule = read_json(schedule_path)
    schedule.update(enabled=result.returncode == 0, weekday='Monday', time='09:00',
                    timezone='system-local', backend='macOS launchd', plist=str(path),
                    notifications='change-only; macOS notification permissions apply')
    atomic_json(schedule_path, schedule)
    if result.returncode:
        raise HouseError('Weekly job not activated: ' + result.stderr.strip())


def main():
    p = argparse.ArgumentParser(description='Install the House checkout without copying research data')
    p.add_argument('--bin-dir', type=Path, default=Path.home() / '.local/bin')
    p.add_argument('--skills-dir', type=Path, default=Path.home() / '.agents/skills')
    p.add_argument('--home', type=Path, default=default_home(),
                   help='Shared state location; custom paths require HOUSE_HOME for later commands')
    p.add_argument('--weekly', action='store_true', help='Enable Monday 09:00 local weekly discovery on macOS')
    args = p.parse_args()
    args.home = args.home.expanduser().absolute()
    try:
        result = install(ROOT, args.bin_dir.expanduser(), args.skills_dir.expanduser(), args.home)
        if args.weekly:
            path = write_schedule(ROOT, args.home, Path.home() / 'Library/LaunchAgents')
            activate_schedule(path, args.home)
            result['weekly'] = str(path)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (HouseError, OSError, ValueError) as exc:
        print('House install: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
