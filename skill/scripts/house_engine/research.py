"""Explicit research scope, immutable source captures and run evidence.

These checks verify declared files and recorded decisions. They do not certify
scientific validity or turn a document into proof of the user's consent.
All contract paths are relative to the project containing house/.
"""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil

from .storage import HouseError, atomic_json, locked, read_json, safe_path, validate_id

CONTRACT = 'research.json'
SHA = re.compile(r'[0-9a-f]{64}')


def template():
    return {'schema_version': 1,
            'scope': {'status': 'unconfirmed', 'purpose': '', 'allowed_writes': [],
                      'protected_meaning': [], 'agreement': None, 'agreement_sha256': None},
            'inputs': [], 'criteria': [], 'dependencies': [],
            'preservation': {'allow_compatibility_symlinks': False, 'reason_required': True}}


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise HouseError('{} must be a nonempty string'.format(label))
    return value


def _path(root, value):
    return safe_path(Path(root).resolve().parent, _text(value, 'project-relative path'))


def _hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _file(root, relative):
    path = _path(root, relative)
    if path.name == '.env' or path.name.startswith('.env.'):
        raise HouseError('Environment files cannot be research inputs or evidence')
    if not path.is_file():
        raise HouseError('Required file is missing or invalid: ' + str(path))
    return path


def _existing(root, relative):
    path = _path(root, relative)
    if not (path.is_file() or path.is_dir()):
        raise HouseError('Declared dependency is missing or invalid: ' + str(path))
    return path


def _list(value, label):
    if not isinstance(value, list):
        raise HouseError(label + ' must be a list')
    return value


def _keys(value, required, optional=()):
    if not isinstance(value, dict) or not set(required).issubset(value) or set(value) - set(required) - set(optional):
        raise HouseError('Invalid research fields; required={}, optional={}'.format(','.join(required), ','.join(optional)))


def validate(root, value):
    _keys(value, ('schema_version', 'scope', 'inputs', 'criteria', 'dependencies', 'preservation'))
    if type(value.get('schema_version')) is not int or value['schema_version'] != 1:
        raise HouseError('Unsupported research contract schema')
    scope = value.get('scope')
    _keys(scope, ('status', 'purpose', 'allowed_writes', 'protected_meaning', 'agreement', 'agreement_sha256'))
    if not isinstance(scope, dict) or scope.get('status') not in ('unconfirmed', 'agreed'):
        raise HouseError('scope.status must be unconfirmed or agreed')
    if not isinstance(scope.get('purpose'), str):
        raise HouseError('scope.purpose must be a string')
    for item in _list(scope.get('allowed_writes'), 'scope.allowed_writes'):
        _path(root, item)
    for item in _list(scope.get('protected_meaning'), 'scope.protected_meaning'):
        _text(item, 'protected meaning')
    if scope.get('agreement') is not None:
        _path(root, scope['agreement'])
        if not isinstance(scope['agreement_sha256'], str) or not SHA.fullmatch(scope['agreement_sha256']):
            raise HouseError('Scope agreement requires its exact agreement_sha256')
    elif scope['agreement_sha256'] is not None:
        raise HouseError('agreement_sha256 requires an agreement path')
    for section in ('inputs', 'criteria'):
        identifiers = set()
        for item in _list(value.get(section), section):
            if not isinstance(item, dict):
                raise HouseError(section + ' entries must be objects')
            key = validate_id(item.get('id'))
            if key in identifiers:
                raise HouseError('Duplicate {} id: {}'.format(section, key))
            identifiers.add(key)
            if section == 'inputs':
                _keys(item, ('id', 'path', 'sha256', 'role'), ('capture', 'capture_sha256'))
                _path(root, item.get('path'))
                if not isinstance(item.get('sha256'), str) or not SHA.fullmatch(item['sha256']):
                    raise HouseError('Every input requires its exact SHA-256: ' + key)
                if item.get('capture') is not None:
                    _path(root, item['capture'])
                    if not isinstance(item.get('capture_sha256'), str) or not SHA.fullmatch(item['capture_sha256']):
                        raise HouseError('Capture reference requires capture_sha256: ' + key)
                elif 'capture_sha256' in item:
                    raise HouseError('capture_sha256 requires a capture reference: ' + key)
                if item.get('role') != 'active':
                    raise HouseError('Archived evidence cannot be a runtime input: ' + key)
            else:
                _keys(item, ('id', 'owner', 'definition', 'sha256', 'status', 'use', 'decision', 'decision_sha256'))
                _text(item.get('owner'), 'criterion owner')
                _path(root, item.get('definition'))
                for field in ('sha256', 'decision_sha256'):
                    if not isinstance(item.get(field), str) or not SHA.fullmatch(item[field]):
                        raise HouseError('Criterion requires exact {}: {}'.format(field, key))
                if item.get('status') not in ('proposed', 'adopted', 'rejected'):
                    raise HouseError('Invalid criterion status: ' + key)
                if item.get('use') not in ('adopted', 'exploratory'):
                    raise HouseError('Criterion use must be adopted or exploratory: ' + key)
                if item.get('decision') is not None:
                    _path(root, item['decision'])
    for item in _list(value.get('dependencies'), 'dependencies'):
        if not isinstance(item, dict):
            raise HouseError('dependencies entries must be objects')
        _keys(item, ('consumer', 'input'))
        _path(root, item.get('consumer'))
        _path(root, item.get('input'))
    policy = value.get('preservation')
    _keys(policy, ('allow_compatibility_symlinks', 'reason_required'))
    if not isinstance(policy, dict) or any(type(policy.get(key)) is not bool for key in
                                         ('allow_compatibility_symlinks', 'reason_required')):
        raise HouseError('preservation requires explicit symlink and reason policies')
    return value


