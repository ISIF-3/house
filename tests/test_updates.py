import copy
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from urllib.request import Request

from house_engine.project import ROOMS, bootstrap, init_project
from house_engine.storage import HouseError, atomic_json, read_json
from house_engine import updates


COMMIT = "a" * 40
NEXT_COMMIT = "b" * 40
SKILL = b"---\nname: example-skill\ndescription: A research test skill.\n---\nRead evidence before making claims.\n"


def archive(entries=None):
    output = io.BytesIO()
    if entries is None:
        entries = [("repo-commit/skills/example/SKILL.md", SKILL, None),
                   ("repo-commit/skills/example/references/guide.md", b"Use source citations.\n", None)]
    with tarfile.open(fileobj=output, mode="w:gz") as stream:
        for name, data, kind in entries:
            info = tarfile.TarInfo(name)
            if kind == "symlink":
                info.type = tarfile.SYMTYPE
                info.linkname = "../../outside"
                stream.addfile(info)
            else:
                info.size = len(data)
                stream.addfile(info, io.BytesIO(data))
    return output.getvalue()


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.home = self.base / "common house"
        bootstrap(self.home)

    def network(self, url, **kwargs):
        if "/commits/" in url:
            return {"sha": COMMIT}
        if "codeload.github.com" in url or "/tarball/" in url:
            return archive()
        if "/search/repositories?" in url:
            return {"items": [{"full_name": "owner/example", "html_url": "https://github.com/owner/example",
                                "description": "Unreviewed research skill", "stargazers_count": 10}]}
        if "github.com/trending" in url:
            return b'<article><h2><a href="/someone/trending">someone / trending</a></h2></article>'
        raise AssertionError("Unexpected request: " + url)

    def stage(self):
        with patch.object(updates, "_request", side_effect=self.network):
            return updates.stage_skill(self.home, "owner/example", "skills/example", "main", "literature", "example")

    def review(self, stage, approved=True):
        report = self.base / "representative-report.md"
        report.write_text("Actual fixture task: read local example and identify its source link. Passed.")
        evidence = self.base / "review.json"
        evidence.write_text(json.dumps({
            "decision": "approve" if approved else "reject", "reviewer": "test reviewer",
            "summary": "Reviewed scope, permissions, license and representative source extraction.",
            "checks": {"content_reviewed": True, "scope_compatible": approved,
                       "representative_test_passed": approved},
            "evidence_paths": [str(report)], "limitations": ["Fixture only; no external model executed."],
        }))
        return updates.record_review(self.home, stage["stage_id"], evidence)

    def notifications(self):
        return list((self.home / "workshop/notifications").glob("*.json"))

    def test_scan_records_provisional_discovery_without_claiming_adoption(self):
        with patch.object(updates, "_request", side_effect=self.network):
            report = updates.scan(self.home, rooms=["literature"], limit=2)
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["status"], "complete")
        self.assertTrue(Path(report["path"]).is_file())
        self.assertEqual({c["source"] for c in report["candidates"]}, {"github_search", "github_trending"})
        self.assertTrue(all(c["provisional"] for c in report["candidates"]))
        self.assertEqual(len(self.notifications()), 1)
        notice = read_json(self.notifications()[0])
        self.assertEqual(notice["kind"], "candidate_discovered")
        self.assertTrue(notice["provisional"])
        self.assertEqual(notice["repository"], "owner/example")

    def test_repeated_candidate_scan_is_quiet_and_trending_never_notifies(self):
        with patch.object(updates, "_request", side_effect=self.network):
            first = updates.scan(self.home, rooms=["literature"])
            second = updates.scan(self.home, rooms=["literature"])
        self.assertEqual(len(first["notifications"]), 1)
        self.assertEqual(second["notifications"], [])
        self.assertEqual(len(self.notifications()), 1)
        with patch.object(updates, "_request", side_effect=self.network):
            other_room = updates.scan(self.home, rooms=["work"])
        self.assertEqual(len(other_room["notifications"]), 1)
        self.assertEqual(len(self.notifications()), 2)

    def test_failed_initial_scan_records_errors_and_no_notification(self):
        with patch.object(updates, "_request", side_effect=HouseError("GitHub HTTP 429")):
            report = updates.scan(self.home, rooms=["main"])
        self.assertEqual(report["status"], "partial")
        self.assertEqual(len(report["errors"]), 2)
        self.assertEqual(report["candidates"], [])
        self.assertEqual(self.notifications(), [])

    def test_malformed_search_item_is_a_recorded_source_error(self):
        def malformed(url, **kwargs):
            return {"items": [None]} if "/search/" in url else self.network(url, **kwargs)
        with patch.object(updates, "_request", side_effect=malformed):
            report = updates.scan(self.home, rooms=["main"])
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["errors"][0]["source"], "github_search")
        self.assertTrue(Path(report["path"]).is_file())

    def test_stage_resolves_exact_commit_and_hashes_complete_tree(self):
        stage = self.stage()
        self.assertEqual(stage["commit"], COMMIT)
        self.assertEqual(stage["room"], "literature")
        self.assertEqual(set(stage["files"]), {"SKILL.md", "references/guide.md"})
        self.assertEqual((Path(stage["path"]) / "SKILL.md").read_bytes(), SKILL)
        self.assertEqual(read_json(self.home / "shared/config.json")["current_release"], "0.1.0")

    def test_archive_download_uses_api_at_resolved_commit(self):
        with patch.object(updates, "_request", side_effect=self.network) as request:
            updates.stage_skill(self.home, "owner/example", "skills/example", "main", "work", "test")
        urls = [call.args[0] for call in request.call_args_list]
        self.assertIn("https://api.github.com/repos/owner/example/tarball/" + COMMIT, urls)

    def test_stage_rejects_traversal_symlinks_and_missing_skill(self):
        cases = [
            [("repo-commit/skills/example/../../escape", b"bad", None)],
            [("repo-commit/skills/example/SKILL.md", b"", "symlink")],
            [("repo-commit/skills/example/notes.md", b"no skill", None)],
            [("/absolute/skills/example/SKILL.md", SKILL, None)],
        ]
        for entries in cases:
            with self.subTest(entries=entries):
                def network(url, **kwargs):
                    return archive(entries) if "codeload" in url or "/tarball/" in url else {"sha": COMMIT}
                with patch.object(updates, "_request", side_effect=network), self.assertRaises(HouseError):
                    updates.stage_skill(self.home, "owner/example", "skills/example", "main", "work", "test")
        self.assertEqual(list((self.home / "shared/skills/store").iterdir()), [])

    def test_stage_rejects_oversized_content_and_bad_user_paths(self):
        with patch.object(updates, "MAX_SKILL_BYTES", 10), patch.object(updates, "_request", side_effect=self.network):
            with self.assertRaises(HouseError):
                updates.stage_skill(self.home, "owner/example", "skills/example", "main", "work", "test")
        for path in ("../skill", "/tmp/skill", "skills/../../outside", "skills\\example"):
            with self.subTest(path=path), self.assertRaises(HouseError):
                updates.stage_skill(self.home, "owner/example", path, "main", "work", "test")

    def test_expanded_archive_limit_includes_pax_metadata(self):
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w:gz", format=tarfile.PAX_FORMAT) as stream:
            info = tarfile.TarInfo("repo-commit/skills/example/SKILL.md")
            info.size = len(SKILL)
            info.pax_headers = {"comment": "x" * 8192}
            stream.addfile(info, io.BytesIO(SKILL))

        def network(url, **kwargs):
            return output.getvalue() if "/tarball/" in url else {"sha": COMMIT}

        with patch.object(updates, "MAX_ARCHIVE_EXPANDED_BYTES", 256), patch.object(updates, "_request", side_effect=network):
            with self.assertRaisesRegex(HouseError, "expanded size"):
                updates.stage_skill(self.home, "owner/example", "skills/example", "main", "work", "test")
        self.assertEqual(list((self.home / "shared/skills/store").iterdir()), [])

    def test_interrupted_store_copy_leaves_shared_state_unchanged_and_retry_succeeds(self):
        stage = self.stage()
        self.review(stage)
        before_config = (self.home / "shared/config.json").read_bytes()
        before_releases = sorted(p.name for p in (self.home / "shared/releases").iterdir())
        store = self.home / "shared/skills/store" / stage["name"] / stage["content_hash"]

        def interrupted_copy(source, destination, *args, **kwargs):
            destination = Path(destination)
            destination.mkdir(parents=True)
            (destination / "SKILL.md").write_bytes(SKILL)
            raise OSError("interrupted during remaining files")

        with patch.object(updates.shutil, "copytree", side_effect=interrupted_copy):
            with self.assertRaises((OSError, HouseError)):
                updates.apply_stage(self.home, stage["stage_id"])
        self.assertFalse(store.exists(), "an incomplete copy must never occupy the final immutable path")
        self.assertEqual(list(store.parent.iterdir()), [])
        self.assertEqual((self.home / "shared/config.json").read_bytes(), before_config)
        self.assertEqual(sorted(p.name for p in (self.home / "shared/releases").iterdir()), before_releases)
        self.assertEqual(self.notifications(), [])
        result = updates.apply_stage(self.home, stage["stage_id"])
        self.assertEqual(result["status"], "applied")
        updates.verify_binding(self.home, result["binding"])

    def test_existing_corrupted_final_store_is_preserved(self):
        stage = self.stage()
        self.review(stage)
        store = self.home / "shared/skills/store" / stage["name"] / stage["content_hash"]
        store.mkdir(parents=True)
        sentinel = store / "user-file.txt"
        sentinel.write_text("do not remove this existing content")
        before_config = (self.home / "shared/config.json").read_bytes()
        with self.assertRaises(HouseError):
            updates.apply_stage(self.home, stage["stage_id"])
        self.assertEqual(sentinel.read_text(), "do not remove this existing content")
        self.assertEqual(list(store.iterdir()), [sentinel])
        self.assertEqual((self.home / "shared/config.json").read_bytes(), before_config)

    def test_apply_requires_actual_approved_review(self):
        stage = self.stage()
        with self.assertRaises(HouseError):
            updates.apply_stage(self.home, stage["stage_id"])
        self.review(stage, approved=False)
        with self.assertRaises(HouseError):
            updates.apply_stage(self.home, stage["stage_id"])
        self.assertEqual(self.notifications(), [])

    def test_review_rejects_approval_without_required_evidence(self):
        stage = self.stage()
        evidence = self.base / "empty-review.json"
        atomic_json(evidence, {"decision": "approve", "reviewer": "test", "summary": "reviewed",
                               "checks": {"content_reviewed": True, "scope_compatible": True,
                                          "representative_test_passed": True},
                               "evidence_paths": [], "limitations": []})
        with self.assertRaises(HouseError):
            updates.record_review(self.home, stage["stage_id"], evidence)

    def test_altered_stage_metadata_or_content_cannot_be_applied(self):
        stage = self.stage()
        self.review(stage)
        (Path(stage["path"]) / "SKILL.md").write_text("Changed after approval")
        with self.assertRaises(HouseError):
            updates.apply_stage(self.home, stage["stage_id"])
        (Path(stage["path"]) / "SKILL.md").write_bytes(SKILL)
        manifest_path = Path(stage["manifest_path"])
        manifest = read_json(manifest_path)
        manifest["room"] = "work"
        atomic_json(manifest_path, manifest)
        with self.assertRaises(HouseError):
            updates.apply_stage(self.home, stage["stage_id"])

    def test_changed_review_or_snapshot_cannot_be_applied(self):
        stage = self.stage()
        review = self.review(stage)
        record_path = Path(review["path"])
        original = record_path.read_bytes()
        record = read_json(record_path)
        record["summary"] = "Changed review"
        atomic_json(record_path, record)
        with self.assertRaises(HouseError):
            updates.apply_stage(self.home, stage["stage_id"])
        record_path.write_bytes(original)
        snapshot = Path(review["evidence_snapshots"][0]["path"])
        snapshot.write_text("changed evidence")
        with self.assertRaises(HouseError):
            updates.apply_stage(self.home, stage["stage_id"])

    def test_apply_preserves_project_pin_and_creates_verifiable_release(self):
        project = self.base / "research"
        project.mkdir()
        root = Path(init_project(project, self.home)["root"])
        old_lock = (root / "hive.lock.json").read_bytes()
        stage = self.stage()
        self.review(stage)
        result = updates.apply_stage(self.home, stage["stage_id"])
        self.assertEqual(result["status"], "applied")
        self.assertNotEqual(result["release"], "0.1.0")
        self.assertEqual((root / "hive.lock.json").read_bytes(), old_lock)
        manifest = read_json(self.home / "shared/releases" / result["release"] / "manifest.json")
        self.assertEqual(manifest["bindings"]["literature"][0]["commit"], COMMIT)
        binding = manifest["bindings"]["literature"][0]
        updates.verify_binding(self.home, binding)
        fresh = self.base / "new research"
        fresh.mkdir()
        self.assertEqual(init_project(fresh, self.home)["release"], result["release"])
        self.assertEqual(len(self.notifications()), 1)
        repeated = updates.apply_stage(self.home, stage["stage_id"])
        self.assertEqual(repeated["status"], "unchanged")
        self.assertEqual(len(self.notifications()), 1)
        (Path(binding["path"]) / "SKILL.md").write_text("unexpected mutation")
        with self.assertRaises(HouseError):
            updates.verify_binding(self.home, binding)

    def test_verify_binding_rejects_external_path_and_added_symlink(self):
        stage = self.stage()
        self.review(stage)
        release = updates.apply_stage(self.home, stage["stage_id"])["release"]
        binding = read_json(self.home / "shared/releases" / release / "manifest.json")["bindings"]["literature"][0]
        bad = copy.deepcopy(binding)
        bad["path"] = str(self.base)
        with self.assertRaises(HouseError):
            updates.verify_binding(self.home, bad)
        (Path(binding["path"]) / "leak").symlink_to(self.base / "outside")
        with self.assertRaises(HouseError):
            updates.verify_binding(self.home, binding)

    def test_tracked_changes_notify_once_but_errors_and_unchanged_do_not(self):
        stage = self.stage()
        self.review(stage)
        updates.apply_stage(self.home, stage["stage_id"])
        with patch.object(updates, "_request", side_effect=self.network):
            unchanged = updates.scan(self.home, rooms=["literature"])
        self.assertEqual(unchanged["tracked"][0]["status"], "unchanged")
        self.assertEqual(len(self.notifications()), 2)

        def changed_network(url, **kwargs):
            return {"sha": NEXT_COMMIT} if "/commits/" in url else self.network(url, **kwargs)

        with patch.object(updates, "_request", side_effect=changed_network):
            changed = updates.scan(self.home, rooms=["literature"])
            updates.scan(self.home, rooms=["literature"])
        self.assertEqual(changed["tracked"][0]["status"], "changed")
        self.assertEqual(len(self.notifications()), 3)
        with patch.object(updates, "_request", side_effect=HouseError("temporarily unavailable")):
            failed = updates.scan(self.home, rooms=["literature"])
        self.assertEqual(failed["tracked"][0]["status"], "error")
        self.assertTrue(failed["errors"])
        self.assertEqual(len(self.notifications()), 3)

    def test_rollback_changes_shared_only_and_pin_records_explicit_reason(self):
        project = self.base / "research"
        project.mkdir()
        root = Path(init_project(project, self.home)["root"])
        (root / "literature/ROOM.md").write_text("User room edits")
        (root / "main/decisions.md").write_text("User decisions")
        stage = self.stage()
        self.review(stage)
        release = updates.apply_stage(self.home, stage["stage_id"])["release"]
        updates.pin_project(root, release, "User accepted the reviewed literature skill")
        pin = read_json(root / "hive.lock.json")
        self.assertEqual(pin["release"], release)
        self.assertEqual(pin["history"][-1]["reason"], "User accepted the reviewed literature skill")
        self.assertEqual((root / "literature/ROOM.md").read_text(), "User room edits")
        self.assertEqual((root / "main/decisions.md").read_text(), "User decisions")
        pinned = (root / "hive.lock.json").read_bytes()
        result = updates.rollback(self.home, "0.1.0")
        self.assertEqual(result["release"], "0.1.0")
        self.assertEqual((root / "hive.lock.json").read_bytes(), pinned)
        count = len(self.notifications())
        self.assertEqual(updates.rollback(self.home, "0.1.0")["status"], "unchanged")
        self.assertEqual(len(self.notifications()), count)
        with self.assertRaises(HouseError):
            updates.pin_project(root, release, " ")

    def test_rollback_can_recover_from_damaged_current_release(self):
        stage = self.stage()
        self.review(stage)
        release = updates.apply_stage(self.home, stage["stage_id"])["release"]
        (self.home / "shared/releases" / release / "rooms/main/ROOM.md").write_text("damaged current guide")
        self.assertEqual(updates.rollback(self.home, "0.1.0")["status"], "rolled_back")
        self.assertEqual(read_json(self.home / "shared/config.json")["current_release"], "0.1.0")

    def test_credentials_are_api_only_and_removed_on_cross_host_redirect(self):
        calls = []

        class Response:
            def __init__(self, url):
                self.url = url
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def geturl(self):
                return self.url
            def read(self, limit):
                return b"{}"

        class Opener:
            def open(self, request, timeout):
                calls.append(request)
                return Response(request.full_url)

        with patch.dict(os.environ, {"HOUSE_GITHUB_TOKEN": "private-test-token"}), patch.object(updates, "build_opener", return_value=Opener()):
            updates._request("https://api.github.com/repos/owner/example")
            updates._request("https://github.com/trending", json_response=False)
            updates._request("https://codeload.github.com/owner/example/tar.gz/" + COMMIT, json_response=False)
        self.assertEqual(calls[0].get_header("Authorization"), "Bearer private-test-token")
        self.assertIsNone(calls[1].get_header("Authorization"))
        self.assertIsNone(calls[2].get_header("Authorization"))
        request = Request("https://api.github.com/repos/owner/example", headers={"Authorization": "Bearer secret"})
        redirected = updates._Redirect().redirect_request(request, None, 302, "", {}, "https://codeload.github.com/owner/example")
        self.assertIsNone(redirected.get_header("Authorization"))
        with self.assertRaises(HouseError):
            updates._Redirect().redirect_request(request, None, 302, "", {}, "https://untrusted.example/download")


if __name__ == "__main__":
    unittest.main()
