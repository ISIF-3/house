#!/usr/bin/env python3
"""Link this checkout into the user's CLI and Codex skill directories."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'skill/scripts'))
from house_engine.project import bootstrap, default_home
from house_engine.storage import HouseError, atomic_json, read_json, safe_path

LABEL = 'research.house.weekly'


def check_link(path, target, previous_target=None):
    accepted = {target.resolve()}
    if previous_target is not None:
        accepted.add(Path(previous_target).resolve())
    if path.is_symlink() and path.resolve() in accepted:
        return
    if path.exists() or path.is_symlink():
        raise HouseError('Existing entry belongs to something else: ' + str(path))


def install(repo, bin_dir, skills_dir, home, views=False, windows=None, previous_repo=None):
    repo = Path(repo).resolve()
    windows = os.name == 'nt' if windows is None else windows
    if windows:
        return install_windows(repo, bin_dir, skills_dir, home, previous_repo)
    previous_repo = Path(previous_repo).resolve() if previous_repo is not None else None
    targets = [(Path(bin_dir) / 'house', repo / 'bin/house'),
               (Path(skills_dir) / 'house', repo / 'skill')]
    previous_targets = {path: previous_repo / target.relative_to(repo) for path, target in targets} \
                       if previous_repo else {}
    for path, target in targets:
        if not target.exists():
            raise HouseError('Incomplete source checkout: ' + str(target))
        check_link(path, target, previous_targets.get(path))
    view_links = [(repo / name, Path(home).resolve() / name) for name in ('shared', 'workshop', 'runtime')] \
                 if views and repo != Path(home).resolve() else []
    for path, target in view_links:
        check_link(path, target)
    bootstrap(Path(home))
    for path, target in targets + view_links:
        path.parent.mkdir(parents=True, exist_ok=True)
        check_link(path, target, previous_targets.get(path))
        if not path.is_symlink():
            path.symlink_to(target, target_is_directory=target.is_dir())
        elif path.resolve() != target.resolve():
            temporary = path.with_name('.' + path.name + '.house-' + uuid.uuid4().hex)
            try:
                temporary.symlink_to(target, target_is_directory=target.is_dir())
                os.replace(str(temporary), str(path))
            finally:
                if temporary.is_symlink():
                    temporary.unlink()
    return {'repository': str(repo), 'home': str(Path(home).resolve()),
            'command': str(targets[0][0]), 'skill': str(targets[1][0]),
            'custom_home_note': 'For a non-default home, set HOUSE_HOME to this home in every CLI/skill session.'}


def install_windows(repo, bin_dir, skills_dir, home, previous_repo=None):
    """A managed copied skill and .cmd launcher need no symlink privilege."""
    repo, bin_dir, skills_dir = Path(repo).resolve(), Path(bin_dir), Path(skills_dir)
    skill = skills_dir / 'house'
    command = bin_dir / 'house.cmd'
    marker = skill / '.house-install.json'
    source = repo / 'skill'
    if not source.is_dir() or not (source / 'SKILL.md').is_file():
        raise HouseError('Incomplete source checkout: ' + str(source))
    old = None
    if skill.exists():
        if skill.is_symlink() or not marker.is_file():
            raise HouseError('Existing entry belongs to something else: ' + str(skill))
        old = read_json(marker)
        accepted = {str(repo)}
        if previous_repo is not None:
            accepted.add(str(Path(previous_repo).resolve()))
        if old.get('repository') not in accepted:
            raise HouseError('Existing Windows skill belongs to another checkout')
        for name, digest in old.get('files', {}).items():
            path = safe_path(skill, name)
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise HouseError('Managed Windows skill was edited: ' + name)
        present = {p.relative_to(skill).as_posix() for p in skill.rglob('*')
                   if p.is_file() and p != marker and '__pycache__' not in p.parts}
        if present != set(old.get('files', {})):
            raise HouseError('Managed Windows skill has additional files; preserved')
    def batch_path(path):
        text = str(path)
        if any(char in text for char in ('"', '\r', '\n', '\0')):
            raise HouseError('Invalid Windows launcher path')
        return '"' + text.replace('%', '%%') + '"'
    line = ('@echo off\r\nsetlocal DisableDelayedExpansion\r\n' + batch_path(sys.executable)
            + ' -X utf8 ' + batch_path(repo / 'skill/scripts/house.py') + ' %*\r\n')
    if command.is_symlink() or command.exists() and (old is None or command.read_text() != old.get('command_text')):
        raise HouseError('Existing command belongs to something else: ' + str(command))
    bootstrap(Path(home))
    skills_dir.mkdir(parents=True, exist_ok=True)
    bin_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=str(skills_dir), prefix='.house-install-') as temporary:
        staged = Path(temporary) / 'house'
        shutil.copytree(source, staged, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.pyo'))
        files = {p.relative_to(staged).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in staged.rglob('*') if p.is_file()}
        # Text mode normalizes CRLF on Windows, including future ownership checks.
        normalized = line.replace('\r\n', '\n')
        atomic_json(staged / '.house-install.json',
                    {'schema_version': 1, 'repository': str(repo), 'files': files, 'command_text': normalized})
        backup = Path(temporary) / 'previous'
        if skill.exists():
            skill.rename(backup)
        try:
            staged.rename(skill)
            temporary_command = None
            try:
                with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=str(bin_dir),
                                                 prefix='.house-cmd-', delete=False) as stream:
                    temporary_command = Path(stream.name)
                    stream.write(normalized)
                    stream.flush()
                    os.fsync(stream.fileno())
                safe_path(bin_dir, command.name)
                os.replace(str(temporary_command), str(command))
            finally:
                if temporary_command is not None and temporary_command.exists():
                    temporary_command.unlink()
        except OSError:
            if skill.exists():
                shutil.rmtree(skill)
            if backup.exists():
                backup.rename(skill)
            raise
    return {'repository': str(repo), 'home': str(Path(home).resolve()), 'command': str(command),
            'skill': str(skill), 'installation_mode': 'windows-managed-copy',
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
    runner = prepare_runner(repo, home)
    value = {
        'Label': LABEL,
        'HouseHome': str(home),
        'ProgramArguments': [str(Path(sys.executable).resolve()), str(runner / 'scripts/weekly_scan.py'), str(home)],
        'WorkingDirectory': str(home),
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


def prepare_runner(repo, home):
    """Snapshot reviewed Python files outside Desktop's background privacy gate."""
    files = {'scripts/weekly_scan.py': (repo / 'scripts/weekly_scan.py').read_bytes()}
    for source in (repo / 'skill/scripts').rglob('*.py'):
        if '__pycache__' not in source.parts:
            files[str(source.relative_to(repo))] = source.read_bytes()
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    revision = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    parent = safe_path(home, 'runtime/runners')
    parent.mkdir(parents=True, exist_ok=True)
    target = safe_path(home, 'runtime/runners/' + revision)
    if not target.exists():
        with tempfile.TemporaryDirectory(dir=str(parent), prefix='.runner-') as temp:
            staged = Path(temp) / 'content'
            staged.mkdir()
            for relative, data in files.items():
                path = safe_path(staged, relative)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            os.rename(str(staged), str(target))
    for relative, expected in hashes.items():
        if hashlib.sha256(safe_path(target, relative).read_bytes()).hexdigest() != expected:
            raise HouseError('Weekly runner snapshot content changed: ' + relative)
    return target


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
    p.add_argument('--upgrade-from', type=Path, help='Replace only managed entries pointing at this exact previous checkout')
    p.add_argument('--weekly', action='store_true', help='Enable Monday 09:00 local weekly discovery on macOS')
    args = p.parse_args()
    args.home = args.home.expanduser().absolute()
    try:
        result = install(ROOT, args.bin_dir.expanduser(), args.skills_dir.expanduser(), args.home,
                         views=True, previous_repo=args.upgrade_from)
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
