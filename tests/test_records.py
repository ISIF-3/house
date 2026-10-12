import errno
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from house_engine import records
from house_engine import __version__ as VERSION
from house_engine.project import bootstrap, init_project, status
from house_engine.storage import HouseError, atomic_json, read_json
from house_engine.templates import HANDOFF_LIMIT, MIGRATED_STATUS, STATUS_MARK

FIXTURE = Path(__file__).resolve().parent / 'fixtures/templates_0_1_0.py'
SCRIPT = Path(__file__).resolve().parents[1] / 'skill/scripts/house.py'
GIT = shutil.which('git') is not None


def legacy_templates():
    spec = importlib.util.spec_from_file_location('templates_0_1_0', str(FIXTURE))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in Path(root).rglob('*') if p.is_file()}


class RecordTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.project = self.base / 'research project'
        self.project.mkdir()
        self.home = self.base / 'shared home'
        # Isolate git from the user's global hooks, signing and identity.
        empty = self.base / 'gitconfig'
        empty.write_text('')
        env = {'GIT_CONFIG_GLOBAL': str(empty), 'GIT_CONFIG_NOSYSTEM': '1',
               'GIT_AUTHOR_NAME': 'House Test', 'GIT_AUTHOR_EMAIL': 'house@example.invalid',
               'GIT_COMMITTER_NAME': 'House Test', 'GIT_COMMITTER_EMAIL': 'house@example.invalid'}
        patcher = patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def init(self):
        return Path(init_project(self.project, self.home)['root'])

    def legacy(self):
        """A project created by House 0.1.0 in a home that predates engine_release."""
        old = legacy_templates()
        with patch('house_engine.project.__version__', '0.1.0'), \
                patch('house_engine.project.project_templates', old.project_templates), \
                patch('house_engine.project.ROOM_INSTRUCTIONS', old.ROOM_INSTRUCTIONS):
            root = Path(init_project(self.project, self.home)['root'])
        config = read_json(self.home / 'shared/config.json')
        del config['engine_release']
        atomic_json(self.home / 'shared/config.json', config)
        return root

    def git(self, cwd, *args):
        return subprocess.run(['git'] + list(args), cwd=str(cwd), capture_output=True, text=True, check=True).stdout

    # ------------------------------------------------------------ templates and status lines

    def test_new_project_has_status_lines_gitignore_and_snapshot_handoff(self):
        root = self.init()
        for relative in ('main/work.md', 'main/handoff.md', 'main/results.md', 'output/summary.md',
                         'main/decisions.md', 'literature/reference.md'):
            self.assertIn(STATUS_MARK, (root / relative).read_text(), relative)
        self.assertIn('!/literature/', (root / '.gitignore').read_text())
        self.assertIn('*.npz', (root / '.gitignore').read_text())
        self.assertIn('스냅샷', (root / 'main/handoff.md').read_text())
        self.assertIn('누적 로그', (root / 'main/work.md').read_text())
        documents = status(root)['documents']
        self.assertEqual(documents['main/work.md']['state'], 'template')
        self.assertFalse(documents['main/work.md']['stale'])

    def test_written_record_with_initial_status_line_is_stale(self):
        root = self.init()
        work = root / 'main/work.md'
        work.write_text(work.read_text() + '| 2026-10-12 | main | 목표 정리 | why.md | 완료 |\n')
        self.assertTrue(status(root)['documents']['main/work.md']['stale'])
        work.write_text(work.read_text().replace('상태: 기록 없음 — 첫 작업 후 갱신', '상태: 목표 정리 완료 — 2026-10-12'))
        self.assertFalse(status(root)['documents']['main/work.md']['stale'])
        self.assertEqual(records.check(root)['stale_status'], [])

    def test_engine_release_becomes_default_once_and_rollback_is_respected(self):
        self.legacy()
        self.assertEqual(read_json(self.home / 'shared/config.json')['current_release'], '0.1.0')
        config = bootstrap(self.home)
        self.assertEqual(config['current_release'], VERSION)
        self.assertEqual(config['engine_release'], VERSION)
        self.assertIn(STATUS_MARK, (self.home / 'shared/templates/main/work.md').read_text())
        from house_engine import updates
        updates.rollback(self.home, '0.1.0')
        self.assertEqual(bootstrap(self.home)['current_release'], '0.1.0')

    def test_default_with_reviewed_skill_bindings_is_not_advanced(self):
        self.legacy()
        derived = self.home / 'shared/releases/0.1.0-u1'
        shutil.copytree(str(self.home / 'shared/releases/0.1.0'), str(derived))
        manifest = read_json(derived / 'manifest.json')
        manifest.update(version='0.1.0-u1')
        manifest['bindings']['literature'] = [{'name': 'reviewed-skill'}]
        atomic_json(derived / 'manifest.json', manifest)
        config = read_json(self.home / 'shared/config.json')
        config.update(current_release='0.1.0-u1', bindings=manifest['bindings'])
        atomic_json(self.home / 'shared/config.json', config)
        self.assertEqual(bootstrap(self.home)['current_release'], '0.1.0-u1')

    # ------------------------------------------------------------ migration

    def used_legacy_project(self):
        root = self.legacy()
        work = root / 'main/work.md'
        work.write_text(work.read_text() + '\n## 2026-10-08\n\n위 초기화 상태는 이전 템플릿 기록으로 보존한다.\n실제 진행 내용.\n')
        handoff = root / 'main/handoff.md'
        handoff.write_text(handoff.read_text() + '\n## 누적 인계\n\n' + '긴 인계 문장. ' * 1200)
        (root / 'literature/ROOM.md').write_text('사용자가 고친 문헌 방 지침\n')
        decisions = root / 'main/decisions.md'
        decisions.write_text(decisions.read_text() + '| 2026-10-08 | 범위 | 확정 | 사용자 요청 | design.md |\n')
        return root

    def test_migrate_updates_owned_lines_preserves_edits_and_pins_with_backup(self):
        root = self.used_legacy_project()
        before_work = (root / 'main/work.md').read_text()
        result = records.migrate(root)
        self.assertEqual(result['status'], 'migrated')
        lock = read_json(root / 'hive.lock.json')
        self.assertEqual(lock['release'], VERSION)
        self.assertEqual(lock['history'][-1]['from_release'], '0.1.0')
        work = (root / 'main/work.md').read_text()
        self.assertIn(MIGRATED_STATUS, work)
        self.assertNotIn('상태: 초기 문서 생성 — 연구 작업은 아직 기록되지 않음', work)
        self.assertIn('실제 진행 내용.', work)
        decisions = (root / 'main/decisions.md').read_text()
        self.assertIn(MIGRATED_STATUS, decisions)
        self.assertNotIn('아직 기록된 결정이 없다.', decisions)
        self.assertIn('| 2026-10-08 | 범위 | 확정 |', decisions)
        self.assertIn(STATUS_MARK, (root / 'main/why.md').read_text())  # untouched template replaced
        self.assertEqual((root / 'literature/ROOM.md').read_text(), '사용자가 고친 문헌 방 지침\n')
        self.assertIn('literature/ROOM.md', [k['path'] for k in result['kept']])
        self.assertTrue((root / '.gitignore').is_file())
        job = root / result['job']
        self.assertEqual((job / 'previous/main/work.md').read_text(), before_work)
        report = (job / 'REPORT.md').read_text()
        self.assertIn('handoff.md', report)
        self.assertIn('main/work.md:', report)  # template note to clean up
        self.assertTrue(status(root)['documents']['main/work.md']['stale'])
        self.assertTrue(records.check(root)['handoff']['over'])
        again = records.migrate(root)
        self.assertEqual(again['status'], 'unchanged')

    def test_migrate_dry_run_changes_no_project_file(self):
        root = self.used_legacy_project()
        before = snapshot(root)
        result = records.migrate(root, dry_run=True)
        self.assertEqual(result['status'], 'planned')
        self.assertIn('main/work.md', [c['path'] for c in result['changes']])
        self.assertEqual(snapshot(root), before)
        self.assertEqual(read_json(root / 'hive.lock.json')['release'], '0.1.0')

    def test_check_recommends_migration_for_old_release(self):
        root = self.used_legacy_project()
        advice = '\n'.join(records.check(root)['advice'])
        self.assertIn('house migrate', advice)
        self.assertIn('main/work.md', records.check(root)['stale_status'])

    # ------------------------------------------------------------ offload

    def raw_run(self, root):
        run = root / 'output/model_v1/run-001'
        (run / 'raw/steps').mkdir(parents=True)
        (run / 'results.md').write_text('# run-001\n')
        (run / 'raw/steps/step-0001.npz').write_bytes(b'x' * 4096)
        (run / 'raw/final.json').write_text('{"ok": true}')
        old = time.time() - 2 * records.RECENT_SECONDS
        for path in [run] + list(run.rglob('*')):
            os.utime(str(path), (old, old))
        return run

    def test_check_finds_raw_directory_without_records(self):
        root = self.init()
        self.raw_run(root)
        report = records.check(root, dir_threshold=1024, file_threshold=1024)
        self.assertEqual([c['path'] for c in report['offload_candidates']], ['output/model_v1/run-001/raw'])
        (root / 'output/model_v1/run-001/table.csv').write_bytes(b'1' * 2048)  # beside results.md
        report = records.check(root, dir_threshold=1024 * 1024, file_threshold=1024)
        self.assertIn({'path': 'output/model_v1/run-001/table.csv', 'kind': 'file', 'bytes': 2048, 'files': 1},
                      report['offload_candidates'])
        self.assertIn('output/model_v1/run-001/raw/steps/step-0001.npz', [f['path'] for f in report['large_files']])
        self.assertIn('House 점검', records.render_check(report))

    def test_offload_moves_data_out_and_leaves_working_relative_link(self):
        root = self.init()
        run = self.raw_run(root)
        before = snapshot(run / 'raw')
        planned = records.offload(root, 'output/model_v1/run-001/raw', dry_run=True)
        self.assertEqual(planned['status'], 'planned')
        self.assertTrue((run / 'raw').is_dir() and not (run / 'raw').is_symlink())
        result = records.offload(root, 'output/model_v1/run-001/raw')
        destination = self.project / 'house-data/output/model_v1/run-001/raw'
        self.assertEqual(Path(result['destination']), destination)
        self.assertTrue((run / 'raw').is_symlink())
        self.assertFalse(os.path.isabs(os.readlink(str(run / 'raw'))))
        self.assertEqual(snapshot(run / 'raw'), before)  # readable through the link
        self.assertEqual(snapshot(destination), before)
        self.assertEqual((self.project / 'house-data/.gitignore').read_text().splitlines()[-1], '*')
        index = read_json(root / 'house-data.json')
        self.assertEqual(index['entries'][0]['files'], 2)
        self.assertEqual(records.verify_offloaded(root)['problems'], [])
        self.assertTrue((run / 'results.md').is_file())
        report = records.check(root, dir_threshold=1024)
        self.assertEqual(report['offload_candidates'], [])
        with self.assertRaises(HouseError):
            records.offload(root, 'output/model_v1/run-001/raw')
        (destination / 'late.bin').write_bytes(b'late')
        self.assertTrue(records.verify_offloaded(root)['problems'])

    def test_offload_refuses_records_rooms_and_existing_destination(self):
        root = self.init()
        run = self.raw_run(root)
        with self.assertRaises(HouseError):
            records.offload(root, 'output/model_v1/run-001')  # contains results.md
        with self.assertRaises(HouseError):
            records.offload(root, 'output')
        with self.assertRaises(HouseError):
            records.offload(root, 'output/model_v1/run-001/raw', to=root / 'inside')
        (self.project / 'house-data/output/model_v1/run-001/raw').mkdir(parents=True)
        with self.assertRaises(HouseError):
            records.offload(root, 'output/model_v1/run-001/raw')
        self.assertTrue((run / 'raw/final.json').is_file())
        moved = records.offload(root, 'output/model_v1/run-001', to=self.base / 'external', allow_records=True)
        self.assertEqual(moved['status'], 'moved')

    def test_cross_device_offload_copies_verifies_then_removes_source(self):
        root = self.init()
        run = self.raw_run(root)
        source = run / 'raw'
        real_rename = os.rename

        def rename(src, dst):
            if Path(src) == source:
                raise OSError(errno.EXDEV, 'cross-device link')
            return real_rename(src, dst)

        before = snapshot(source)
        with patch('house_engine.records.os.rename', side_effect=rename):
            records.offload(root, 'output/model_v1/run-001/raw', content_hash=True)
        self.assertTrue(source.is_symlink())
        self.assertEqual(snapshot(self.project / 'house-data/output/model_v1/run-001/raw'), before)
        self.assertTrue(read_json(root / 'house-data.json')['entries'][0]['content_hashed'])

    def test_failed_copy_verification_keeps_source_and_leaves_no_partial_copy(self):
        root = self.init()
        run = self.raw_run(root)
        source = run / 'raw'
        before = snapshot(source)
        real_manifest = records._manifest
        calls = []

        def manifest(path, content_hash=False):
            calls.append(path)
            entries = real_manifest(path, content_hash)
            return entries[:-1] if len(calls) > 1 else entries

        with patch('house_engine.records.os.rename', side_effect=OSError(errno.EXDEV, 'cross-device')), \
                patch('house_engine.records._manifest', side_effect=manifest):
            with self.assertRaises(HouseError):
                records.offload(root, 'output/model_v1/run-001/raw')
        self.assertEqual(snapshot(source), before)
        self.assertFalse(source.is_symlink())
        parent = self.project / 'house-data/output/model_v1/run-001'
        self.assertEqual([p.name for p in parent.iterdir()], [])

    def test_recent_output_is_not_moved_without_explicit_permission(self):
        root = self.init()
        run = self.raw_run(root)
        (run / 'raw/steps/step-0002.npz').write_bytes(b'still running')
        with self.assertRaises(HouseError) as caught:
            records.offload(root, 'output/model_v1/run-001/raw')
        self.assertIn('--allow-recent', str(caught.exception))
        self.assertFalse((run / 'raw').is_symlink())
        self.assertEqual(records.offload(root, 'output/model_v1/run-001/raw', allow_recent=True)['status'], 'moved')

    def test_suggested_offload_moves_finished_candidates_and_skips_recent(self):
        root = self.init()
        self.raw_run(root)
        active = root / 'output/model_v2/run-001'
        (active / 'cases').mkdir(parents=True)
        (active / 'results.md').write_text('# running\n')
        (active / 'cases/state.npz').write_bytes(b'y' * 4096)
        plan = records.offload_suggested(root, dry_run=True, dir_threshold=1024, file_threshold=1024 * 1024)
        self.assertEqual([m['path'] for m in plan['moved']], ['output/model_v1/run-001/raw'])
        self.assertEqual([s['path'] for s in plan['skipped']], ['output/model_v2/run-001/cases'])
        self.assertFalse((root / 'output/model_v1/run-001/raw').is_symlink())
        records.offload_suggested(root, dir_threshold=1024, file_threshold=1024 * 1024)
        self.assertTrue((root / 'output/model_v1/run-001/raw').is_symlink())
        self.assertFalse((active / 'cases').is_symlink())

    # ------------------------------------------------------------ git

    @unittest.skipUnless(GIT, 'git is not installed')
    def test_track_creates_house_repository_and_save_commits_only_records(self):
        root = self.init()
        (root / 'output/model_v1').mkdir()
        (root / 'output/model_v1/state.npz').write_bytes(b'0' * 100)
        with self.assertRaises(HouseError):
            records.commit(root, 'not yet tracked')
        result = records.commit(root, 'house: start', init=True)
        self.assertEqual(result['status'], 'committed')
        self.assertTrue(result['initialized'])
        tracked = self.git(root, 'ls-files').splitlines()
        self.assertIn('main/work.md', tracked)
        self.assertIn('.gitignore', tracked)
        self.assertNotIn('output/model_v1/state.npz', tracked)
        self.assertEqual(records.commit(root, 'nothing')['status'], 'unchanged')
        work = root / 'main/work.md'
        work.write_text(work.read_text() + '| 2026-10-12 | main | 기록 | work.md | 완료 |\n')
        self.assertEqual(records.commit(root, 'house: log')['status'], 'committed')
        self.assertEqual(records.check(root)['git']['pending_files'], 0)

    @unittest.skipUnless(GIT, 'git is not installed')
    def test_commit_refuses_raw_sized_files(self):
        root = self.init()
        (root / 'output/big.csv').write_bytes(b'1,2\n' * 1024)
        with self.assertRaises(HouseError) as caught:
            records.commit(root, 'house: start', init=True, max_file=1024)
        self.assertIn('output/big.csv', str(caught.exception))
        self.assertEqual(subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=str(root),
                                        capture_output=True).returncode != 0, True)

    @unittest.skipUnless(GIT, 'git is not installed')
    def test_parent_repository_commit_keeps_other_staged_work_and_reincludes_rooms(self):
        self.git(self.project, 'init', '-q')
        (self.project / '.gitignore').write_text('literature/\n')
        (self.project / 'analysis.py').write_text('print(1)\n')
        self.git(self.project, 'add', '.gitignore', 'analysis.py')
        self.git(self.project, 'commit', '-q', '-m', 'project')
        (self.project / 'analysis.py').write_text('print(2)\n')
        self.git(self.project, 'add', 'analysis.py')
        root = self.init()
        self.assertEqual(records.git_state(root)['ignored_rooms'], [])
        result = records.commit(root, 'house: start', init=True)
        self.assertEqual(result['mode'], 'parent')
        self.assertFalse(result['initialized'])
        committed = self.git(self.project, 'show', '--name-only', '--format=', 'HEAD').splitlines()
        self.assertIn('house/literature/reference.md', committed)
        self.assertNotIn('analysis.py', committed)
        self.assertEqual(self.git(self.project, 'diff', '--cached', '--name-only').split(), ['analysis.py'])

    # ------------------------------------------------------------ command line

    def run_house(self, *args):
        env = dict(os.environ, HOUSE_HOME=str(self.home), HOUSE_CODEX_BIN=sys.executable)
        return subprocess.run([sys.executable, str(SCRIPT)] + list(args), cwd=str(self.project), env=env,
                              capture_output=True, text=True)

    def test_cli_check_offload_and_migrate_plan(self):
        root = self.used_legacy_project()
        self.raw_run(root)
        result = self.run_house('check')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('house migrate', result.stdout)
        plan = self.run_house('migrate', '--dry-run', '--json')
        self.assertEqual(plan.returncode, 0, plan.stderr)
        self.assertEqual(json.loads(plan.stdout)['status'], 'planned')
        applied = self.run_house('migrate')
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertIn('REPORT.md', applied.stdout)
        moved = self.run_house('offload', 'house/output/model_v1/run-001/raw', '--json')
        self.assertEqual(moved.returncode, 0, moved.stderr)
        self.assertTrue((root / 'output/model_v1/run-001/raw').is_symlink())
        context = self.run_house('context', 'main', '--json')
        prompt = json.loads(context.stdout)['prompt']
        self.assertIn('main/handoff.md', prompt)
        self.assertIn('save -m', prompt)


if __name__ == '__main__':
    unittest.main()