def load(root):
    root = Path(root).resolve()
    path = safe_path(root, CONTRACT)
    if not path.exists():
        from .project import base_version
        pin = read_json(safe_path(root, 'hive.lock.json'))
        if (base_version(pin['release']) or (0,)) >= (0, 4, 1):
            raise HouseError('Required research.json is missing; restore the agreed contract, do not guess it')
        return None  # An older pinned project has not adopted a research contract.
    return validate(root, read_json(path))


def require_scope(root, value):
    scope = value['scope']
    if scope['status'] != 'agreed' or not scope['purpose'].strip() or not scope['allowed_writes']:
        raise HouseError('Record the actual agreed purpose and output scope before this operation')
    _agreement(root, scope)
    return scope


def _agreement(root, scope):
    path = _file(root, scope.get('agreement'))
    if _hash(path) != scope['agreement_sha256']:
        raise HouseError('Scope agreement changed; review the actual task scope before continuing')
    return path


def check(root, value=None):
    value = load(root) if value is None else validate(root, value)
    if value is None:
        return {'status': 'undeclared', 'ready': False, 'errors': ['No research contract was declared']}
    errors = []
    scope = value['scope']
    if scope['status'] != 'agreed':
        errors.append('Research scope is unconfirmed; record the actual agreement before execution')
    if not scope['purpose'].strip() or not scope['allowed_writes']:
        errors.append('Declare the task purpose and allowed output paths')
    checks = []

    def inspect(label, action):
        try:
            result = action()
            checks.append({'item': label, 'status': 'checked'})
            return result
        except (HouseError, OSError, UnicodeError) as exc:
            errors.append('{}: {}'.format(label, exc))
            return None

    inspect('scope agreement', lambda: _agreement(root, scope))
    for item in value['inputs']:
        def verify(item=item):
            path = _file(root, item['path'])
            if _hash(path) != item['sha256']:
                raise HouseError('Input SHA-256 mismatch; do not substitute a different source')
            if item.get('capture'):
                capture_path = _file(root, item['capture'])
                if _hash(capture_path) != item['capture_sha256']:
                    raise HouseError('Source capture metadata changed')
                capture = read_json(capture_path)
                if capture.get('schema_version') != 1 or capture.get('path') != item['path'] or capture.get('sha256') != item['sha256']:
                    raise HouseError('Input does not match its source capture')
        inspect('input ' + item['id'], verify)
    for item in value['criteria']:
        def verify(item=item):
            if _hash(_file(root, item['definition'])) != item['sha256']:
                raise HouseError('Criterion definition changed; review its meaning and adoption before use')
            if item['status'] == 'rejected' or (item['use'] == 'adopted' and item['status'] != 'adopted'):
                raise HouseError('A proposal is not an adopted criterion; declare an authorized exploratory use explicitly')
            if _hash(_file(root, item.get('decision'))) != item['decision_sha256']:
                raise HouseError('Criterion decision evidence changed; confirm the owning decision')
        inspect('criterion ' + item['id'], verify)
    for item in value['dependencies']:
        inspect('dependency ' + item['consumer'],
                lambda item=item: (_existing(root, item['consumer']), _existing(root, item['input'])))
    return {'status': 'ready' if not errors else 'blocked', 'ready': not errors,
            'errors': errors, 'checks': checks, 'scope': scope,
            'note': 'File checks and recorded decisions are not independent proof of consent or scientific validity'}


