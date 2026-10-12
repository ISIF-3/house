"""Keep House records light: diagnose, version with git, move raw data out and migrate releases.

Raw data is moved, never deleted: a cross-device move removes the source only after the
copy matches the source manifest. Research text is changed only where a line is House-owned.
"""

from datetime import datetime, timezone
import errno
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import time
import uuid

from . import __version__
from .project import ROOMS, _lock, _release, _root, base_version, bootstrap, document_status
from .storage import HouseError, atomic_json, locked, read_json, safe_path
from .templates import HANDOFF_LIMIT, LEGACY_PLACEHOLDERS, MIGRATED_STATUS, STATUS_MARK, status_line

MB = 1024 * 1024
RECENT_SECONDS = 30 * 60
INDEX = "house-data.json"
DATA_DIR = "house-data"
INSTRUCTION_FILES = ("HOUSE.md", "AGENTS.md", ".gitignore")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _guard(home, root):
    return safe_path(home, "runtime/project-" + hashlib.sha256(str(root).encode("utf-8")).hexdigest() + ".lock")


def _size(value):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return ("{:.0f}{}" if unit == "B" else "{:.1f}{}").format(value, unit)
        value /= 1024.0


# ---------------------------------------------------------------- size scan

def _scan(root, file_threshold):
    """Walk house/ without following links; per-directory own bytes, files and Markdown presence."""
    own, links, large_files = {}, [], []
    for dirpath, dirnames, filenames in os.walk(str(root)):
        rel = os.path.relpath(dirpath, str(root))
        rel = "" if rel == "." else rel.replace(os.sep, "/")
        if rel == "":
            dirnames[:] = [d for d in dirnames if d != ".git"]
        for name in list(dirnames):
            if os.path.islink(os.path.join(dirpath, name)):
                links.append((rel + "/" if rel else "") + name)
        size = count = 0
        has_md = False
        for name in filenames:
            full = os.path.join(dirpath, name)
            info = os.lstat(full)
            relative = (rel + "/" if rel else "") + name
            if stat.S_ISLNK(info.st_mode):
                links.append(relative)
                continue
            size += info.st_size
            count += 1
            has_md = has_md or name.endswith(".md")
            if info.st_size >= file_threshold:
                large_files.append({"path": relative, "bytes": info.st_size})
        own[rel] = [size, count, has_md]
    total = {key: list(value) for key, value in own.items()}
    for rel in sorted(own, key=lambda r: r.count("/") if r else -1, reverse=True):
        if rel:
            parent = total[os.path.dirname(rel)]
            child = total[rel]
            parent[0] += child[0]
            parent[1] += child[1]
            parent[2] = parent[2] or child[2]
    return total, links, large_files


def _offload_candidates(total, large_files, threshold):
    """Largest record-free directories below a record-bearing level, then large files outside them.

    Large files are included because each one would also block `house track`.
    """
    found = []
    for rel, (size, count, has_md) in total.items():
        if not rel or rel in ROOMS or size < threshold or has_md:
            continue
        parent = os.path.dirname(rel)
        if parent in ROOMS or parent == "" or total[parent][2]:
            found.append({"path": rel, "kind": "directory", "bytes": size, "files": count})
    covered = tuple(item["path"] + "/" for item in found)
    found += [{"path": item["path"], "kind": "file", "bytes": item["bytes"], "files": 1}
              for item in large_files if not item["path"].startswith(covered) and not item["path"].endswith(".md")]
    return sorted(found, key=lambda item: -item["bytes"])[:30]


# ---------------------------------------------------------------- git

def _git(cwd, *args, check=True):
    try:
        result = subprocess.run(["git"] + list(args), cwd=str(cwd), capture_output=True, text=True)
    except OSError as exc:
        raise HouseError("git을 실행할 수 없습니다: {}".format(exc)) from exc
    if check and result.returncode != 0:
        raise HouseError("git {} 실패: {}".format(args[0], (result.stderr or result.stdout).strip()))
    return result


