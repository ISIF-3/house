"""Small, conservative filesystem primitives shared by House commands."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import tempfile
import time
import shlex


class HouseError(Exception):
    """An actionable failure that a command may show without a traceback."""


def windows_shell_command(argv):
    """Quote every argument for Codex's default Windows cmd.exe hook shell.

    CRT quoting alone leaves no-space cmd metacharacters unquoted. Expansion
    characters cannot be safely passed by this representation, so reject them
    rather than expanding an unintended path or command.
    """
    values = []
    for value in argv:
        if not isinstance(value, str) or any(char in value for char in ('"', '%', '!', '\r', '\n', '\0')):
            raise HouseError('Windows hook arguments cannot contain quotes, %, ! or control newlines')
        tail = len(value) - len(value.rstrip('\\'))
        values.append('"' + value + '\\' * tail + '"')
    return ' '.join(values)


def shell_command(argv):
    """Format a POSIX shell command or the default Windows cmd.exe command."""
    return windows_shell_command(argv) if os.name == 'nt' else shlex.join(argv)


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
def locked(path: Path, timeout=0):
    """Hold an OS lock; keep the lock file and optionally wait a bounded time."""
    if not isinstance(timeout, (int, float)) or not 0 <= timeout <= 10:
        raise HouseError("Lock timeout must be between 0 and 10 seconds")
    if os.name == 'nt':
        import msvcrt
        def acquire(descriptor):
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        def release(descriptor):
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        def acquire(descriptor):
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        def release(descriptor):
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    path = Path(path)
    path = safe_path(path.parent, path.name)
    descriptor = None
    acquired = False
    try:
        descriptor = os.open(str(path), os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        if os.name == 'nt' and os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b'\0')
        deadline = time.monotonic() + timeout
        while True:
            try:
                acquire(descriptor)
                break
            except OSError as exc:
                if exc.errno not in (11, 13, 35, 36):
                    raise
                if time.monotonic() >= deadline:
                    raise HouseError("House resource is in use: {}".format(path)) from exc
                time.sleep(min(0.02, max(0, deadline - time.monotonic())))
        acquired = True
        yield
    except OSError as exc:
        raise HouseError("Could not lock {}: {}".format(path, exc)) from exc
    finally:
        if descriptor is not None:
            if acquired:
                release(descriptor)
            os.close(descriptor)
