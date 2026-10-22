"""Local v2 observations shared by project hook clients.

This adapter never changes legacy session records, research contracts or native
policy. Native hook output is a delivery attempt, not a UI acknowledgement.
Only identifiable completed compactions contribute to the ten-auto threshold.
"""

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import sys
import uuid

from . import sessions
from .project import ROOMS, _lock
from .storage import HouseError, atomic_json, locked, read_json, safe_path, shell_command


RUNTIME = 'main/integration/v2'
# This is an observation freshness policy, not a token estimate: after five
# minutes the same record stays inspectable but cannot drive a new ratio alert.
USAGE_MAX_AGE_SECONDS = 300
DELIVERY_LEASE_SECONDS = 30
EVENTS = ('SessionStart', 'UserPromptSubmit', 'PostToolUse', 'PostCompact',
          'PreCompact', 'Stop', 'contextCompaction', 'compaction_completed')


def _now():
    return datetime.now(timezone.utc)


def _stamp():
    return _now().isoformat()


def _opaque(value, field, required=False):
    if value is None and not required:
        return None
    if (not isinstance(value, str) or not value.strip() or len(value) > 1024
            or any(ord(character) < 32 for character in value)):
        raise HouseError('Invalid ' + field)
    return value


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _owner_fingerprint():
    host = platform.node()
    if not host:
        raise HouseError('Runtime owner host is unknown; use an explicit owner connection')
    return _digest({'platform': sys.platform, 'host': host,
                    'wsl_distro': os.environ.get('WSL_DISTRO_NAME') or None})


def _validate_owner(value):
    owner = value.get('owner_fingerprint')
    if owner is None:
        raise HouseError('v2 runtime owner is unknown; existing metadata cannot be adopted automatically. '
                         'Use explicit WSL forwarding or an owner connection')
    if not isinstance(owner, str) or not re.fullmatch(r'[0-9a-f]{64}', owner):
        raise HouseError('Invalid v2 runtime owner fingerprint; use an explicit owner connection')
    if owner != _owner_fingerprint():
        raise HouseError('v2 runtime belongs to another OS/host owner; do not share its file lock. '
                         'Use explicit WSL forwarding or an owner connection')


def _owner_guard(folder):
    path = safe_path(folder, 'project.json')
    if path.exists():
        _validate_owner(read_json(path))
    elif folder.exists() and any(path.name != '.lock' for path in folder.iterdir()):
        raise HouseError('v2 runtime owner is unknown because its identity metadata is missing; '
                         'use explicit WSL forwarding or an owner connection')


def _runtime(root, create=False):
    root = Path(root).expanduser().absolute()
    _lock(root)
    path = safe_path(root, RUNTIME)
    # Check ownership before touching the lock: different OS lock backends must
    # not be assumed to coordinate writers on a shared filesystem.
    _owner_guard(path)
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return root, path


def _project(folder):
    path = safe_path(folder, 'project.json')
    _owner_guard(folder)
    if not path.exists():
        atomic_json(path, {'schema_version': 2, 'project_id': str(uuid.uuid4()),
                           'execution_environment_id': str(uuid.uuid4()),
                           'owner_fingerprint': _owner_fingerprint(), 'created_at': _stamp()})
    value = read_json(path)
    _validate_owner(value)
    if value.get('schema_version') != 2:
        raise HouseError('Unsupported v2 project identity')
    for field in ('project_id', 'execution_environment_id'):
        _opaque(value.get(field), field, required=True)
    return value


def prepare(root):
    """Prepare local project/environment identity without inventing a callback."""
    _, folder = _runtime(root, create=True)
    with locked(safe_path(folder, '.lock'), timeout=2):
        value = _project(folder)
        return {field: value[field] for field in ('project_id', 'execution_environment_id')}


