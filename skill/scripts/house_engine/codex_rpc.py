"""Bounded JSONL transport for one owned Codex app-server process."""

from collections import deque
from concurrent.futures import Future, TimeoutError
import json
import math
import queue
import subprocess
import threading

from . import __version__
from .storage import HouseError

MAX_LINE_BYTES = 2 * 1024 * 1024
STDERR_TAIL_BYTES = 8192
QUEUE_LIMIT = 128


def _request_id(value):
    return type(value) is int or isinstance(value, str)


def _reject_constant(value):
    raise ValueError('Non-finite JSON number')


def _put_terminal(channel, value):
    try:
        channel.put_nowait(value)
    except queue.Full:
        try:
            channel.get_nowait()
        except queue.Empty:
            pass
        channel.put_nowait(value)


class CodexRPC:
    def __init__(self, argv, cwd=None, env=None, on_message=None):
        if (not isinstance(argv, (list, tuple)) or not argv
                or any(not isinstance(arg, str) for arg in argv)):
            raise HouseError('Codex command must be a nonempty argument list')
        self.argv, self.cwd, self.env = list(argv), cwd, env
        self.on_message = on_message
        self.process = None
        self._state_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._emit_lock = threading.Lock()
        self._pending = {}
        self._expired = deque(maxlen=QUEUE_LIMIT)
        self._next_id = 1
        self._failure = None
        self._closed = False
        self._outgoing = queue.Queue(maxsize=QUEUE_LIMIT)
        self._messages = queue.Queue(maxsize=QUEUE_LIMIT)
        self._threads = []
        self._stderr_tail = b''

    def start(self):
        with self._state_lock:
            if self.process is not None or self._closed:
                raise HouseError('Codex client has already started or closed')
            try:
                self.process = subprocess.Popen(self.argv, cwd=self.cwd, env=self.env,
                                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                                stderr=subprocess.PIPE)
            except OSError as exc:
                raise HouseError('Could not start Codex app-server: ' + str(exc.strerror)) from exc
        for name, target in (('messages', self._dispatch), ('writer', self._write),
                             ('stderr', self._read_stderr), ('reader', self._read)):
            worker = threading.Thread(target=target, name='house-codex-' + name, daemon=True)
            self._threads.append(worker)
            worker.start()
        try:
            result = self.request('initialize', {
                'clientInfo': {'name': 'house', 'title': 'House', 'version': __version__},
                'capabilities': {'experimentalApi': True},
            })
            self.notify('initialized')
            return result
        except HouseError:
            self.close()
            raise

    def _check_connected(self):
        if self._failure:
            raise HouseError(self._failure)
        if self.process is None or self._closed:
            raise HouseError('Codex app-server is not running')

    def _send(self, message):
        try:
            data = json.dumps(message, ensure_ascii=False, allow_nan=False).encode('utf-8') + b'\n'
        except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
            raise HouseError('Codex message must contain valid JSON') from exc
        if len(data) > MAX_LINE_BYTES:
            raise HouseError('Codex message exceeds the transport size limit')
        with self._state_lock:
            self._check_connected()
            try:
                self._outgoing.put_nowait(data)
                return
            except queue.Full:
                pass
        self._fail('Codex outgoing message queue overflowed')
        raise HouseError('Codex outgoing message queue overflowed')

    def request(self, method, params=None, timeout=20):
        if (type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0):
            raise HouseError('Codex request timeout must be a positive finite number')
        if not isinstance(method, str) or not method:
            raise HouseError('Codex RPC method must be a nonempty string')
        future = Future()
        with self._state_lock:
            self._check_connected()
            if len(self._pending) >= QUEUE_LIMIT:
                raise HouseError('Too many pending Codex requests')
            request_id = self._next_id
            self._next_id += 1
            self._pending[request_id] = future
        message = {'id': request_id, 'method': method}
        if params is not None:
            message['params'] = params
        try:
            self._send(message)
            return future.result(timeout=timeout)
        except TimeoutError as exc:
            with self._state_lock:
                self._expired.append(request_id)
            raise HouseError('Codex RPC {} timed out'.format(method)) from exc
        finally:
            with self._state_lock:
                self._pending.pop(request_id, None)

    def respond(self, request_id, result=None, error=None):
        if not _request_id(request_id):
            raise HouseError('Invalid native Codex request ID')
        if error is not None:
            if (result is not None or not isinstance(error, dict) or type(error.get('code')) is not int
                    or not isinstance(error.get('message'), str)):
                raise HouseError('Codex error response requires code and message')
            self._send({'id': request_id, 'error': error})
        else:
            if result is not None and not isinstance(result, dict):
                raise HouseError('Codex response result must be an object')
            self._send({'id': request_id, 'result': {} if result is None else result})

    def notify(self, method, params=None):
        if not isinstance(method, str) or not method:
            raise HouseError('Codex notification method must be a nonempty string')
        message = {'method': method}
        if params is not None:
            message['params'] = params
        self._send(message)

    def _write(self):
        try:
            while True:
                data = self._outgoing.get()
                if data is None:
                    return
                with self._state_lock:
                    if self._failure:
                        return
                with self._write_lock:
                    self.process.stdin.write(data)
                    self.process.stdin.flush()
        except (OSError, ValueError):
            self._fail('Codex app-server input closed')

    def _read_stderr(self):
        try:
            while True:
                block = self.process.stderr.read1(4096)
                if not block:
                    return
                # Diagnostic bytes stay bounded and private; never append them to errors.
                self._stderr_tail = (self._stderr_tail + block)[-STDERR_TAIL_BYTES:]
        except (OSError, ValueError):
            return

    def _read(self):
        try:
            while True:
                line = self.process.stdout.readline(MAX_LINE_BYTES + 1)
                if not line:
                    try:
                        code = self.process.wait(timeout=0.1)
                    except subprocess.TimeoutExpired:
                        code = None
                    reason = 'Codex app-server stdout closed'
                    if code is not None:
                        reason += ' (exit {})'.format(code)
                    self._fail(reason)
                    return
                if len(line) > MAX_LINE_BYTES or not line.endswith(b'\n'):
                    self._fail('Codex app-server sent an oversized or incomplete JSONL message')
                    return
                try:
                    message = json.loads(line, parse_constant=_reject_constant)
                except (ValueError, UnicodeError, RecursionError):
                    self._fail('Codex app-server sent invalid JSON')
                    return
                if not self._route(message):
                    return
        except (OSError, ValueError):
            self._fail('Codex app-server output closed')

    def _route(self, message):
        if not isinstance(message, dict):
            self._fail('Codex app-server sent a malformed message')
            return False
        if 'method' in message:
            if (not isinstance(message['method'], str) or not message['method']
                    or ('id' in message and not _request_id(message['id']))
                    or 'result' in message or 'error' in message):
                self._fail('Codex app-server sent a malformed event')
                return False
            self._emit(message)
            return True
        if (not _request_id(message.get('id')) or ('result' in message) == ('error' in message)):
            self._fail('Codex app-server sent a malformed response')
            return False
        error = message.get('error')
        if 'error' in message:
            if not isinstance(error, dict) or type(error.get('code')) is not int or not isinstance(error.get('message'), str):
                self._fail('Codex app-server sent a malformed RPC error')
                return False
        elif not isinstance(message['result'], dict):
            self._fail('Codex app-server RPC result was not an object')
            return False
        with self._state_lock:
            future = self._pending.pop(message['id'], None)
            if future is not None:
                if error is not None:
                    future.set_exception(HouseError('Codex RPC failed (code {})'.format(error['code'])))
                else:
                    future.set_result(message['result'])
                return True
            if message['id'] in self._expired:
                return True
        self._fail('Codex app-server replied to an unknown request')
        return False

    def _emit(self, message, terminal=False):
        if self.on_message is None and message is not None:
            return
        with self._emit_lock:
            if terminal:
                _put_terminal(self._messages, message)
                return
            if self._failure:
                return
            try:
                self._messages.put_nowait(message)
                return
            except queue.Full:
                pass
        self._fail('Codex incoming event queue overflowed')

    def _dispatch(self):
        while True:
            message = self._messages.get()
            if message is None:
                return
            try:
                self.on_message(message)
            except Exception:
                self._fail('Codex message handler failed')

    def _fail(self, reason):
        with self._state_lock:
            if self._failure is not None:
                return
            self._failure = reason
            for future in self._pending.values():
                future.set_exception(HouseError(reason))
            self._pending.clear()
        self._emit({'method': '_house/disconnected', 'params': {'reason': reason}}, terminal=True)
        if self.process is not None and self.process.poll() is None:
            try:
                self.process.terminate()
            except OSError:
                pass

    def close(self):
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
        self._fail('Codex connection closed')
        if self.process is not None:
            try:
                self.process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                try:
                    self.process.kill()
                except OSError:
                    pass
                try:
                    self.process.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    pass
        with self._state_lock:
            _put_terminal(self._outgoing, None)
        self._emit(None, terminal=True)
        for worker in self._threads:
            if worker is not threading.current_thread():
                worker.join(timeout=0.15)
        if self.process is not None:
            for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass
