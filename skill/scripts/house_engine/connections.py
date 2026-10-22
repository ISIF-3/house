"""Project-scoped, reversible native hook registration. Never grants hook trust."""
from datetime import datetime, timezone
import copy
import hashlib
import json
import ntpath
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import uuid

from . import __version__
from .project import _lock
from .storage import HouseError, atomic_json, locked, read_json, safe_path, windows_shell_command

EVENTS = ('SessionStart', 'UserPromptSubmit', 'PostToolUse', 'PostCompact', 'Stop')
LIMIT = 1024 * 1024


def _now():
    return datetime.now(timezone.utc).isoformat()


def environment():
    return {'os': os.name, 'platform': sys.platform, 'python': sys.version.split()[0],
            'wsl': 'microsoft' in platform.release().lower(),
            'environment_id': hashlib.sha256(
                (sys.platform + ':' + platform.node() + ':' + os.environ.get('WSL_DISTRO_NAME', '')).encode()
            ).hexdigest()[:24]}


def receipt_path(root):
    root = Path(root).resolve()
    home = Path(_lock(root)['home'])
    key = hashlib.sha256(str(root).encode()).hexdigest()[:24]
    return safe_path(home, 'runtime/connections/' + key + '/receipt.json')


def _text(path):
    path = safe_path(path.parent, path.name)
    if not path.exists():
        return None
    if path.stat().st_size > LIMIT:
        raise HouseError('Connection configuration exceeds 1 MiB: ' + str(path))
    return path.read_text(encoding='utf-8')


def _atomic_text(path, text):
    safe_path(path.parent, path.name)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=str(path.parent),
                                         prefix='.house-connect-', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        safe_path(path.parent, path.name)
        os.replace(str(temporary), str(path))
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _digest(text):
    return hashlib.sha256((text or '').encode()).hexdigest()


def _read_receipt(root):
    path = receipt_path(root)
    if not path.exists():
        return None
    value = read_json(path)
    if (value.get('schema_version') != 1 or value.get('root') != str(Path(root).resolve())
            or value.get('kind') != 'house-project-hooks'):
        raise HouseError('Invalid House connection receipt')
    return value


def _json_config(text):
    try:
        value = json.loads(text) if text is not None else {}
    except (ValueError, UnicodeError) as exc:
        raise HouseError('Invalid hooks.json; no configuration was changed') from exc
    if not isinstance(value, dict) or not isinstance(value.get('hooks', {}), dict):
        raise HouseError('hooks.json must contain a hooks object')
    for groups in value.get('hooks', {}).values():
        if not isinstance(groups, list):
            raise HouseError('hooks.json event entries must be arrays')
    return value


def _commands(root, connection_id, wsl_distro=None):
    base = [sys.executable, '-X', 'utf8', str(Path(__file__).resolve().parents[1] / 'house.py'),
            'connection', 'hook', '--project', str(root), '--connection-id', connection_id]
    posix = {event: shlex.join(base + ['--event', event]) for event in EVENTS}
    windows = {event: windows_shell_command(base + ['--event', event]) for event in EVENTS}
    if wsl_distro:
        if os.name == 'nt' or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', wsl_distro):
            raise HouseError('WSL forwarding requires a POSIX owner and a valid distribution name')
        windows = {event: windows_shell_command(
            ['wsl.exe', '--distribution', wsl_distro, '--exec', *base,
             '--event', event, '--windows-paths']) for event in EVENTS}
    return posix, windows


def _owned_groups(commands, windows):
    return {event: {'hooks': [{'type': 'command', 'command': commands[event],
                              'commandWindows': windows[event], 'timeout': 10}]}
            for event in EVENTS}


def _toml_block(connection_id, groups):
    lines = ['\n# BEGIN HOUSE CONNECTION ' + connection_id]
    for event, group in groups.items():
        hook = group['hooks'][0]
        lines += ['[[hooks.' + event + ']]', '[[hooks.' + event + '.hooks]]',
                  'type = "command"', 'command = ' + json.dumps(hook['command']),
                  'command_windows = ' + json.dumps(hook['commandWindows']), 'timeout = 10']
    lines += ['# END HOUSE CONNECTION ' + connection_id, '']
    return '\n'.join(lines)


