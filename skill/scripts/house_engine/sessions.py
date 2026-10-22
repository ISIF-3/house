"""Room-local native session records; recommendations never create a chat.

Transcript parsing is best effort because rollout JSONL is not a stable API.
PostCompact has no documented delivery ID: actual compacted records permit
deduplication; without one, each hook delivery counts once (duplicates may count).
"""
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import uuid

from .project import ROOMS, _lock
from .storage import HouseError, atomic_json, locked, read_json, safe_path, validate_id

TAIL_BYTES = 2 * 1024 * 1024
LINE_BYTES = 64 * 1024
HEADINGS = ('목표', '확정 결정', '실행 중인 작업', '다음 행동')
INITIAL_HANDOFF = '# 세션 인계\n\n' + '\n\n'.join('## ' + h + '\n기록 필요' for h in HEADINGS) + '\n'


def _now():
    return datetime.now(timezone.utc).isoformat()


def workspace(root, room, session_id):
    root = Path(root).expanduser().absolute()
    _lock(root)
    if room not in ROOMS:
        raise HouseError('Unknown House room: ' + str(room))
    return safe_path(root, room + '/sessions/' + validate_id(session_id))


def _load(directory):
    value = read_json(safe_path(directory, 'session.json'))
    if (value.get('schema_version') != 1 or value.get('session_id') != directory.name
            or value.get('room') != directory.parent.parent.name
            or value.get('count_scope') not in ('session', 'observed_since_attach')):
        raise HouseError('Invalid House session state: ' + str(directory))
    for field in ('auto_compactions', 'manual_compactions'):
        if type(value.get(field)) is not int or value[field] < 0:
            raise HouseError('Invalid session counter: ' + field)
    if (type(value.get('review_required')) is not bool
            or type(value.get('context_warning_emitted')) is not bool
            or not isinstance(value.get('compact_keys'), list)
            or any(not isinstance(key, str) for key in value['compact_keys'])):
        raise HouseError('Invalid House session review state')
    percent = value.get('context_percent')
    if percent is not None and (type(percent) not in (int, float) or not 0 <= percent <= 100):
        raise HouseError('Invalid session context observation')
    floor = value.get('token_floor', {})
    if (not isinstance(floor, dict) or type(floor.get('offset', 0)) is not int
            or floor.get('offset', 0) < 0 or not isinstance(floor.get('identity', ''), (str, type(None)))):
        raise HouseError('Invalid session transcript offset')
    saved = value.get('checkpoint')
    if saved is not None:
        _snapshot_path(directory, saved)
    transcript = value.get('transcript_path')
    if transcript is not None and (not isinstance(transcript, str) or not Path(transcript).is_absolute()):
        raise HouseError('Invalid session transcript path')
    if value.get('status') not in ('active', 'cli_exited', 'cli_failed'):
        raise HouseError('Invalid session lifecycle state')
    return value


def _snapshot_path(directory, saved, verify=False):
    if (not isinstance(saved, dict) or not isinstance(saved.get('path'), str)
            or not isinstance(saved.get('saved_at'), str)
            or not isinstance(saved.get('hash'), str) or not re.fullmatch(r'[0-9a-f]{64}', saved['hash'])
            or type(saved.get('auto_compactions')) is not int or saved['auto_compactions'] < 0):
        raise HouseError('Invalid saved session checkpoint')
    try:
        relative = Path(saved['path']).relative_to(directory)
    except ValueError as exc:
        raise HouseError('Checkpoint must belong to its session') from exc
    if len(relative.parts) != 2 or relative.parts[0] != 'snapshots' or relative.suffix != '.md':
        raise HouseError('Invalid checkpoint snapshot path')
    path = safe_path(directory, relative.as_posix())
    if verify:
        try:
            with path.open('rb') as stream:
                content = stream.read(32001)
        except OSError as exc:
            raise HouseError('Saved checkpoint is unavailable') from exc
        if hashlib.sha256(content).hexdigest() != saved['hash']:
            raise HouseError('Saved checkpoint content changed: ' + str(path))
    return path


