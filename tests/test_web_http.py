from email.parser import BytesParser
from io import BytesIO
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from house_engine import web
from house_engine.storage import HouseError


class MemorySocket:
    def __init__(self, request):
        self.request = BytesIO(request)
        self.response = bytearray()

    def makefile(self, *args, **kwargs):
        return self.request

    def sendall(self, data):
        self.response.extend(data)


class HTTPController:
    def __init__(self):
        self.calls = []
        self.failure = None

    def record(self, name, *values):
        self.calls.append((name,) + values)
        if self.failure is not None:
            raise self.failure

    def snapshot(self):
        self.record('state')
        return {'connected': True, 'thread_id': 'native-http', 'rooms': []}

    def read_file(self, path):
        self.record('file', path)
        return {'path': path, 'text': '저장된 인계'}

    def submit(self, room, text):
        self.record('message', room, text)
        return {'queued': True}

    def respond(self, request_id, decision=None, answers=None):
        self.record('respond', request_id, decision, answers)
        return {'responded': request_id}

    def select_model(self, model, effort=None, fast=None):
        if any(value is not None and not isinstance(value, str) for value in (model, effort)):
            raise HouseError('모델 이름과 추론강도가 필요합니다.')
        if fast is not None and not isinstance(fast, bool):
            raise HouseError('Fast는 bool 또는 null이어야 합니다.')
        self.record('model', model, effort, fast)
        return {'selected_model': model, 'selected_effort': effort, 'selected_fast': fast}