def _location(root, receipt):
    project = Path(root).resolve().parent
    directory = safe_path(project, '.codex')
    if receipt and receipt.get('status') == 'registered':
        relative = receipt.get('config_relative')
        if relative not in ('.codex/hooks.json', '.codex/config.toml'):
            raise HouseError('Invalid connection configuration path')
        return safe_path(project, relative), receipt['format']
    toml = safe_path(project, '.codex/config.toml')
    text = _text(toml)
    if text and re.search(r'(?m)^\s*\[\[?hooks(?:[.\]])', text):
        return toml, 'toml'
    return safe_path(directory, 'hooks.json'), 'json'


def _registered_content(root, receipt):
    if not receipt or receipt.get('status') != 'registered':
        return False
    path, mode = _location(root, receipt)
    text = _text(path)
    if text is None:
        return False
    if mode == 'toml':
        return text.count(receipt['managed_text']) == 1
    value = _json_config(text)
    return all(value.get('hooks', {}).get(event, []).count(group) == 1
               for event, group in receipt['groups'].items())


def registered(root):
    receipt = _read_receipt(root)
    return bool(_registered_content(root, receipt))


def commands(root, origin='project'):
    """Return exactly the project-registered commands, without asserting trust."""
    receipt = _read_receipt(root)
    if not receipt or not _registered_content(root, receipt):
        raise HouseError('House project hook registration is absent or changed')
    field = 'commandWindows' if os.name == 'nt' else 'command'
    suffix = ' --origin launcher' if origin == 'launcher' else ''
    return {name: entry['hooks'][0][field] + suffix for name, entry in receipt['groups'].items()}


def connect(root, apply=False, wsl_distro=None):
    root = Path(root).resolve()
    receipt = _read_receipt(root)
    path, mode = _location(root, receipt)
    original = _text(path)
    project = root.parent
    config = _text(safe_path(project, '.codex/config.toml')) or ''
    features = re.search(r'(?ms)^\s*\[features\]\s*(.*?)(?=^\s*\[|\Z)', config)
    if features and re.search(r'(?m)^\s*hooks\s*=\s*false\s*(?:#.*)?$', features.group(1)):
        raise HouseError('Project hooks are explicitly disabled; House will not enable them')
    if receipt and receipt.get('status') == 'registered':
        if not _registered_content(root, receipt):
            raise HouseError('Registered House hooks changed; inspect the current configuration before reconnecting')
        if receipt.get('engine_script') != str(Path(__file__).resolve().parents[1] / 'house.py'):
            raise HouseError('Connection uses another engine; disconnect it before replacing its binding')
        return dict({key: value for key, value in receipt.items() if key not in ('original_text', 'managed_text')},
                    status='registered', changed=False, applied=False,
                    observation='not_asserted', trust='unknown')
    connection_id = str(uuid.uuid4())
    cmd, win = _commands(root, connection_id, wsl_distro)
    groups = _owned_groups(cmd, win)
    block = _toml_block(connection_id, groups) if mode == 'toml' else None
    if mode == 'json':
        value = _json_config(original)
        for event, group in groups.items():
            value.setdefault('hooks', {}).setdefault(event, []).append(copy.deepcopy(group))
        updated = json.dumps(value, ensure_ascii=False, indent=2) + '\n'
    else:
        updated = (original or '') + block
    info = dict(schema_version=1, kind='house-project-hooks', connection_id=connection_id,
                root=str(root), config_relative=str(path.relative_to(project)).replace(os.sep, '/'),
                format=mode, engine_version=__version__,
                engine_script=str(Path(__file__).resolve().parents[1] / 'house.py'),
                environment=environment(), status='registered',
                original_text=original, before_sha256=_digest(original), after_sha256=_digest(updated),
                groups=groups, managed_text=block, registered_at=_now(), wsl_distro=wsl_distro)
    plan = {key: val for key, val in info.items() if key not in ('original_text', 'managed_text')}
    plan.update(status='would_register', changed=True, applied=False, trust='requires_native_review',
                config_path=str(path), observation='not_observed',
                machine_local=True, exclude_new_config_from_git=original is None and mode == 'json',
                note='Project hook trust and host reload may be required. No native trust was granted.')
    if not apply:
        return plan
    location = receipt_path(root)
    location.parent.mkdir(parents=True, exist_ok=True)
    with locked(safe_path(location.parent, '.lock')):
        if _text(path) != original:
            raise HouseError('Configuration changed while preparing the connection')
        path.parent.mkdir(parents=True, exist_ok=True)
        if original is None and mode == 'json':
            ignore = safe_path(path.parent, '.gitignore')
            ignored = _text(ignore) or ''
            if '/hooks.json' not in ignored.splitlines():
                _atomic_text(ignore, ignored + ('' if not ignored or ignored.endswith('\n') else '\n') + '/hooks.json\n')
        _ignore_runtime(root)
        from .session_bridge import prepare
        prepare(root)
        _atomic_text(path, updated)
        try:
            # The exact prior text is retained locally, never printed in diagnostics.
            atomic_json(location, info)
        except (OSError, HouseError):
            if _text(path) == updated:
                if original is None:
                    path.unlink()
                else:
                    _atomic_text(path, original)
            raise
    return dict(plan, status='registered', applied=True, receipt_path=str(location))