def _store(folder):
    path = safe_path(folder, 'state.json')
    if not path.exists():
        return {'schema_version': 2, 'conversations': {}}
    value = read_json(path)
    if value.get('schema_version') != 2 or not isinstance(value.get('conversations'), dict):
        raise HouseError('Invalid v2 observation store')
    for key, state in value['conversations'].items():
        if (not isinstance(state, dict) or key != state.get('conversation_key')
                or not isinstance(state.get('alerts'), list)
                or not isinstance(state.get('compact_events'), list)):
            raise HouseError('Invalid v2 conversation state')
        for field in ('auto_compactions', 'manual_compactions', 'unknown_compactions',
                      'unidentified_auto_deliveries', 'unidentified_manual_deliveries',
                      'unidentified_compaction_deliveries', 'context_epoch'):
            if type(state.get(field)) is not int or state[field] < 0:
                raise HouseError('Invalid v2 observation counter: ' + field)
    return value


def _identity(project, event):
    thread = _opaque(event.get('native_thread_id') or event.get('thread_id'), 'native thread ID')
    session = _opaque(event.get('native_session_id') or event.get('session_id'), 'native session ID')
    parent = _opaque(event.get('parent_thread_id'), 'parent thread ID')
    agent = _opaque(event.get('agent_id'), 'agent ID')
    transcript = event.get('transcript_path')
    if transcript is not None:
        if not isinstance(transcript, str) or not Path(transcript).is_absolute():
            raise HouseError('Transcript path must be absolute')
        path = Path(transcript)
        transcript = str(safe_path(path.parent, path.name))
    if not thread and (not session or not transcript or not Path(transcript).is_file()):
        raise HouseError('Without a native thread ID, an actual session ID and readable transcript are required')
    basis = [project['project_id'], project['execution_environment_id']]
    if thread:
        basis += ['native_thread', thread]
    else:
        # The local key is not a native chat ID. Forks/children with the same
        # session ID and different transcripts or agent evidence stay separate.
        basis += ['provisional_stream', session, transcript, parent, agent]
    return {'conversation_key': 'conversation-' + _digest(basis),
            'native_thread_id': thread, 'native_session_id': session,
            'parent_thread_id': parent, 'agent_id': agent, 'transcript_path': transcript,
            'identity_status': 'native_thread' if thread else 'provisional',
            'identity_evidence': {'basis': 'native thread ID' if thread else 'session ID + actual transcript + parent/agent evidence',
                                  'native_thread_id_reason': None if thread else 'Not supplied; no native ID inferred from session ID',
                                  'automatic_merge': False}}


def _workspace(folder, key):
    if not isinstance(key, str) or len(key) != 77 or not key.startswith('conversation-'):
        raise HouseError('Invalid v2 conversation key')
    try:
        int(key[13:], 16)
    except ValueError as exc:
        raise HouseError('Invalid v2 conversation key') from exc
    return safe_path(folder, 'conversations/' + key)


def _new_state(folder, project, identity, room, event):
    directory = _workspace(folder, identity['conversation_key'])
    directory.mkdir(parents=True, exist_ok=True)
    handoff = safe_path(directory, 'handoff.md')
    if not handoff.exists():
        with handoff.open('x', encoding='utf-8') as stream:
            stream.write(sessions.INITIAL_HANDOFF)
    return dict(identity, schema_version=2, project_id=project['project_id'],
                execution_environment_id=project['execution_environment_id'], room=room,
                workspace=str(directory), handoff_path=str(handoff), attached_at=_stamp(),
                attachment_event=event, count_scope='observed_since_attach',
                prior_compactions=None, legacy_history={'status': 'not_imported', 'auto_compactions': None,
                    'reason': 'No verified legacy identity mapping; original legacy records remain unchanged'},
                auto_compactions=0, manual_compactions=0, unknown_compactions=0,
                unidentified_auto_deliveries=0, unidentified_manual_deliveries=0,
                unidentified_compaction_deliveries=0, compact_events=[],
                context_percent=None, context_usage=None, context_epoch=0,
                context_warning_emitted=False, review_required=False,
                checkpoint=None, recommended_at=None, alerts=[])


