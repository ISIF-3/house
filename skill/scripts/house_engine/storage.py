"""Small, conservative filesystem primitives shared by House commands."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import tempfile


class HouseError(Exception):
    """An actionable failure that a command may show without a traceback."""


def validate_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value):
        raise HouseError("Invalid identifier: {!r}".format(value))
    return value


def safe_path(root: Path, relative: str) -> Path:
    """Return a path inside root, refusing traversal and existing symlinks.

    The supplied root is a trust boundary; its OS ancestors may contain normal
    platform aliases such as macOS /var. No symlink at or below root is followed.
    """
    root = Path(root).expanduser().absolute()
    if root.is_symlink():
        raise HouseError("Symlink root is not allowed: {}".format(root))
    if not isinstance(relative, str) or not relative or "\\" in relative or "\x00" in relative:
        raise HouseError("Unsafe relative path: {!r}".format(relative))
    fragment = Path(relative)
    if fragment.is_absolute() or any(p in ("..", ".") for p in relative.split("/")):
        raise HouseError("Path must stay inside its owned root: {!r}".format(relative))
    base = root.resolve()
    candidate = base
    for part in fragment.parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise HouseError("Symlink is not allowed inside owned root: {}".format(candidate))
    try:
        candidate.resolve().relative_to(base)
    except (ValueError, RuntimeError) as exc:
        raise HouseError("Path escapes owned root: {}".format(candidate)) from exc
    return candidate


def atomic_json(path: Path, value) -> None:
    """Replace a JSON document atomically, without following its final symlink."""
    path = Path(path)
    path = safe_path(path.parent, path.name)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=str(path.parent),
                                         prefix=".{}-".format(path.name), delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        safe_path(path.parent, path.name)
        os.replace(str(temporary), str(path))
    except (OSError, ValueError, TypeError) as exc:
        raise HouseError("Could not write JSON {}: {}".format(path, exc)) from exc
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def read_json(path: Path) -> dict:
    path = Path(path)
    path = safe_path(path.parent, path.name)
    try:
        with path.open(encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, ValueError, UnicodeError) as exc:
        raise HouseError("Could not read JSON {}: {}".format(path, exc)) from exc
    if not isinstance(value, dict):
        raise HouseError("JSON document must be an object: {}".format(path))
    return value


@contextmanager
def locked(path: Path):
    """Hold a nonblocking POSIX advisory lock; retain its inode after release."""
    try:
        import fcntl
    except ImportError as exc:
        raise HouseError("House requires POSIX file locks (macOS or Linux)") from exc
    path = Path(path)
    path = safe_path(path.parent, path.name)
    descriptor = None
    acquired = False
    try:
        descriptor = os.open(str(path), os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise HouseError("House resource is in use: {}".format(path)) from exc
        acquired = True
        yield
    except OSError as exc:
        raise HouseError("Could not lock {}: {}".format(path, exc)) from exc
    finally:
        if descriptor is not None:
            if acquired:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
