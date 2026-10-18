"""One reusable agent per research room, scoped to a House CLI invocation."""

import hashlib
import json
from pathlib import Path
import shlex
import sys

from .storage import HouseError, atomic_json, locked, read_json, safe_path, validate_id

WORKER_ROOMS = ('literature', 'work', 'output', 'discussion')
POLICY = (
    'House 위임 정책: Main만 literature, work, output, discussion 역할에 위임할 수 있습니다. '
    'spawn_agent의 agent_type에는 이 네 이름만 사용하세요. 방마다 에이전트는 한 명이며 '
    '기존 ID에 send_input으로 후속 작업을 보내고 필요하면 resume_agent로 재개하세요. '
    '작업별 에이전트, default/worker/explorer 역할, Main 복제와 재귀 위임은 금지합니다. '
    '하위 방은 다른 에이전트를 만들지 않고 배정된 방의 파일과 세션 작업 폴더에서만 작성합니다. '
    'Main이 공유 진행·결정 기록을 관리하고 같은 파일을 동시에 배정하지 않습니다.'
)


def _folder(root, session_id):
    return safe_path(root, 'main/sessions/' + validate_id(session_id))


def _deny(reason):
    return {'hookSpecificOutput': {'hookEventName': 'PreToolUse',
                                  'permissionDecision': 'deny',
                                  'permissionDecisionReason': reason}}


def _registry(folder):
    path = safe_path(folder, 'room-agents.json')
    value = read_json(path) if path.exists() else {'schema_version': 1, 'rooms': {}}
    if value.get('schema_version') != 1 or not isinstance(value.get('rooms'), dict):
        raise HouseError('Invalid House room-agent registry')
    for role, entry in value['rooms'].items():
        if role not in WORKER_ROOMS or not isinstance(entry, dict):
            raise HouseError('Invalid House room-agent role')
        if entry.get('agent_id'):
            validate_id(entry['agent_id'])
    return value


def _is_parent(folder, event):
    state = read_json(safe_path(folder, 'session.json'))
    original, actual = state.get('transcript_path'), event.get('transcript_path')
    return bool(original and actual and Path(original).resolve() == Path(actual).resolve())


def _completed_spawn(entry, transcript):
    """Recover a missed result by call ID; elapsed time never releases a slot."""
    if not entry or entry.get('agent_id') or not entry.get('tool_use_id'):
        return {}
    from .sessions import _transcript
    for _, _, value in reversed(_transcript(transcript)[0]):
        payload = value.get('payload')
        if (value.get('type') != 'response_item' or not isinstance(payload, dict)
                or payload.get('type') != 'function_call_output'
                or payload.get('call_id') != entry['tool_use_id']):
            continue
        output = payload.get('output')
        try:
            result = json.loads(output) if isinstance(output, str) else output
        except ValueError:
            result = None
        if isinstance(result, dict):
            if result.get('agent_id'):
                return {'agent_id': validate_id(result['agent_id'])}
            if result.get('error'):
                return {'error': True}
        # Native 0.154.0 emits plain FunctionCallError text and no PostToolUse
        # on failure. Recognize confirmed spawn errors; leave unknowns reserved.
        if isinstance(output, str):
            text = output.removeprefix('Error: ')
            if (text in ('Provide one of: message or items',
                         'Provide either message or items, but not both')
                    or text.startswith('collab spawn failed:')):
                return {'error': True}
        return {}
    return {}


