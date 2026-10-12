"""Create and resume an owned House workspace without replacing user files."""

import copy
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Optional

from . import __version__
from .storage import HouseError, atomic_json, locked, read_json, safe_path, validate_id
from .templates import EMPTY_DIRECTORIES, ROOMS, ROOM_INSTRUCTIONS, project_templates, status_line, MIGRATED_STATUS


def default_home() -> Path:
    default = Path.home() / ('Library/Application Support/House' if sys.platform == 'darwin' else '.local/share/house')
    return Path(os.environ.get("HOUSE_HOME") or default).expanduser()


def _root(path: Path) -> Path:
    path = Path(path).expanduser().absolute()
    if path.is_symlink():
        raise HouseError("Symlink root is not allowed: {}".format(path))
    if path.exists() and not path.is_dir():
        raise HouseError("Expected a directory: {}".format(path))
    return path.resolve()


def _bindings(value):
    if not isinstance(value, dict) or set(value) != set(ROOMS) or any(not isinstance(value[r], list) for r in ROOMS):
        raise HouseError("Bindings must contain a list for each of the five rooms")
    return copy.deepcopy(value)


def _config(value):
    if value.get("schema_version") != 1:
        raise HouseError("Unsupported shared config schema")
    validate_id(value.get("current_release"))
    _bindings(value.get("bindings"))
    return value


def _lock(root: Path) -> dict:
    value = read_json(safe_path(root, "hive.lock.json"))
    if value.get("schema_version") != 1:
        raise HouseError("Unsupported project lock schema: {}".format(root))
    validate_id(value.get("release"))
    home = value.get("home")
    if not isinstance(home, str) or not Path(home).is_absolute():
        raise HouseError("Project lock requires an absolute shared home: {}".format(root))
    _bindings(value.get("bindings"))
    return value


def _preflight(root: Path, directories, files) -> None:
    """Check the entire plan before any directory or file is created."""
    _root(root)
    for relative in directories:
        path = safe_path(root, relative)
        if path.exists() and not path.is_dir():
            raise HouseError("Directory conflicts with existing file: {}".format(path))
    for relative in files:
        path = safe_path(root, relative)
        if path.exists() and not path.is_file():
            raise HouseError("File conflicts with existing directory: {}".format(path))
    for relative in list(directories) + list(files):
        path = safe_path(root, relative)
        for parent in path.parents:
            if parent == root:
                break
            if parent.exists() and not parent.is_dir():
                raise HouseError("Parent conflicts with existing file: {}".format(parent))