def _paths(output):
    return [item for item in output.split("\0") if item]


def _pending(root):
    """Files that `git add -A -- .` would stage below house/, with their sizes."""
    names = set(_paths(_git(root, "ls-files", "-z", "--others", "--exclude-standard", "--", ".").stdout))
    names.update(_paths(_git(root, "ls-files", "-z", "--modified", "--", ".").stdout))
    pending = []
    for name in sorted(names):
        path = Path(root) / name
        try:
            pending.append((name, os.lstat(str(path)).st_size))
        except FileNotFoundError:
            pending.append((name, 0))
    return pending


def git_state(root):
    if shutil.which("git") is None:
        return {"available": False, "mode": "unavailable"}
    top = _git(root, "rev-parse", "--show-toplevel", check=False)
    if top.returncode != 0:
        return {"available": True, "mode": "none"}
    toplevel = Path(top.stdout.strip()).resolve()
    mode = "house" if toplevel == Path(root).resolve() else "parent"
    pending = _pending(root)
    state = {"available": True, "mode": mode, "toplevel": str(toplevel),
             "tracked_files": len(_paths(_git(root, "ls-files", "-z", "--", ".").stdout)),
             "pending_files": len(pending), "pending_bytes": sum(size for _, size in pending),
             "ignored_rooms": [room for room in ROOMS
                               if _git(root, "check-ignore", "-q", room + "/", check=False).returncode == 0]}
    if mode == "parent":
        relative = os.path.relpath(str(Path(root).resolve()), str(toplevel))
        state["house_ignored"] = _git(toplevel, "check-ignore", "-q", relative + "/", check=False).returncode == 0
    return state


def commit(root, message, init=False, max_file=10 * MB, max_total=200 * MB, max_files=5000, dry_run=False):
    """Commit only house/ records; refuse raw-data-sized additions instead of committing them."""
    root = Path(root).resolve()
    if not isinstance(message, str) or not message.strip():
        raise HouseError("커밋 메시지가 필요합니다.")
    state = git_state(root)
    if state["mode"] == "unavailable":
        raise HouseError("git이 설치되어 있지 않습니다.")
    initialized = False
    if state["mode"] == "none":
        if not init:
            raise HouseError("house/가 git 저장소에 없습니다. 먼저 house track을 실행하세요.")
        if dry_run:
            return {"status": "would_init", "root": str(root), "mode": "house"}
        _git(root, "init", "-q")
        initialized = True
        state = git_state(root)
    if state.get("house_ignored"):
        raise HouseError("상위 저장소의 ignore 규칙이 house/ 전체를 제외합니다: " + state["toplevel"])
    pending = _pending(root)
    total = sum(size for _, size in pending)
    large = [(name, size) for name, size in pending if size > max_file]
    if large or total > max_total or len(pending) > max_files:
        lines = ["git에 추가할 기록이 원본 데이터 규모입니다. 커밋하지 않았습니다.",
                 "추가 예정: {}개, {} (기준: 파일당 {}, 합계 {}, {}개)".format(
                     len(pending), _size(total), _size(max_file), _size(max_total), max_files)]
        lines += ["  {}  {}".format(_size(size), name) for name, size in sorted(large, key=lambda x: -x[1])[:15]]
        lines.append("house check로 옮길 폴더를 확인하고 house offload <경로>로 house/ 밖에 둔 뒤 다시 실행하세요.")
        if initialized:
            lines.append("빈 git 저장소는 만들어 두었습니다: " + str(root / ".git"))
        raise HouseError("\n".join(lines))
    result = {"root": str(root), "mode": state["mode"], "initialized": initialized,
              "files": len(pending), "bytes": total}
    if dry_run:
        return dict(result, status="would_commit", paths=[name for name, _ in pending][:200])
    if not pending:
        return dict(result, status="unchanged")
    _git(root, "add", "-A", "--", ".")
    # A pathspec commit leaves the user's other staged changes in a parent repository untouched.
    _git(root, "commit", "-q", "-m", message.strip(), "--", ".")
    return dict(result, status="committed", commit=_git(root, "rev-parse", "--short", "HEAD").stdout.strip())


