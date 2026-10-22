"""Loopback research-room UI backed by one owned Codex app-server."""
from collections import deque
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import secrets
import shlex
import shutil
import sys
import threading
from urllib.parse import parse_qs, urlsplit
import uuid
import webbrowser

from .project import ROOMS
from .storage import HouseError, atomic_json, locked, read_json, safe_path, validate_id, shell_command
from . import sessions

LABELS = {'main': 'Main · 감독관', 'literature': 'Literature · 문헌', 'work': 'Work · 계산·분석',
          'output': 'Output · 결과', 'discussion': 'Discussion · 해석'}
HOOK_EVENTS = ('SessionStart', 'PostCompact', 'UserPromptSubmit', 'PostToolUse',
               'PreToolUse', 'SubagentStart', 'Stop')
APPROVALS = ('item/commandExecution/requestApproval', 'item/fileChange/requestApproval')
TEXT_EXTENSIONS = ('.md', '.txt', '.json', '.csv', '.py', '.r', '.jl', '.toml', '.yaml', '.yml', '.log')


def _now():
    return datetime.now(timezone.utc).isoformat()


def _invalid_constant(value):
    raise ValueError('JSON에는 유한한 숫자만 사용할 수 있습니다.')


def _model_name(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 256


def _fast_tier(tiers):
    return next((tier['id'] for tier in tiers
                 if tier['name'].strip().lower() == 'fast' or tier['id'] in ('fast', 'priority')), None)


def _tier_alias(value, tiers):
    if value in (None, 'default'):
        return None
    return (_fast_tier(tiers) or value) if value == 'fast' else value


def _catalog_model(item):
    efforts, tiers = item.get('supportedReasoningEfforts', []), item.get('serviceTiers', [])
    for options, key in ((efforts, 'reasoningEffort'), (tiers, 'id')):
        if not isinstance(options, list) or len(options) > 32:
            raise HouseError('Codex 모델의 추론강도·서비스 목록이 올바르지 않습니다.')
        for option in options:
            if (not isinstance(option, dict) or not _model_name(option.get(key))
                    or not isinstance(option.get('description'), str) or len(option['description']) > 4000
                    or key == 'id' and not _model_name(option.get('name'))):
                raise HouseError('Codex 모델의 추론강도·서비스 항목이 올바르지 않습니다.')
    default_effort, default_tier = item.get('defaultReasoningEffort'), item.get('defaultServiceTier')
    if ('defaultReasoningEffort' in item and not _model_name(default_effort)
            or default_tier is not None and not _model_name(default_tier)):
        raise HouseError('Codex 모델의 기본 추론강도·서비스가 올바르지 않습니다.')
    if 'supportedReasoningEfforts' not in item:
        default_effort = None
    elif default_effort is not None and default_effort not in {e['reasoningEffort'] for e in efforts}:
        raise HouseError('Codex 기본 추론강도가 지원 목록에 없습니다.')
    default_tier = _tier_alias(default_tier, tiers) if 'serviceTiers' in item else None
    if default_tier is not None and default_tier not in {tier['id'] for tier in tiers}:
        raise HouseError('Codex 기본 서비스가 지원 목록에 없습니다.')
    return dict(model=item['model'], display_name=item['displayName'],
                supported_efforts=[{'effort': e['reasoningEffort'], 'description': e['description']} for e in efforts],
                default_effort=default_effort,
                service_tiers=[{key: tier[key] for key in ('id', 'name', 'description')} for tier in tiers],
                default_service_tier=default_tier)


def _hook_commands(root):
    base = [sys.executable, str(Path(__file__).resolve().parents[1] / 'house.py'),
            'session', 'hook', '--project', str(root), '--room', 'main']
    result = {event: shell_command(base + ['--event', event]) for event in HOOK_EVENTS}
    from .connections import registered, commands
    if registered(root):
        result.update(commands(root, origin='launcher'))
    return result


def authorized(headers, port, token, api=True):
    host = headers.get('Host', '')
    if host not in ('127.0.0.1:' + str(port), 'localhost:' + str(port)):
        return False
    if headers.get('Origin') not in (None, 'http://' + host):
        return False
    value = headers.get('Authorization', '')
    return not api or secrets.compare_digest(value, 'Bearer ' + token)


class WebController:
    def __init__(self, root, home, prompt, rpc_factory, resume=None, launch_id=None):
        self.root, self.home = Path(root).resolve(), Path(home).resolve()
        self.prompt, self.rpc_factory, self.resume = prompt, rpc_factory, resume
        self.launch_id = launch_id
        self.condition = threading.Condition(threading.RLock())
        self.rpc = None
        self.generation = 0
        self.closed = self.connected = self.ready = False
        self.error = None
        self.models, self.current_model, self.selected_model, self.model_error = [], None, None, None
        self.current_effort = self.selected_effort = self.current_service_tier = self.selected_fast = None
        self.thread_id = self.session_id = self.transcript = self.turn_id = None
        self.requests, self.queue, self.threads = {}, [], {}
        self.current_request = None
        self.completed_turns = deque(maxlen=128)
        self.worker = None
        self.rooms = {room: dict(id=room, label=LABELS[room], status='idle' if room == 'main' else 'unassigned',
                     task='', agent_id=None, last_activity=None, events=[], messages=[], files=[])
                     for room in ROOMS}

    def start(self):
        if self.resume:
            folder = sessions.workspace(self.root, 'main', self.resume)
            state = read_json(safe_path(folder, 'session.json'))
            if state.get('session_id') != self.resume or state.get('room') != 'main':
                raise HouseError('이 House에 속한 Main 세션만 재개할 수 있습니다.')
        self._connect([])
        self._verify_hooks()
        self.worker = threading.Thread(target=self._work, daemon=True, name='house-web-work')
        self.worker.start()

    def _connect(self, extra):
        with self.condition:
            self.generation += 1
            generation = self.generation
            old = self.rpc
            self.connected = self.ready = False
        if old:
            old.close()
        client = self.rpc_factory(extra, lambda value: self.on_message(value, generation))
        self.rpc = client
        client.start()
        with self.condition:
            self.connected = True
            self.error = None
        self._load_models()

    def _load_models(self):
        models, seen, cursors = [], set(), set()
        cursor, error = None, None
        try:
            for _ in range(5):
                params = {'limit': 100, 'includeHidden': False}
                if cursor is not None:
                    params['cursor'] = cursor
                result = self.rpc.request('model/list', params, timeout=5)
                data = result.get('data') if isinstance(result, dict) else None
                if not isinstance(data, list) or len(data) > 100:
                    raise HouseError('Codex 모델 목록 응답이 올바르지 않습니다.')
                for item in data:
                    if (not isinstance(item, dict) or not _model_name(item.get('model'))
                            or not _model_name(item.get('displayName'))):
                        raise HouseError('Codex 모델 목록 항목이 올바르지 않습니다.')
                    model = _catalog_model(item)
                    if item.get('hidden') is not True and item['model'] not in seen:
                        models.append(model)
                        seen.add(item['model'])
                cursor = result.get('nextCursor')
                if cursor is None:
                    break
                if not isinstance(cursor, str) or not cursor or len(cursor) > 2048 or cursor in cursors:
                    raise HouseError('Codex 모델 목록 페이지가 올바르지 않습니다.')
                cursors.add(cursor)
            else:
                raise HouseError('Codex 모델 목록이 5페이지 한도를 초과했습니다.')
        except (HouseError, OSError, ValueError, TypeError) as exc:
            models, error = [], str(exc)[:2000]
        with self.condition:
            self.models, self.model_error = models, error

    def _verify_hooks(self, approved=None):
        result = self.rpc.request('hooks/list', {'cwds': [str(self.root / 'main')]})
        expected = _hook_commands(self.root)
        found = {}
        for entry in result.get('data', []):
            if entry.get('errors'):
                raise HouseError('Codex 훅 설정 오류를 먼저 확인하세요.')
            for hook in entry.get('hooks', []):
                name = hook.get('eventName')
                if isinstance(name, str) and name:
                    name = name[0].upper() + name[1:]
                if name in expected and hook.get('command') == expected[name]:
                    if name in found:
                        raise HouseError('중복된 House 훅이 있습니다: ' + name)
                    found[name] = hook
        if set(found) != set(expected):
            raise HouseError('이 Codex가 House의 필수 훅을 노출하지 않습니다. CLI 버전을 확인하세요.')
        hooks = list(found.values())
        if any(not hook.get('enabled') for hook in hooks):
            raise HouseError('House 필수 훅이 Codex 설정에서 비활성화되어 있습니다. 사용자 설정을 확인하세요.')
        if approved is not None:
            before = {(h['key'], h['currentHash'], h['command']) for h in approved}
            after = {(h['key'], h['currentHash'], h['command']) for h in hooks}
            if before != after:
                raise HouseError('승인한 훅과 현재 훅이 달라졌습니다. House를 다시 시작해 확인하세요.')
        if any(hook.get('trustStatus') not in ('trusted', 'managed') for hook in hooks):
            if approved is not None:
                raise HouseError('Codex가 이번 실행의 훅 신뢰를 확인하지 못했습니다.')
            with self.condition:
                self.requests['house-hooks'] = dict(id='house-hooks', method='house/hooks/trust', room='main',
                    params={'hooks': hooks, 'message': '세션 기록·방별 고정 에이전트 제한 훅을 이번 실행에 허용합니다. 전역 설정은 바꾸지 않습니다.'})
                self.rooms['main']['status'] = 'waiting'
            return
        self._open_thread()

    def _open_thread(self):
        params = {'cwd': str(self.root / 'main'), 'developerInstructions': self.prompt}
        if self.resume:
            params['threadId'] = self.resume
        result = self.rpc.request('thread/resume' if self.resume else 'thread/start', params)
        thread = result['thread']
        thread_id = validate_id(thread['id'])
        session_id = validate_id(thread.get('sessionId') or thread_id)
        if self.resume and session_id != self.resume:
            raise HouseError('재개한 세션 ID가 House 기록과 다릅니다.')
        transcript = thread.get('path')
        with self.condition:
            self.thread_id, self.session_id, self.transcript = thread_id, session_id, transcript
            self.current_model = result.get('model') if _model_name(result.get('model')) else None
            self.current_effort = result.get('reasoningEffort') if _model_name(result.get('reasoningEffort')) else None
            self.current_service_tier = result.get('serviceTier') if _model_name(result.get('serviceTier')) else None
            self.threads[thread_id] = 'main'
            self.rooms['main']['agent_id'] = thread_id
            self.rooms['main']['status'] = 'idle'
            self._status('main', thread.get('status', {}))
            # Native hooks normally create this; attach also restores older saved sessions.
            sessions.handle_hook(self.root, 'main', dict(hook_event_name='SessionStart', session_id=session_id,
                transcript_path=transcript, source='resume' if self.resume else 'startup'))
            folder = sessions.workspace(self.root, 'main', session_id)
            if self.launch_id:
                with locked(safe_path(folder, '.lock')):
                    state_path = safe_path(folder, 'session.json')
                    state = read_json(state_path)
                    state['launch_id'] = validate_id(self.launch_id)
                    atomic_json(state_path, state)
            self._restore_registry()
            path = safe_path(folder, 'web-queue.json')
            if path.exists():
                saved = read_json(path)
                items = saved.get('items', [])
                if not isinstance(items, list) or any(not isinstance(i, dict) or i.get('room') not in ROOMS
                        or not isinstance(i.get('text'), str) or len(i['text']) > 8000
                        or any(i.get(k) is not None and not _model_name(i[k])
                               for k in ('model', 'effort', 'service_tier')) for i in items):
                    raise HouseError('저장된 웹 지시함이 올바르지 않습니다.')
                for item in items[-100:]:
                    if item.get('status') == 'running':
                        item['status'] = 'delivery_unknown'
                self.queue = items[-100:] + self.queue
            self.ready = True
            self._event('main', 'Codex 연결과 House 세션 기록을 확인했습니다.')
            self._save_queue()
            self.condition.notify_all()

    def _event(self, room, text):
        value = self.rooms[room]
        value['last_activity'] = _now()
        value['events'] = (value['events'] + [{'at': value['last_activity'], 'text': str(text)[:2000]}])[-40:]

    def _message(self, room, key, role, text, append=False):
        messages = self.rooms[room]['messages']
        existing = next((item for item in messages if item['id'] == key), None)
        if existing:
            existing['text'] = ((existing['text'] if append else '') + str(text))[-24000:]
        else:
            messages.append({'id': key, 'role': role, 'text': str(text)[-24000:]})
        self.rooms[room]['messages'] = messages[-50:]

    def _status(self, room, status):
        kind = status.get('type') if isinstance(status, dict) else status
        flags = status.get('activeFlags', []) if isinstance(status, dict) else []
        self.rooms[room]['status'] = ('waiting' if any(f in flags for f in ('waitingOnApproval', 'waitingOnUserInput'))
                                    else {'active': 'working', 'idle': 'idle', 'notLoaded': 'unknown',
                                          'systemError': 'failed'}.get(kind, 'unknown'))

    def _restore_registry(self):
        if not self.session_id:
            return
        registry = safe_path(sessions.workspace(self.root, 'main', self.session_id), 'room-agents.json')
        if not registry.exists():
            return
        for role, entry in read_json(registry).get('rooms', {}).items():
            if role not in ROOMS[1:] or not isinstance(entry, dict) or not entry.get('agent_id'):
                continue
            identifier = validate_id(entry['agent_id'])
            if not self.rooms[role]['agent_id']:
                self.rooms[role].update(agent_id=identifier, status='unknown')
            self.threads[identifier] = role

    def on_message(self, value, generation=None):
        with self.condition:
            if self.closed or generation is not None and generation != self.generation:
                return
            method, params = value.get('method', ''), value.get('params') or {}
            if method == '_house/disconnected':
                self.connected = self.ready = False
                self.error = str(params.get('reason', 'Codex 연결이 종료되었습니다.'))[:2000]
                for room in self.rooms.values():
                    if room['agent_id']:
                        room['status'] = 'unknown'
                self.condition.notify_all()
                return
            if method == 'thread/started':
                thread = params.get('thread', {})
                role, identifier = thread.get('agentRole'), thread.get('id')
                if (role in ROOMS[1:] and thread.get('parentThreadId') == self.thread_id
                        and thread.get('sessionId') == self.session_id and isinstance(identifier, str)):
                    validate_id(identifier)
                    current = self.rooms[role]['agent_id']
                    if current and current != identifier:
                        self.error = '같은 방의 에이전트가 중복 관측되었습니다: ' + role
                        return
                    self.threads[identifier] = role
                    self.rooms[role]['agent_id'] = identifier
                    self.rooms[role]['task'] = str(thread.get('preview', ''))[:2000]
                    self._status(role, thread.get('status', {}))
                    self._event(role, '방 담당 에이전트 연결')
                return
            room = self.threads.get(params.get('threadId'))
            if room is None:
                self._restore_registry()
                room = self.threads.get(params.get('threadId'))
            if room is None:
                if 'id' in value:
                    self.rpc.respond(value['id'], error={'code': -32601, 'message': 'House does not support this request: ' + method})
                    self.error = '지원되지 않는 Codex 확인 요청입니다: ' + method
                return
            if 'id' in value:
                if method in APPROVALS or method in ('item/tool/requestUserInput', 'item/permissions/requestApproval'):
                    key = str(value['id'])
                    self.requests[key] = dict(id=value['id'], method=method, room=room, params=params)
                    self.rooms[room]['status'] = 'waiting'
                    self._event(room, '연구자 확인 요청')
                else:
                    self.rpc.respond(value['id'], error={'code': -32601, 'message': 'House does not support this request: ' + method})
                    self._event(room, '지원되지 않는 확인 요청: ' + method)
                return
            if method == 'serverRequest/resolved':
                self.requests.pop(str(params.get('requestId')), None)
            elif method == 'thread/status/changed':
                self._status(room, params.get('status'))
            elif method == 'thread/settings/updated' and room == 'main':
                settings = params.get('threadSettings')
                if isinstance(settings, dict) and _model_name(settings.get('model')):
                    self.current_model = settings['model']
                    self.current_effort = settings.get('effort') if _model_name(settings.get('effort')) else None
                    self.current_service_tier = settings.get('serviceTier') if _model_name(settings.get('serviceTier')) else None
            elif method == 'turn/started':
                if (params.get('threadId'), params.get('turn', {}).get('id')) in self.completed_turns:
                    return
                self.rooms[room]['status'] = 'working'
                if room == 'main':
                    self.turn_id = params.get('turn', {}).get('id')
                self._event(room, '작업 응답 시작')
            elif method == 'turn/completed':
                turn = params.get('turn', {})
                key = (params.get('threadId'), turn.get('id'))
                if key in self.completed_turns:
                    return
                self.completed_turns.append(key)
                status = turn.get('status')
                self.rooms[room]['status'] = 'failed' if status == 'failed' else 'idle'
                self._event(room, '요청 처리 ' + str(status) + ' · 연구 완료 판단과 구분합니다.')
                if room == 'main':
                    self.turn_id = None
                    if self.current_request:
                        self.current_request['status'] = status if status in ('completed', 'failed', 'interrupted') else 'delivery_unknown'
                        self.current_request = None
                        self._save_queue()
                    self.condition.notify_all()
            elif method == 'item/agentMessage/delta':
                self._message(room, params.get('itemId', 'stream'), 'assistant', params.get('delta', ''), True)
            elif method in ('item/started', 'item/completed'):
                item = params.get('item', {})
                kind = item.get('type')
                if kind == 'agentMessage' and method == 'item/completed':
                    self._message(room, item.get('id', 'reply'), 'assistant', item.get('text', ''))
                elif kind == 'commandExecution':
                    self._event(room, ('실행: ' if method == 'item/started' else '명령 결과: ') + str(item.get('command', '')))
                elif kind == 'fileChange' and method == 'item/completed':
                    self._event(room, '파일 변경 관측')
                elif kind in ('webSearch', 'collabAgentToolCall', 'subAgentActivity', 'contextCompaction'):
                    if kind == 'collabAgentToolCall' and item.get('prompt'):
                        self._restore_registry()
                        for identifier in item.get('receiverThreadIds', []):
                            target = self.threads.get(identifier)
                            if target and target != 'main':
                                self.rooms[target]['task'] = str(item['prompt'])[:2000]
                    self._event(room, kind + (' 시작' if method == 'item/started' else ' 완료'))
            elif method == 'error':
                self.error = str(params.get('error', {}).get('message', 'Codex 오류'))[:2000]
                self._event(room, self.error)

    def _save_queue(self):
        if self.session_id:
            folder = sessions.workspace(self.root, 'main', self.session_id)
            atomic_json(safe_path(folder, 'web-queue.json'), {'schema_version': 1, 'items': self.queue[-100:]})

    def select_model(self, model, effort=None, fast=None):
        with self.condition:
            if self.closed or not self.connected or not self.ready:
                raise HouseError('Codex 연결과 House 운영 훅 허용을 확인한 뒤 모델을 선택하세요.')
            catalog = next((i for i in self.models if i['model'] == (model or self.current_model)), {})
            if model is not None and (not _model_name(model) or not catalog):
                raise HouseError('현재 Codex 모델 목록에서 모델을 선택하세요.')
            if effort is not None and (not _model_name(effort)
                    or effort not in {e['effort'] for e in catalog.get('supported_efforts', [])}):
                raise HouseError('선택한 모델이 지원하는 추론강도를 선택하세요.')
            if fast is not None and not isinstance(fast, bool):
                raise HouseError('Fast는 bool 또는 null이어야 합니다.')
            if fast is True and not _fast_tier(catalog.get('service_tiers', [])):
                raise HouseError('선택한 모델은 Fast 서비스를 제공하지 않습니다.')
            self.selected_model, self.selected_effort, self.selected_fast = model, effort, fast
            return {'selected_model': model, 'selected_effort': effort, 'selected_fast': fast}

    def _submission_settings(self):
        model = self.selected_model or self.current_model
        catalog = next((i for i in self.models if i['model'] == model), {})
        efforts = {e['effort'] for e in catalog.get('supported_efforts', [])}
        effort = self.selected_effort
        if effort is not None and effort not in efforts:
            raise HouseError('현재 모델이 바뀌었습니다. 지원하는 추론강도를 다시 선택하세요.')
        if effort is None:
            effort = (self.current_effort if model == self.current_model
                      and (not efforts or self.current_effort in efforts)
                      else catalog.get('default_effort'))
        tiers = catalog.get('service_tiers', [])
        tier = None
        if self.selected_fast is True:
            tier = _fast_tier(tiers)
            if not tier:
                raise HouseError('현재 모델이 바뀌었습니다. Fast 지원 여부를 다시 확인하세요.')
        elif self.selected_fast is None:
            observed = _tier_alias(self.current_service_tier, tiers)
            tier = (observed if model == self.current_model
                    and (not tiers or observed is None or observed in {t['id'] for t in tiers})
                    else catalog.get('default_service_tier'))
        return model, effort, tier

    def submit(self, room, text):
        if room not in ROOMS or not isinstance(text, str) or not text.strip() or len(text) > 8000:
            raise HouseError('방과 1~8,000자의 작업 지시를 입력하세요.')
        with self.condition:
            if self.closed or not self.connected or not self.ready:
                raise HouseError('Codex 연결과 House 운영 훅 허용을 확인한 뒤 다시 보내세요.')
            if sum(item['status'] == 'queued' for item in self.queue) >= 20:
                raise HouseError('대기 지시가 20개입니다. 기존 작업을 확인하세요.')
            model, effort, tier = self._submission_settings()
            item = dict(id=uuid.uuid4().hex, room=room, text=text.strip(), status='queued', at=_now(),
                        model=model, effort=effort, service_tier=tier)
            self.queue.append(item)
            self.queue = self.queue[-100:]
            self._save_queue()
            self.condition.notify_all()
            return dict(item)

    def _work(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.closed or (self.ready and self.connected
                    and self.rooms['main']['status'] in ('idle', 'failed') and not self.requests
                    and self.current_request is None and self.turn_id is None
                    and any(i['status'] == 'queued' for i in self.queue)))
                if self.closed:
                    return
                item = next(i for i in self.queue if i['status'] == 'queued')
                item['status'] = 'running'
                self.current_request = item
                self.rooms['main'].update(status='working', task=item['text'])
                self._message('main', item['id'], 'user', '[' + item['room'] + '] ' + item['text'])
                self._save_queue()
            try:
                text = ('연구자의 웹 지시입니다. 대상 방: ' + item['room'] + '. '
                        'Main이 기존 담당자와 공유 기록을 조율해 처리하세요. 새 작업별 에이전트를 만들지 마세요.\n\n' + item['text'])
                params = {'threadId': self.thread_id, 'input': [{'type': 'text', 'text': text}]}
                if item.get('model') is not None:
                    params['model'] = item['model']
                if item.get('effort') is not None:
                    params['effort'] = item['effort']
                if 'service_tier' in item:
                    params['serviceTier'] = item['service_tier']
                self.rpc.request('turn/start', params)
            except HouseError as exc:
                with self.condition:
                    # A timeout can mean native acceptance without an acknowledgement; do not retry.
                    item['status'] = 'delivery_unknown'
                    self.current_request = None
                    self.rooms['main']['status'] = 'unknown'
                    self.error = str(exc)
                    self._save_queue()

    def respond(self, identifier, decision=None, answers=None):
        with self.condition:
            key = str(identifier)
            request = self.requests.get(key)
            if not request:
                raise HouseError('이미 처리되었거나 존재하지 않는 확인 요청입니다.')
            method = request['method']
            params = request['params']
            if method == 'house/hooks/trust':
                if decision not in ('accept', 'decline', 'cancel'):
                    raise HouseError('허용 또는 거절을 선택하세요.')
                if decision != 'accept':
                    self.requests.pop(key)
                    self.error = 'House 운영 훅을 허용하지 않아 작업을 시작하지 않았습니다. 다시 사용하려면 house web을 재실행하세요.'
                    return {'accepted': False}
                approved = params['hooks']
                table = ','.join(json.dumps(h['key']) + '={trusted_hash=' + json.dumps(h['currentHash']) + '}' for h in approved)
                self.ready = False
                self.requests.pop(key)
            elif method in APPROVALS:
                if decision not in ('accept', 'decline', 'cancel'):
                    raise HouseError('허용·거절·취소 중 하나를 선택하세요.')
                available = params.get('availableDecisions')
                if available and decision not in available:
                    raise HouseError('이 요청에서 지원하지 않는 결정입니다.')
                result = {'decision': decision}
            elif method == 'item/permissions/requestApproval':
                if decision not in ('accept', 'decline', 'cancel'):
                    raise HouseError('권한 요청에 대한 결정을 선택하세요.')
                result = {'permissions': params.get('permissions', {}) if decision == 'accept' else {}, 'scope': 'turn'}
            elif method == 'item/tool/requestUserInput':
                questions = {q['id'] for q in params.get('questions', [])}
                if (not isinstance(answers, dict) or set(answers) != questions
                        or any(not isinstance(v, list) or not v or any(not isinstance(t, str) or len(t) > 8000 for t in v)
                               for v in answers.values())):
                    raise HouseError('각 질문에 답변을 입력하세요.')
                result = {'answers': {q: {'answers': answer} for q, answer in answers.items()}}
            else:
                raise HouseError('지원되지 않는 확인 요청입니다.')
            if method != 'house/hooks/trust':
                # Claim once before I/O; a fast native completion must remain authoritative.
                self.requests.pop(key)
        if method == 'house/hooks/trust':
            try:
                self._connect(['hooks.state={' + table + '}'])
                self._verify_hooks(approved)
            except (HouseError, OSError, KeyError) as exc:
                with self.condition:
                    self.ready = False
                    self.error = str(exc)
                raise HouseError(str(exc)) from exc
        else:
            self.rpc.respond(request['id'], result=result)
            with self.condition:
                self.condition.notify_all()
        return {'accepted': True}

    def read_file(self, relative):
        if not isinstance(relative, str) or not relative or relative.split('/')[0] not in ROOMS:
            raise HouseError('House 방 안의 기록만 열 수 있습니다.')
        if any(part.startswith('.') for part in relative.split('/')):
            raise HouseError('숨김 파일은 웹에서 열지 않습니다.')
        path = safe_path(self.root, relative)
        if path.suffix.lower() not in TEXT_EXTENSIONS or not path.is_file():
            raise HouseError('지원하는 텍스트 기록 파일을 선택하세요.')
        with path.open('rb') as stream:
            data = stream.read(256 * 1024 + 1)
        if len(data) > 256 * 1024 or b'\x00' in data:
            raise HouseError('웹 미리보기는 256KiB 이내 텍스트만 지원합니다.')
        try:
            return {'path': relative, 'text': data.decode('utf-8')}
        except UnicodeError as exc:
            raise HouseError('UTF-8 텍스트 파일이 아닙니다.') from exc

    def snapshot(self):
        with self.condition:
            result = dict(project=str(self.root), connected=self.connected, ready=self.ready, error=self.error,
                          thread_id=self.thread_id, goal='', rooms=list(self.rooms.values()),
                          requests=list(self.requests.values()), queue=self.queue[-20:], session=None,
                          models=self.models, current_model=self.current_model,
                          selected_model=self.selected_model, model_error=self.model_error,
                          current_effort=self.current_effort, selected_effort=self.selected_effort,
                          current_service_tier=self.current_service_tier, selected_fast=self.selected_fast)
            if self.session_id:
                folder = sessions.workspace(self.root, 'main', self.session_id)
                result['session'] = read_json(safe_path(folder, 'session.json'))
                from .connections import registered
                if registered(self.root):
                    from .session_bridge import observations
                    matches = [item for item in observations(self.root)
                               if item.get('native_session_id') == self.session_id
                               and item.get('transcript_path') == self.transcript
                               and not item.get('agent_id') and not item.get('parent_thread_id')]
                    exact = [item for item in matches if item.get('native_thread_id') == self.thread_id]
                    if len(exact) == 1:
                        matches = exact
                    if len(matches) == 1:
                        result['session'] = matches[0]
                    elif len(matches) > 1:
                        result['session'] = None
                        result['error'] = 'House 관측 대화 식별이 모호합니다. doctor로 연결을 확인하세요.'
                self._restore_registry()
            for room in ROOMS:
                bases = [safe_path(self.root, room)]
                if self.session_id:
                    local = self.root / room / 'sessions' / self.session_id
                    bases += [local]
                    agent = self.rooms[room]['agent_id'] if room != 'main' else None
                    if agent:
                        bases += [local / 'agents' / validate_id(agent)]
                files = []
                budget, stack = 400, [(base, 0) for base in reversed(bases)]
                while stack and budget > 0 and len(files) < 30:
                    base, depth = stack.pop()
                    if not base.is_dir() or base.is_symlink():
                        continue
                    with os.scandir(base) as entries:
                        for entry in entries:
                            budget -= 1
                            if budget < 0:
                                break
                            if entry.name.startswith('.') or entry.is_symlink():
                                continue
                            path = Path(entry.path)
                            if entry.is_dir(follow_symlinks=False):
                                if depth < 3 and entry.name not in ('sessions', 'agents', '__pycache__'):
                                    stack.append((path, depth + 1))
                                continue
                            if path.suffix.lower() in TEXT_EXTENSIONS:
                                relative = path.relative_to(self.root).as_posix()
                                safe_path(self.root, relative)
                                files.append({'path': relative, 'label': path.name})
                                if len(files) >= 30:
                                    break
                self.rooms[room]['files'] = files
            goal = safe_path(self.root, 'main/target.md')
            if goal.exists():
                with goal.open(encoding='utf-8') as stream:
                    text = stream.read(8000)
                before, heading, body = text.partition('## 사용자가 제시한 목표')
                result['goal'] = (body.split('\n## ', 1)[0].strip() if heading else before)[:2000]
            return json.loads(json.dumps(result, ensure_ascii=False))

    def close(self):
        with self.condition:
            self.closed = True
            self.ready = False
            self.generation += 1
            self.condition.notify_all()
        if self.rpc:
            self.rpc.close()
        if self.worker and self.worker is not threading.current_thread():
            self.worker.join(timeout=2)


def make_server(controller, port=8765, token=None):
    token = token or secrets.token_urlsafe(32)
    assets = Path(__file__).resolve().parents[2] / 'web'

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, status, value, content_type='application/json; charset=utf-8'):
            data = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(data)

        def dispatch(self):
            parts = urlsplit(self.path)
            api = parts.path.startswith('/api/')
            if not authorized(self.headers, self.server.server_port, token, api):
                return self.send(403, {'error': '로컬 주소와 이번 실행의 접근 토큰을 확인하세요.'})
            if self.command == 'GET':
                if parts.path == '/api/state':
                    return self.send(200, controller.snapshot())
                if parts.path == '/api/file':
                    path = parse_qs(parts.query).get('path', [''])[0]
                    return self.send(200, controller.read_file(path))
                name = {'/': 'index.html', '/index.html': 'index.html', '/style.css': 'style.css', '/app.js': 'app.js'}.get(parts.path)
                if name:
                    return self.send(200, (assets / name).read_bytes(), mimetypes.guess_type(name)[0] + '; charset=utf-8')
            elif self.command == 'POST' and api:
                if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                    return self.send(415, {'error': 'JSON 요청만 지원합니다.'})
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 64 * 1024 or self.headers.get('Transfer-Encoding'):
                    return self.send(413, {'error': '요청 크기가 올바르지 않습니다.'})
                payload = self.rfile.read(length)
                if len(payload) != length:
                    raise HouseError('요청 본문의 길이가 올바르지 않습니다.')
                value = json.loads(payload, parse_constant=_invalid_constant)
                if not isinstance(value, dict):
                    raise HouseError('JSON 객체가 필요합니다.')
                if parts.path == '/api/message':
                    return self.send(202, controller.submit(value.get('room'), value.get('text')))
                if parts.path == '/api/respond':
                    return self.send(200, controller.respond(value.get('id'), value.get('decision'), value.get('answers')))
                if parts.path == '/api/model':
                    if 'model' not in value:
                        raise HouseError('모델 이름 또는 null을 입력하세요.')
                    return self.send(200, controller.select_model(value['model'], value.get('effort'), value.get('fast')))
            return self.send(404, {'error': '없는 경로입니다.'})

        def do_GET(self):
            try:
                self.dispatch()
            except (BrokenPipeError, ConnectionResetError):
                return
            except (HouseError, OSError, ValueError, KeyError, TypeError, RecursionError) as exc:
                self.send(400, {'error': str(exc)})
            except Exception:
                self.send(500, {'error': 'House 내부 오류가 발생했습니다. 연결과 기록을 확인해 주세요.'})

        do_POST = do_GET

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    server.house_token = token
    return server