def handle_hook(root, room, event):
    """Reserve before spawning; native SubagentStart supplies the actual agent ID."""
    name = event.get('hook_event_name')
    tool = str(event.get('tool_name', '')).split('.')[-1]
    spawning = tool in ('spawn_agent', 'Agent')
    if name not in ('PreToolUse', 'PostToolUse', 'SubagentStart'):
        return {}
    if name != 'SubagentStart' and not spawning:
        return {}
    if room != 'main':
        return _deny('House의 하위 방은 재귀 위임할 수 없습니다. 현재 방에서 작업하세요.') if name == 'PreToolUse' else {}
    folder = _folder(root, event.get('session_id'))
    if name == 'PreToolUse' and (not folder.is_dir() or not _is_parent(folder, event)):
        return _deny('Main의 원래 세션에서만 방 에이전트를 만들 수 있습니다. 재귀 위임은 금지합니다.')
    if not folder.is_dir():
        return {}
    if name == 'PostToolUse' and not _is_parent(folder, event):
        return {}
    inputs = event.get('tool_input')
    role = event.get('agent_type') if name == 'SubagentStart' else (inputs.get('agent_type') if isinstance(inputs, dict) else None)
    if role not in WORKER_ROOMS:
        return _deny('허용 역할: literature, work, output, discussion. 역할을 명시하고 기존 방 에이전트를 재사용하세요.') if name == 'PreToolUse' else {}
    with locked(safe_path(folder, '.room-agents.lock')):
        registry = _registry(folder)
        rooms = registry['rooms']
        entry = rooms.get(role)
        if name == 'PreToolUse':
            recovered = _completed_spawn(entry, event.get('transcript_path'))
            if recovered.get('agent_id'):
                entry['agent_id'] = recovered['agent_id']
                atomic_json(safe_path(folder, 'room-agents.json'), registry)
            elif recovered.get('error'):
                del rooms[role]
                entry = None
            if entry:
                agent_id = entry.get('agent_id')
                reason = ('기존 {} 에이전트 {}에 send_input/resume_agent를 사용하세요.'.format(role, agent_id)
                          if agent_id else '{} 에이전트를 만드는 중입니다. /subagents에서 확인한 뒤 재사용하세요.'.format(role))
                return _deny(reason)
            rooms[role] = {'tool_use_id': event.get('tool_use_id'), 'agent_id': None}
        elif name == 'SubagentStart':
            agent_id = validate_id(event.get('agent_id'))
            if entry and entry.get('agent_id') not in (None, agent_id):
                return {'systemMessage': 'House: {} 방의 기존 에이전트를 재사용하세요.'.format(role)}
            rooms[role] = dict(entry or {}, agent_id=agent_id)
            agent_folder = safe_path(root, role + '/sessions/' + validate_id(event['session_id']) + '/agents/' + agent_id)
            safe_path(agent_folder, 'work').mkdir(parents=True, exist_ok=True)
            note = safe_path(agent_folder, 'SESSION.md')
            if not note.exists():
                with note.open('x', encoding='utf-8') as stream:
                    stream.write('# {} 방 에이전트\n\nID: {}\n\n작업 기록과 산출물 경로를 여기에 남긴다.\n'.format(role, agent_id))
            atomic_json(safe_path(folder, 'room-agents.json'), registry)
            text = ('당신은 House의 {} 방 담당입니다. 작업 폴더: {}. '
                    '이 방의 ROOM.md/GUIDE.md와 Main의 배정 내용을 읽고 이 폴더의 SESSION.md에 진행을 남기세요. '
                    '추가 에이전트를 만들지 마세요. 공유 Main 기록은 직접 고치지 말고 결과 경로를 Main에 전달하세요.').format(role, agent_folder)
            return {'hookSpecificOutput': {'hookEventName': 'SubagentStart', 'additionalContext': text}}
        else:
            response = event.get('tool_response')
            if isinstance(response, str):
                try:
                    response = json.loads(response)
                except ValueError:
                    response = None
            if entry and entry.get('tool_use_id') == event.get('tool_use_id') and isinstance(response, dict):
                if response.get('agent_id'):
                    entry['agent_id'] = validate_id(response['agent_id'])
                elif response.get('error') and not entry.get('agent_id'):
                    del rooms[role]
        atomic_json(safe_path(folder, 'room-agents.json'), registry)
    return {}


def role_overrides(root, home, contexts, write=False):
    """Return TOML overrides; each content-addressed role file preserves the pin."""
    result = ['agents.max_threads=4', 'agents.max_depth=1']
    for role in WORKER_ROOMS:
        instructions = contexts[role]['prompt'] + '\n' + POLICY
        content = 'developer_instructions = ' + json.dumps(instructions, ensure_ascii=False) + '\n'
        guard = shlex.join([sys.executable, str(Path(__file__).resolve().parents[1] / 'house.py'),
                            'session', 'hook', '--project', str(root), '--room', role, '--event', 'PreToolUse'])
        content += ('\n[[hooks.PreToolUse]]\nmatcher = "^(spawn_agent|Agent)$"\n'
                    '[[hooks.PreToolUse.hooks]]\ntype = "command"\ncommand = '
                    + json.dumps(guard, ensure_ascii=False) + '\ntimeout = 10\n')
        digest = hashlib.sha256(content.encode('utf-8')).hexdigest()
        path = safe_path(home, 'runtime/room-agents/' + digest + '/' + role + '.toml')
        if write:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                if path.read_text(encoding='utf-8') != content:
                    raise HouseError('House agent configuration hash mismatch: ' + str(path))
            else:
                with path.open('x', encoding='utf-8') as stream:
                    stream.write(content)
        result += ['agents.' + role + '.description=' + json.dumps('House ' + role + ' 방의 고정 담당'),
                   'agents.' + role + '.config_file=' + json.dumps(str(path), ensure_ascii=False)]
    return result