class WebHTTPTests(unittest.TestCase):
    def setUp(self):
        self.controller = HTTPController()
        self.port = 8765
        self.token = 'only-this-launch-token'

        def capture(address, handler):
            self.handler = handler
            return SimpleNamespace(server_address=address, server_port=address[1], server_name='localhost')

        with patch.object(web, 'ThreadingHTTPServer', side_effect=capture):
            self.server = web.make_server(self.controller, self.port, self.token)

    def request(self, method='GET', path='/api/state', body=None, headers=None, authenticated=True):
        data = b'' if body is None else body
        if not isinstance(data, bytes):
            data = json.dumps(data, ensure_ascii=False).encode('utf-8')
        values = {'Host': '127.0.0.1:' + str(self.port),
                  'Origin': 'http://127.0.0.1:' + str(self.port)}
        if authenticated:
            values['Authorization'] = 'Bearer ' + self.token
        if method == 'POST':
            values.update({'Content-Type': 'application/json', 'Content-Length': str(len(data))})
        values.update(headers or {})
        lines = [method + ' ' + path + ' HTTP/1.1']
        lines.extend(name + ': ' + value for name, value in values.items() if value is not None)
        sock = MemorySocket(('\r\n'.join(lines) + '\r\n\r\n').encode('latin-1') + data)
        self.handler(sock, ('127.0.0.1', 40000), self.server)
        head, content = bytes(sock.response).split(b'\r\n\r\n', 1)
        status, raw_headers = head.split(b'\r\n', 1)
        parsed = BytesParser().parsebytes(raw_headers + b'\r\n\r\n')
        self.assertEqual(int(parsed['Content-Length']), len(content))
        return int(status.split()[1]), parsed, content

    def json_request(self, *args, **kwargs):
        status, headers, body = self.request(*args, **kwargs)
        self.assertTrue(headers['Content-Type'].startswith('application/json'))
        return status, headers, json.loads(body)

    def test_valid_bearer_origin_and_host_return_state_with_security_headers(self):
        status, headers, result = self.json_request()
        self.assertEqual(status, 200)
        self.assertEqual(result['thread_id'], 'native-http')
        self.assertEqual(self.controller.calls, [('state',)])
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(headers['Referrer-Policy'], 'no-referrer')
        self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])

    def test_missing_wrong_and_query_only_tokens_are_forbidden_before_controller(self):
        for path, headers in (('/api/state', {}),
                              ('/api/state?token=' + self.token, {}),
                              ('/api/state', {'Authorization': 'Bearer wrong'}),
                              ('/api/state', {'Authorization': 'bearer ' + self.token})):
            with self.subTest(path=path, headers=headers):
                self.assertEqual(self.json_request(path=path, headers=headers, authenticated=False)[0], 403)
        self.assertEqual(self.controller.calls, [])

    def test_foreign_host_wrong_port_and_foreign_origin_are_forbidden(self):
        for headers in ({'Host': 'evil.test:8765'}, {'Host': '127.0.0.1:8766'},
                        {'Origin': 'https://evil.test'}, {'Origin': 'null'},
                        {'Host': None}, {'Origin': 'http://localhost:8765'}):
            with self.subTest(headers=headers):
                self.assertEqual(self.json_request(headers=headers)[0], 403)
        self.assertEqual(self.controller.calls, [])

    def test_localhost_and_absent_origin_follow_loopback_contract(self):
        self.assertEqual(self.json_request(headers={'Host': 'localhost:8765',
                                                   'Origin': 'http://localhost:8765'})[0], 200)
        self.assertEqual(self.json_request(headers={'Origin': None})[0], 200)

    def test_only_fixed_static_assets_are_served_without_api_token(self):
        for path, content_type in (('/', 'text/html'), ('/index.html', 'text/html'),
                                   ('/style.css', 'text/css'), ('/app.js', 'javascript')):
            with self.subTest(path=path):
                status, headers, body = self.request(path=path, authenticated=False)
                self.assertEqual(status, 200)
                self.assertIn(content_type, headers['Content-Type'])
                self.assertTrue(body)
        for path in ('/main/handoff.md', '/.git/config', '/../README.md', '/%2e%2e/README.md',
                     '/skill/web/', '/api/unknown'):
            with self.subTest(path=path):
                self.assertEqual(self.json_request(path=path)[0], 404)
        self.assertEqual(self.controller.calls, [])

    def test_file_route_decodes_query_and_returns_controller_result(self):
        status, _, result = self.json_request(path='/api/file?path=main%2Fhandoff.md')
        self.assertEqual(status, 200)
        self.assertEqual(result, {'path': 'main/handoff.md', 'text': '저장된 인계'})
        self.assertEqual(self.controller.calls, [('file', 'main/handoff.md')])

    def test_message_and_native_response_routes_forward_exact_arguments(self):
        status, _, result = self.json_request('POST', '/api/message', {'room': 'work', 'text': 'A를 비교해줘'})
        self.assertEqual((status, result), (202, {'queued': True}))
        self.assertEqual(self.controller.calls[-1], ('message', 'work', 'A를 비교해줘'))
        status, _, result = self.json_request('POST', '/api/respond', {'id': 'approval-1', 'decision': 'accept'})
        self.assertEqual((status, result), (200, {'responded': 'approval-1'}))
        self.assertEqual(self.controller.calls[-1], ('respond', 'approval-1', 'accept', None))
        self.json_request('POST', '/api/respond', {'id': 'question-1', 'answers': {'design': ['A']}})
        self.assertEqual(self.controller.calls[-1], ('respond', 'question-1', None, {'design': ['A']}))

    def test_post_without_auth_is_forbidden_before_parsing_or_side_effects(self):
        status, _, _ = self.json_request('POST', '/api/message', b'{invalid', authenticated=False)
        self.assertEqual(status, 403)
        self.assertEqual(self.controller.calls, [])

    def test_model_route_accepts_native_name_or_null_and_requires_auth_and_field(self):
        status, _, result = self.json_request('POST', '/api/model', {'model': 'native-b'})
        self.assertEqual((status, result), (200, {'selected_model': 'native-b', 'selected_effort': None,
                                                'selected_fast': None}))
        self.assertEqual(self.controller.calls[-1], ('model', 'native-b', None, None))
        self.assertEqual(self.json_request('POST', '/api/model', {'model': None})[2],
                         {'selected_model': None, 'selected_effort': None, 'selected_fast': None})
        before = list(self.controller.calls)
        self.assertEqual(self.json_request('POST', '/api/model', {'model': 'native-b'}, authenticated=False)[0], 403)
        self.assertEqual(self.json_request('POST', '/api/model', {})[0], 400)
        self.assertEqual(self.json_request('POST', '/api/model', {'model': []})[0], 400)
        self.assertEqual(self.controller.calls, before)

    def test_model_route_forwards_model_effort_together_and_keeps_omission_compatible(self):
        status, _, result = self.json_request('POST', '/api/model', {'model': 'native-b', 'effort': 'medium'})
        self.assertEqual((status, result), (200, {'selected_model': 'native-b', 'selected_effort': 'medium',
                                                'selected_fast': None}))
        self.assertEqual(self.controller.calls[-1], ('model', 'native-b', 'medium', None))
        self.json_request('POST', '/api/model', {'model': 'native-a', 'effort': None})
        self.assertEqual(self.controller.calls[-1], ('model', 'native-a', None, None))
        before = list(self.controller.calls)
        self.assertEqual(self.json_request('POST', '/api/model', {'effort': 'high'})[0], 400)
        self.assertEqual(self.json_request('POST', '/api/model', {'model': 'native-a', 'effort': []})[0], 400)
        self.assertEqual(self.json_request('POST', '/api/model', {'model': 'native-a', 'effort': 'high'},
                                          authenticated=False)[0], 403)
        self.assertEqual(self.controller.calls, before)

    def test_fast_route_forwards_boolean_null_and_rejects_other_types(self):
        for fast in (True, False, None):
            status, _, result = self.json_request('POST', '/api/model',
                                                 {'model': 'native-a', 'effort': 'medium', 'fast': fast})
            self.assertEqual((status, result), (200, {'selected_model': 'native-a', 'selected_effort': 'medium',
                                                    'selected_fast': fast}))
            self.assertEqual(self.controller.calls[-1], ('model', 'native-a', 'medium', fast))
        before = list(self.controller.calls)
        for fast in (0, 1, 'true', [], {}):
            with self.subTest(fast=fast):
                self.assertEqual(self.json_request('POST', '/api/model', {'model': 'native-a', 'fast': fast})[0], 400)
        self.assertEqual(self.controller.calls, before)

    def test_non_json_content_type_is_unsupported_and_json_charset_is_accepted(self):
        for content_type in ('text/plain', 'application/x-www-form-urlencoded', None):
            with self.subTest(content_type=content_type):
                self.assertEqual(self.json_request('POST', '/api/message', {},
                                                  headers={'Content-Type': content_type})[0], 415)
        self.assertEqual(self.controller.calls, [])
        self.assertEqual(self.json_request('POST', '/api/message', {'room': 'main', 'text': '확인'},
                                          headers={'Content-Type': 'application/json; charset=utf-8'})[0], 202)

    def test_oversized_empty_negative_or_chunked_bodies_are_rejected_before_read(self):
        for headers in ({'Content-Length': '65537'}, {'Content-Length': '-1'},
                        {'Content-Length': '0'}, {'Content-Length': None},
                        {'Transfer-Encoding': 'chunked'}):
            with self.subTest(headers=headers):
                self.assertEqual(self.json_request('POST', '/api/message', {}, headers=headers)[0], 413)
        self.assertEqual(self.controller.calls, [])

    def test_exact_body_limit_is_accepted_but_truncated_http_body_is_rejected(self):
        body = b'{"room":"main","text":"check"}'
        padded = body + b' ' * (65536 - len(body))
        self.assertEqual(self.json_request('POST', '/api/message', padded)[0], 202)
        self.controller.calls.clear()
        self.assertEqual(self.json_request('POST', '/api/message', body,
                                          headers={'Content-Length': '1000'})[0], 400)
        self.assertEqual(self.controller.calls, [])

    def test_non_finite_constants_are_not_accepted_as_standard_json(self):
        for constant in ('NaN', 'Infinity', '-Infinity'):
            body = ('{"room":"main","text":"check","extra":' + constant + '}').encode()
            with self.subTest(constant=constant):
                self.assertEqual(self.json_request('POST', '/api/message', body)[0], 400)
        self.assertEqual(self.controller.calls, [])

    def test_invalid_json_encoding_length_and_non_object_bodies_are_client_errors(self):
        for body in (b'{invalid', b'\xff', b'null', b'[]', b'42', b'"text"'):
            with self.subTest(body=body):
                status, _, result = self.json_request('POST', '/api/message', body)
                self.assertEqual(status, 400)
                self.assertIn('error', result)
        self.assertEqual(self.json_request('POST', '/api/message', {},
                                          headers={'Content-Length': 'not-an-integer'})[0], 400)
        self.assertEqual(self.controller.calls, [])

    def test_route_methods_cannot_invoke_an_unrelated_operation(self):
        for method, path in (('GET', '/api/message'), ('POST', '/api/state'), ('POST', '/')):
            with self.subTest(method=method, path=path):
                self.assertEqual(self.json_request(method, path, {} if method == 'POST' else None)[0], 404)
        self.assertEqual(self.controller.calls, [])

    def test_expected_controller_errors_become_json_without_server_error_or_traceback(self):
        for error in (HouseError('입력 확인 필요'), OSError('file unavailable'), ValueError('invalid field'),
                      KeyError('unknown request'), TypeError('invalid value')):
            self.controller.failure = error
            with self.subTest(error=type(error).__name__):
                status, _, result = self.json_request()
                self.assertEqual(status, 400)
                self.assertIn('error', result)
                self.assertNotIn('Traceback', json.dumps(result))

    def test_excessively_nested_json_is_rejected_without_escaping_handler(self):
        body = b'[' * 2000 + b'0' + b']' * 2000
        self.assertEqual(self.json_request('POST', '/api/message', body)[0], 400)
        self.assertEqual(self.controller.calls, [])

    def test_unexpected_controller_failure_returns_sanitized_error_without_traceback(self):
        self.controller.failure = RuntimeError('PRIVATE_INTERNAL_DETAIL')
        status, _, result = self.json_request()
        self.assertIn(status, (400, 500))
        self.assertIn('error', result)
        self.assertNotIn('PRIVATE_INTERNAL_DETAIL', json.dumps(result))
        self.assertNotIn('Traceback', json.dumps(result))


if __name__ == '__main__':
    unittest.main()