def require_ready(root, value=None):
    report = check(root, value)
    if not report['ready']:
        raise HouseError('Research execution is blocked:\n  ' + '\n  '.join(report['errors']))
    return report


def gate_work(root):
    """New/adopted contracts gate Work launch; old pins stay explicitly undeclared."""
    if load(root) is not None:
        require_ready(root)


def allowed_write(root, relative, value=None):
    value = load(root) if value is None else validate(root, value)
    candidate = _path(root, relative)
    if value is None:
        return candidate
    prefixes = [_path(root, p) for p in value['scope']['allowed_writes']]
    if not any(candidate == p or p in candidate.parents for p in prefixes):
        raise HouseError('Output is outside the declared task scope: ' + relative)
    return candidate


def guard_move(root, relative, link, reason):
    value = load(root)
    if value is None:
        return {'status': 'undeclared'}
    require_scope(root, value)
    if value['preservation']['reason_required']:
        _text(reason, 'Preservation purpose (--reason)')
    if link and not value['preservation']['allow_compatibility_symlinks']:
        raise HouseError('Project policy forbids compatibility symlinks; use --no-link and review dependencies')
    owned = Path(root).resolve().name + '/' + relative
    source = _path(root, owned)
    allowed_write(root, owned)
    protected = [{'input': value['scope']['agreement'], 'consumer': 'scope agreement'}]
    protected += [{'input': item['path'], 'consumer': 'research.json input ' + item['id']}
                  for item in value['inputs']]
    protected += [{'input': item['capture'], 'consumer': 'source capture ' + item['id']}
                  for item in value['inputs'] if item.get('capture')]
    for item in value['criteria']:
        protected += [{'input': item[k], 'consumer': 'criterion ' + item['id']}
                      for k in ('definition', 'decision') if item.get(k)]
    protected_refs = [item['consumer'] for item in protected
                      if _path(root, item['input']) == source or source in _path(root, item['input']).parents]
    if protected_refs:
        raise HouseError('Moving this path breaks required research references (links are not input substitutes): ' + ', '.join(protected_refs))
    affected = [item['consumer'] for item in value['dependencies']
                if _path(root, item['input']) == source or source in _path(root, item['input']).parents]
    if affected and not link:
        raise HouseError('Moving this path breaks declared references; review their owners first: ' + ', '.join(affected))
    return {'status': 'checked', 'reason': reason, 'affected_references': affected,
            'policy': value['preservation'], 'contract_sha256': _hash(safe_path(root, CONTRACT)),
            'note': 'Only declared dependencies are checked; unlisted consumers require review'}


def guard_destination(root, destination):
    if load(root) is None:
        return
    try:
        relative = Path(destination).relative_to(Path(root).resolve().parent).as_posix()
    except ValueError as exc:
        raise HouseError('Destination is outside the declared project; agree its scope before moving data') from exc
    allowed_write(root, relative)