def _resolve(state, kinds, reason):
    for alert in state['alerts']:
        if alert['kind'] in kinds and alert.get('resolved_at') is None:
            alert.update(resolved_at=_stamp(), resolution=reason, status='resolved')


def _alert(state, kind, message, basis):
    state['alerts'].append({'alert_id': str(uuid.uuid4()), 'kind': kind, 'message': message,
                            'created_at': _stamp(), 'basis': basis, 'status': 'pending',
                            'resolved_at': None, 'deliveries': {}})


def _reset_context(state, identity, size, reason):
    state.update(context_percent=None, context_usage=None,
                 context_warning_emitted=False, context_epoch=state['context_epoch'] + 1,
                 token_floor={'identity': identity, 'offset': size})
    _resolve(state, ('context_warning',), reason)


def _compact_keys(state, event, records, identity):
    native_id = _native_event_id(event)
    keys = ['native:' + native_id] if native_id else []
    floor = state.get('compaction_record_floor', {})
    compacted = [(offset, end, value) for offset, end, value in records if value.get('type') == 'compacted'
                 and not (floor.get('identity') == identity and end <= floor.get('offset', 0))]
    if compacted:
        offset, end, value = compacted[-1]
        # An old last record cannot identify a different, newly identified event.
        known_native = any('native:' + native_id in item['keys'] for item in state['compact_events']) if native_id else False
        cursor = state.get('compact_cursor', {})
        if (not native_id or known_native or cursor.get('identity') != identity
                or end > cursor.get('offset', 0)):
            keys.append('record:' + _digest([identity, offset, value]))
            state['compact_cursor'] = {'identity': identity, 'offset': end}
    return keys


def _native_event_id(event):
    item = event.get('item')
    item_id = item.get('id') if isinstance(item, dict) and item.get('type') == 'contextCompaction' else None
    return _opaque(event.get('native_event_id') or event.get('event_id') or item_id, 'native event ID')


def _compaction(state, name, event, records, identity, size):
    trigger = event.get('trigger') or event.get('compaction_trigger')
    classification = trigger if name == 'PostCompact' and trigger in ('auto', 'manual') else 'unknown'
    keys = _compact_keys(state, event, records, identity)
    if not keys:
        field = {'auto': 'unidentified_auto_deliveries', 'manual': 'unidentified_manual_deliveries',
                 'unknown': 'unidentified_compaction_deliveries'}[classification]
        state[field] += 1
        _reset_context(state, identity, size, 'unidentified compaction delivery')
        return '압축 전달을 관측했지만 고유 식별 근거가 없어 확정 자동 압축 횟수에 포함하지 않습니다.'
    found = [item for item in state['compact_events'] if set(keys).intersection(item['keys'])]
    fresh = not found
    if found:
        item = found[0]
        item['keys'] = sorted(set(item['keys'] + keys))
        prior = item['classification']
        if prior == classification or classification == 'unknown':
            return ''
        if prior != 'unknown' or item.get('classification_conflict'):
            # Contradictory cause evidence is not a confirmed automatic event.
            if prior != 'unknown':
                state[prior + '_compactions'] -= 1
                state['unknown_compactions'] += 1
            item.update(classification='unknown', classification_conflict=True)
            return '압축 원인 근거가 충돌해 해당 사건은 분류 불명으로 남깁니다.'
        state['unknown_compactions'] -= 1
        item['classification'] = classification
    else:
        item = {'keys': keys, 'classification': classification, 'observed_at': _stamp(),
                'native_event_id': _native_event_id(event)}
        state['compact_events'].append(item)
    state[classification + '_compactions'] += 1
    if fresh:
        _reset_context(state, identity, size, 'completed compaction')
    if classification == 'auto':
        state['review_required'] = True
        _resolve(state, ('compaction_review',), 'newer completed automatic compaction')
        _alert(state, 'compaction_review',
               '자동 압축 {}회 확인: 목표·확정 결정·실행 중인 작업·다음 행동을 재확인하고 인계를 저장하세요.'.format(state['auto_compactions']),
               {'compact_keys': item['keys'], 'auto_compactions': state['auto_compactions']})
    return ''


