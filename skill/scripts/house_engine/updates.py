"""Bounded GitHub discovery and reviewed, immutable skill releases.

Downloaded code is data throughout this module. No subprocess, import or script
execution is used to validate an upstream skill.
"""

import copy
from datetime import datetime, timezone
import gzip
import hashlib
from html.parser import HTMLParser
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tarfile
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
import uuid

from . import __version__
from .project import ROOMS, _bindings, _config, _lock, _release, _root, bootstrap, find_house
from .storage import HouseError, atomic_json, locked, read_json, safe_path, validate_id

MAX_ARCHIVE_BYTES = 25 * 1024 * 1024
MAX_ARCHIVE_EXPANDED_BYTES = 100 * 1024 * 1024
MAX_SKILL_BYTES = 10 * 1024 * 1024
MAX_SKILL_FILES = 1000
MAX_EVIDENCE_BYTES = 5 * 1024 * 1024
QUERIES = {"main": "agent skills research planning", "literature": "agent skills literature research",
           "work": "agent skills data analysis", "output": "agent skills scientific writing",
           "discussion": "agent skills research review"}
HOSTS = {"api.github.com", "github.com", "codeload.github.com"}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _json_hash(value):
    return _hash(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8"))


def _url(url):
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in HOSTS or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise HouseError("Only HTTPS GitHub endpoints are allowed")
    return parsed


class _Redirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = _url(newurl)
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None and target.hostname != "api.github.com":
            redirected.remove_header("Authorization")
        return redirected


def _request(url, json_response=True, max_bytes=2 * 1024 * 1024):
    """Single injectable network boundary. Credentials never leave the API host."""
    parsed = _url(url)
    headers = {"User-Agent": "House/" + __version__, "Accept": "application/vnd.github+json" if json_response else "*/*"}
    token = os.environ.get("HOUSE_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if parsed.hostname == "api.github.com" and token:
        headers["Authorization"] = "Bearer " + token
    try:
        with build_opener(_Redirect()).open(Request(url, headers=headers), timeout=20) as response:
            _url(response.geturl())
            body = response.read(max_bytes + 1)
        if len(body) > max_bytes:
            raise HouseError("GitHub response exceeded the download size limit")
        if json_response:
            value = json.loads(body.decode("utf-8"))
            if not isinstance(value, dict):
                raise HouseError("Expected a GitHub JSON object")
            return value
        return body
    except HTTPError as exc:
        raise HouseError("GitHub HTTP {} (request failed; rate limits or authentication may apply)".format(exc.code)) from None
    except (URLError, TimeoutError, OSError):
        raise HouseError("GitHub network request failed or timed out") from None
    except (ValueError, UnicodeError):
        raise HouseError("GitHub returned an invalid response") from None


def _repository(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", value):
        raise HouseError("Repository must be OWNER/REPOSITORY")
    return value


def _source_path(value):
    if value == ".":
        return ""
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise HouseError("Invalid skill subtree path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(p in ("", ".", "..") for p in value.split("/")):
        raise HouseError("Skill path must remain inside the repository")
    return str(path)


def _room(value):
    if value not in ROOMS:
        raise HouseError("Unknown House room: {}".format(value))
    return value


def _commit(repository, ref):
    _repository(repository)
    if not isinstance(ref, str) or not ref.strip() or len(ref) > 200 or any(ord(c) < 32 for c in ref):
        raise HouseError("Invalid GitHub revision")
    result = _request("https://api.github.com/repos/{}/commits/{}".format(repository, quote(ref, safe="")))
    commit = result.get("sha")
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", commit):
        raise HouseError("GitHub did not resolve an exact commit")
    return commit.lower()


def _tree_manifest(root):
    root = _root(root)
    if not root.is_dir():
        raise HouseError("Skill directory does not exist: {}".format(root))
    files, total = {}, 0
    for path in sorted(root.rglob("*")):
        relative = str(path.relative_to(root))
        checked = safe_path(root, relative)
        if checked.is_dir():
            continue
        if not checked.is_file():
            raise HouseError("Skill contains a special file: {}".format(relative))
        size = checked.stat().st_size
        total += size
        if total > MAX_SKILL_BYTES or len(files) >= MAX_SKILL_FILES:
            raise HouseError("Skill exceeds the file or size limit")
        files[relative] = _hash(checked.read_bytes())
    if "SKILL.md" not in files:
        raise HouseError("Selected directory must contain SKILL.md")
    try:
        text = (root / "SKILL.md").read_text(encoding="utf-8")
    except (UnicodeError, OSError) as exc:
        raise HouseError("SKILL.md must be readable UTF-8") from exc
    frontmatter = re.match(r"\A---\s*\n(.*?)\n---(?:\s*\n|\Z)", text, flags=re.S)
    if not frontmatter or not all(re.search(r"^{}:\s*\S".format(key), frontmatter.group(1), re.M) for key in ("name", "description")):
        raise HouseError("SKILL.md requires name and description frontmatter")
    return files


def _extract(blob, source_path, destination):
    if not isinstance(blob, bytes) or len(blob) > MAX_ARCHIVE_BYTES:
        raise HouseError("Archive exceeds the download limit")
    total, selected, selected_size, archive_root = 0, {}, 0, None
    try:
        # Tarfile consumes PAX/GNU metadata internally before yielding members.
        # Bound the full decompressed stream, including those hidden records.
        with gzip.GzipFile(fileobj=io.BytesIO(blob), mode="rb") as compressed:
            expanded = compressed.read(MAX_ARCHIVE_EXPANDED_BYTES + 1)
        if len(expanded) > MAX_ARCHIVE_EXPANDED_BYTES:
            raise HouseError("Archive exceeds the expanded size limit")
        with tarfile.open(fileobj=io.BytesIO(expanded), mode="r:") as archive:
            for index, member in enumerate(archive):
                if index >= 20000:
                    raise HouseError("Archive contains too many entries")
                name = member.name
                if name.startswith("/") or "\\" in name or "\x00" in name or ".." in name.split("/"):
                    raise HouseError("Unsafe archive path")
                parts = PurePosixPath(name).parts
                if not parts:
                    continue
                if archive_root is None:
                    archive_root = parts[0]
                if parts[0] != archive_root:
                    raise HouseError("Archive has multiple repository roots")
                total += max(member.size, 0)
                if total > MAX_ARCHIVE_EXPANDED_BYTES:
                    raise HouseError("Archive exceeds the expanded size limit")
                inner = "/".join(parts[1:])
                prefix = source_path + "/" if source_path else ""
                if source_path and not inner.startswith(prefix):
                    continue
                relative = inner[len(prefix):]
                if not relative or member.isdir():
                    continue
                if not member.isfile() or member.issym() or member.islnk():
                    raise HouseError("Skill archive contains a link or special file")
                if relative in selected:
                    raise HouseError("Skill archive contains duplicate paths")
                selected_size += member.size
                if selected_size > MAX_SKILL_BYTES or len(selected) >= MAX_SKILL_FILES:
                    raise HouseError("Skill exceeds the file or size limit")
                target = safe_path(destination, relative)
                with archive.extractfile(member) as stream:
                    content = stream.read(MAX_SKILL_BYTES + 1)
                if len(content) != member.size:
                    raise HouseError("Truncated or oversized skill archive entry")
                selected[relative] = (target, content)
        if "SKILL.md" not in selected:
            raise HouseError("Selected directory must contain SKILL.md")
        for relative, (target, content) in selected.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as stream:
                stream.write(content)
    except (tarfile.TarError, EOFError, OSError) as exc:
        raise HouseError("Could not safely stage GitHub archive: {}".format(type(exc).__name__)) from exc


def _stage(home, stage_id):
    validate_id(stage_id)
    stage_root = safe_path(home, "workshop/candidates/" + stage_id)
    manifest_path = safe_path(stage_root, "stage.json")
    manifest = read_json(manifest_path)
    if manifest.get("schema_version") != 1 or manifest.get("stage_id") != stage_id:
        raise HouseError("Invalid stage manifest")
    _repository(manifest.get("repository"))
    _source_path(manifest.get("source_path") or ".")
    _room(manifest.get("room"))
    validate_id(manifest.get("name"))
    if not re.fullmatch(r"[0-9a-f]{40}", manifest.get("commit", "")):
        raise HouseError("Stage must identify an exact commit")
    skill = safe_path(stage_root, "skill")
    files = _tree_manifest(skill)
    if files != manifest.get("files") or _json_hash(files) != manifest.get("content_hash"):
        raise HouseError("Staged content changed; stage and review it again")
    return manifest, skill, manifest_path


def stage_skill(home, repository, path, ref, room, name):
    _repository(repository)
    source_path = _source_path(path)
    _room(room)
    validate_id(name)
    home = _root(home)
    bootstrap(home)
    commit = _commit(repository, ref)
    blob = _request("https://api.github.com/repos/{}/tarball/{}".format(repository, commit),
                    json_response=False, max_bytes=MAX_ARCHIVE_BYTES)
    stage_id = "stage-{}-{}-{}".format(name[:48], commit[:12], uuid.uuid4().hex[:12])
    stage_root = safe_path(home, "workshop/candidates/" + stage_id)
    with tempfile.TemporaryDirectory(prefix="house-stage-", dir=str(safe_path(home, "runtime"))) as temporary:
        temporary_root = Path(temporary)
        skill = temporary_root / "skill"
        skill.mkdir()
        _extract(blob, source_path, skill)
        files = _tree_manifest(skill)
        manifest = {"schema_version": 1, "stage_id": stage_id, "created_at": _now(), "name": name,
                    "room": room, "repository": repository, "source_path": source_path,
                    "ref": ref, "commit": commit, "files": files, "content_hash": _json_hash(files)}
        atomic_json(temporary_root / "stage.json", manifest)
        with locked(safe_path(home, "runtime/shared.lock")):
            if stage_root.exists():
                raise HouseError("Stage identifier already exists")
            temporary_root.rename(stage_root)
    return dict(manifest, path=str(stage_root / "skill"), manifest_path=str(stage_root / "stage.json"), status="staged")


def _evidence(value):
    if value.get("decision") not in ("approve", "reject"):
        raise HouseError("Review decision must be approve or reject")
    for field in ("reviewer", "summary"):
        if not isinstance(value.get(field), str) or not value[field].strip():
            raise HouseError("Review requires a nonempty " + field)
    if not isinstance(value.get("checks"), dict):
        raise HouseError("Review requires explicit checks")
    if value["decision"] == "approve" and any(value["checks"].get(check) is not True for check in
                                               ("content_reviewed", "scope_compatible", "representative_test_passed")):
        raise HouseError("Approval requires content, scope and representative task checks")
    if not isinstance(value.get("limitations"), list) or any(not isinstance(v, str) for v in value["limitations"]):
        raise HouseError("Review limitations must be a list of strings")
    paths = value.get("evidence_paths")
    if not isinstance(paths, list) or not paths or len(paths) > 20 or any(not isinstance(p, str) for p in paths):
        raise HouseError("Review requires 1–20 existing evidence files")
    return value


def record_review(home, stage_id, evidence_path):
    home = _root(home)
    manifest, _, _ = _stage(home, stage_id)
    evidence_path = Path(evidence_path).expanduser().absolute()
    safe_path(evidence_path.parent, evidence_path.name)
    if evidence_path.stat().st_size > MAX_EVIDENCE_BYTES:
        raise HouseError("Review evidence JSON exceeds the size limit")
    value = _evidence(read_json(evidence_path))
    evidence_bytes = evidence_path.read_bytes()
    sources, total = [], len(evidence_bytes)
    for source in value["evidence_paths"]:
        path = Path(source)
        if not path.is_absolute():
            raise HouseError("Evidence paths must be absolute")
        path = safe_path(path.parent, path.name)
        if not path.is_file():
            raise HouseError("Evidence file does not exist: {}".format(path))
        total += path.stat().st_size
        if total > MAX_EVIDENCE_BYTES:
            raise HouseError("Review evidence exceeds the total size limit")
        sources.append((str(path), path.read_bytes()))
    review_id = "review-" + uuid.uuid4().hex
    relative = "workshop/validation/" + stage_id + "/" + review_id
    review_root = safe_path(home, relative)
    with locked(safe_path(home, "runtime/shared.lock")):
        current, _, _ = _stage(home, stage_id)
        if current != manifest:
            raise HouseError("Stage changed during review")
        review_root.mkdir(parents=True)
        (review_root / "evidence.json").write_bytes(evidence_bytes)
        snapshots = []
        for index, (source, data) in enumerate(sources):
            filename = "evidence-{}.bin".format(index)
            (review_root / filename).write_bytes(data)
            snapshots.append({"source": source, "file": filename, "sha256": _hash(data)})
        record = {"schema_version": 1, "stage_id": stage_id, "review_id": review_id,
                  "created_at": _now(), "stage_hash": _json_hash(manifest),
                  "content_hash": manifest["content_hash"], "decision": value["decision"],
                  "reviewer": value["reviewer"], "summary": value["summary"],
                  "evidence_hash": _hash(evidence_bytes), "snapshots": snapshots}
        atomic_json(review_root / "record.json", record)
        atomic_json(safe_path(home, "workshop/validation/" + stage_id + "/current.json"),
                    {"review_id": review_id, "record_hash": _json_hash(record)})
    return {"stage_id": stage_id, "review_id": review_id, "decision": value["decision"],
            "path": str(review_root / "record.json"), "evidence_path": str(review_root / "evidence.json"),
            "evidence_snapshots": [dict(s, path=str(review_root / s["file"])) for s in snapshots]}


def _review(home, manifest):
    validation = safe_path(home, "workshop/validation/" + manifest["stage_id"])
    pointer = read_json(safe_path(validation, "current.json"))
    review_id = validate_id(pointer.get("review_id"))
    root = safe_path(validation, review_id)
    record = read_json(safe_path(root, "record.json"))
    if _json_hash(record) != pointer.get("record_hash") or record.get("stage_hash") != _json_hash(manifest):
        raise HouseError("Stage or review record changed after review")
    evidence_path = safe_path(root, "evidence.json")
    if _hash(evidence_path.read_bytes()) != record.get("evidence_hash"):
        raise HouseError("Review evidence changed after review")
    evidence = _evidence(read_json(evidence_path))
    if record.get("decision") != "approve" or evidence["decision"] != "approve":
        raise HouseError("Stage has not been approved")
    snapshots = record.get("snapshots")
    if not isinstance(snapshots, list) or len(snapshots) != len(evidence["evidence_paths"]):
        raise HouseError("Review evidence snapshot list is invalid")
    for snapshot in snapshots:
        path = safe_path(root, snapshot["file"])
        if _hash(path.read_bytes()) != snapshot["sha256"]:
            raise HouseError("Representative task evidence changed after review")
    return record


def verify_binding(home, binding):
    home = _root(home)
    if not isinstance(binding, dict):
        raise HouseError("Invalid skill binding")
    name = validate_id(binding.get("name"))
    digest = binding.get("content_hash")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise HouseError("Invalid skill content hash")
    expected = safe_path(home, "shared/skills/store/{}/{}".format(name, digest))
    if binding.get("path") != str(expected):
        raise HouseError("Skill binding must use its exact immutable store path")
    _repository(binding.get("repository"))
    _source_path(binding.get("source_path") or ".")
    _room(binding.get("room"))
    if not re.fullmatch(r"[0-9a-f]{40}", binding.get("commit", "")):
        raise HouseError("Skill binding must pin an exact commit")
    files = _tree_manifest(expected)
    if files != binding.get("files") or _json_hash(files) != digest:
        raise HouseError("Immutable skill content hash mismatch")
    return binding


def _verify_release_bindings(home, manifest):
    for room, bindings in manifest["bindings"].items():
        for binding in bindings:
            if binding.get("room") != room:
                raise HouseError("Skill binding room does not match its release")
            verify_binding(home, binding)


def _notify(home, event, key):
    path = safe_path(home, "workshop/notifications/" + _json_hash(key) + ".json")
    if path.exists():
        return None
    atomic_json(path, dict(event, created_at=_now()))
    return str(path)


def apply_stage(home, stage_id):
    home = _root(home)
    bootstrap(home)
    with locked(safe_path(home, "runtime/shared.lock")):
        stage, skill, _ = _stage(home, stage_id)
        review = _review(home, stage)
        config = _config(read_json(safe_path(home, "shared/config.json")))
        previous, rooms, templates = _release(home, config["current_release"])
        _verify_release_bindings(home, previous)
        store = safe_path(home, "shared/skills/store/{}/{}".format(stage["name"], stage["content_hash"]))
        binding = {key: copy.deepcopy(stage[key]) for key in
                   ("name", "room", "repository", "source_path", "ref", "commit", "content_hash", "files")}
        binding["path"] = str(store)
        existing = previous["bindings"][stage["room"]]
        if binding in existing:
            return {"status": "unchanged", "stage_id": stage_id, "release": previous["version"], "binding": binding}
        if not store.exists():
            store.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=".house-store-", dir=str(store.parent)) as temporary:
                staged_store = Path(temporary) / "skill"
                shutil.copytree(skill, staged_store)
                copied_files = _tree_manifest(staged_store)
                if copied_files != stage["files"] or _json_hash(copied_files) != stage["content_hash"]:
                    raise HouseError("Copied skill content hash mismatch; immutable store was not published")
                safe_path(home, "shared/skills/store/{}/{}".format(stage["name"], stage["content_hash"]))
                if store.exists():
                    raise HouseError("Immutable store path appeared during publication; existing content was preserved")
                staged_store.rename(store)
        verify_binding(home, binding)
        release = __version__ + "-u" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:12]
        release_root = safe_path(home, "shared/releases/" + release)
        manifest = copy.deepcopy(previous)
        manifest.update(version=release, parent_release=previous["version"], created_at=_now(),
                        applied_stage=stage_id, review_id=review["review_id"])
        manifest["bindings"][stage["room"]] = [b for b in existing if b["name"] != stage["name"]] + [binding]
        release_root.mkdir()
        for room, text in rooms.items():
            target = safe_path(release_root, "rooms/" + room + "/ROOM.md")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        for relative, text in templates.items():
            target = safe_path(release_root, "templates/" + relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        atomic_json(release_root / "manifest.json", manifest)
        _release(home, release)
        config.update(current_release=release, bindings=_bindings(manifest["bindings"]))
        atomic_json(safe_path(home, "shared/config.json"), config)
        notice = _notify(home, {"kind": "applied", "release": release, "room": stage["room"],
                              "message": "house v{} #{} skill update".format(release, stage["room"])},
                         {"kind": "applied", "release": release})
    return {"status": "applied", "stage_id": stage_id, "release": release, "binding": binding, "notification": notice}


def rollback(home, release):
    home = _root(home)
    validate_id(release)
    # A damaged active release is a reason to roll back. Validate the target,
    # without requiring the current release to pass bootstrap's integrity check.
    if not safe_path(home, "shared/config.json").exists():
        bootstrap(home)
    with locked(safe_path(home, "runtime/shared.lock")):
        manifest, _, _ = _release(home, release)
        _verify_release_bindings(home, manifest)
        config = _config(read_json(safe_path(home, "shared/config.json")))
        previous = config["current_release"]
        if previous == release:
            return {"status": "unchanged", "release": release}
        config.update(current_release=release, bindings=_bindings(manifest["bindings"]))
        atomic_json(safe_path(home, "shared/config.json"), config)
        notice = _notify(home, {"kind": "rollback", "from_release": previous, "release": release},
                         {"kind": "rollback", "from_release": previous, "release": release})
    return {"status": "rolled_back", "from_release": previous, "release": release, "notification": notice}


def pin_project(root, release, reason):
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
        raise HouseError("Project pin requires a concrete reason (1–2000 characters)")
    validate_id(release)
    root = find_house(root)
    if root is None:
        raise HouseError("Project is not an initialized House workspace")
    initial = _lock(root)
    home = _root(Path(initial["home"]))
    guard = "runtime/project-" + _hash(str(root).encode("utf-8")) + ".lock"
    with locked(safe_path(home, "runtime/shared.lock")), locked(safe_path(home, guard)):
        manifest, _, _ = _release(home, release)
        _verify_release_bindings(home, manifest)
        current = _lock(root)
        if current["home"] != str(home):
            raise HouseError("Project home changed during pin")
        previous = current["release"]
        if previous == release:
            return {"status": "unchanged", "root": str(root), "release": release}
        history = current.get("history", [])
        if not isinstance(history, list):
            raise HouseError("Project pin history must be a list")
        history.append({"at": _now(), "from_release": previous, "release": release, "reason": reason.strip()})
        current.update(release=release, bindings=_bindings(manifest["bindings"]), history=history)
        atomic_json(safe_path(root, "hive.lock.json"), current)
    return {"status": "pinned", "root": str(root), "from_release": previous, "release": release, "reason": reason.strip()}


class _Trending(HTMLParser):
    def __init__(self):
        super().__init__()
        self.heading = False
        self.repositories = []

    def handle_starttag(self, tag, attrs):
        if tag == "h2":
            self.heading = True
        if tag == "a" and self.heading:
            href = dict(attrs).get("href", "")
            try:
                repository = _repository(href.lstrip("/"))
            except HouseError:
                return
            if repository not in self.repositories:
                self.repositories.append(repository)

    def handle_endtag(self, tag):
        if tag == "h2":
            self.heading = False


def scan(home, rooms=None, limit=5):
    home = _root(home)
    chosen = list(ROOMS) if rooms is None else list(dict.fromkeys(rooms))
    if not chosen or not isinstance(limit, int) or not 1 <= limit <= 20:
        raise HouseError("Scan requires rooms and a limit from 1 to 20")
    for room in chosen:
        _room(room)
    config = bootstrap(home)
    release, _, _ = _release(home, config["current_release"])
    report = {"schema_version": 1, "scan_id": "scan-" + uuid.uuid4().hex, "created_at": _now(),
              "release": release["version"], "rooms": chosen, "candidates": [], "tracked": [], "errors": []}
    for room in chosen:
        try:
            data = _request("https://api.github.com/search/repositories?" + urlencode({"q": QUERIES[room], "sort": "updated", "per_page": limit}))
            items = data.get("items")
            if not isinstance(items, list):
                raise HouseError("GitHub repository search returned no valid items list")
            for item in items[:limit]:
                if not isinstance(item, dict):
                    raise HouseError("GitHub repository search returned an invalid item")
                repository = _repository(item.get("full_name"))
                report["candidates"].append({"source": "github_search", "room": room, "repository": repository,
                    "url": "https://github.com/" + repository, "description": str(item.get("description") or "")[:1000],
                    "stars": item.get("stargazers_count"), "provisional": True, "skill_path": None,
                    "note": "Repository discovery only; SKILL.md, licensing and suitability are unreviewed."})
        except (HouseError, ValueError, TypeError) as exc:
            report["errors"].append({"source": "github_search", "room": room, "error": str(exc)})
    try:
        page = _request("https://github.com/trending?since=weekly", json_response=False)
        parser = _Trending()
        parser.feed(page.decode("utf-8"))
        if not parser.repositories:
            raise HouseError("Trending yielded no repository links; page format may have changed")
        for repository in parser.repositories[:limit]:
            report["candidates"].append({"source": "github_trending", "room": None, "repository": repository,
                "url": "https://github.com/" + repository, "provisional": True, "skill_path": None,
                "note": "Weekly popularity discovery only; room relevance and skill content are unreviewed."})
    except (HouseError, ValueError, UnicodeError) as exc:
        report["errors"].append({"source": "github_trending", "error": str(exc)})
    for room in chosen:
        for binding in release["bindings"][room]:
            tracked = {"room": room, "name": binding.get("name"), "repository": binding.get("repository"),
                       "installed_commit": binding.get("commit"), "source_path": binding.get("source_path")}
            try:
                verify_binding(home, binding)
                ref = binding.get("ref", "HEAD")
                if re.fullmatch(r"[0-9a-fA-F]{40}", ref):
                    ref = "HEAD"
                latest = _commit(binding["repository"], ref)
                tracked.update(ref=ref, upstream_commit=latest,
                               status="unchanged" if latest == binding["commit"] else "changed")
            except (HouseError, ValueError, TypeError) as exc:
                tracked.update(status="error", error=str(exc))
                report["errors"].append({"source": "tracked_upstream", "room": room,
                                          "repository": binding.get("repository"), "error": str(exc)})
            report["tracked"].append(tracked)
    report["status"] = "partial" if report["errors"] else "complete"
    report["path"] = str(safe_path(home, "workshop/scans/" + report["scan_id"] + ".json"))
    with locked(safe_path(home, "runtime/shared.lock")):
        report["notifications"] = []
        for candidate in report["candidates"]:
            if candidate["source"] == "github_search":
                key = {"kind": "candidate_discovered", "room": candidate["room"],
                       "repository": candidate["repository"]}
                notice = _notify(home, dict(key, provisional=True, url=candidate["url"],
                    message="Provisional room-search candidate; content, relevance and quality require review. Not adopted."), key)
                if notice:
                    report["notifications"].append(notice)
        for tracked in report["tracked"]:
            if tracked["status"] == "changed":
                notice = _notify(home, dict(tracked, kind="upstream_changed"), dict(tracked, kind="upstream_changed"))
                if notice:
                    report["notifications"].append(notice)
        atomic_json(Path(report["path"]), report)
        atomic_json(safe_path(home, "workshop/candidates/" + report["scan_id"] + ".json"),
                    {"scan_id": report["scan_id"], "candidates": report["candidates"], "errors": report["errors"]})
    return report