def capture(root, source, identifier, source_label, purpose, archived=False):
    """Copy one explicitly selected source to a new immutable capture, never refresh in place."""
    root = Path(root).resolve()
    identifier = validate_id(identifier)
    _text(source_label, 'source')
    _text(purpose, 'capture purpose')
    source = Path(source).expanduser().absolute()
    if source.name == '.env' or source.name.startswith('.env.'):
        raise HouseError('Environment/credential files cannot be captured')
    if not source.is_file() or source.is_symlink():
        raise HouseError('Capture requires an explicit regular source file')
    metadata = safe_path(root, 'literature/captures/' + identifier + '.json')
    folder = _path(root, 'house-data/captures/' + identifier)
    value = load(root)
    if value is not None:
        require_scope(root, value)
        allowed_write(root, metadata.relative_to(root.parent).as_posix())
        allowed_write(root, folder.relative_to(root.parent).as_posix())
    ignore = _path(root, 'house-data/.gitignore')
    if not ignore.exists():
        allowed_write(root, 'house-data/.gitignore')
    with locked(safe_path(root, '.research.lock')):
        if metadata.exists() or folder.exists():
            raise HouseError('Capture id already exists; use a new id, never overwrite a source state')
        digest = _hash(source)
        folder.mkdir(parents=True, exist_ok=False)
        copied = folder / source.name
        try:
            if not ignore.exists():
                with ignore.open('x', encoding='utf-8') as stream:
                    stream.write('# House raw source captures\n*\n')
            with source.open('rb') as incoming, copied.open('xb') as outgoing:
                shutil.copyfileobj(incoming, outgoing)
            if _hash(copied) != digest or _hash(source) != digest:
                raise HouseError('Source changed during capture; no source state was adopted')
            relative = copied.relative_to(root.parent).as_posix()
            record = {'schema_version': 1, 'id': identifier, 'source': source_label, 'purpose': purpose,
                      'captured_at': datetime.now(timezone.utc).isoformat(), 'path': relative,
                      'sha256': digest, 'bytes': copied.stat().st_size, 'role': 'active'}
            if archived:
                record['archived_source'] = {'path': str(source), 'sha256': digest}
            metadata.parent.mkdir(parents=True, exist_ok=True)
            atomic_json(metadata, record)
        except BaseException:
            if copied.exists():
                copied.unlink()
            folder.rmdir()
            raise
    return dict(record, record=metadata.relative_to(root.parent).as_posix(), capture_sha256=_hash(metadata))


def _document(root, relative):
    path = _file(root, relative)
    if path.stat().st_size > 2 * 1024 * 1024:
        raise HouseError('Definition/decision document exceeds 2 MiB; provide the owning evidence document')
    data = path.read_bytes()
    return {'path': relative, 'sha256': hashlib.sha256(data).hexdigest(), 'text': data.decode('utf-8')}