def _observe_usage(state, records, identity):
    previous = state.get('token_observation')
    sessions._observe_tokens(state, records, identity)
    observed = state.get('token_observation')
    if state.get('context_percent') is None or not observed:
        state['context_usage'] = None
        return
    repeated = previous and all(previous.get(field) == observed.get(field) for field in ('identity', 'offset'))
    if repeated:
        observed['at'] = previous['at']
    at = datetime.fromisoformat(observed['at'])
    record_time = None
    for _, end, value in records:
        if end == observed['offset'] and isinstance(value.get('timestamp'), str):
            try:
                parsed = datetime.fromisoformat(value['timestamp'].replace('Z', '+00:00'))
                if parsed.tzinfo is not None:
                    record_time = parsed
            except ValueError:
                pass
    if ((_now() - at).total_seconds() > USAGE_MAX_AGE_SECONDS or
            record_time is not None and (_now() - record_time).total_seconds() > USAGE_MAX_AGE_SECONDS):
        state['context_percent'] = None
        state['context_usage'] = dict(observed, status='stale', source='bounded_transcript:last_token_usage',
                                      occurred_at=record_time.isoformat() if record_time else None)
        return
    previous_usage = state.get('context_usage')
    if previous_usage and previous_usage.get('window') != observed['window']:
        state['context_warning_emitted'] = False
        state['context_epoch'] += 1
        _resolve(state, ('context_warning',), 'new observed context window')
    state['context_usage'] = dict(observed, status='observed', source='bounded_transcript:last_token_usage',
                                  observed_at=observed['at'], occurred_at=record_time.isoformat() if record_time else None,
                                  freshness='unchanged' if repeated else 'new_observation')
    if state['context_percent'] >= 70 and not state['context_warning_emitted']:
        state['context_warning_emitted'] = True
        _alert(state, 'context_warning', '컨텍스트 {:.1f}% 관측: 다음 작업 전에 세션 인계를 준비하세요.'.format(state['context_percent']),
               {'epoch': state['context_epoch'], 'used': observed['used'], 'window': observed['window'],
                'source': state['context_usage']['source'], 'observed_at': observed['at']})


def _deliver(state, endpoint, name, event):
    messages = []
    for alert in state['alerts']:
        if alert.get('resolved_at') is not None:
            continue
        delivery = alert['deliveries'].get(endpoint)
        attempts = delivery.get('attempts', []) if delivery else []
        retry = bool(attempts and len(attempts) < 2 and
                     (name == 'UserPromptSubmit' or event.get('redisplay_alerts') is True) and
                     _now() >= datetime.fromisoformat(attempts[-1]['lease_expires_at']))
        if attempts and not retry:
            continue
        attempt = {'attempted_at': _stamp(),
                   'lease_expires_at': (_now() + timedelta(seconds=DELIVERY_LEASE_SECONDS)).isoformat(),
                   'transport_status': 'prepared_for_native_output',
                   'host_acceptance': 'unknown', 'display_confirmation': 'unknown'}
        delivery = delivery or {}
        delivery.update(attempts=attempts + [attempt], display_confirmation='unknown')
        alert['deliveries'][endpoint] = delivery
        messages.append('[House {}] {}'.format(alert['alert_id'], alert['message']))
    return messages


def _chat_notices(state, endpoint, name):
    if name not in sessions.MODEL_CONTEXT_EVENTS:
        return []
    messages = []
    for alert in state['alerts']:
        if alert.get('resolved_at') is not None:
            continue
        delivery = alert['deliveries'].get(endpoint)
        if not delivery or delivery.get('additional_context_prepared_at'):
            continue
        delivery['additional_context_prepared_at'] = _stamp()
        # This records preparation of model context, not model/UI acknowledgement.
        delivery['chat_display_confirmation'] = 'unknown'
        messages.append('[House {}] {}'.format(alert['alert_id'], alert['message']))
    return messages


