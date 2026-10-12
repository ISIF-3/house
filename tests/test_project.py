import hashlib
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


from house_engine.project import ROOMS, bootstrap, default_home, find_house, init_project, status
from house_engine.storage import HouseError, atomic_json, locked, read_json, safe_path, validate_id
from house_engine import __version__ as VERSION


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.project = self.base / "research project"
        self.project.mkdir()
        self.home = self.base / "shared home"

    def init(self, goal=None):
        return init_project(self.project, self.home, goal)

    def test_nonempty_project_keeps_outer_files_and_has_five_rooms(self):
        notes = self.project / "notes.md"
        notes.write_text("original research notes", encoding="utf-8")
        agents = self.project / "AGENTS.md"
        agents.write_text("existing user rules", encoding="utf-8")
        result = self.init()
        root = self.project / "house"
        self.assertEqual(result["root"], str(root))
        self.assertEqual(ROOMS, ("main", "literature", "work", "output", "discussion"))
        self.assertEqual(set(p.name for p in root.iterdir() if p.is_dir()), set(ROOMS))
        self.assertEqual(notes.read_text(), "original research notes")
        self.assertEqual(agents.read_text(), "existing user rules")
        for path in ("HOUSE.md", "AGENTS.md", "hive.lock.json", "main/why.md", "main/handoff.md",
                     "main/target.md", "main/design.md", "main/decisions.md", "main/work.md",
                     "main/reference.md", "main/method.md", "main/results.md",
                     "literature/GUIDE.md", "literature/reference.md", "work/GUIDE.md",
                     "output/summary.md", "discussion/GUIDE.md"):
            self.assertTrue((root / path).is_file(), path)
        for room in ROOMS:
            self.assertTrue((root / room / "ROOM.md").is_file())
        for path in ("literature/reference", "literature/extract", "work/data", "work/src",
                     "discussion/requests"):
            self.assertEqual(list((root / path).iterdir()), [], path)

    def test_bootstrap_contract_and_hashed_immutable_release(self):
        result = bootstrap(self.home)
        config = read_json(self.home / "shared/config.json")
        self.assertEqual(config, {"schema_version": 1, "current_release": VERSION,
                                  "bindings": {room: [] for room in ROOMS}, "engine_release": VERSION})
        self.assertEqual(result["current_release"], VERSION)
        manifest_path = self.home / f"shared/releases/{VERSION}/manifest.json"
        manifest = read_json(manifest_path)
        self.assertEqual(manifest["version"], VERSION)
        self.assertEqual(manifest["bindings"], config["bindings"])
        for room, digest in manifest["room_hashes"].items():
            guide = self.home / f"shared/releases/{VERSION}/rooms" / room / "ROOM.md"
            self.assertEqual(hashlib.sha256(guide.read_bytes()).hexdigest(), digest)

        self.assertEqual(set(manifest["room_hashes"]), set(ROOMS))
        self.assertIn("main/design.md", manifest["template_hashes"])
        for relative, digest in manifest["template_hashes"].items():
            template = self.home / "shared/templates" / relative
            self.assertEqual(hashlib.sha256(template.read_bytes()).hexdigest(), digest)
        for path in ("shared/skills/store", "workshop/scans", "workshop/candidates",
                     "workshop/validation", "workshop/notifications", "runtime"):
            self.assertTrue((self.home / path).is_dir())
        self.assertEqual(read_json(self.home / "workshop/schedule.json"),
                         {"schema_version": 1, "frequency": "weekly", "timezone": "Asia/Seoul",
                          "enabled": False, "weekday": None, "time": None})
        before = manifest_path.read_bytes()
        bootstrap(self.home)
        self.assertEqual(manifest_path.read_bytes(), before)

    def test_engine_template_addition_does_not_invalidate_old_project_pin(self):
        from house_engine.templates import project_templates
        self.init()
        custom_directory = self.project / 'house/main/future-template.md'
        custom_directory.mkdir()
        (custom_directory / 'keep.txt').write_text('user material')
        expanded = project_templates()
        expanded['main/future-template.md'] = '# Future capability\n'
        with patch('house_engine.project.project_templates', return_value=expanded):
            result = self.init()
            self.assertEqual(result['release'], VERSION)
            self.assertEqual(status(self.project)['design_status'], 'unconfirmed')
        self.assertEqual((custom_directory / 'keep.txt').read_text(), 'user material')

    def test_reentry_preserves_all_user_edits_and_lock(self):
        self.init("Initial goal")
        root = self.project / "house"
        changed = root / "main/target.md"
        changed.write_text("user-written goal\n", encoding="utf-8")
        guide = root / "literature/ROOM.md"
        guide.write_text("user room instructions", encoding="utf-8")
        before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}
        result = self.init("replacement must not overwrite")
        after = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(result["created"], [])
        self.assertIn("main/target.md", result["existing"])

    def test_goal_seeds_only_initial_target(self):
        self.init("Compare a baseline with the candidate")
        root = self.project / "house"
        self.assertIn("Compare a baseline with the candidate", (root / "main/target.md").read_text())
        for p in root.rglob("*.md"):
            if p != root / "main/target.md":
                self.assertNotIn("Compare a baseline with the candidate", p.read_text())
        (root / "main/target.md").unlink()
        self.init("late goal must not become agreement")
        self.assertNotIn("late goal must not become agreement", (root / "main/target.md").read_text())

    def test_ancestor_discovery_and_nested_init_do_not_nest_house(self):
        first = self.init()
        root = Path(first["root"])
        nested = root / "work/src"
        self.assertEqual(find_house(nested), root)
        result = init_project(nested, self.base / "ignored home")
        self.assertEqual(result["root"], str(root))
        self.assertEqual(result["created"], [])
        self.assertFalse((root / "house").exists())
        self.assertFalse((nested / "house").exists())
        self.assertFalse((self.base / "ignored home").exists())
        self.assertEqual(find_house(root / "main/target.md"), root)

    def test_unmanaged_collision_is_rejected_before_any_changes(self):
        target = self.project / "house"
        target.mkdir()
        (target / "user.txt").write_text("keep this")
        with self.assertRaises(HouseError):
            self.init()
        self.assertEqual(list(target.iterdir()), [target / "user.txt"])
        self.assertFalse(self.home.exists())

    def test_existing_file_collision_is_rejected_before_shared_bootstrap(self):
        target = self.project / "house"
        target.write_text("this is not a directory")
        with self.assertRaises(HouseError):
            self.init()
        self.assertEqual(target.read_text(), "this is not a directory")
        self.assertFalse(self.home.exists())

    def test_unsafe_root_and_room_symlinks_are_rejected_without_changes(self):
        outside = self.base / "outside"
        outside.mkdir()
        (self.project / "house").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(HouseError):
            self.init()
        self.assertEqual(list(outside.iterdir()), [])
        self.assertFalse(self.home.exists())
        (self.project / "house").unlink()
        root = Path(self.init()["root"])
        (root / "main/target.md").unlink()
        (root / "main/target.md").symlink_to(outside / "target.md")
        (root / "main/design.md").unlink()
        with self.assertRaises(HouseError):
            self.init()
        self.assertFalse((root / "main/design.md").exists())
        self.assertFalse((outside / "target.md").exists())

    def test_shared_symlink_conflict_preflight_preserves_project(self):
        outside = self.base / "outside"
        outside.mkdir()
        (self.home / "shared").mkdir(parents=True)
        (self.home / "shared/templates").symlink_to(outside, target_is_directory=True)
        before = sorted(str(p.relative_to(self.home)) for p in self.home.rglob("*"))
        with self.assertRaises(HouseError):
            self.init()
        self.assertFalse((self.project / "house").exists())
        self.assertEqual(before, sorted(str(p.relative_to(self.home)) for p in self.home.rglob("*")))
        self.assertEqual(list(outside.iterdir()), [])

    def test_shared_default_change_does_not_migrate_existing_project(self):
        root = Path(self.init()["root"])
        lock_before = (root / "hive.lock.json").read_bytes()
        config = read_json(self.home / "shared/config.json")
        config["current_release"] = "future-release"
        config["bindings"]["main"] = [{"path": "/new/skill"}]
        atomic_json(self.home / "shared/config.json", config)
        result = self.init()
        self.assertEqual(result["release"], VERSION)
        self.assertEqual((root / "hive.lock.json").read_bytes(), lock_before)
        self.assertEqual(read_json(root / "hive.lock.json")["bindings"], {r: [] for r in ROOMS})

    def test_invalid_shared_current_release_does_not_partially_repair_home(self):
        bootstrap(self.home)
        config = read_json(self.home / "shared/config.json")
        config["current_release"] = "missing-release"
        atomic_json(self.home / "shared/config.json", config)
        (self.home / "workshop/validation").rmdir()
        before = sorted(str(p.relative_to(self.home)) for p in self.home.rglob("*"))
        with self.assertRaises(HouseError):
            bootstrap(self.home)
        self.assertEqual(before, sorted(str(p.relative_to(self.home)) for p in self.home.rglob("*")))

    def test_concurrent_initialization_preserves_winning_goal(self):
        raced = False

        @contextmanager
        def delayed_lock(path):
            nonlocal raced
            if path.name.startswith("project-") and not raced:
                raced = True
                self.init("goal created by the winning initializer")
            with locked(path):
                yield

        with patch("house_engine.project.locked", delayed_lock):
            result = self.init("losing initializer must preserve winner")
        self.assertEqual(result["created"], [])
        self.assertIn("goal created by the winning initializer",
                      (self.project / "house/main/target.md").read_text())

    def test_no_fabricated_models_or_completed_research(self):
        root = Path(self.init()["root"])
        self.assertEqual(list(root.rglob("*.ipynb")), [])
        self.assertEqual(list((root / "work/src").iterdir()), [])
        self.assertEqual([p.name for p in (root / "output").iterdir() if p.is_dir()], [])
        state = status(root / "work")
        self.assertEqual(state["root"], str(root))
        self.assertEqual(state["release"], VERSION)
        self.assertEqual(state["design_status"], "unconfirmed")
        self.assertEqual(state["results_status"], "not_recorded")
        self.assertEqual(set(state["rooms"]), set(ROOMS))
        self.assertIn("model_v1", (root / "work/ROOM.md").read_text())
        self.assertIn("output/<version>/<run>/", (root / "output/ROOM.md").read_text())

    def test_default_home_uses_explicit_environment(self):
        with patch.dict(os.environ, {"HOUSE_HOME": str(self.home)}):
            self.assertEqual(default_home(), self.home)
        with patch.dict(os.environ, {}, clear=True):
            with patch('house_engine.project.sys.platform', 'darwin'):
                self.assertEqual(default_home(), Path.home() / "Library/Application Support/House")
            with patch('house_engine.project.sys.platform', 'linux'):
                self.assertEqual(default_home(), Path.home() / ".local/share/house")

    def test_status_and_find_for_uninitialized_project(self):
        self.assertIsNone(find_house(self.project))
        with self.assertRaises(HouseError):
            status(self.project)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()

    def test_atomic_json_roundtrip_and_invalid_json(self):
        path = self.base / "state.json"
        atomic_json(path, {"한글": [1, 2]})
        self.assertEqual(read_json(path), {"한글": [1, 2]})
        atomic_json(path, {"new": True})
        self.assertEqual(read_json(path), {"new": True})
        self.assertEqual([p.name for p in self.base.iterdir()], ["state.json"])
        for raw in ("broken", "[]"):
            path.write_text(raw)
            with self.assertRaises(HouseError):
                read_json(path)

    def test_safe_path_and_identifier_reject_escapes_and_symlinks(self):
        self.assertEqual(safe_path(self.base, "room/guide.md"), self.base / "room/guide.md")
        for value in ("../outside", "/outside", "room/../../outside", "", "room\\..\\outside"):
            with self.subTest(value=value), self.assertRaises(HouseError):
                safe_path(self.base, value)
        outside = self.base.parent / "outside"
        (self.base / "link").symlink_to(outside)
        with self.assertRaises(HouseError):
            safe_path(self.base, "link/data.json")
        for value in ("../x", "x/y", "a b", "", ".", "..", "x\\y"):
            with self.subTest(value=value), self.assertRaises(HouseError):
                validate_id(value)
        self.assertEqual(validate_id("0.1.0-test_2"), "0.1.0-test_2")

    def test_json_and_lock_reject_symlink_targets(self):
        outside = self.base / "user.json"
        outside.write_text('{"keep": true}')
        target = self.base / "link.json"
        target.symlink_to(outside)
        with self.assertRaises(HouseError):
            atomic_json(target, {"replacement": True})
        with self.assertRaises(HouseError):
            with locked(target):
                self.fail("symlink lock must not be acquired")
        self.assertEqual(outside.read_text(), '{"keep": true}')

    def test_lock_fails_clearly_while_in_use_and_releases_on_exception(self):
        path = self.base / "task.lock"
        script = ("from pathlib import Path; from house_engine.storage import locked; "
                  "ctx = locked(Path(__import__('sys').argv[1])); ctx.__enter__()")
        with self.assertRaisesRegex(RuntimeError, "deliberate"):
            with locked(path):
                child = subprocess.run([sys.executable, "-c", script, str(path)],
                                       text=True, capture_output=True)
                self.assertNotEqual(child.returncode, 0)
                self.assertIn("in use", child.stderr.lower())
                raise RuntimeError("deliberate")
        with locked(path):
            pass


if __name__ == "__main__":
    unittest.main()