def snapshot(root, run):
    """Freeze the declared basis before execution; this does not claim a run succeeded."""
    root = Path(root).resolve()
    if not (run.startswith('output/') and len(Path(run).parts) >= 3):
        raise HouseError('Run path must be output/<version>/<run>')
    with locked(safe_path(root, '.research.lock')):
        value = load(root)
        require_ready(root, value)
        folder = allowed_write(root, root.name + '/' + run, value)
        target = safe_path(folder, 'basis.json')
        if target.exists():
            raise HouseError('Run basis already exists; choose a new run id')
        if folder.exists() and (not folder.is_dir() or any(folder.iterdir())):
            raise HouseError('Run directory already contains records or outputs; never backfill a historical basis')
        criteria = [dict(item, definition_evidence=_document(root, item['definition']),
                         decision_evidence=_document(root, item['decision'])) for item in value['criteria']]
        agreement = _document(root, value['scope']['agreement'])
        if agreement['sha256'] != value['scope']['agreement_sha256'] or any(
                item['definition_evidence']['sha256'] != item['sha256'] or
                item['decision_evidence']['sha256'] != item['decision_sha256'] for item in criteria):
            raise HouseError('Decision/definition changed while recording the run basis')
        basis = {'schema_version': 1, 'record_type': 'research_basis',
                 'created_at': datetime.now(timezone.utc).isoformat(),
                 'execution_status': 'not_recorded', 'scope': value['scope'],
                 'agreement_evidence': agreement,
                 'inputs': [dict(item, capture_evidence=_document(root, item['capture']))
                            if item.get('capture') else dict(item) for item in value['inputs']],
                 'criteria': criteria}
        folder.mkdir(parents=True, exist_ok=True)
        created = False
        try:
            with target.open('x', encoding='utf-8') as stream:
                created = True
                json.dump(basis, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.write('\n')
            if load(root) != value:
                raise HouseError('Research contract changed while recording the run basis')
            require_ready(root, value)
        except BaseException:
            if created:
                target.unlink()
            raise
    return {'status': 'basis_recorded', 'path': str(target), 'sha256': _hash(target),
            'note': 'Record actual execution separately in run.json; a basis snapshot is not a completed run'}


def verify_run(root, run):
    """Verify preserved run evidence without applying the current upstream criterion state."""
    if not (run.startswith('output/') and len(Path(run).parts) >= 3):
        raise HouseError('Run path must be output/<version>/<run>')
    folder = safe_path(Path(root).resolve(), run)
    basis_path = safe_path(folder, 'basis.json')
    basis = read_json(basis_path)
    if basis.get('schema_version') != 1 or basis.get('record_type') != 'research_basis':
        raise HouseError('Invalid research basis snapshot')
    # Validate the frozen declaration's shape, not the current upstream contract.
    frozen = {'schema_version': 1, 'scope': basis.get('scope'), 'dependencies': [],
              'preservation': {'allow_compatibility_symlinks': False, 'reason_required': True},
              'inputs': [], 'criteria': []}
    for section in ('inputs', 'criteria'):
        for item in _list(basis.get(section), 'snapshot ' + section):
            if not isinstance(item, dict):
                raise HouseError('Snapshot entries must be objects')
            frozen[section].append({key: value for key, value in item.items()
                                    if key not in ('capture_evidence', 'definition_evidence', 'decision_evidence')})
    validate(root, frozen)
    if frozen['scope']['status'] != 'agreed':
        raise HouseError('Frozen scope was not agreed')
    if not isinstance(basis.get('agreement_evidence'), dict) or basis['agreement_evidence'].get('sha256') != frozen['scope']['agreement_sha256']:
        raise HouseError('Frozen scope agreement evidence is invalid')
    for item in _list(basis.get('inputs'), 'snapshot inputs'):
        if _hash(_file(root, item['path'])) != item['sha256']:
            raise HouseError('Preserved run input is missing or changed: ' + item['id'])
    evidence = [basis.get('agreement_evidence')]
    evidence.extend(item['capture_evidence'] for item in basis['inputs'] if item.get('capture_evidence'))
    for item in basis['inputs']:
        if item.get('capture') and (not isinstance(item.get('capture_evidence'), dict) or
                                  item['capture_evidence'].get('sha256') != item['capture_sha256']):
            raise HouseError('Frozen source capture evidence is missing or mismatched')
    for item in _list(basis.get('criteria'), 'snapshot criteria'):
        if item['status'] == 'rejected' or (item['use'] == 'adopted' and item['status'] != 'adopted'):
            raise HouseError('Frozen criterion was not adopted for its declared use')
        for name, checksum in (('definition_evidence', 'sha256'), ('decision_evidence', 'decision_sha256')):
            if not isinstance(item.get(name), dict) or item[name].get('sha256') != item[checksum]:
                raise HouseError('Frozen definition/decision evidence is invalid')
        evidence.extend([item.get('definition_evidence'), item.get('decision_evidence')])
    for item in evidence:
        if not isinstance(item, dict) or not isinstance(item.get('text'), str) or hashlib.sha256(item['text'].encode('utf-8')).hexdigest() != item.get('sha256'):
            raise HouseError('Frozen definition/decision evidence is invalid')
    run_record = read_json(safe_path(folder, 'run.json'))
    if run_record.get('basis_sha256') != _hash(basis_path):
        raise HouseError('run.json must reference the exact basis_sha256 used for execution')
    if run_record.get('status') not in ('completed', 'failed') or not isinstance(run_record.get('command'), str) or not run_record['command'].strip():
        raise HouseError('Record the actual command and completed/failed status in run.json')
    return {'status': 'evidence_checked', 'execution_status': run_record['status'],
            'basis_sha256': _hash(basis_path),
            'note': 'Recorded execution and file integrity do not certify scientific correctness'}