def _save(directory, state, event):
    state['updated_at'] = _now()
    atomic_json(safe_path(directory, 'session.json'), state)
    # Only locally selected metadata is logged, never arbitrary hook input.
    entry = {key: state.get(key) for key in ('session_id', 'updated_at', 'auto_compactions',
                                            'manual_compactions', 'review_required', 'context_percent')}
    entry['event'] = event
    with safe_path(directory, 'events.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(entry, ensure_ascii=False) + '\n')


def _transcript(path):
    if not path:
        return [], None, 0
    source = Path(path).expanduser().absolute()
    source = safe_path(source.parent, source.name)
    if not source.exists():
        return [], None, 0
    with source.open('rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise HouseError('Transcript must be a regular file')
        identity = '{}:{}'.format(info.st_dev, info.st_ino)
        start = max(0, info.st_size - TAIL_BYTES)
        stream.seek(max(0, start - 1))
        previous = stream.read(1) if start else b'\n'
        data = stream.read(TAIL_BYTES)
    lines = data.splitlines(keepends=True)
    offset = start
    records = []
    for index, line in enumerate(lines):
        end = offset + len(line)
        if (line.endswith(b'\n') and len(line) <= LINE_BYTES
                and (index != 0 or previous == b'\n')):
            try:
                value = json.loads(line)
                if isinstance(value, dict):
                    records.append((offset, end, value))
            except (ValueError, UnicodeError, RecursionError):
                pass
        offset = end
    return records, identity, info.st_size


def _observe_tokens(state, records, identity):
    floor = state.get('token_floor', {})
    state['context_percent'] = None
    newest = None
    for offset, end, value in records:
        if floor.get('identity') == identity and offset < floor.get('offset', 0):
            continue
        payload = value.get('payload')
        if value.get('type') != 'event_msg' or not isinstance(payload, dict) or payload.get('type') != 'token_count':
            continue
        newest = (end, payload.get('info'))
    if newest is None:
        return
    end, info = newest
    if not isinstance(info, dict):
        return
    usage = info.get('last_token_usage')
    used = usage.get('total_tokens') if isinstance(usage, dict) else None
    window = info.get('model_context_window')
    if type(used) not in (int, float) or type(window) not in (int, float):
        return
    try:
        used, window = float(used), float(window)
    except OverflowError:
        return
    if not math.isfinite(used) or not math.isfinite(window) or not 0 <= used <= window or window <= 0:
        return
    state.update(context_percent=100 * (used / window),
                 token_observation={'identity': identity, 'offset': end, 'used': used,
                                    'window': window, 'at': _now()})


MODEL_CONTEXT_EVENTS = ('SessionStart', 'UserPromptSubmit', 'PostToolUse')


def _output(name, instructions='', message='', notice=''):
    output = {}
    context = instructions
    if notice:
        context = ('House 관측 알림 — 다음 사용자용 메시지에서 아래 경고를 짧게 한 번 표시하세요. '
                   '일반 채팅 메시지로 전달하며 native 팝업이 표시됐다고 주장하지 마세요. '
                   '현재 작업이나 계산을 종료하지 말고 인계를 준비하세요.\n' + notice
                   + ('\n' + instructions if instructions else ''))
    if name in MODEL_CONTEXT_EVENTS and context:
        output['hookSpecificOutput'] = {'hookEventName': name, 'additionalContext': context}
    elif instructions:
        message = instructions + ('\n' + message if message else '')
    if message:
        output['systemMessage'] = message
    return output


def list_sessions(root, room=None):
    root = Path(root).expanduser().absolute()
    _lock(root)
    if room is not None and room not in ROOMS:
        raise HouseError('Unknown House room: ' + str(room))
    result = []
    for selected in (room,) if room else ROOMS:
        parent = safe_path(root, selected + '/sessions')
        if not parent.exists():
            continue
        for child in sorted(parent.iterdir()):
            child = safe_path(parent, child.name)
            if child.is_dir() and safe_path(child, 'session.json').exists():
                value = _load(child)
                result.append(dict(value, workspace=str(child)))
    return result


def _checkpoint_text(directory):
    path = safe_path(directory, 'handoff.md')
    with path.open(encoding='utf-8') as stream:
        text = stream.read(8001)
    if len(text) > 8000:
        raise HouseError('Session handoff must be at most 8,000 characters')
    sections = re.split(r'^##\s+(.+?)\s*$', text, flags=re.MULTILINE)
    if any(sections[1::2].count(heading) != 1 for heading in HEADINGS):
        raise HouseError('Session handoff requires each heading exactly once')
    content = dict(zip(sections[1::2], sections[2::2]))
    for heading in HEADINGS:
        body = content.get(heading, '').strip()
        if (not body or re.search(r'기록 필요|작성 필요|아직 기록|미작성|TODO|TBD|미기록', body, re.IGNORECASE)
                or not re.sub(r'[\s#*_\-]', '', body)):
            raise HouseError('Session handoff requires actual content: ' + heading)
    return text


def checkpoint(root, room, session_id, boundary=False):
    directory = workspace(root, room, session_id)
    if not directory.is_dir():
        raise HouseError('House session has not started: ' + session_id)
    with locked(safe_path(directory, '.lock')):
        state = _load(directory)
        text = _checkpoint_text(directory)
        folder = safe_path(directory, 'snapshots')
        folder.mkdir(exist_ok=True)
        snapshot = safe_path(folder, uuid.uuid4().hex + '.md')
        content = text.encode('utf-8')
        with snapshot.open('xb') as stream:
            stream.write(content)
        saved = {'path': str(snapshot), 'hash': hashlib.sha256(content).hexdigest(),
                 'saved_at': _now(), 'auto_compactions': state['auto_compactions']}
        recommend = bool(boundary and state['auto_compactions'] >= 10 and not state.get('recommended_at'))
        state.update(checkpoint=saved, review_required=False)
        if recommend:
            state['recommended_at'] = _now()
        _save(directory, state, 'checkpoint_boundary' if boundary else 'checkpoint')
        return dict(state, recommend_new_session=recommend)


def handle_hook(root, room, event):
    if not isinstance(event, dict):
        raise HouseError('Hook event must be a JSON object')
    name = event.get('hook_event_name')
    if name not in ('SessionStart', 'UserPromptSubmit', 'PostCompact', 'PostToolUse', 'Stop', 'PreCompact'):
        return {}
    directory = workspace(root, room, event.get('session_id'))
    if not directory.exists() and name != 'SessionStart':
        return {}
    transcript = event.get('transcript_path')
    if transcript is not None and (not isinstance(transcript, str) or not Path(transcript).is_absolute()):
        raise HouseError('Transcript path must be absolute')
    state_path = safe_path(directory, 'session.json')
    if state_path.exists():
        existing = _load(directory)
        if transcript != existing.get('transcript_path'):
            return {}
    directory.mkdir(parents=True, exist_ok=True)
    with locked(safe_path(directory, '.lock')):
        if state_path.exists():
            state = _load(directory)
            if transcript != state.get('transcript_path'):
                return {}
        else:
            state = {'schema_version': 1, 'session_id': directory.name, 'room': room,
                     'started_at': _now(), 'status': 'active', 'launch_id': os.environ.get('HOUSE_LAUNCH_ID'),
                     'transcript_path': transcript, 'auto_compactions': 0, 'manual_compactions': 0,
                     'count_scope': 'session' if event.get('source') in ('startup', 'clear') else 'observed_since_attach',
                     'review_required': False, 'context_percent': None, 'context_warning_emitted': False,
                     'checkpoint': None, 'recommended_at': None, 'compact_keys': []}
            for relative, content in [('SESSION.md', '# House 세션\n\n세션: ' + directory.name + '\n방: ' + room + '\n'),
                                      ('handoff.md', INITIAL_HANDOFF)]:
                path = safe_path(directory, relative)
                if not path.exists():
                    with path.open('x', encoding='utf-8') as stream:
                        stream.write(content)
            safe_path(directory, 'work').mkdir(exist_ok=True)
        records, identity, size = _transcript(transcript)
        floor = state.get('token_floor', {})
        if floor and identity and (floor.get('identity') != identity or size < floor.get('offset', 0)):
            # A replaced/truncated rollout may contain old copied usage: wait for an append.
            state['token_floor'] = {'identity': identity, 'offset': size}
        instructions, message = '', ''
        if name == 'PostCompact' and event.get('trigger') in ('auto', 'manual'):
            compacted = [(offset, value) for offset, _, value in records if value.get('type') == 'compacted']
            key = None
            if compacted:
                offset, value = compacted[-1]
                key = '{}:{}:{}:{}'.format(identity, offset, value.get('timestamp', ''), event['trigger'])
            if key is not None and key in state['compact_keys']:
                return {}
            if key:
                state['compact_keys'] = (state['compact_keys'] + [key])[-256:]
            field = 'auto_compactions' if event['trigger'] == 'auto' else 'manual_compactions'
            state[field] += 1
            state.update(context_percent=None, context_warning_emitted=False,
                         token_floor={'identity': identity, 'offset': size})
            state['pending_chat_notices'] = []
            if event['trigger'] == 'auto':
                state['review_required'] = True
                message = '자동 압축 {}회 관측: 세션 인계를 검토하세요. 압축 횟수만으로 새 채팅을 열지 않습니다.'.format(state[field])
        elif name != 'PreCompact':
            _observe_tokens(state, records, identity)
        if name == 'SessionStart':
            state['status'] = 'active'
            if os.environ.get('HOUSE_LAUNCH_ID'):
                state['launch_id'] = os.environ['HOUSE_LAUNCH_ID']
            state.pop('ended_at', None)
            state.pop('exit_code', None)
            instructions = '먼저 {}와 {}를 읽고 현재 상태를 확인하세요. 세션 작업은 {}에 기록하세요.'.format(
                Path(root).resolve() / 'main/handoff.md', directory / 'handoff.md', directory / 'work')
            candidates = [(item['checkpoint'], Path(item['workspace'])) for item in list_sessions(root, room)
                          if item['session_id'] != directory.name and item.get('checkpoint')]
            saved, origin = state.get('checkpoint'), directory
            if saved is None and candidates:
                saved, origin = max(candidates, key=lambda item: item[0]['saved_at'])
            if saved:
                instructions += '\n이 방의 최근 저장된 인계: ' + str(_snapshot_path(origin, saved, verify=True))
            if state['review_required']:
                instructions += '\n자동 압축 후 검토 필요: 목표·확정 결정·실행 중인 작업·다음 행동을 확인하고 세션 인계를 저장하세요.'
            if state['count_scope'] == 'observed_since_attach':
                instructions += '\n과거 압축 횟수는 알 수 없습니다. 표시 횟수는 이 세션을 관측하기 시작한 이후 누적입니다.'
        percent = state.get('context_percent')
        if percent is not None and percent >= 70 and not state['context_warning_emitted']:
            state['context_warning_emitted'] = True
            message = '컨텍스트 {:.1f}% 관측: 다음 작업 전에 세션 인계를 준비하세요.'.format(percent)
        pending = state.get('pending_chat_notices', [])
        if message and message not in pending:
            pending.append(message)
        notice = '\n'.join(pending) if name in MODEL_CONTEXT_EVENTS else ''
        state['pending_chat_notices'] = [] if notice else pending
        if name != 'PreCompact':
            _save(directory, state, name)
        return _output(name, instructions, message, notice)


def finish_launch(root, room, launch_id, exit_code):
    for item in list_sessions(root, room):
        if launch_id and item.get('launch_id') == launch_id:
            directory = workspace(root, room, item['session_id'])
            with locked(safe_path(directory, '.lock')):
                state = _load(directory)
                if state.get('launch_id') != launch_id:
                    continue
                state.update(status='cli_exited' if exit_code == 0 else 'cli_failed',
                             exit_code=exit_code, ended_at=_now())
                _save(directory, state, 'launch_exit')