# ---------------------------------------------------------------- offload

def _manifest(path, content_hash=False):
    path = Path(path)
    if not path.is_dir() or path.is_symlink():
        return [_entry(path, ".", content_hash)]
    entries = []
    for dirpath, dirnames, filenames in os.walk(str(path)):
        dirnames.sort()
        rel = os.path.relpath(dirpath, str(path))
        for name in sorted(filenames) + [d for d in dirnames if os.path.islink(os.path.join(dirpath, d))]:
            relative = name if rel == "." else (rel.replace(os.sep, "/") + "/" + name)
            entries.append(_entry(Path(dirpath) / name, relative, content_hash))
    return entries


def _entry(path, relative, content_hash):
    info = os.lstat(str(path))
    if stat.S_ISLNK(info.st_mode):
        return [relative, "link", os.readlink(str(path))]
    entry = [relative, "file", info.st_size]
    if content_hash:
        digest = hashlib.sha256()
        with open(str(path), "rb") as stream:
            for block in iter(lambda: stream.read(MB), b""):
                digest.update(block)
        entry.append(digest.hexdigest())
    return entry


def _digest(entries):
    shape = [entry[:3] for entry in entries]
    return hashlib.sha256(json.dumps(shape, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def _house_relative(root, target, cwd):
    given = Path(target).expanduser()
    for candidate in ([given] if given.is_absolute() else [Path(cwd) / given, root / given]):
        candidate = Path(os.path.realpath(str(candidate.parent))) / candidate.name
        try:
            relative = candidate.relative_to(root).as_posix()
        except ValueError:
            continue
        if candidate.exists() or candidate.is_symlink():
            return relative
    raise HouseError("house/ 안의 기존 경로가 아닙니다: {}".format(target))


def _read_index(root):
    path = safe_path(root, INDEX)
    if not path.exists():
        return {"schema_version": 1, "entries": []}
    index = read_json(path)
    if index.get("schema_version") != 1 or not isinstance(index.get("entries"), list):
        raise HouseError("지원하지 않는 원본 데이터 목록 형식입니다: {}".format(path))
    return index


def _newest_change(path):
    newest = os.lstat(str(path)).st_mtime
    if Path(path).is_dir() and not Path(path).is_symlink():
        for dirpath, _, filenames in os.walk(str(path)):
            newest = max([newest, os.lstat(dirpath).st_mtime] +
                         [os.lstat(os.path.join(dirpath, name)).st_mtime for name in filenames])
    return newest


def offload(root, target, to=None, link=True, content_hash=False, allow_records=False, dry_run=False, cwd=None,
            allow_recent=False):
    """Move a raw-data path out of house/, leaving a relative link and a size/file manifest."""
    root = Path(root).resolve()
    lock = _lock(root)
    relative = _house_relative(root, target, cwd or Path.cwd())
    parts = relative.split("/")
    if len(parts) < 2 or parts[0] not in ROOMS:
        raise HouseError("방 아래의 하위 경로만 옮길 수 있습니다: {}".format(relative))
    if (root / relative).is_symlink():
        raise HouseError("이미 house/ 밖으로 연결된 경로입니다: {}".format(relative))
    source = safe_path(root, relative)
    if not (source.is_dir() or source.is_file()):
        raise HouseError("옮길 파일이나 폴더가 아닙니다: {}".format(source))
    # Resolve once so the relative link and the recorded location describe the same path.
    data_root = Path(os.path.abspath(str(Path(to).expanduser() if to else root.parent / DATA_DIR))).resolve()
    if data_root == root or root in data_root.parents:
        raise HouseError("원본 데이터 위치는 house/ 밖이어야 합니다: {}".format(data_root))
    destination = data_root / relative
    if destination.exists() or destination.is_symlink():
        raise HouseError("대상 경로가 이미 있습니다. 기존 데이터를 덮어쓰지 않습니다: {}".format(destination))
    if source.is_dir():
        records = sorted(str(p.relative_to(root)) for p in source.rglob("*.md") if not p.is_symlink())
        if records and not allow_records:
            raise HouseError("기록 문서(.md)가 들어 있어 옮기지 않았습니다. 원본만 든 하위 폴더를 지정하거나 "
                             "--allow-records를 사용하세요:\n  " + "\n  ".join(records[:5]))
    age = time.time() - _newest_change(source)
    if age < RECENT_SECONDS and not allow_recent:
        raise HouseError("최근 {}분 안에 바뀐 파일이 있어 실행 중인 계산의 출력일 수 있습니다. 옮기지 않았습니다: {}\n"
                         "계산이 끝났는지 확인한 뒤 --allow-recent로 다시 실행하세요.".format(int(age // 60) + 1, relative))
    entries = _manifest(source, content_hash and not dry_run)
    files = sum(1 for entry in entries if entry[1] == "file")
    size = sum(entry[2] for entry in entries if entry[1] == "file")
    plan = {"root": str(root), "path": relative, "destination": str(destination), "link": link,
            "kind": "directory" if source.is_dir() else "file", "files": files, "bytes": size}
    if dry_run:
        return dict(plan, status="planned")
    home = _root(Path(lock["home"]))
    with locked(_guard(home, root)):
        data_root.mkdir(parents=True, exist_ok=True)
        ignore = data_root / ".gitignore"
        if not ignore.exists() and not ignore.is_symlink():
            # The data directory excludes itself from any enclosing repository.
            ignore.write_text("# House 원본 데이터: git에서 제외한다.\n*\n", encoding="utf-8")
        destination.parent.mkdir(parents=True, exist_ok=True)
        # rename() would silently replace an empty directory or a file created since the first check.
        if destination.exists() or destination.is_symlink():
            raise HouseError("대상 경로가 이미 있습니다. 기존 데이터를 덮어쓰지 않습니다: {}".format(destination))
        try:
            os.rename(str(source), str(destination))
        except OSError as exc:
            if exc.errno != errno.EXDEV:
                raise HouseError("옮기지 못했습니다: {}".format(exc)) from exc
            _copy_verified(source, destination, entries, content_hash)
            if source.is_dir():
                shutil.rmtree(str(source))
            else:
                source.unlink()
        if link:
            os.symlink(os.path.relpath(str(destination), str(source.parent)), str(source))
        manifest_path = destination.parent / (destination.name + ".house-manifest.json")
        atomic_json(manifest_path, {"schema_version": 1, "path": relative, "created_at": _now(),
                                    "content_hashed": content_hash, "entries": entries})
        record = {"path": relative, "location": os.path.relpath(str(destination), str(root)),
                  "absolute": str(destination), "link": link, "kind": plan["kind"], "files": files,
                  "bytes": size, "digest": _digest(entries), "content_hashed": content_hash,
                  "manifest": str(manifest_path), "moved_at": _now()}
        if content_hash:
            record["content_digest"] = hashlib.sha256(json.dumps(entries, separators=(",", ":")).encode("utf-8")).hexdigest()
        index = _read_index(root)
        index["entries"] = [e for e in index["entries"] if e.get("path") != relative] + [record]
        atomic_json(safe_path(root, INDEX), index)
    return dict(plan, status="moved", record=record)


def offload_suggested(root, to=None, link=True, content_hash=False, dry_run=False, dir_threshold=100 * MB,
                      file_threshold=10 * MB):
    """Offload every current check candidate; recently changed or refused paths are reported, not moved."""
    root = Path(root).resolve()
    moved, skipped = [], []
    for candidate in check(root, dir_threshold, file_threshold)["offload_candidates"]:
        try:
            result = offload(root, candidate["path"], to=to, link=link, content_hash=content_hash,
                             dry_run=dry_run, cwd=root)
        except HouseError as exc:
            skipped.append({"path": candidate["path"], "bytes": candidate["bytes"], "reason": str(exc).splitlines()[0]})
            continue
        moved.append({"path": candidate["path"], "bytes": candidate["bytes"], "status": result["status"]})
    return {"root": str(root), "status": "planned" if dry_run else "moved", "moved": moved, "skipped": skipped,
            "bytes": sum(item["bytes"] for item in moved),
            "note": "후보는 한 번에 최대 30개입니다. 남은 후보는 house check로 다시 확인하세요."}


def _copy_verified(source, destination, entries, content_hash):
    temporary = destination.parent / (".house-offload-" + uuid.uuid4().hex[:8] + "-" + destination.name)
    try:
        if source.is_dir():
            shutil.copytree(str(source), str(temporary), symlinks=True)
        else:
            shutil.copy2(str(source), str(temporary))
        if _manifest(temporary, content_hash) != entries:
            raise HouseError("복사본이 원본 목록과 다릅니다. 원본을 그대로 두었습니다.")
        os.rename(str(temporary), str(destination))
    except BaseException:
        if temporary.is_dir() and not temporary.is_symlink():
            shutil.rmtree(str(temporary), ignore_errors=True)
        elif temporary.exists():
            temporary.unlink()
        raise


def verify_offloaded(root):
    problems = []
    entries = _read_index(root)["entries"]
    for entry in entries:
        path = entry.get("path", "?")
        location = (Path(root) / entry["location"]) if entry.get("location") else Path(entry.get("absolute", ""))
        location = Path(os.path.abspath(str(location)))
        if not location.exists():
            problems.append({"path": path, "problem": "원본 위치가 없음", "location": str(location)})
            continue
        link = Path(root) / path
        if entry.get("link") and not (link.is_symlink() and Path(os.path.realpath(str(link))) == location.resolve()):
            problems.append({"path": path, "problem": "house/ 안의 링크가 없거나 다른 곳을 가리킴"})
        if _digest(_manifest(location)) != entry.get("digest"):
            problems.append({"path": path, "problem": "파일 목록·크기가 옮길 때와 다름"})
    return {"entries": len(entries), "problems": problems}


# ---------------------------------------------------------------- diagnosis

def _template_notes(root, templates):
    notes = []
    for relative, template in sorted(templates.items()):
        if status_line(template) is None:
            continue
        path = safe_path(root, relative)
        if not path.is_file():
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "템플릿" in line and STATUS_MARK not in line:
                notes.append({"path": relative, "line": number, "text": line.strip()[:160]})
    return notes


def check(root, dir_threshold=100 * MB, file_threshold=10 * MB):
    root = Path(root).resolve()
    lock = _lock(root)
    _, _, templates = _release(_root(Path(lock["home"])), lock["release"])
    total, links, large_files = _scan(root, file_threshold)
    candidates = _offload_candidates(total, large_files, dir_threshold)
    documents = document_status(root, templates)
    stale = sorted(rel for rel, info in documents.items() if info.get("stale"))
    handoff = safe_path(root, "main/handoff.md")
    handoff_chars = len(handoff.read_text(encoding="utf-8")) if handoff.is_file() else 0
    git = git_state(root)
    offloaded = verify_offloaded(root)
    notes = _template_notes(root, templates)
    advice = []
    if lock["release"] != __version__ and (base_version(lock["release"]) or (0,)) < base_version(__version__):
        advice.append("이 프로젝트는 House {}에 고정되어 있습니다. house migrate로 {} 구조를 적용할 수 있습니다.".format(
            lock["release"], __version__))
    if candidates:
        advice.append("원본 데이터로 보이는 폴더·파일 {}개({}): house offload <경로>로 house/ 밖에 옮기세요.".format(
            len(candidates), _size(sum(c["bytes"] for c in candidates))))
    if git["mode"] == "none":
        advice.append("house/가 git으로 추적되지 않습니다. 원본을 옮긴 뒤 house track을 실행하세요.")
    elif git["mode"] in ("house", "parent"):
        if git.get("house_ignored") or git.get("ignored_rooms"):
            advice.append("상위 ignore 규칙이 house/ 일부를 가립니다: {}".format(
                ", ".join(git.get("ignored_rooms") or ["house/"])))
        if git["tracked_files"] == 0:
            advice.append("git 저장소 안에 있지만 house/ 기록이 아직 커밋되지 않았습니다. house track을 실행하세요.")
        elif git["pending_files"]:
            advice.append("커밋되지 않은 기록 {}개: house save -m '<요약>'.".format(git["pending_files"]))
    if stale:
        advice.append("내용이 생겼지만 상태 줄이 초기 문구인 문서: " + ", ".join(stale))
    if notes:
        advice.append("템플릿 문구를 해명하는 줄 {}개는 상태 줄을 갱신한 뒤 정리할 수 있습니다.".format(len(notes)))
    if handoff_chars > HANDOFF_LIMIT:
        advice.append("main/handoff.md가 {}자입니다. 현재 상태 스냅샷으로 다시 써서 {}자 이내로 줄이세요.".format(
            handoff_chars, HANDOFF_LIMIT))
    if offloaded["problems"]:
        advice.append("옮긴 원본 데이터에 문제가 있습니다: {}건".format(len(offloaded["problems"])))
    return {"root": str(root), "release": lock["release"], "engine": __version__,
            "size": {"bytes": total[""][0], "files": total[""][1], "links": len(links),
                     "rooms": {room: total.get(room, [0])[0] for room in ROOMS}},
            "offload_candidates": candidates,
            "large_files": sorted(large_files, key=lambda item: -item["bytes"])[:30],
            "git": git, "stale_status": stale, "template_notes": notes,
            "handoff": {"chars": handoff_chars, "limit": HANDOFF_LIMIT, "over": handoff_chars > HANDOFF_LIMIT},
            "offloaded": offloaded, "advice": advice}


def render_check(report):
    lines = ["House 점검 · {} (고정 {}, 엔진 {})".format(report["root"], report["release"], report["engine"]),
             "크기: {} · 파일 {}개 · 링크 {}개".format(_size(report["size"]["bytes"]), report["size"]["files"],
                                                report["size"]["links"]),
             "  " + "  ".join("{} {}".format(room, _size(size)) for room, size in report["size"]["rooms"].items())]
    git = report["git"]
    lines.append("git: " + {"unavailable": "설치되지 않음", "none": "추적 안 함",
                            "house": "house/ 자체 저장소", "parent": "상위 저장소 " + git.get("toplevel", "")}[git["mode"]]
                 + ("" if git["mode"] in ("none", "unavailable") else
                    " · 추적 {}개 · 미커밋 {}개".format(git["tracked_files"], git["pending_files"])))
    if report["offload_candidates"]:
        lines.append("옮길 후보 (기록 문서가 없는 큰 폴더와 큰 파일):")
        lines += ["  {:>8}  {}".format(_size(c["bytes"]), c["path"]) for c in report["offload_candidates"][:15]]
    if report["template_notes"]:
        lines.append("템플릿 해명 줄:")
        lines += ["  {}:{}  {}".format(n["path"], n["line"], n["text"][:80]) for n in report["template_notes"][:15]]
    lines.append("권장 작업:" if report["advice"] else "권장 작업: 없음")
    lines += ["  - " + item for item in report["advice"]]
    return "\n".join(lines)


# ---------------------------------------------------------------- migration

def _fix_status(current, old_template, new_template):
    """Replace 0.1.0 status/placeholder lines left in a written record with one House status line."""
    new_line = status_line(new_template)
    if old_template is None or new_line is None or STATUS_MARK not in new_line:
        return None
    old_line = status_line(old_template)
    legacy = {line for line in old_template.splitlines() if line == old_line or line in LEGACY_PLACEHOLDERS}
    lines = current.splitlines(True)
    if not legacy or any(STATUS_MARK in line for line in lines):
        return None
    output, done = [], False
    for line in lines:
        if line.rstrip("\n") in legacy:
            if not done:
                output.append(MIGRATED_STATUS + ("\n" if line.endswith("\n") else ""))
                done = True
            continue
        output.append(line)
    return "".join(output) if done else None


def migrate(root, release=None, reason=None, dry_run=False):
    root = Path(root).resolve()
    lock = _lock(root)
    home = _root(Path(lock["home"]))
    bootstrap(home)  # shared state only: publishes the engine release a migration reads
    target = release or __version__
    origin = lock["release"]
    old_version, new_version = base_version(origin), base_version(target)
    if old_version and new_version and new_version < old_version:
        raise HouseError("이전 버전으로는 migrate하지 않습니다. 필요하면 house pin을 사용하세요.")
    _, old_rooms, old_templates = _release(home, origin)
    _, new_rooms, new_templates = _release(home, target)
    owned = {room + "/ROOM.md": (old_rooms.get(room), new_rooms[room]) for room in ROOMS}
    owned.update({rel: (old_templates.get(rel), text) for rel, text in new_templates.items()})
    changes, kept = [], []
    for relative in sorted(owned):
        old, new = owned[relative]
        path = safe_path(root, relative)
        if not path.exists():
            changes.append({"path": relative, "action": "create", "text": new})
            continue
        if not path.is_file():
            kept.append({"path": relative, "reason": "파일이 아닌 경로가 있어 건드리지 않음"})
            continue
        current = path.read_text(encoding="utf-8")
        if current == new:
            continue
        if old is not None and current == old:
            changes.append({"path": relative, "action": "replace_untouched", "text": new})
            continue
        fixed = _fix_status(current, old, new)
        if fixed is not None:
            changes.append({"path": relative, "action": "status_line", "text": fixed})
        elif relative in INSTRUCTION_FILES or relative.endswith(("/ROOM.md", "/GUIDE.md")):
            kept.append({"path": relative, "reason": "사용자 편집이 있어 보존 (새 지침은 공용 릴리스에서 읽음)"})
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    job = "main/jobs/house-migrate-{}-to-{}-{}".format(origin, target, stamp)
    summary = {"root": str(root), "from_release": origin, "release": target, "job": job,
               "changes": [{"path": c["path"], "action": c["action"]} for c in changes], "kept": kept}
    if dry_run:
        report = check(root)
        summary.update(status="planned", check=report)
        summary["report"] = _report(summary, report, dry_run=True)
        return summary
    if not changes and origin == target:
        return dict(summary, status="unchanged", job=None, report="이미 House {}의 구조입니다.".format(target))
    with locked(_guard(home, root)):
        for change in changes:
            path = safe_path(root, change["path"])
            if path.exists():
                backup = safe_path(root, job + "/previous/" + change["path"])
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(path), str(backup))
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.parent / (".house-migrate-" + uuid.uuid4().hex[:8])
            temporary.write_text(change["text"], encoding="utf-8")
            os.replace(str(temporary), str(path))
    if origin != target:
        from .updates import pin_project
        pin_project(root, target, reason or "House {} → {} 구조 개선(상태 줄, handoff 스냅샷, git 기록, 원본 데이터 분리) 적용".format(origin, target))
    report = check(root)
    summary.update(status="migrated", check=report)
    summary["report"] = _report(summary, report, dry_run=False)
    job_root = safe_path(root, job)
    job_root.mkdir(parents=True, exist_ok=True)
    (job_root / "REPORT.md").write_text(summary["report"], encoding="utf-8")
    return summary


def _report(summary, report, dry_run):
    def section(action, title):
        paths = [c["path"] for c in summary["changes"] if c["action"] == action]
        return ["## " + title, ""] + (["- " + p for p in paths] if paths else ["- 없음"]) + [""]

    head = "# House 마이그레이션 {} → {}{}".format(summary["from_release"], summary["release"], " (계획)" if dry_run else "")
    lines = [head, "",
             "{}: {}".format("점검 시각" if dry_run else "적용 시각", _now()),
             "프로젝트: {}".format(summary["root"]), ""]
    if not dry_run:
        lines += ["바꾼 파일의 원본은 이 폴더의 previous/에 있다. 고정 버전 변경과 이유는 hive.lock.json의 history에 있다.", ""]
    lines += section("create", "새로 만든 파일")
    lines += section("replace_untouched", "손대지 않은 House 파일을 새 버전으로 교체")
    lines += section("status_line", "초기 상태 문구를 House 상태 줄로 교체")
    lines += ["## 보존한 파일", ""] + (["- {} — {}".format(k["path"], k["reason"]) for k in summary["kept"]] or ["- 없음"]) + [""]
    todo, chat = [], []
    if report["template_notes"]:
        todo.append("템플릿 문구를 해명하던 줄을 정리한다 (상태 줄이 대신한다):\n" + "\n".join(
            "   - {}:{} — {}".format(n["path"], n["line"], n["text"][:100]) for n in report["template_notes"][:20]))
    if report["stale_status"] or any(c["action"] == "status_line" for c in summary["changes"]):
        todo.append("`<!-- house:status -->` 줄을 각 문서의 실제 현재 상태로 바꾼다.")
        chat.append("상태 줄")
    if report["handoff"]["over"]:
        todo.append("main/handoff.md({}자)를 현재 상태 스냅샷으로 다시 쓴다. 기준 {}자. 지난 내용은 work.md와 previous/에 남아 있다.".format(
            report["handoff"]["chars"], report["handoff"]["limit"]))
        chat.append("handoff 다시 쓰기")
    if report["offload_candidates"]:
        todo.append("원본 데이터 폴더를 house/ 밖으로 옮긴다. 실행 중인 계산의 출력 폴더는 제외한다:\n" + "\n".join(
            "   - house offload {}  ({}, {}개)".format(c["path"], _size(c["bytes"]), c["files"])
            for c in report["offload_candidates"][:20]))
    git = report["git"]
    if git["mode"] == "none":
        todo.append("원본을 옮긴 뒤 `house track`으로 house/ 기록의 git 추적을 시작한다.")
    elif git.get("tracked_files") == 0:
        todo.append("원본을 옮긴 뒤 `house track`으로 상위 저장소({})에 house/ 기록을 커밋한다.".format(git.get("toplevel")))
    lines += ["## 남은 작업", ""] + (["{}. {}".format(i, t) for i, t in enumerate(todo, 1)] or ["- 없음"]) + [""]
    if report["template_notes"]:
        chat.insert(0, "템플릿 해명 줄 정리")
    commands = report["offload_candidates"] or git["mode"] == "none" or git.get("tracked_files") == 0
    if chat or commands:
        request = "$house main에서 {}/REPORT.md의 남은 작업을 진행해줘.".format(summary["job"])
        if chat:
            request += " 먼저 " + ", ".join(chat) + " 작업을 진행해줘."
        if commands:
            request += " 원본 데이터 이동(house offload)과 git 추적은 대상 목록을 보여주고 내 확인을 받은 뒤 해줘."
        lines += ["## Codex에 줄 요청 예시", "", "```text", request, "```", ""]
    return "\n".join(lines)