def _instructions(root, state):
    command = shell_command(['house', 'connection', 'checkpoint', state['conversation_key'],
                             '--project', str(root.parent)])
    return ('House 관측 인계: {}\n실제 내용이 있는 목표·확정 결정·실행 중인 작업·다음 행동 네 절을 작성한 뒤 '
            '{} 로 저장하세요. '
            '실제 작업 구간 종료 때만 --boundary를 추가하세요. '
            '이 연결 이전 압축 횟수와 화면 표시 확인은 unknown입니다.').format(
                state['handoff_path'], command)


def _endpoint(event, explicit):
    supplied = explicit if explicit is not None else event.get('endpoint_id')
    if supplied is not None:
        return _opaque(supplied, 'endpoint ID', required=True), 'explicit endpoint ID'
    metadata = {}
    for field in ('client_id', 'client_name', 'host_id', 'host_name', 'client', 'host', 'client_info'):
        value = event.get(field)
        if isinstance(value, dict):
            value = {key: item for key, item in value.items() if key in ('id', 'name', 'kind', 'client_id', 'host_id')
                     and type(item) in (str, int)}
        if value and type(value) in (str, int, dict):
            metadata[field] = value
    if metadata:
        return 'endpoint-' + _digest(metadata), 'supplied host/client metadata'
    return 'native-hook:unspecified', 'unknown; no endpoint or host/client identity supplied'


def _native_output(name, message, instructions='', notice=''):
    context = instructions if name in sessions.MODEL_CONTEXT_EVENTS else ''
    return sessions._output(name, context, message, notice)


def handle_event(root, event, room='main', endpoint_id=None):
    """Observe an actual callback and return diagnostic/alert native hook JSON.

    Hook failures do not fabricate a permission decision or bypass other hooks.
    """
    try:
        if not isinstance(event, dict):
            raise HouseError('Hook event must be a JSON object')
        name = event.get('hook_event_name') or event.get('kind')
        item = event.get('item')
        if isinstance(item, dict) and item.get('type') == 'contextCompaction':
            name = 'contextCompaction'
        if name not in EVENTS:
            return {}
        if room not in ROOMS:
            raise HouseError('Unknown House room: ' + str(room))
        endpoint, endpoint_evidence = _endpoint(event, endpoint_id)
        root, folder = _runtime(root, create=True)
        with locked(safe_path(folder, '.lock'), timeout=2):
            project = _project(folder)
            for field in ('project_id', 'execution_environment_id'):
                if event.get(field) is not None and event[field] != project[field]:
                    raise HouseError('Callback ' + field + ' differs from the owning runtime')
            identity = _identity(project, event)
            records, transcript_identity, size = sessions._transcript(identity['transcript_path'])
            store = _store(folder)
            state = store['conversations'].get(identity['conversation_key'])
            attached = state is None
            if attached:
                state = _new_state(folder, project, identity, room, name)
                if name not in ('PostCompact', 'contextCompaction', 'compaction_completed'):
                    state['compaction_record_floor'] = {'identity': transcript_identity, 'offset': size}
                store['conversations'][identity['conversation_key']] = state
            elif state['room'] != room:
                raise HouseError('Conversation is already attached to another room')
            state.update(identity, last_event=name, last_observed_at=_stamp(),
                         last_native_event_id=_native_event_id(event),
                         last_source=_opaque(event.get('source'), 'event source'),
                         occurred_at=_opaque(event.get('occurred_at'), 'event time'),
                         last_endpoint_id=endpoint, endpoint_identity_evidence=endpoint_evidence)
            cursor = state.get('transcript_cursor', {})
            if cursor and transcript_identity and (cursor.get('identity') != transcript_identity or size < cursor.get('offset', 0)):
                _reset_context(state, transcript_identity, size, 'transcript replaced or truncated')
                state['compaction_record_floor'] = {'identity': transcript_identity, 'offset': size}
            state['transcript_cursor'] = {'identity': transcript_identity, 'offset': size}
            model = _opaque(event.get('model') or event.get('model_id'), 'model ID')
            if model and state.get('model') and model != state['model']:
                _reset_context(state, transcript_identity, size, 'model changed; waiting for new usage')
            if model:
                state['model'] = model
            diagnostic = ''
            if name in ('PostCompact', 'contextCompaction', 'compaction_completed'):
                diagnostic = _compaction(state, name, event, records, transcript_identity, size)
            elif name != 'PreCompact':
                _observe_usage(state, records, transcript_identity)
            messages = _deliver(state, endpoint, name, event)
            notices = _chat_notices(state, endpoint, name)
            if diagnostic:
                messages.append(diagnostic)
            instructions = _instructions(root, state) if attached or messages or notices else ''
            if attached and name not in ('SessionStart', 'UserPromptSubmit'):
                messages.append(instructions)
            elif messages and name not in ('SessionStart', 'UserPromptSubmit'):
                messages.append(instructions)
            atomic_json(safe_path(folder, 'state.json'), store)
            return _native_output(name, '\n'.join(messages), instructions, '\n'.join(notices))
    except (HouseError, OSError, ValueError, TypeError, OverflowError,
            KeyError, AttributeError, RecursionError) as exc:
        return {'systemMessage': 'House session observation unavailable: ' + str(exc)}