def launch_web(info, home, executable=None, port=8765, open_browser=True, resume=None, dry_run=False, as_json=False):
    from .cli import emit, session_config
    from .codex_rpc import CodexRPC
    executable = shutil.which(executable or os.environ.get('HOUSE_CODEX_BIN') or 'codex')
    if not executable:
        raise HouseError('Codex CLI를 찾을 수 없습니다.')
    if not 0 <= port <= 65535:
        raise HouseError('포트는 0~65535 사이여야 합니다.')
    root = Path(info['root'])
    overrides = session_config(info, home, write=not dry_run)
    argv = [executable, 'app-server', '--listen', 'stdio://']
    for value in overrides:
        argv += ['-c', value]
    if dry_run:
        emit(dict(root=str(root), mode='web', argv=argv, host='127.0.0.1', port=port, resume=resume), as_json)
        return 0
    launch_id = uuid.uuid4().hex
    env = dict(os.environ, HOUSE_LAUNCH_ID=launch_id, HOUSE_CONNECTION_ROOM='main',
               HOUSE_CONNECTION_ORIGIN='launcher')
    def factory(extra, callback):
        command = list(argv)
        for value in extra:
            command += ['-c', value]
        return CodexRPC(command, cwd=str(root / 'main'), env=env, on_message=callback)
    key = hashlib.sha256((str(root) + ':main').encode()).hexdigest()[:24]
    safe_path(home, 'runtime/locks').mkdir(parents=True, exist_ok=True)
    controller = WebController(root, home, info['prompt'], factory, resume, launch_id)
    server = None
    code = 1
    with locked(safe_path(home, 'runtime/locks/' + key + '.lock')):
        try:
            server = make_server(controller, port)
            controller.start()
            url = 'http://127.0.0.1:' + str(server.server_port) + '/#token=' + server.house_token
            print('House 연구실 · ' + str(root), flush=True)
            print(url, flush=True)
            print('브라우저를 닫아도 작업은 계속됩니다. Ctrl-C로 웹 서버와 Codex 연결을 종료합니다.', flush=True)
            if open_browser:
                webbrowser.open(url)
            server.serve_forever(poll_interval=.25)
            code = 0
        except KeyboardInterrupt:
            code = 0
            return 0
        finally:
            controller.close()
            if server:
                server.server_close()
            sessions.finish_launch(root, 'main', launch_id, code)
    return 0
