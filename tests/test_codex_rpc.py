import json
import os
from pathlib import Path
import queue
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from house_engine import __version__
from house_engine.storage import HouseError

try:
    from house_engine import codex_rpc
except ImportError:
    codex_rpc = None


FAKE = r'''
import json, os, signal, sys, threading, time
mode, capture = sys.argv[1:]
write_lock = threading.Lock()
pending = None
def send(value):
    with write_lock:
        print(json.dumps(value), flush=True)
def result(message, value):
    send({'id': message['id'], 'result': value})
for line in sys.stdin:
    message = json.loads(line)
    with open(capture, 'a') as stream:
        stream.write(json.dumps(message) + '\n')
    method = message.get('method')
    if method == 'initialize':
        if mode == 'initerror':
            send({'id': message['id'], 'error': {'code': -32001, 'message': 'Initialization refused'}})
        else:
            result(message, {'userAgent': 'fixture', 'codexHome': os.getcwd(),
                             'platformOs': 'test', 'platformFamily': 'unix'})
    elif method == 'initialized':
        if mode == 'ignoreterm':
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
    elif method == 'echo':
        result(message, {'echo': message.get('params'), 'env': os.environ.get('HOUSE_RPC_TEST')})
    elif method == 'events':
        send({'method': 'thread/started', 'params': {'thread': {'id': 'thread-one'}}})
        result(message, {})
    elif method == 'approval':
        pending = message
        send({'id': 'approval-one', 'method': 'item/commandExecution/requestApproval',
              'params': {'threadId': 'thread-one', 'command': 'fixture command'}})
    elif method == 'callback':
        send({'method': 'fixture/needs-rpc', 'params': {}})
        result(message, {})
    elif method == 'rpcerror':
        send({'id': message['id'], 'error': {'code': -32005, 'message': 'Fixture denied',
                                          'data': {'private': 'AUTH_PRIVATE_DATA'}}})
    elif method == 'hang':
        pass
    elif method == 'late':
        threading.Timer(0.15, result, args=(message, {})).start()
    elif method == 'eof':
        os.write(2, b'Authorization: Bearer AUTH_PRIVATE_DATA\n')
        os._exit(7)
    elif method == 'malformed':
        print('AUTH_PRIVATE_DATA this is not JSON', flush=True)
    elif method == 'oversized':
        print('x' * 65536, flush=True)
    elif method == 'unknown-response':
        send({'id': 987654321, 'result': {}})
    elif method == 'non-object-result':
        result(message, [])
    elif method == 'stderr':
        os.write(2, b'S' * 100000)
        result(message, {})
    elif method == 'stop-reading':
        result(message, {})
        while True:
            time.sleep(1)
    elif method is None and message.get('id') == 'approval-one':
        result(pending, {'answer': message})
if mode == 'ignoreterm':
    while True:
        time.sleep(1)
'''


class CodexRPCTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(codex_rpc, 'House needs its native app-server client')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.script = self.root / 'fake_app_server.py'
        self.script.write_text(FAKE)
        self.capture = self.root / 'received.jsonl'
        self.messages = []
        self.changed = threading.Event()

    def receive(self, message):
        self.messages.append(message)
        self.changed.set()

    def client(self, mode='normal', callback=None, env=None):
        rpc = codex_rpc.CodexRPC([sys.executable, str(self.script), mode, str(self.capture)],
                                 cwd=self.root, env=env, on_message=callback or self.receive)
        self.addCleanup(rpc.close)
        return rpc

    def wait_message(self, method):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            self.changed.clear()
            for message in list(self.messages):
                if message.get('method') == method:
                    return message
            self.changed.wait(max(0, deadline - time.monotonic()))
        self.fail('Missing native message: ' + method)

    def test_native_handshake_and_notification_use_current_client_metadata(self):
        rpc = self.client(env=dict(os.environ, HOUSE_RPC_TEST='passed'))
        initialized = rpc.start()
        self.assertEqual(initialized['userAgent'], 'fixture')
        self.assertEqual(Path(initialized['codexHome']), self.root)
        self.assertEqual(rpc.request('echo', {'hello': '한글'})['echo'], {'hello': '한글'})
        self.assertEqual(rpc.request('echo')['env'], 'passed')
        received = [json.loads(line) for line in self.capture.read_text().splitlines()]
        self.assertEqual(received[0]['method'], 'initialize')
        self.assertEqual(received[0]['params']['clientInfo'],
                         {'name': 'house', 'title': 'House', 'version': __version__})
        self.assertTrue(received[0]['params']['capabilities']['experimentalApi'])
        self.assertEqual(received[1], {'method': 'initialized'})
        rpc.notify('fixture/client-notification', {'state': 'ready'})
        rpc.request('echo')
        self.assertIn({'method': 'fixture/client-notification', 'params': {'state': 'ready'}},
                      [json.loads(line) for line in self.capture.read_text().splitlines()])

    def test_native_notifications_and_server_request_ids_reach_callback(self):
        def callback(message):
            self.receive(message)
            if message.get('id') == 'approval-one':
                rpc.respond(message['id'], result={'decision': 'accept'})
        rpc = self.client(callback=callback)
        rpc.start()
        rpc.request('events')
        self.assertEqual(self.wait_message('thread/started')['params']['thread']['id'], 'thread-one')
        answer = rpc.request('approval')
        self.assertEqual(answer['answer'], {'id': 'approval-one', 'result': {'decision': 'accept'}})

    def test_callback_can_request_rpc_without_blocking_reader(self):
        done = threading.Event()
        replies = []
        def callback(message):
            if message.get('method') == 'fixture/needs-rpc':
                replies.append(rpc.request('echo', {'callback': True}, timeout=1))
                done.set()
        rpc = self.client(callback=callback)
        rpc.start()
        rpc.request('callback')
        self.assertTrue(done.wait(2), 'Callback RPC must not run on the reader thread')
        self.assertEqual(replies[0]['echo'], {'callback': True})

    def test_concurrent_requests_keep_ids_and_json_lines_separate(self):
        rpc = self.client()
        rpc.start()
        answers, failures = [], []
        def request(number):
            try:
                answers.append(rpc.request('echo', {'number': number})['echo']['number'])
            except Exception as exc:
                failures.append(exc)
        workers = [threading.Thread(target=request, args=(i,)) for i in range(20)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(3)
        self.assertEqual(failures, [])
        self.assertEqual(sorted(answers), list(range(20)))

    def test_rpc_error_omits_private_error_data_and_connection_remains_usable(self):
        rpc = self.client()
        rpc.start()
        with self.assertRaises(HouseError) as raised:
            rpc.request('rpcerror')
        self.assertIn('-32005', str(raised.exception))
        self.assertNotIn('AUTH_PRIVATE_DATA', str(raised.exception))
        self.assertEqual(rpc.request('echo')['echo'], None)

    def test_timeout_is_bounded_and_late_response_does_not_break_next_request(self):
        rpc = self.client()
        rpc.start()
        started = time.monotonic()
        with self.assertRaisesRegex(HouseError, 'timed out'):
            rpc.request('late', timeout=0.03)
        self.assertLess(time.monotonic() - started, 1)
        time.sleep(0.2)
        self.assertEqual(rpc.request('echo', {'after': 'timeout'})['echo'], {'after': 'timeout'})

    def test_eof_wakes_waiter_and_emits_one_redacted_disconnection(self):
        rpc = self.client()
        rpc.start()
        with self.assertRaises(HouseError) as raised:
            rpc.request('eof', timeout=2)
        notice = self.wait_message('_house/disconnected')
        self.assertIsInstance(notice['params']['reason'], str)
        self.assertNotIn('AUTH_PRIVATE_DATA', json.dumps(self.messages) + str(raised.exception))
        with self.assertRaises(HouseError):
            rpc.request('echo')
        rpc.close()
        self.assertEqual(sum(m.get('method') == '_house/disconnected' for m in self.messages), 1)

    def test_malformed_or_unmatched_stdout_fails_without_echoing_raw_data(self):
        for method in ('malformed', 'unknown-response'):
            with self.subTest(method=method):
                rpc = self.client()
                rpc.start()
                with self.assertRaises(HouseError) as raised:
                    rpc.request(method, timeout=2)
                self.assertNotIn('AUTH_PRIVATE_DATA', str(raised.exception))
                rpc.close()

    def test_stdout_lines_and_stderr_storage_are_bounded(self):
        rpc = self.client()
        rpc.start()
        rpc.request('stderr')
        self.assertLessEqual(len(rpc._stderr_tail), codex_rpc.STDERR_TAIL_BYTES)
        with patch.object(codex_rpc, 'MAX_LINE_BYTES', 1024):
            with self.assertRaises(HouseError):
                rpc.request('oversized', timeout=2)

    def test_non_object_result_is_reported_as_protocol_error(self):
        rpc = self.client()
        rpc.start()
        with self.assertRaises(HouseError):
            rpc.request('non-object-result')

    def test_close_wakes_pending_requests_and_kills_only_owned_child_in_bounded_time(self):
        rpc = self.client(mode='ignoreterm')
        rpc.start()
        failed = threading.Event()
        def wait():
            try:
                rpc.request('hang', timeout=10)
            except HouseError:
                failed.set()
        worker = threading.Thread(target=wait)
        worker.start()
        rpc.request('echo')
        started = time.monotonic()
        rpc.close()
        self.assertLess(time.monotonic() - started, 3)
        self.assertTrue(failed.wait(1))
        worker.join(1)
        self.assertIsNotNone(rpc.process.poll())
        rpc.close()

    def test_failed_initialization_reaps_the_child_and_request_requires_start(self):
        rpc = self.client(mode='initerror')
        with self.assertRaises(HouseError):
            rpc.request('echo')
        with self.assertRaises(HouseError):
            rpc.start()
        self.assertIsNotNone(rpc.process.poll())

    def test_blocked_child_input_does_not_block_request_timeout_or_shutdown(self):
        rpc = self.client()
        rpc.start()
        rpc.request('stop-reading')
        started = time.monotonic()
        with self.assertRaisesRegex(HouseError, 'timed out'):
            rpc.request('echo', {'large': 'x' * 1000000}, timeout=0.05)
        self.assertLess(time.monotonic() - started, 1)
        rpc.close()
        self.assertIsNotNone(rpc.process.poll())

    def test_callback_failure_disconnects_without_leaking_exception_body(self):
        def callback(message):
            if message.get('method') == 'fixture/needs-rpc':
                raise RuntimeError('AUTH_PRIVATE_DATA')
            self.receive(message)
        rpc = self.client(callback=callback)
        rpc.start()
        try:
            rpc.request('callback')
        except HouseError:
            pass
        notice = self.wait_message('_house/disconnected')
        self.assertNotIn('AUTH_PRIVATE_DATA', json.dumps(notice))
        with self.assertRaises(HouseError):
            rpc.request('echo')

    def test_close_tolerates_consumer_draining_an_overflowed_event_queue(self):
        class DrainedQueue(queue.Queue):
            def put_nowait(self, value):
                try:
                    super().put_nowait(value)
                except queue.Full:
                    # A consumer can empty the queue between Full and recovery.
                    self.get_nowait()
                    raise
        rpc = self.client()
        rpc._messages = DrainedQueue(maxsize=1)
        rpc._messages.put_nowait({'method': 'older-event'})
        rpc.close()

    def test_server_request_can_receive_an_error_response(self):
        def callback(message):
            if message.get('id') == 'approval-one':
                rpc.respond(message['id'], error={'code': -32602, 'message': 'Invalid answer'})
        rpc = self.client(callback=callback)
        rpc.start()
        answer = rpc.request('approval')['answer']
        self.assertEqual(answer, {'id': 'approval-one', 'error': {'code': -32602, 'message': 'Invalid answer'}})


if __name__ == '__main__':
    unittest.main()