def _write_new(root: Path, relative: str, content: str) -> bool:
    path = safe_path(root, relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Exclusive creation also protects user edits made after preflight.
        with path.open("x", encoding="utf-8") as stream:
            stream.write(content)
        return True
    except FileExistsError:
        safe_path(root, relative)
        if not path.is_file():
            raise HouseError("File conflicts with existing directory: {}".format(path))
        return False
    except OSError as exc:
        raise HouseError("Could not create {}: {}".format(path, exc)) from exc


def _json_text(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _release(home: Path, version: str):
    validate_id(version)
    release_root = safe_path(home, "shared/releases/{}".format(version))
    manifest = read_json(safe_path(release_root, "manifest.json"))
    if manifest.get("version") != version or manifest.get("schema_version") != 1:
        raise HouseError("Invalid release manifest: {}".format(release_root))
    _bindings(manifest.get("bindings"))
    room_hashes = manifest.get("room_hashes")
    template_hashes = manifest.get("template_hashes")
    if not isinstance(room_hashes, dict) or set(room_hashes) != set(ROOMS):
        raise HouseError("Release is missing room instruction hashes: {}".format(version))
    if not isinstance(template_hashes, dict) or not template_hashes:
        raise HouseError("Release is missing template hashes: {}".format(version))
    rooms, templates = {}, {}
    for hashes, prefix, destination in ((room_hashes, "rooms", rooms), (template_hashes, "templates", templates)):
        for relative, digest in hashes.items():
            suffix = relative + "/ROOM.md" if prefix == "rooms" else relative
            path = safe_path(release_root, prefix + "/" + suffix)
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                raise HouseError("Could not read immutable release file {}: {}".format(path, exc)) from exc
            if not isinstance(digest, str) or _digest(content) != digest:
                raise HouseError("Immutable release content hash mismatch: {}".format(path))
            destination[relative] = content
    # A release may update text, but must preserve the agreed workspace contract.
    schema_v1_required = {'HOUSE.md', 'AGENTS.md', 'main/target.md', 'main/design.md',
                          'main/work.md', 'literature/reference.md', 'output/summary.md'}
    if not schema_v1_required.issubset(templates):
        raise HouseError("Release is missing required research templates: {}".format(version))
    return manifest, rooms, templates


def base_version(release):
    """Numeric engine version of a release name such as 0.2.0 or 0.1.0-u...; None if unknown."""
    head = str(release).split("-", 1)[0].split(".")
    if len(head) != 3 or not all(part.isdigit() for part in head):
        return None
    return tuple(int(part) for part in head)


def _advance_default(home: Path, config: dict, config_path: Path) -> dict:
    """Once per engine version, move an unbound older default to the engine release.

    A default carrying reviewed skill bindings, or a later explicit rollback, is kept.
    """
    if config.get("engine_release") == __version__:
        return config
    current, _, _ = _release(home, config["current_release"])
    older = base_version(config["current_release"])
    if older is not None and older < base_version(__version__) and not any(current["bindings"].values()):
        manifest, _, templates = _release(home, __version__)
        config.update(current_release=__version__, bindings=_bindings(manifest["bindings"]))
        for relative, text in templates.items():
            # shared/templates is House's informational copy of the default release.
            target = safe_path(home, "shared/templates/" + relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
    config["engine_release"] = __version__
    atomic_json(config_path, config)
    return config


def bootstrap(home: Path) -> dict:
    """Create shared defaults once; return the current shared configuration."""
    home = _root(home)
    templates = project_templates()
    bindings = {room: [] for room in ROOMS}
    initial_config = {"schema_version": 1, "current_release": __version__, "bindings": bindings,
                      "engine_release": __version__}
    manifest = {
        "schema_version": 1, "version": __version__, "bindings": bindings,
        "room_hashes": {room: _digest(text) for room, text in ROOM_INSTRUCTIONS.items()},
        "template_hashes": {relative: _digest(text) for relative, text in templates.items()},
    }
    release_prefix = "shared/releases/{}".format(__version__)
    directories = ["shared/templates", "shared/skills/store", "workshop/scans", "workshop/candidates",
                   "workshop/validation", "workshop/notifications", "runtime"]
    files = {"shared/config.json": _json_text(initial_config),
             "workshop/schedule.json": _json_text({"schema_version": 1, "frequency": "weekly",
                                                   "timezone": "Asia/Seoul", "enabled": False,
                                                   "weekday": None, "time": None})}
    for relative, text in templates.items():
        files["shared/templates/" + relative] = text
        files[release_prefix + "/templates/" + relative] = text
    for room, text in ROOM_INSTRUCTIONS.items():
        files[release_prefix + "/rooms/" + room + "/ROOM.md"] = text
    files[release_prefix + "/manifest.json"] = _json_text(manifest)
    _preflight(home, directories, list(files) + ["runtime/shared.lock"])
    config_path = safe_path(home, "shared/config.json")
    if config_path.exists():
        config = _config(read_json(config_path))
        if config["current_release"] != __version__:
            _release(home, config["current_release"])
    release_path = safe_path(home, release_prefix)
    if release_path.exists():
        if not (release_path / "manifest.json").is_file():
            raise HouseError("Existing unmanaged release directory: {}".format(release_path))
        _release(home, __version__)
    home.mkdir(parents=True, exist_ok=True)
    for relative in directories:
        safe_path(home, relative).mkdir(parents=True, exist_ok=True)
    with locked(safe_path(home, "runtime/shared.lock")):
        # Recheck after taking the advisory lock: another process may have run.
        _preflight(home, directories, list(files))
        if (release_path / "manifest.json").exists():
            _release(home, __version__)
        for relative, text in files.items():
            _write_new(home, relative, text)
        config = _advance_default(home, _config(read_json(config_path)), config_path)
        _release(home, config["current_release"])
        return config


def find_house(start: Path) -> Optional[Path]:
    """Locate a managed ancestor, including when start names a room file."""
    start = Path(start).expanduser().absolute()
    origin = start
    if start.is_file():
        start = start.parent
    for candidate in (start,) + tuple(start.parents):
        marker = candidate / "hive.lock.json"
        if marker.exists() or marker.is_symlink():
            root = _root(candidate)
            if origin != candidate:
                safe_path(candidate, str(origin.relative_to(candidate)))
            _lock(root)
            return root
    return None


def _project_plan(rooms, templates):
    files = dict(templates)
    files.update({room + "/ROOM.md": text for room, text in rooms.items()})
    return list(ROOMS) + list(EMPTY_DIRECTORIES), files


def init_project(project: Path, home: Path, goal: Optional[str] = None) -> dict:
    """Initialize a new owned house/ or repair missing files in its pinned release."""
    project = Path(project).expanduser().absolute()
    root = find_house(project)
    existing_lock = None
    if root is None:
        project = _root(project)
        if not project.is_dir():
            raise HouseError("Project directory does not exist: {}".format(project))
        root = safe_path(project, "house")
        if root.exists():
            if not root.is_dir() or not (root / "hive.lock.json").is_file():
                raise HouseError("Existing unmanaged house path; nothing changed: {}".format(root))
            existing_lock = _lock(root)
    else:
        existing_lock = _lock(root)

    # Preflight project collisions even before creating a shared home.
    if existing_lock is None:
        directories, base_files = _project_plan(ROOM_INSTRUCTIONS, project_templates())
        _preflight(root, directories, list(base_files) + ["hive.lock.json"])
    if existing_lock is not None:
        home = _root(Path(existing_lock["home"]))
        manifest, rooms, templates = _release(home, existing_lock["release"])
        project_lock = existing_lock
    else:
        home = _root(home)
        config = bootstrap(home)
        manifest, rooms, templates = _release(home, config["current_release"])
        project_lock = {"schema_version": 1, "home": str(home), "release": manifest["version"],
                        "bindings": _bindings(manifest["bindings"])}
        if goal:
            # An explicit initial goal is input, never proof of design agreement.
            templates["main/target.md"] = project_templates(goal)["main/target.md"]
    directories, files = _project_plan(rooms, templates)
    _preflight(root, directories, list(files) + ["hive.lock.json"])
    runtime = safe_path(home, "runtime")
    if runtime.exists() and not runtime.is_dir():
        raise HouseError("Runtime path must be a directory: {}".format(runtime))
    runtime.mkdir(parents=True, exist_ok=True)
    guard = "project-{}.lock".format(hashlib.sha256(str(root).encode("utf-8")).hexdigest())
    with locked(safe_path(runtime, guard)):
        if existing_lock is None and root.exists():
            # Avoid replacing a new path created concurrently by another caller.
            if not (root / "hive.lock.json").is_file():
                raise HouseError("House path appeared during initialization: {}".format(root))
            project_lock = _lock(root)
            home = _root(Path(project_lock["home"]))
            manifest, rooms, templates = _release(home, project_lock["release"])
            directories, files = _project_plan(rooms, templates)
        _preflight(root, directories, list(files) + ["hive.lock.json"])
        root.mkdir(parents=True, exist_ok=True)
        created, existing = [], []
        if _write_new(root, "hive.lock.json", _json_text(project_lock)):
            created.append("hive.lock.json")
        else:
            existing.append("hive.lock.json")
        for relative in directories:
            path = safe_path(root, relative)
            if path.exists():
                existing.append(relative + "/")
            else:
                path.mkdir(parents=True)
                created.append(relative + "/")
        for relative, content in files.items():
            (created if _write_new(root, relative, content) else existing).append(relative)
    return {"root": str(root), "created": created, "existing": existing, "release": project_lock["release"]}


def status(root: Path) -> dict:
    """Inspect recorded state without interpreting edits as scientific success."""
    found = find_house(root)
    if found is None:
        child = safe_path(_root(root), "house")
        found = find_house(child) if child.is_dir() else None
    if found is None:
        raise HouseError("No initialized House workspace found: {}".format(root))
    lock = _lock(found)
    _, _, templates = _release(_root(Path(lock["home"])), lock["release"])

    def document_state(relative, initial, edited):
        path = safe_path(found, relative)
        if not path.exists():
            return "missing"
        try:
            return initial if path.read_text(encoding="utf-8") == templates[relative] else edited
        except (OSError, UnicodeError) as exc:
            raise HouseError("Could not read research record {}: {}".format(path, exc)) from exc

    return {
        "root": str(found), "home": lock["home"], "release": lock["release"],
        "bindings": _bindings(lock["bindings"]),
        "rooms": {room: {"path": str(safe_path(found, room)), "exists": safe_path(found, room).is_dir()}
                  for room in ROOMS},
        "design_status": document_state("main/design.md", "unconfirmed", "edited_review_required"),
        "results_status": document_state("output/summary.md", "not_recorded", "recorded_unverified"),
        "documents": document_status(found, templates),
    }


def document_status(root: Path, templates: dict) -> dict:
    """Report each research record's status line; stale means content was written but the line was not."""
    result = {}
    for relative, template in sorted(templates.items()):
        initial = status_line(template)
        if initial is None:
            continue
        path = safe_path(root, relative)
        if not path.is_file():
            result[relative] = {"state": "missing"}
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise HouseError("Could not read research record {}: {}".format(path, exc)) from exc
        state = "template" if text == template else "edited"
        current = status_line(text)
        lines = text.splitlines()
        result[relative] = {"state": state, "status": current,
                            "stale": state == "edited" and (initial in lines or MIGRATED_STATUS in lines)}
    return result