def _ignore_runtime(root):
    path = safe_path(root, '.gitignore')
    text = _text(path) or ''
    line = '/main/integration/'
    if line not in text.splitlines():
        _atomic_text(path, text + ('' if not text or text.endswith('\n') else '\n') + line + '\n')


def disconnect(root, apply=False):
    root = Path(root).resolve()
    receipt = _read_receipt(root)
    if not receipt or receipt.get('status') != 'registered':
        return {'status': 'not_registered', 'changed': False, 'applied': False}
    path, mode = _location(root, receipt)
    text = _text(path)
    if not _registered_content(root, receipt):
        raise HouseError('House hooks were edited or removed; configuration was preserved')
    if mode == 'toml':
        updated = text.replace(receipt['managed_text'], '', 1)
    else:
        value = _json_config(text)
        before = _json_config(receipt['original_text'])
        for event, group in receipt['groups'].items():
            value['hooks'][event].remove(group)
            if not value['hooks'][event] and event not in before.get('hooks', {}):
                del value['hooks'][event]
        if not value.get('hooks') and 'hooks' not in before:
            value.pop('hooks', None)
        updated = (receipt['original_text'] if value == before and receipt['original_text'] is not None
                   else json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    result = {'status': 'would_disconnect', 'changed': True, 'applied': False,
              'config_path': str(path), 'preserves_session_records': True}
    if apply:
        with locked(safe_path(receipt_path(root).parent, '.lock')):
            if _text(path) != text:
                raise HouseError('Configuration changed while disconnecting')
            _atomic_text(path, updated)
            receipt.update(status='disconnected', disconnected_at=_now())
            try:
                atomic_json(receipt_path(root), receipt)
            except (OSError, HouseError):
                if _text(path) == updated:
                    _atomic_text(path, text)
                raise
        result.update(status='disconnected', applied=True)
    return result


def doctor(root, native=False, executable=None):
    root = Path(root).resolve()
    receipt = _read_receipt(root)
    valid = _registered_content(root, receipt)
    from .session_bridge import observations
    observed = observations(root)
    age = None
    if observed:
        try:
            latest = max(datetime.fromisoformat(item['last_observed_at']) for item in observed)
            age = max(0, (datetime.now(timezone.utc) - latest).total_seconds())
        except (KeyError, TypeError, ValueError):
            pass
    result = {'engine_version': __version__, 'root': str(root), 'environment': environment(),
            'registration': 'registered' if valid else 'changed' if receipt and receipt.get('status') == 'registered' else 'not_registered',
            'connection_id': receipt.get('connection_id') if receipt else None,
            'native_trust': 'unknown', 'host_support': 'not_verified',
            'event_observation': 'observed' if age is not None and age <= 300 else 'stale' if observed else 'not_observed',
            'observation_age_seconds': age,
            'screen_delivery': 'unknown', 'conversations': observed,
            'note': 'Registration, hook execution and visible UI warnings are separate checks. Cloud orchestration may not support local hooks.'}
    if native:
        result['native_probe'] = _native_probe(root, executable)
        result['native_trust'] = result['native_probe']['trust']
        result['host_support'] = result['native_probe']['discovery']
    return result


def _native_probe(root, executable):
    """Inspect the real host parser without starting any model turn or trusting hooks."""
    from .codex_rpc import CodexRPC
    binary = shutil.which(executable or os.environ.get('HOUSE_CODEX_BIN') or 'codex')
    if binary is None:
        return {'discovery': 'codex_not_found', 'trust': 'unknown', 'hooks': [], 'model_turns_started': 0}
    if not registered(root):
        return {'discovery': 'not_registered', 'trust': 'unknown', 'hooks': [], 'model_turns_started': 0}
    expected = set(commands(root).values())
    rpc = CodexRPC([binary, 'app-server', '--listen', 'stdio://'], cwd=str(Path(root).parent))
    try:
        rpc.start()
        listing = rpc.request('hooks/list', {'cwds': [str(Path(root).parent)]}, timeout=15)
        hooks = []
        errors = []
        warnings = []
        for row in listing.get('data', []):
            errors.extend(row.get('errors', []))
            warnings.extend(row.get('warnings', []))
            for hook in row.get('hooks', []):
                if hook.get('command') in expected:
                    hooks.append({key: hook.get(key) for key in
                                  ('key', 'eventName', 'currentHash', 'enabled', 'trustStatus')})
        names = {str(hook.get('eventName', '')).lower() for hook in hooks}
        complete = len(hooks) == len(EVENTS) and names == {name.lower() for name in EVENTS}
        trust = ('disabled' if any(hook.get('enabled') is False for hook in hooks)
                 else 'requires_review' if any(hook.get('trustStatus') == 'untrusted' for hook in hooks)
                 else 'trusted' if complete and all(hook.get('trustStatus') in ('trusted', 'managed') for hook in hooks)
                 else 'unknown')
        return {'discovery': 'native_hooks_discovered' if complete else 'not_discovered_or_conflicting',
                'trust': trust, 'hooks': hooks, 'errors': errors, 'warnings': warnings, 'model_turns_started': 0,
                'screen_delivery': 'unknown'}
    except (HouseError, OSError, ValueError) as exc:
        return {'discovery': 'probe_failed', 'trust': 'unknown', 'hooks': [],
                'error': str(exc), 'model_turns_started': 0}
    finally:
        rpc.close()


def handle_event(root, event, connection_id, windows_paths=False, origin='project'):
    receipt = _read_receipt(root)
    if (not receipt or receipt.get('status') != 'registered'
            or receipt.get('connection_id') != connection_id or not _registered_content(root, receipt)):
        raise HouseError('House connection binding is missing or changed; run house doctor')
    # An owned launcher always supplies its own callbacks. Project callbacks may
    # also run, but cannot become a second producer or disable that fallback.
    selected = os.environ.get('HOUSE_CONNECTION_ORIGIN', 'project')
    if selected != origin:
        return {}
    if windows_paths:
        if not receipt.get('wsl_distro') or os.name == 'nt':
            raise HouseError('Windows path forwarding was not configured for this connection')
        event = copy.deepcopy(event)
        for field in ('transcript_path', 'cwd'):
            value = event.get(field)
            if value and ntpath.isabs(value) and not Path(value).is_absolute():
                result = subprocess.run(['wslpath', '-u', value], capture_output=True, text=True, timeout=5)
                converted = result.stdout.strip()
                if result.returncode or not Path(converted).is_absolute():
                    raise HouseError('Cannot map the native Windows ' + field + ' into the configured WSL owner')
                event[field] = converted
    from .session_bridge import handle_event as observe, prepare
    prepare(root)
    room = os.environ.get('HOUSE_CONNECTION_ROOM', 'main')
    output = observe(root, event, room=room, endpoint_id=event.get('endpoint_id'))
    if output.get('systemMessage', '').startswith('House session observation unavailable:'):
        return output
    if origin == 'launcher':
        from . import room_agents
        if room_agents.bind_parent(root, room, event) and event.get('hook_event_name') == 'PostToolUse':
            room_agents.handle_hook(root, room, event)
    return output