def observations(root):
    """Read only: no state means no observed callbacks, not healthy sessions."""
    _, folder = _runtime(root)
    if not folder.exists():
        return []
    with locked(safe_path(folder, '.lock'), timeout=2):
        return list(_store(folder)['conversations'].values())


def checkpoint(root, conversation_key, boundary=False):
    """Save a validated v2 handoff; a recommendation never starts/stops chats."""
    if type(boundary) is not bool:
        raise HouseError('Checkpoint boundary must be a boolean')
    _, folder = _runtime(root)
    directory = _workspace(folder, conversation_key)
    if not folder.exists():
        raise HouseError('No v2 observations exist')
    with locked(safe_path(folder, '.lock'), timeout=2):
        store = _store(folder)
        state = store['conversations'].get(conversation_key)
        if state is None:
            raise HouseError('Conversation has not been observed')
        text = sessions._checkpoint_text(directory)
        snapshots = safe_path(directory, 'snapshots')
        snapshots.mkdir(exist_ok=True)
        snapshot = safe_path(snapshots, uuid.uuid4().hex + '.md')
        with snapshot.open('x', encoding='utf-8') as stream:
            stream.write(text)
        saved = {'path': str(snapshot), 'hash': hashlib.sha256(text.encode()).hexdigest(),
                 'saved_at': _stamp(), 'auto_compactions': state['auto_compactions'], 'boundary': bool(boundary)}
        recommend = bool(boundary and state['auto_compactions'] >= 10 and not state['recommended_at'])
        state.update(checkpoint=saved, review_required=False)
        _resolve(state, ('context_warning', 'compaction_review'), 'validated handoff saved')
        if recommend:
            state['recommended_at'] = _stamp()
            _alert(state, 'new_session_recommendation',
                   '확인된 자동 압축 10회 이상이며 인계를 저장한 작업 경계입니다. 새 대화에서 인계를 읽고 이어가기를 권합니다. 현재 대화나 계산을 자동 종료하지 않습니다.',
                   {'checkpoint': saved, 'auto_compactions': state['auto_compactions']})
        state['last_boundary_saved_at'] = _stamp() if boundary else state.get('last_boundary_saved_at')
        atomic_json(safe_path(folder, 'state.json'), store)
        return dict(state, recommend_new_session=recommend)
