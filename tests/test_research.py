import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from house_engine import cli, records, research, room_agents
from house_engine.project import init_project
from house_engine.storage import HouseError, atomic_json, read_json


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # macOS exposes /var through /private/var; compare canonical paths.
        self.base = Path(self.tmp.name).resolve()
        self.project = self.base / 'project with spaces'
        self.project.mkdir()
        self.home = self.base / 'shared'
        self.root = Path(init_project(self.project, self.home)['root'])
        self.agreement = self.project / 'agreement.md'
        self.agreement.write_text('Test fixture agreement; not an actual user approval.\n')
        self.data = self.project / 'input.json'
        self.data.write_text('{"value": 1}\n')
        self.rule = self.project / 'criterion.md'
        self.rule.write_bytes(b'Criterion A\r\n')

    def agreed(self, criterion=False):
        value = research.template()
        value['scope'].update(status='agreed', purpose='Test fixture task',
                              allowed_writes=['house', 'house-data'], agreement='agreement.md',
                              agreement_sha256=digest(self.agreement),
                              protected_meaning=['Keep the selected-system definition unchanged'])
        value['inputs'] = [{'id': 'data', 'path': 'input.json', 'sha256': digest(self.data), 'role': 'active'}]
        if criterion:
            value['criteria'] = [{'id': 'group', 'owner': 'criterion responsibility unit',
                                  'status': 'adopted', 'use': 'adopted', 'definition': 'criterion.md',
                                  'sha256': digest(self.rule), 'decision': 'agreement.md',
                                  'decision_sha256': digest(self.agreement)}]
        self.save(value)
        return value

    def save(self, value):
        atomic_json(self.root / research.CONTRACT, value)

    def run_record(self, snapshot, **changes):
        record = {'command': 'python analysis.py --input input.json', 'status': 'completed',
                  'basis_sha256': snapshot['sha256']}
        record.update(changes)
        atomic_json(Path(snapshot['path']).parent / 'run.json', record)

    def raw(self):
        path = self.root / 'output/v1/run-001/raw.bin'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'raw')
        old = time.time() - records.RECENT_SECONDS * 2
        os.utime(path, (old, old))
        return path

    def test_new_project_is_unconfirmed_and_main_can_plan(self):
        self.assertEqual(research.check(self.root)['status'], 'blocked')
        with self.assertRaisesRegex(HouseError, 'unconfirmed'):
            research.gate_work(self.root)
        self.assertIn('research.json', cli.context(self.root, 'main')['prompt'])

    def test_missing_contract_is_not_repaired_on_resume(self):
        (self.root / research.CONTRACT).unlink()
        with self.assertRaisesRegex(HouseError, 'missing'):
            init_project(self.project, self.home)
        self.assertFalse((self.root / research.CONTRACT).exists())

    def test_missing_or_changed_input_does_not_use_alternative(self):
        self.agreed()
        self.data.unlink()
        (self.project / 'other-input.json').write_text('{"value": 1}\n')
        self.assertIn('missing', ' '.join(research.check(self.root)['errors']))
        self.data.write_text('different')
        self.assertIn('SHA-256 mismatch', ' '.join(research.check(self.root)['errors']))

    def test_unsafe_paths_archived_roles_and_duplicates_fail_clearly(self):
        for change in ({'path': '../elsewhere'}, {'role': 'archived'}, {'sha256': ''}):
            value = self.agreed()
            value['inputs'][0].update(change)
            self.save(value)
            with self.assertRaises(HouseError):
                research.check(self.root)
        value = self.agreed()
        value['inputs'].append(dict(value['inputs'][0]))
        self.save(value)
        with self.assertRaisesRegex(HouseError, 'Duplicate'):
            research.check(self.root)

    def test_unknown_fields_and_wrong_schema_types_are_not_ignored(self):
        for mutation in ({'schema_version': True}, {'schema_version': 2}, {'input': []}):
            value = self.agreed()
            value.update(mutation)
            self.save(value)
            with self.assertRaises(HouseError):
                research.check(self.root)

    def test_input_symlink_is_not_a_fallback(self):
        value = self.agreed()
        self.data.unlink()
        target = self.base / 'outside.json'
        target.write_text('outside')
        self.data.symlink_to(target)
        with self.assertRaisesRegex(HouseError, 'Symlink'):
            research.check(self.root)

    def test_proposal_requires_explicit_exploratory_use_and_decision(self):
        value = self.agreed(criterion=True)
        value['criteria'][0]['status'] = 'proposed'
        self.save(value)
        self.assertFalse(research.check(self.root)['ready'])
        value['criteria'][0]['use'] = 'exploratory'
        self.save(value)
        self.assertTrue(research.check(self.root)['ready'])
        value['criteria'][0]['status'] = 'rejected'
        self.save(value)
        self.assertFalse(research.check(self.root)['ready'])

    def test_changed_definition_or_decision_requires_review(self):
        self.agreed(criterion=True)
        self.rule.write_text('Criterion B')
        self.assertIn('definition changed', ' '.join(research.check(self.root)['errors']))
        self.agreed(criterion=True)
        self.agreement.write_text('A different decision')
        self.assertIn('decision evidence changed', ' '.join(research.check(self.root)['errors']))

    def test_scope_agreement_changes_block_execution_and_preservation(self):
        self.agreed()
        raw = self.raw()
        self.agreement.write_text('A different task scope')
        self.assertIn('Scope agreement changed', ' '.join(research.check(self.root)['errors']))
        with self.assertRaisesRegex(HouseError, 'Scope agreement changed'):
            records.offload(self.root, raw, link=False, reason='separation')
        self.assertTrue(raw.exists())

    def test_owner_is_required(self):
        value = self.agreed(criterion=True)
        value['criteria'][0]['owner'] = ''
        self.save(value)
        with self.assertRaisesRegex(HouseError, 'owner'):
            research.check(self.root)

    def test_cli_check_exit_status_and_work_launch_preflight(self):
        self.assertEqual(cli.main(['research', 'check', '--project', str(self.project), '--json']), 1)
        info = cli.context(self.root, 'work')
        with patch('house_engine.cli.codex_argv') as native:
            with self.assertRaises(HouseError):
                cli.launch(info, self.home)
            native.assert_not_called()
        self.agreed()
        self.assertEqual(cli.main(['research', 'check', '--project', str(self.project), '--json']), 0)

    def test_main_work_delegation_fails_before_reserving_worker(self):
        folder = self.root / 'main/sessions/chat-001'
        folder.mkdir(parents=True)
        transcript = self.base / 'transcript.jsonl'
        transcript.write_text('')
        atomic_json(folder / 'session.json', {'transcript_path': str(transcript)})
        event = {'hook_event_name': 'PreToolUse', 'tool_name': 'spawn_agent', 'session_id': 'chat-001',
                 'transcript_path': str(transcript), 'tool_use_id': 'call-1', 'tool_input': {'agent_type': 'work'}}
        answer = room_agents.handle_hook(self.root, 'main', event)
        self.assertEqual(answer['hookSpecificOutput']['permissionDecision'], 'deny')
        self.assertFalse((folder / 'room-agents.json').exists())
        self.agreed()
        self.assertEqual(room_agents.handle_hook(self.root, 'main', event), {})
        self.assertIn('work', read_json(folder / 'room-agents.json')['rooms'])
        atomic_json(folder / 'room-agents.json', {'schema_version': 1, 'rooms': {'work': {'agent_id': 'work-001'}}})
        self.data.write_text('changed input before follow-up')
        for tool in ('send_input', 'resume_agent'):
            follow = dict(event, tool_name=tool, tool_input={'id': 'work-001'})
            denied = room_agents.handle_hook(self.root, 'main', follow)
            self.assertEqual(denied['hookSpecificOutput']['permissionDecision'], 'deny')

    def test_capture_creates_separate_states_and_never_refreshes_existing_id(self):
        self.agreed()
        first = research.capture(self.root, self.data, 'query-001', 'https://example.invalid/source', 'first query')
        old_metadata = (self.project / first['record']).read_bytes()
        self.data.write_text('new source state')
        with self.assertRaisesRegex(HouseError, 'already exists'):
            research.capture(self.root, self.data, 'query-001', 'source', 'refresh')
        second = research.capture(self.root, self.data, 'query-002', 'source', 'new query')
        self.assertNotEqual(first['sha256'], second['sha256'])
        self.assertEqual((self.project / first['record']).read_bytes(), old_metadata)
        self.assertEqual(digest(self.project / first['path']), first['sha256'])
        self.assertTrue((self.project / 'house-data/.gitignore').is_file())

    def test_archived_capture_records_source_and_checksum_in_active_copy(self):
        self.agreed()
        result = research.capture(self.root, self.data, 'reused', 'archived study', 'needed evidence only', archived=True)
        self.assertEqual(result['archived_source'], {'path': str(self.data), 'sha256': digest(self.data)})
        self.assertNotEqual(result['path'], str(self.data))

    def test_capture_rejects_environment_files_and_outside_scope(self):
        value = self.agreed()
        secret = self.base / '.env'
        secret.write_text('TEST_ONLY=placeholder')
        with self.assertRaisesRegex(HouseError, 'credential'):
            research.capture(self.root, secret, 'forbidden', 'test', 'test')
        value['scope']['allowed_writes'] = ['house/output']
        self.save(value)
        with self.assertRaisesRegex(HouseError, 'outside'):
            research.capture(self.root, self.data, 'outside', 'test', 'test')
        self.assertFalse((self.project / 'house-data/captures/outside').exists())

    def test_capture_ref_must_match_the_input(self):
        value = self.agreed()
        record = research.capture(self.root, self.data, 'source', 'test source', 'input evidence')
        value['inputs'][0].update(path=record['path'], capture=record['record'], capture_sha256=record['capture_sha256'])
        self.save(value)
        self.assertTrue(research.check(self.root)['ready'])
        metadata = read_json(self.project / record['record'])
        metadata['path'] = 'a-different-file'
        atomic_json(self.project / record['record'], metadata)
        self.assertFalse(research.check(self.root)['ready'])

    def test_source_change_during_capture_leaves_no_adopted_capture(self):
        self.agreed()
        original_copy = research.shutil.copyfileobj
        def copy_and_change(incoming, outgoing):
            original_copy(incoming, outgoing)
            self.data.write_text('source changed after copying')
        with patch('house_engine.research.shutil.copyfileobj', side_effect=copy_and_change):
            with self.assertRaisesRegex(HouseError, 'changed during capture'):
                research.capture(self.root, self.data, 'unstable', 'source', 'test')
        self.assertFalse((self.project / 'house-data/captures/unstable').exists())
        self.assertFalse((self.root / 'literature/captures/unstable.json').exists())

    def test_snapshot_preserves_old_basis_after_upstream_criterion_changes(self):
        value = self.agreed(criterion=True)
        result = research.snapshot(self.root, 'output/v1/run-001')
        before = Path(result['path']).read_bytes()
        self.run_record(result)
        self.rule.write_text('Criterion B')
        value['criteria'][0].update(sha256=digest(self.rule), status='proposed', use='exploratory')
        self.save(value)
        checked = research.verify_run(self.root, 'output/v1/run-001')
        self.assertEqual(checked['execution_status'], 'completed')
        self.assertEqual(Path(result['path']).read_bytes(), before)
        frozen = read_json(Path(result['path']))
        self.assertEqual(frozen['criteria'][0]['definition_evidence']['text'], 'Criterion A\r\n')
        self.assertEqual(frozen['criteria'][0]['status'], 'adopted')

    def test_snapshot_does_not_claim_execution_and_cannot_be_overwritten(self):
        self.agreed()
        result = research.snapshot(self.root, 'output/v1/run-001')
        self.assertEqual(read_json(Path(result['path']))['execution_status'], 'not_recorded')
        with self.assertRaisesRegex(HouseError, 'already exists'):
            research.snapshot(self.root, 'output/v1/run-001')
        with self.assertRaises(HouseError):
            research.verify_run(self.root, 'output/v1/run-001')

    def test_snapshot_never_backfills_existing_run_records_with_current_criteria(self):
        self.agreed(criterion=True)
        folder = self.root / 'output/v1/old-run'
        folder.mkdir(parents=True)
        previous = '{"status":"completed","command":"previous execution"}\n'
        (folder / 'run.json').write_text(previous)
        with self.assertRaisesRegex(HouseError, 'never backfill'):
            research.snapshot(self.root, 'output/v1/old-run')
        self.assertFalse((folder / 'basis.json').exists())
        self.assertEqual((folder / 'run.json').read_text(), previous)

    def test_capture_metadata_is_frozen_with_run_basis(self):
        value = self.agreed()
        record = research.capture(self.root, self.data, 'captured', 'source A', 'execution input')
        value['inputs'][0].update(path=record['path'], capture=record['record'], capture_sha256=record['capture_sha256'])
        self.save(value)
        result = research.snapshot(self.root, 'output/v1/run-001')
        self.run_record(result)
        (self.project / record['record']).write_text('{"different": "metadata"}')
        self.assertEqual(research.verify_run(self.root, 'output/v1/run-001')['status'], 'evidence_checked')
        basis = read_json(Path(result['path']))
        self.assertIn('source A', basis['inputs'][0]['capture_evidence']['text'])

    def test_malformed_frozen_entry_fails_without_an_unhandled_type_error(self):
        self.agreed()
        result = research.snapshot(self.root, 'output/v1/run-001')
        self.run_record(result)
        basis = read_json(Path(result['path']))
        basis['inputs'] = [None]
        atomic_json(Path(result['path']), basis)
        with self.assertRaises(HouseError):
            research.verify_run(self.root, 'output/v1/run-001')

    def test_run_verification_requires_exact_basis_and_actual_command_status(self):
        self.agreed()
        result = research.snapshot(self.root, 'output/v1/run-001')
        for changes in ({'basis_sha256': '0' * 64}, {'command': ''}, {'status': 'planned'}):
            self.run_record(result, **changes)
            with self.assertRaises(HouseError):
                research.verify_run(self.root, 'output/v1/run-001')
        self.run_record(result, status='failed')
        self.assertEqual(research.verify_run(self.root, 'output/v1/run-001')['execution_status'], 'failed')

    def test_changed_input_or_frozen_evidence_fails_run_verification(self):
        self.agreed(criterion=True)
        result = research.snapshot(self.root, 'output/v1/run-001')
        self.run_record(result)
        self.data.write_text('changed input')
        with self.assertRaisesRegex(HouseError, 'changed'):
            research.verify_run(self.root, 'output/v1/run-001')
        self.data.write_text('{"value": 1}\n')
        basis = read_json(Path(result['path']))
        basis['criteria'][0]['definition_evidence']['text'] = 'silently changed'
        atomic_json(Path(result['path']), basis)
        with self.assertRaisesRegex(HouseError, 'evidence is invalid'):
            research.verify_run(self.root, 'output/v1/run-001')

    def test_offload_requires_scope_purpose_and_obeys_link_policy(self):
        self.agreed()
        raw = self.raw()
        with self.assertRaisesRegex(HouseError, 'purpose'):
            records.offload(self.root, raw, link=False)
        with self.assertRaisesRegex(HouseError, 'symlinks'):
            records.offload(self.root, raw, reason='raw separation')
        moved = records.offload(self.root, raw, link=False, reason='raw separation')
        self.assertFalse(raw.exists())
        self.assertFalse(raw.is_symlink())
        self.assertEqual(moved['record']['preservation']['reason'], 'raw separation')
        self.assertEqual(records.verify_offloaded(self.root)['problems'], [])

    def test_offload_no_link_refuses_declared_consumers_and_required_inputs(self):
        value = self.agreed()
        raw = self.raw()
        report = self.root / 'main/results.md'
        value['dependencies'] = [{'consumer': 'house/main/results.md', 'input': raw.relative_to(self.project).as_posix()}]
        self.save(value)
        with self.assertRaisesRegex(HouseError, 'breaks declared references'):
            records.offload(self.root, raw, link=False, reason='raw separation')
        self.assertTrue(raw.exists())
        value['dependencies'] = []
        value['inputs'][0].update(path=raw.relative_to(self.project).as_posix(), sha256=digest(raw))
        self.save(value)
        with self.assertRaisesRegex(HouseError, 'research.json input'):
            records.offload(self.root, raw, link=False, reason='raw separation')
        value['preservation']['allow_compatibility_symlinks'] = True
        self.save(value)
        with self.assertRaisesRegex(HouseError, 'research.json input'):
            records.offload(self.root, raw, link=True, reason='keep old path')

    def test_offload_links_only_when_policy_explicitly_permits_them(self):
        value = self.agreed()
        value['preservation']['allow_compatibility_symlinks'] = True
        self.save(value)
        raw = self.raw()
        records.offload(self.root, raw, reason='preserve existing path')
        self.assertTrue(raw.is_symlink())

    def test_offload_destination_stays_within_the_agreed_project_scope(self):
        self.agreed()
        raw = self.raw()
        with self.assertRaisesRegex(HouseError, 'outside the declared project'):
            records.offload(self.root, raw, to=self.base / 'elsewhere', link=False, reason='separation')
        self.assertTrue(raw.exists())

    def test_existing_manifest_is_never_overwritten(self):
        self.agreed()
        raw = self.raw()
        manifest = self.project / 'house-data/output/v1/run-001/raw.bin.house-manifest.json'
        manifest.parent.mkdir(parents=True)
        manifest.write_text('preserved user evidence')
        with self.assertRaisesRegex(HouseError, 'manifest'):
            records.offload(self.root, raw, link=False, reason='separation')
        self.assertTrue(raw.exists())
        self.assertEqual(manifest.read_text(), 'preserved user evidence')

    def test_concurrent_manifest_creation_refuses_before_moving_source(self):
        self.agreed()
        raw = self.raw()
        manifest = self.project / 'house-data/output/v1/run-001/raw.bin.house-manifest.json'
        original_open = Path.open
        def racing_open(path, *args, **kwargs):
            if path == manifest and args and args[0] == 'x':
                with original_open(path, 'w', encoding='utf-8') as stream:
                    stream.write('concurrent evidence')
                raise FileExistsError('concurrent manifest')
            return original_open(path, *args, **kwargs)
        with patch.object(Path, 'open', new=racing_open):
            with self.assertRaisesRegex(HouseError, 'concurrent manifest'):
                records.offload(self.root, raw, link=False, reason='separation')
        self.assertTrue(raw.exists())
        self.assertEqual(manifest.read_text(), 'concurrent evidence')
        self.assertFalse((manifest.parent / raw.name).exists())

    def test_all_skipped_offload_is_reported_as_blocked(self):
        self.agreed()
        self.raw()
        result = records.offload_suggested(self.root, dir_threshold=1, file_threshold=1)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['moved'], [])
        self.assertTrue(result['skipped'])

    def test_dependency_can_name_a_directory(self):
        value = self.agreed()
        folder = self.project / 'data'
        folder.mkdir()
        value['dependencies'] = [{'consumer': 'criterion.md', 'input': 'data'}]
        self.save(value)
        self.assertTrue(research.check(self.root)['ready'])

    def test_check_reports_contract_errors_and_length_is_only_review_advice(self):
        report = records.check(self.root)
        self.assertEqual(report['research']['status'], 'blocked')
        (self.root / 'main/work.md').write_text('long record\n' * 2500)
        advice = '\n'.join(records.check(self.root)['advice'])
        self.assertIn('길이만으로 이동하지 않습니다', advice)
        self.assertFalse((self.root / 'main/archive').exists())

    def test_old_pin_without_contract_stays_undeclared(self):
        lock = read_json(self.root / 'hive.lock.json')
        lock['release'] = '0.4.0'
        atomic_json(self.root / 'hive.lock.json', lock)
        (self.root / research.CONTRACT).unlink()
        self.assertEqual(research.check(self.root)['status'], 'undeclared')
        research.gate_work(self.root)
        with self.assertRaises(HouseError):
            research.require_ready(self.root)

    def test_migrate_does_not_reconstruct_a_missing_adopted_contract(self):
        self.agreed()
        (self.root / research.CONTRACT).unlink()
        with self.assertRaisesRegex(HouseError, 'missing'):
            records.migrate(self.root)
        self.assertFalse((self.root / research.CONTRACT).exists())

    def test_renamed_house_uses_its_actual_owned_path(self):
        value = self.agreed()
        renamed = self.project / 'research-workspace'
        self.root.rename(renamed)
        self.root = renamed
        value['scope']['allowed_writes'] = ['research-workspace', 'house-data']
        self.save(value)
        result = research.snapshot(self.root, 'output/v1/run-001')
        self.assertTrue(Path(result['path']).is_relative_to(renamed))
        self.assertFalse((self.project / 'house').exists())

    def test_failed_basis_write_leaves_no_partial_snapshot(self):
        self.agreed()
        with patch('house_engine.research.json.dump', side_effect=OSError('simulated full disk')):
            with self.assertRaisesRegex(HouseError, 'simulated full disk'):
                research.snapshot(self.root, 'output/v1/run-001')
        self.assertFalse((self.root / 'output/v1/run-001/basis.json').exists())

    def test_input_change_during_snapshot_never_returns_a_stale_basis(self):
        self.agreed()
        original_dump = research.json.dump
        def dump_then_change(value, stream, **kwargs):
            original_dump(value, stream, **kwargs)
            self.data.write_text('changed while freezing the run')
        with patch('house_engine.research.json.dump', side_effect=dump_then_change):
            with self.assertRaisesRegex(HouseError, 'SHA-256 mismatch'):
                research.snapshot(self.root, 'output/v1/run-001')
        self.assertFalse((self.root / 'output/v1/run-001/basis.json').exists())

    def test_contract_change_during_snapshot_is_not_silently_adopted(self):
        value = self.agreed()
        original_dump = research.json.dump
        def dump_then_change(basis, stream, **kwargs):
            original_dump(basis, stream, **kwargs)
            value['scope']['purpose'] = 'a different scope'
            (self.root / research.CONTRACT).write_text(json.dumps(value))
        with patch('house_engine.research.json.dump', side_effect=dump_then_change):
            with self.assertRaisesRegex(HouseError, 'contract changed'):
                research.snapshot(self.root, 'output/v1/run-001')
        self.assertFalse((self.root / 'output/v1/run-001/basis.json').exists())

    def test_cli_capture_snapshot_execution_and_verification_lifecycle(self):
        value = self.agreed(criterion=True)
        script = Path(__file__).resolve().parents[1] / 'skill/scripts/house.py'
        def invoke(*arguments):
            return subprocess.run([sys.executable, str(script), 'research'] + list(arguments) +
                                  ['--project', str(self.project), '--json'], capture_output=True, text=True)
        captured = invoke('capture', str(self.data), '--id', 'cli-source', '--source', 'test fixture',
                          '--purpose', 'CLI lifecycle test')
        self.assertEqual(captured.returncode, 0, captured.stderr)
        capture = json.loads(captured.stdout)
        value['inputs'][0].update(path=capture['path'], capture=capture['record'], capture_sha256=capture['capture_sha256'])
        self.save(value)
        self.assertEqual(invoke('check').returncode, 0)
        frozen = invoke('snapshot', 'output/v1/cli-run')
        self.assertEqual(frozen.returncode, 0, frozen.stderr)
        snapshot = json.loads(frozen.stdout)
        # A real, small fixture execution; no model or scientific result is invented.
        command = [sys.executable, '-c', 'import json,sys; print(json.load(open(sys.argv[1]))["value"])',
                   str(self.project / capture['path'])]
        executed = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(executed.returncode, 0, executed.stderr)
        self.run_record(snapshot, command=json.dumps(command), status='completed')
        verified = invoke('verify-run', 'output/v1/cli-run')
        self.assertEqual(verified.returncode, 0, verified.stderr)
        self.assertEqual(json.loads(verified.stdout)['execution_status'], 'completed')
        self.assertNotEqual(invoke('snapshot', 'output/v1/cli-run').returncode, 0)
        (self.project / capture['path']).write_text('changed retained input')
        self.assertNotEqual(invoke('verify-run', 'output/v1/cli-run').returncode, 0)


if __name__ == '__main__':
    unittest.main()
