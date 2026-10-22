import json
from pathlib import Path
import re
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

from house_engine import cli
from house_engine.project import init_project
from house_engine.storage import HouseError
from house_engine import web


class FakeRPC:
    def __init__(self, factory, extra, callback):
        self.factory, self.extra, self.callback = factory, extra, callback
        self.calls, self.responses = [], []
        self.closed = False

    def start(self):
        return {'userAgent': 'fake'}

    def request(self, method, params=None, timeout=20):
        self.calls.append((method, params))
        if method == 'model/list':
            if self.factory.catalog_error:
                raise self.factory.catalog_error
            return self.factory.model_pages[params.get('cursor')]
        if method == 'hooks/list':
            hooks = []
            for flag in cli.session_config(cli.context(self.factory.root, 'main'), self.factory.home):
                if not flag.startswith('hooks.'):
                    continue
                name = flag.split('.', 1)[1].split('=', 1)[0]
                command = json.loads(re.search(r'command=("(?:[^"\\]|\\.)*")', flag).group(1))
                hooks.append(dict(key='key-' + name, eventName=name[0].lower() + name[1:], command=command, currentHash='hash-' + name,
                                  enabled=True, trustStatus='trusted' if self.factory.trusted or self.extra else 'untrusted',
                                  sourcePath='/fake/config.toml'))
            return {'data': [{'cwd': str(self.factory.root / 'main'), 'hooks': hooks, 'errors': [], 'warnings': []}]}
        if method in ('thread/start', 'thread/resume'):
            result = {'thread': {'id': 'native-web', 'sessionId': 'native-web',
                                 'path': str(self.factory.transcript), 'status': {'type': 'idle'}, 'turns': []}}
            if self.factory.current_model is not None:
                result['model'] = self.factory.current_model
            result['reasoningEffort'] = self.factory.current_effort
            result['serviceTier'] = self.factory.current_service_tier
            return result
        if method == 'turn/start':
            number = sum(name == 'turn/start' for name, _ in self.calls)
            turn = {'id': 'turn-' + str(number), 'status': 'inProgress'}
            self.callback({'method': 'turn/started', 'params': {'threadId': 'native-web', 'turn': turn}})
            return {'turn': turn}
        raise HouseError('Unexpected fake method: ' + method)

    def respond(self, request_id, result=None, error=None):
        self.responses.append((request_id, result, error))

    def close(self):
        self.closed = True
        self.callback({'method': '_house/disconnected', 'params': {'reason': 'closed'}})


class FakeFactory:
    def __init__(self, root, home, trusted=True):
        self.root, self.home, self.trusted = root, home, trusted
        self.transcript = root.parent / 'rollout.jsonl'
        self.transcript.touch()
        self.instances = []
        self.current_model = 'native-a'
        self.current_effort = None
        self.current_service_tier = None
        self.catalog_error = None
        self.model_pages = {None: {'data': [
            {'id': 'picker-a', 'model': 'native-a', 'displayName': 'Native A'},
            {'id': 'picker-b', 'model': 'native-b', 'displayName': 'Native B'},
        ], 'nextCursor': None}}

    def __call__(self, extra, callback):
        client = FakeRPC(self, extra, callback)
        self.instances.append(client)
        return client


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.project = self.base / 'research'
        self.project.mkdir()
        self.home = self.base / 'shared'
        self.root = Path(init_project(self.project, self.home)['root'])

    def controller(self, trusted=True, resume=None, launch_id=None, factory=None):
        factory = factory or FakeFactory(self.root, self.home, trusted)
        args = [self.root, self.home, cli.context(self.root, 'main')['prompt'], factory, resume]
        controller = web.WebController(*args, **({'launch_id': launch_id} if launch_id else {}))
        self.addCleanup(controller.close)
        controller.start()
        return controller, factory

    def test_connected_project_uses_common_observations_for_web_warning_state(self):
        from house_engine import connections
        plan = connections.connect(self.root, apply=True)
        controller, factory = self.controller()
        factory.transcript.write_text(json.dumps({'type':'event_msg','payload':{
            'type':'token_count','info':{'last_token_usage':{'total_tokens':70},'model_context_window':100}}})+'\n')
        output = connections.handle_event(self.root, {'hook_event_name':'UserPromptSubmit',
            'session_id':'native-web','transcript_path':str(factory.transcript)}, plan['connection_id'])
        self.assertIn('70', output['systemMessage'])
        value = controller.snapshot()['session']
        self.assertEqual(value['context_percent'],70)
        self.assertTrue(any(alert['kind']=='context_warning' for alert in value['alerts']))
        self.assertEqual(value['native_session_id'],'native-web')

    def test_connected_web_ambiguity_is_not_resolved_by_newest_record(self):
        from house_engine import connections
        plan = connections.connect(self.root, apply=True)
        controller, factory = self.controller()
        for thread in ('native-thread-one','native-thread-two'):
            connections.handle_event(self.root, {'hook_event_name':'UserPromptSubmit',
                'native_thread_id':thread,'session_id':'native-web',
                'transcript_path':str(factory.transcript)},plan['connection_id'])
        state = controller.snapshot()
        self.assertIsNone(state['session'])
        self.assertIn('모호',state['error'])

    def test_connected_web_prefers_actual_thread_over_provisional_observation(self):
        from house_engine import connections
        plan = connections.connect(self.root, apply=True)
        controller, factory = self.controller()
        event = {'hook_event_name':'UserPromptSubmit','session_id':'native-web',
                 'transcript_path':str(factory.transcript)}
        connections.handle_event(self.root,event,plan['connection_id'])
        connections.handle_event(self.root,dict(event,native_thread_id=controller.thread_id),plan['connection_id'])
        state = controller.snapshot()
        self.assertEqual(state['session']['native_thread_id'],controller.thread_id)

    def wait_for(self, predicate):
        deadline = time.monotonic() + 2
        while not predicate() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(predicate())

    def effort_factory(self):
        factory = FakeFactory(self.root, self.home)
        factory.current_effort = 'high'
        factory.model_pages = {None: {'data': [
            {'model': 'native-a', 'displayName': 'Native A', 'defaultReasoningEffort': 'medium',
             'supportedReasoningEfforts': [{'reasoningEffort': 'medium', 'description': 'Balanced'},
                                          {'reasoningEffort': 'high', 'description': 'More reasoning'}],
             'serviceTiers': [{'id': 'priority', 'name': 'Fast', 'description': 'Faster with higher usage'}],
             'defaultServiceTier': None},
            {'model': 'native-b', 'displayName': 'Native B', 'defaultReasoningEffort': 'low',
             'supportedReasoningEfforts': [{'reasoningEffort': 'low', 'description': 'Faster'},
                                          {'reasoningEffort': 'medium', 'description': 'Balanced'}],
             'serviceTiers': [{'id': 'express', 'name': 'Fast', 'description': 'Native advertised tier'}],
             'defaultServiceTier': None},
        ], 'nextCursor': None}}
        return factory

    def test_start_creates_real_session_and_waits_for_user_work(self):
        controller, factory = self.controller()
        state = controller.snapshot()
        self.assertEqual(state['thread_id'], 'native-web')
        self.assertEqual(len(state['rooms']), 5)
        self.assertEqual(state['rooms'][1]['status'], 'unassigned')
        self.assertTrue((self.root / 'main/sessions/native-web/work').is_dir())
        self.assertFalse(any(name == 'turn/start' for name, _ in factory.instances[-1].calls))

    def test_browser_instructions_go_through_main_and_queue_without_interrupt(self):
        controller, factory = self.controller()
        client = factory.instances[-1]
        controller.submit('work', '조건 A를 비교해줘')
        self.wait_for(lambda: any(name == 'turn/start' for name, _ in client.calls))
        controller.submit('literature', '관련 근거를 확인해줘')
        time.sleep(.03)
        turns = [params for name, params in client.calls if name == 'turn/start']
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]['threadId'], 'native-web')
        self.assertIn('work', turns[0]['input'][0]['text'])
        controller.on_message({'method': 'turn/completed', 'params': {'threadId': 'native-web',
                               'turn': {'id': 'turn-1', 'status': 'completed'}}})
        self.wait_for(lambda: sum(name == 'turn/start' for name, _ in client.calls) == 2)

    def test_untrusted_house_hooks_require_explicit_invocation_only_consent(self):
        controller, factory = self.controller(trusted=False)
        self.assertIsNone(controller.snapshot()['thread_id'])
        with self.assertRaises(HouseError):
            controller.submit('main', '확인해줘')
        self.assertFalse(any(name == 'turn/start' for name, _ in factory.instances[0].calls))
        request = controller.snapshot()['requests'][0]
        self.assertEqual(request['method'], 'house/hooks/trust')
        controller.respond(request['id'], decision='accept')
        self.wait_for(lambda: controller.snapshot()['thread_id'] == 'native-web')
        self.assertTrue(factory.instances[0].closed)
        self.assertTrue(factory.instances[-1].extra)
        self.assertTrue(controller.snapshot()['connected'])
        self.assertFalse(any(name == 'config/batchWrite' for client in factory.instances for name, _ in client.calls))

    def test_session_launch_binding_is_saved_before_the_first_model_turn(self):
        controller, _ = self.controller(launch_id='owned-web-run')
        self.assertEqual(controller.snapshot()['session']['launch_id'], 'owned-web-run')

    def test_fast_native_completion_after_approval_does_not_revert_to_working(self):
        controller, factory = self.controller()
        controller.on_message({'id': 11, 'method': 'item/commandExecution/requestApproval',
                               'params': {'threadId': 'native-web', 'command': 'python compare.py'}})
        original = factory.instances[-1].respond
        def fast_response(*args, **kwargs):
            original(*args, **kwargs)
            controller.on_message({'method': 'turn/completed', 'params': {'threadId': 'native-web',
                                   'turn': {'id': 't1', 'status': 'completed'}}})
        factory.instances[-1].respond = fast_response
        controller.respond('11', decision='accept')
        self.assertEqual(controller.snapshot()['rooms'][0]['status'], 'idle')

    def test_concurrent_approval_submits_to_native_only_once(self):
        controller, factory = self.controller()
        controller.on_message({'id': 12, 'method': 'item/commandExecution/requestApproval',
                               'params': {'threadId': 'native-web', 'command': 'python compare.py'}})
        entered, release = threading.Event(), threading.Event()
        original = factory.instances[-1].respond
        def blocked_response(*args, **kwargs):
            original(*args, **kwargs)
            entered.set()
            release.wait(2)
        factory.instances[-1].respond = blocked_response
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(controller.respond, '12', 'accept')
            self.assertTrue(entered.wait(1))
            try:
                second = executor.submit(controller.respond, '12', 'decline')
                with self.assertRaises(HouseError):
                    second.result(timeout=1)
            finally:
                release.set()
            first.result(timeout=1)
        self.assertEqual(len(factory.instances[-1].responses), 1)

    def test_unknown_global_server_request_fails_explicitly_instead_of_hanging(self):
        controller, factory = self.controller()
        controller.on_message({'id': 99, 'method': 'future/serverRequest', 'params': {}})
        self.assertEqual(factory.instances[-1].responses[-1][0], 99)
        self.assertEqual(factory.instances[-1].responses[-1][2]['code'], -32601)

    def test_resumed_registry_routes_existing_child_events_and_lists_work_artifacts(self):
        controller, _ = self.controller()
        folder = self.root / 'main/sessions/native-web'
        (folder / 'room-agents.json').write_text(json.dumps({'schema_version': 1, 'rooms': {'work': {'agent_id': 'work-agent'}}}))
        artifact = self.root / 'work/sessions/native-web/agents/work-agent/work/analysis.csv'
        artifact.parent.mkdir(parents=True)
        artifact.write_text('condition,value\nA,1\n')
        controller.snapshot()
        controller.on_message({'method': 'turn/started', 'params': {'threadId': 'work-agent',
                               'turn': {'id': 'existing-child', 'status': 'inProgress'}}})
        room = next(r for r in controller.snapshot()['rooms'] if r['id'] == 'work')
        self.assertEqual(room['status'], 'working')
        self.assertIn(artifact.relative_to(self.root).as_posix(), [f['path'] for f in room['files']])

    def test_child_events_map_to_existing_room_without_changing_parent_counters(self):
        controller, _ = self.controller()
        controller.on_message({'method': 'thread/started', 'params': {'thread': {
            'id': 'work-agent', 'sessionId': 'native-web', 'parentThreadId': 'native-web',
            'agentRole': 'work', 'status': {'type': 'idle'}}}})
        controller.on_message({'method': 'turn/started', 'params': {'threadId': 'work-agent',
                               'turn': {'id': 'child-turn', 'status': 'inProgress'}}})
        controller.on_message({'method': 'item/completed', 'params': {'threadId': 'work-agent', 'item': {
            'type': 'agentMessage', 'id': 'reply-1', 'text': '입력 조건을 확인했습니다.'}}})
        work = next(room for room in controller.snapshot()['rooms'] if room['id'] == 'work')
        self.assertEqual(work['agent_id'], 'work-agent')
        self.assertEqual(work['status'], 'working')
        self.assertEqual(work['messages'][-1]['text'], '입력 조건을 확인했습니다.')
        self.assertEqual(controller.snapshot()['session']['auto_compactions'], 0)

    def test_native_approvals_and_questions_require_exact_pending_request(self):
        controller, factory = self.controller()
        controller.on_message({'id': 10, 'method': 'item/commandExecution/requestApproval',
            'params': {'threadId': 'native-web', 'command': 'python compare.py'}})
        self.assertEqual(controller.snapshot()['rooms'][0]['status'], 'waiting')
        with self.assertRaises(HouseError):
            controller.respond('absent', decision='accept')
        controller.respond('10', decision='accept')
        self.assertEqual(factory.instances[-1].responses[-1][:2], (10, {'decision': 'accept'}))
        controller.on_message({'id': 'question-1', 'method': 'item/tool/requestUserInput', 'params': {
            'threadId': 'native-web', 'questions': [{'id': 'design', 'question': '조건?', 'options': None}]}})
        controller.respond('question-1', answers={'design': ['A']})
        self.assertEqual(factory.instances[-1].responses[-1][1], {'answers': {'design': {'answers': ['A']}}})

    def test_disconnect_is_unknown_and_stale_server_events_are_not_completion(self):
        controller, _ = self.controller()
        controller.on_message({'method': '_house/disconnected', 'params': {'reason': 'lost'}})
        state = controller.snapshot()
        self.assertFalse(state['connected'])
        self.assertEqual(state['rooms'][0]['status'], 'unknown')

    def test_file_preview_is_bounded_and_cannot_escape_or_follow_links(self):
        controller, _ = self.controller()
        self.assertIn('text', controller.read_file('main/handoff.md'))
        for path in ('../outside.md', '/etc/passwd', 'hive.lock.json', 'main/.env'):
            with self.assertRaises(HouseError):
                controller.read_file(path)
        (self.root / 'work/leak.md').symlink_to(self.base / 'outside.md')
        with self.assertRaises(HouseError):
            controller.read_file('work/leak.md')
        (self.root / 'work/large.txt').write_text('x' * (256 * 1024 + 1))
        with self.assertRaises(HouseError):
            controller.read_file('work/large.txt')

    def test_http_guard_rejects_foreign_host_origin_and_missing_token(self):
        controller, _ = self.controller()
        self.assertTrue(web.authorized({'Host': '127.0.0.1:8765', 'Authorization': 'Bearer abc'}, 8765, 'abc'))
        for headers in ({'Host': 'evil.test:8765', 'Authorization': 'Bearer abc'},
                        {'Host': 'localhost:8765'},
                        {'Host': 'localhost:8765', 'Authorization': 'Bearer abc', 'Origin': 'https://evil.test'}):
            self.assertFalse(web.authorized(headers, 8765, 'abc'))

    def test_resume_requires_a_session_record_owned_by_this_house(self):
        with self.assertRaises(HouseError):
            self.controller(resume='other-project-thread')

    def test_native_model_catalog_paginates_and_uses_model_not_picker_id(self):
        factory = FakeFactory(self.root, self.home)
        factory.model_pages = {
            None: {'data': [{'id': 'picker-a', 'model': 'native-a', 'displayName': 'Native A'},
                            {'model': 'hidden', 'displayName': 'Hidden', 'hidden': True}], 'nextCursor': 'second'},
            'second': {'data': [{'model': 'native-b', 'displayName': 'Native B'}], 'nextCursor': None},
        }
        controller, _ = self.controller(factory=factory)
        state = controller.snapshot()
        self.assertEqual(state['models'], [{'model': 'native-a', 'display_name': 'Native A',
                                           'supported_efforts': [], 'default_effort': None,
                                           'service_tiers': [], 'default_service_tier': None},
                                          {'model': 'native-b', 'display_name': 'Native B',
                                           'supported_efforts': [], 'default_effort': None,
                                           'service_tiers': [], 'default_service_tier': None}])
        self.assertEqual(state['current_model'], 'native-a')
        self.assertIsNone(state['selected_model'])
        self.assertIsNone(state['model_error'])
        requests = [params for method, params in factory.instances[-1].calls if method == 'model/list']
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[-1]['cursor'], 'second')
        with self.assertRaises(HouseError):
            controller.select_model('picker-a')

    def test_catalog_failure_preserves_default_work_and_exposes_separate_error(self):
        factory = FakeFactory(self.root, self.home)
        factory.catalog_error = HouseError('model listing unavailable')
        controller, _ = self.controller(factory=factory)
        state = controller.snapshot()
        self.assertTrue(state['ready'])
        self.assertEqual(state['models'], [])
        self.assertIn('unavailable', state['model_error'])
        self.assertIsNone(state['error'])
        self.assertEqual(controller.select_model(None),
                         {'selected_model': None, 'selected_effort': None, 'selected_fast': None})
        with self.assertRaises(HouseError):
            controller.select_model('native-a')
        controller.submit('main', '기존 설정으로 확인')
        self.wait_for(lambda: any(method == 'turn/start' for method, _ in factory.instances[-1].calls))
        turn = next(params for method, params in factory.instances[-1].calls if method == 'turn/start')
        self.assertEqual(turn['model'], 'native-a')

    def test_catalog_cycle_and_excessive_or_malformed_pages_fail_without_blocking_start(self):
        cases = ({None: {'data': [], 'nextCursor': 'cycle'}, 'cycle': {'data': [], 'nextCursor': 'cycle'}},
                 {None: {'data': [{'model': 'm', 'displayName': 'M'}] * 1001}},
                 {None: {'data': [{'model': 'm', 'displayName': 12}]}},
                 {None: {'data': 'not an array'}})
        for pages in cases:
            with self.subTest(pages=str(pages)[:80]):
                factory = FakeFactory(self.root, self.home)
                factory.model_pages = pages
                controller, _ = self.controller(factory=factory)
                self.assertTrue(controller.snapshot()['ready'])
                self.assertIsNotNone(controller.snapshot()['model_error'])
                self.assertEqual(controller.snapshot()['models'], [])
                self.assertLessEqual(sum(method == 'model/list' for method, _ in factory.instances[-1].calls), 10)
                controller.close()

    def test_only_main_settings_observations_change_current_model(self):
        controller, _ = self.controller()
        controller.select_model('native-b')
        self.assertEqual(controller.snapshot()['current_model'], 'native-a')
        controller.on_message({'method': 'thread/started', 'params': {'thread': {
            'id': 'work-agent', 'sessionId': 'native-web', 'parentThreadId': 'native-web',
            'agentRole': 'work', 'status': {'type': 'idle'}}}})
        controller.on_message({'method': 'thread/settings/updated', 'params': {
            'threadId': 'work-agent', 'threadSettings': {'model': 'child-model'}}})
        self.assertEqual(controller.snapshot()['current_model'], 'native-a')
        controller.on_message({'method': 'thread/settings/updated', 'params': {
            'threadId': 'native-web', 'threadSettings': {'model': 'native-b'}}})
        self.assertEqual(controller.snapshot()['current_model'], 'native-b')

    def test_select_model_only_changes_local_choice_and_validates_catalog(self):
        controller, factory = self.controller()
        before = list(factory.instances[-1].calls)
        self.assertEqual(controller.select_model('native-b'),
                         {'selected_model': 'native-b', 'selected_effort': None, 'selected_fast': None})
        self.assertEqual(controller.snapshot()['selected_model'], 'native-b')
        for model in ('', 'not-in-native-list', 5, True, [], {}):
            with self.subTest(model=model), self.assertRaises(HouseError):
                controller.select_model(model)
        self.assertEqual(controller.select_model(None),
                         {'selected_model': None, 'selected_effort': None, 'selected_fast': None})
        self.assertEqual(factory.instances[-1].calls, before)
        self.assertEqual(controller.snapshot()['current_model'], 'native-a')

    def test_select_model_requires_native_connection_and_ready_thread(self):
        controller, _ = self.controller(trusted=False)
        with self.assertRaises(HouseError):
            controller.select_model('native-a')
        with self.assertRaises(HouseError):
            controller.select_model(None)
        controller.close()
        with self.assertRaises(HouseError):
            controller.select_model(None)

    def test_running_and_queued_requests_keep_model_captured_at_submission(self):
        controller, factory = self.controller()
        client = factory.instances[-1]
        first = controller.submit('main', '첫 작업')
        self.wait_for(lambda: any(method == 'turn/start' for method, _ in client.calls))
        controller.select_model('native-b')
        second = controller.submit('work', '다음 작업')
        controller.select_model('native-a')
        third = controller.submit('literature', '후속 작업')
        self.assertEqual([first['model'], second['model'], third['model']], ['native-a', 'native-b', 'native-a'])
        self.assertEqual(sum(method == 'turn/start' for method, _ in client.calls), 1)
        controller.on_message({'method': 'turn/completed', 'params': {'threadId': 'native-web',
                               'turn': {'id': 'turn-1', 'status': 'completed'}}})
        self.wait_for(lambda: sum(method == 'turn/start' for method, _ in client.calls) == 2)
        controller.on_message({'method': 'thread/settings/updated', 'params': {
            'threadId': 'native-web', 'threadSettings': {'model': 'native-b'}}})
        controller.select_model(None)
        fourth = controller.submit('main', '현재 설정 유지')
        self.assertEqual(fourth['model'], 'native-b')
        controller.on_message({'method': 'turn/completed', 'params': {'threadId': 'native-web',
                               'turn': {'id': 'turn-2', 'status': 'completed'}}})
        self.wait_for(lambda: sum(method == 'turn/start' for method, _ in client.calls) == 3)
        turns = [params for method, params in client.calls if method == 'turn/start']
        self.assertEqual([turn['model'] for turn in turns], ['native-a', 'native-b', 'native-a'])
        self.assertTrue(all(turn['threadId'] == 'native-web' for turn in turns))
        self.assertFalse(any(method in ('turn/interrupt', 'config/batchWrite') for method, _ in client.calls))

    def test_unknown_current_model_keeps_native_default_without_inventing_a_model(self):
        factory = FakeFactory(self.root, self.home)
        factory.current_model = None
        controller, _ = self.controller(factory=factory)
        self.assertIsNone(controller.snapshot()['current_model'])
        item = controller.submit('main', '현재 설정으로 계속')
        self.assertIsNone(item['model'])
        self.wait_for(lambda: any(method == 'turn/start' for method, _ in factory.instances[-1].calls))
        turn = next(params for method, params in factory.instances[-1].calls if method == 'turn/start')
        self.assertNotIn('model', turn)

    def test_restored_legacy_queue_omits_model_and_resume_uses_native_observation(self):
        original, _ = self.controller()
        original.close()
        folder = self.root / 'main/sessions/native-web'
        (folder / 'web-queue.json').write_text(json.dumps({'schema_version': 1, 'items': [
            {'id': 'legacy', 'room': 'main', 'text': '이전 지시', 'status': 'queued'},
            {'id': 'null-model', 'room': 'main', 'text': '설정 유지', 'status': 'queued', 'model': None,
             'effort': None, 'service_tier': None},
        ]}))
        factory = FakeFactory(self.root, self.home)
        factory.current_model = 'native-b'
        controller, _ = self.controller(resume='native-web', factory=factory)
        self.assertEqual(controller.snapshot()['current_model'], 'native-b')
        self.wait_for(lambda: sum(method == 'turn/start' for method, _ in factory.instances[-1].calls) == 1)
        controller.on_message({'method': 'turn/completed', 'params': {'threadId': 'native-web',
                               'turn': {'id': 'turn-1', 'status': 'completed'}}})
        self.wait_for(lambda: sum(method == 'turn/start' for method, _ in factory.instances[-1].calls) == 2)
        turns = [params for method, params in factory.instances[-1].calls if method == 'turn/start']
        self.assertTrue(all('model' not in turn for turn in turns))
        self.assertTrue(all('effort' not in turn for turn in turns))
        self.assertNotIn('serviceTier', turns[0])
        self.assertIn('serviceTier', turns[1])
        self.assertIsNone(turns[1]['serviceTier'])

    def test_model_captured_in_saved_queue_survives_resume_and_later_selection(self):
        controller, _ = self.controller()
        with controller.condition:
            controller.rooms['main']['status'] = 'working'
            controller.turn_id = 'already-running'
        controller.select_model('native-b')
        controller.submit('main', '저장된 지시')
        controller.close()
        resumed, factory = self.controller(resume='native-web')
        resumed.select_model('native-a')
        self.wait_for(lambda: any(method == 'turn/start' for method, _ in factory.instances[-1].calls))
        turn = next(params for method, params in factory.instances[-1].calls if method == 'turn/start')
        self.assertEqual(turn['model'], 'native-b')

    def test_catalog_exposes_native_efforts_and_initial_main_observation(self):
        controller, _ = self.controller(factory=self.effort_factory())
        state = controller.snapshot()
        self.assertEqual(state['models'][0]['supported_efforts'], [
            {'effort': 'medium', 'description': 'Balanced'},
            {'effort': 'high', 'description': 'More reasoning'}])
        self.assertEqual(state['models'][0]['default_effort'], 'medium')
        self.assertEqual(state['current_effort'], 'high')
        self.assertIsNone(state['selected_effort'])

    def test_malformed_effort_catalog_preserves_default_work_without_guessing(self):
        cases = ({'supportedReasoningEfforts': None},
                 {'supportedReasoningEfforts': [{'reasoningEffort': '', 'description': 'x'}]},
                 {'supportedReasoningEfforts': [{'reasoningEffort': 'high', 'description': 7}]},
                 {'supportedReasoningEfforts': [{'reasoningEffort': 'high', 'description': 'x'}] * 33},
                 {'defaultReasoningEffort': 7}, {'defaultReasoningEffort': None},
                 {'defaultReasoningEffort': 'not-supported'})
        for changes in cases:
            with self.subTest(changes=changes):
                factory = self.effort_factory()
                factory.model_pages[None]['data'][0].update(changes)
                controller, _ = self.controller(factory=factory)
                state = controller.snapshot()
                self.assertTrue(state['ready'])
                self.assertEqual(state['models'], [])
                self.assertIsNotNone(state['model_error'])
                self.assertIsNone(state['error'])
                item = controller.submit('main', '현재 설정으로 계속')
                self.assertEqual(item['effort'], 'high')
                self.wait_for(lambda: any(method == 'turn/start' for method, _ in factory.instances[-1].calls))
                turn = next(params for method, params in factory.instances[-1].calls if method == 'turn/start')
                self.assertEqual(turn['effort'], 'high')
                controller.close()

    def test_effort_selection_is_supported_atomic_and_sends_no_native_request(self):
        controller, factory = self.controller(factory=self.effort_factory())
        before = list(factory.instances[-1].calls)
        self.assertEqual(controller.select_model('native-a', 'high'),
                         {'selected_model': 'native-a', 'selected_effort': 'high', 'selected_fast': None})
        for model, effort in [('native-b', 'high'), ('missing', 'medium'), ('native-a', ''),
                              ('native-a', 7), ('native-a', True), ('native-a', []), ('native-a', {})]:
            with self.subTest(model=model, effort=effort), self.assertRaises(HouseError):
                controller.select_model(model, effort)
            state = controller.snapshot()
            self.assertEqual((state['selected_model'], state['selected_effort']), ('native-a', 'high'))
        self.assertEqual(controller.select_model(None, 'medium'),
                         {'selected_model': None, 'selected_effort': 'medium', 'selected_fast': None})
        self.assertEqual(controller.select_model('native-b'),
                         {'selected_model': 'native-b', 'selected_effort': None, 'selected_fast': None})
        self.assertEqual(factory.instances[-1].calls, before)
        self.assertEqual(controller.snapshot()['current_effort'], 'high')

    def test_automatic_effort_prefers_supported_current_then_model_default(self):
        controller, _ = self.controller(factory=self.effort_factory())
        with controller.condition:
            controller.rooms['main']['status'] = 'working'
            controller.turn_id = 'existing-turn'
        first = controller.submit('main', '현재 설정 유지')
        controller.select_model('native-b', None)
        second = controller.submit('main', '다른 모델의 기본 강도')
        controller.select_model('native-a', None)
        third = controller.submit('main', '현재 Main의 지원되는 강도')
        controller.on_message({'method': 'thread/settings/updated', 'params': {
            'threadId': 'native-web', 'threadSettings': {'model': 'native-a', 'effort': 'unsupported'}}})
        fourth = controller.submit('main', '지원 안 되는 현재 강도는 모델 기본값')
        self.assertEqual([(i['model'], i['effort']) for i in (first, second, third, fourth)],
                         [('native-a', 'high'), ('native-b', 'low'), ('native-a', 'high'), ('native-a', 'medium')])

    def test_missing_effort_metadata_is_compatible_and_never_invents_override(self):
        factory = FakeFactory(self.root, self.home)
        factory.current_effort = 'high'
        controller, _ = self.controller(factory=factory)
        self.assertEqual(controller.snapshot()['models'][0]['supported_efforts'], [])
        self.assertIsNone(controller.snapshot()['models'][0]['default_effort'])
        with self.assertRaises(HouseError):
            controller.select_model('native-a', 'high')
        item = controller.submit('main', '지원 정보가 없으면 기존 설정 유지')
        self.assertEqual(item['effort'], 'high')
        self.wait_for(lambda: any(method == 'turn/start' for method, _ in factory.instances[-1].calls))
        turn = next(params for method, params in factory.instances[-1].calls if method == 'turn/start')
        self.assertEqual(turn['effort'], 'high')

    def test_catalog_failure_preserves_observed_main_effort_and_fast_for_default_work(self):
        factory = self.effort_factory()
        factory.catalog_error = HouseError('catalog unavailable')
        factory.current_service_tier = 'priority'
        controller, _ = self.controller(factory=factory)
        item = controller.submit('main', '관측된 현재 설정 유지')
        self.assertEqual((item['effort'], item['service_tier']), ('high', 'priority'))
        self.wait_for(lambda: any(method == 'turn/start' for method, _ in factory.instances[-1].calls))
        turn = next(params for method, params in factory.instances[-1].calls if method == 'turn/start')
        self.assertEqual((turn['effort'], turn['serviceTier']), ('high', 'priority'))
        with self.assertRaises(HouseError):
            controller.select_model(None, 'high', True)

    def test_missing_capabilities_keep_observed_values_only_for_same_model(self):
        factory = self.effort_factory()
        factory.current_service_tier = 'fast'
        factory.model_pages[None]['data'][0].pop('supportedReasoningEfforts')
        factory.model_pages[None]['data'][0].pop('serviceTiers')
        factory.model_pages[None]['data'][1]['defaultServiceTier'] = 'express'
        controller, _ = self.controller(factory=factory)
        with controller.condition:
            controller.rooms['main']['status'] = 'working'
            controller.turn_id = 'existing-turn'
        first = controller.submit('main', '지원 목록 없이 실제 관측값 유지')
        controller.select_model('native-b')
        second = controller.submit('main', '다른 모델은 그 모델의 기본값')
        self.assertEqual((first['effort'], first['service_tier']), ('high', 'fast'))
        self.assertEqual((second['effort'], second['service_tier']), ('low', 'express'))

    def test_only_main_effort_notifications_update_observation_and_null_clears(self):
        controller, _ = self.controller(factory=self.effort_factory())
        controller.on_message({'method': 'thread/started', 'params': {'thread': {
            'id': 'work-agent', 'sessionId': 'native-web', 'parentThreadId': 'native-web',
            'agentRole': 'work', 'status': {'type': 'idle'}}}})
        controller.on_message({'method': 'thread/settings/updated', 'params': {
            'threadId': 'work-agent', 'threadSettings': {'model': 'native-a', 'effort': 'medium'}}})
        self.assertEqual(controller.snapshot()['current_effort'], 'high')
        controller.on_message({'method': 'thread/settings/updated', 'params': {
            'threadId': 'native-web', 'threadSettings': {'model': 'native-a', 'effort': 'medium'}}})
        self.assertEqual(controller.snapshot()['current_effort'], 'medium')
        controller.on_message({'method': 'thread/settings/updated', 'params': {
            'threadId': 'native-web', 'threadSettings': {'model': 'native-a', 'effort': None}}})
        self.assertIsNone(controller.snapshot()['current_effort'])

    def test_running_and_queued_efforts_stay_fixed_after_later_selection(self):
        controller, factory = self.controller(factory=self.effort_factory())
        client = factory.instances[-1]
        first = controller.submit('main', '현재 강도로 실행')
        self.wait_for(lambda: any(method == 'turn/start' for method, _ in client.calls))
        controller.select_model('native-a', 'medium')
        second = controller.submit('work', '다음 강도')
        controller.select_model('native-b', 'low', True)
        third = controller.submit('literature', '다른 모델과 강도')
        controller.select_model('native-b', 'medium')
        self.assertEqual([i['effort'] for i in (first, second, third)], ['high', 'medium', 'low'])
        self.assertEqual(sum(method == 'turn/start' for method, _ in client.calls), 1)
        for number in (1, 2):
            controller.on_message({'method': 'turn/completed', 'params': {'threadId': 'native-web',
                                   'turn': {'id': 'turn-' + str(number), 'status': 'completed'}}})
            self.wait_for(lambda: sum(method == 'turn/start' for method, _ in client.calls) == number + 1)
        turns = [params for method, params in client.calls if method == 'turn/start']
        self.assertEqual([(i['model'], i['effort']) for i in turns],
                         [('native-a', 'high'), ('native-a', 'medium'), ('native-b', 'low')])
        self.assertFalse(any(method in ('turn/interrupt', 'config/batchWrite') for method, _ in client.calls))

    def test_saved_effort_is_restored_and_resume_observes_native_current_effort(self):
        controller, _ = self.controller(factory=self.effort_factory())
        with controller.condition:
            controller.rooms['main']['status'] = 'working'
            controller.turn_id = 'existing-turn'
        controller.select_model('native-b', 'low', True)
        controller.submit('main', '이 강도로 저장된 지시')
        controller.close()
        factory = self.effort_factory()
        factory.current_effort = 'medium'
        factory.current_service_tier = 'priority'
        resumed, _ = self.controller(resume='native-web', factory=factory)
        resumed.select_model('native-b', 'medium', False)
        self.assertEqual(resumed.snapshot()['current_effort'], 'medium')
        self.assertEqual(resumed.snapshot()['current_service_tier'], 'priority')
        self.wait_for(lambda: any(method == 'turn/start' for method, _ in factory.instances[-1].calls))
        turn = next(params for method, params in factory.instances[-1].calls if method == 'turn/start')
        self.assertEqual((turn['model'], turn['effort']), ('native-b', 'low'))
        self.assertEqual(turn['serviceTier'], 'express')

    def test_explicit_effort_is_revalidated_if_current_model_changes_before_submit(self):
        controller, _ = self.controller(factory=self.effort_factory())
        controller.select_model(None, 'high')
        controller.on_message({'method': 'thread/settings/updated', 'params': {
            'threadId': 'native-web', 'threadSettings': {'model': 'native-b', 'effort': 'low'}}})
        with self.assertRaises(HouseError):
            controller.submit('main', '바뀐 모델에 지원되지 않는 강도')
        self.assertEqual(controller.snapshot()['queue'], [])

    def test_service_tier_catalog_and_current_observation_use_native_ids(self):
        factory = self.effort_factory()
        factory.current_service_tier = 'priority'
        controller, _ = self.controller(factory=factory)
        state = controller.snapshot()
        self.assertEqual(state['models'][0]['service_tiers'],
                         [{'id': 'priority', 'name': 'Fast', 'description': 'Faster with higher usage'}])
        self.assertIsNone(state['models'][0]['default_service_tier'])
        self.assertEqual(state['current_service_tier'], 'priority')
        self.assertIsNone(state['selected_fast'])

    def test_standard_and_fast_native_aliases_resolve_to_supported_wire_values(self):
        factory = self.effort_factory()
        factory.current_service_tier = 'fast'
        factory.model_pages[None]['data'][0]['defaultServiceTier'] = 'default'
        factory.model_pages[None]['data'][1]['defaultServiceTier'] = 'fast'
        controller, _ = self.controller(factory=factory)
        with controller.condition:
            controller.rooms['main']['status'] = 'working'
            controller.turn_id = 'existing-turn'
        first = controller.submit('main', '현재 Fast alias를 advertised id로 정규화')
        controller.on_message({'method': 'thread/settings/updated', 'params': {
            'threadId': 'native-web', 'threadSettings': {'model': 'native-a', 'serviceTier': 'default'}}})
        second = controller.submit('main', '현재 표준 baseline 유지')
        controller.select_model('native-b')
        third = controller.submit('main', 'catalog Fast alias도 advertised id로 정규화')
        self.assertIsNone(controller.snapshot()['models'][0]['default_service_tier'])
        self.assertEqual(controller.snapshot()['models'][1]['default_service_tier'], 'express')
        self.assertEqual([i['service_tier'] for i in (first, second, third)], ['priority', None, 'express'])

    def test_fast_requires_advertised_tier_and_boolean_selection_is_atomic(self):
        factory = self.effort_factory()
        factory.model_pages[None]['data'].append({'model': 'legacy', 'displayName': 'Legacy',
                                                'additionalSpeedTiers': ['fast']})
        controller, _ = self.controller(factory=factory)
        before = list(factory.instances[-1].calls)
        self.assertEqual(controller.select_model('native-a', 'high', True),
                         {'selected_model': 'native-a', 'selected_effort': 'high', 'selected_fast': True})
        for fast in (1, 0, 'true', [], {}):
            with self.subTest(fast=fast), self.assertRaises(HouseError):
                controller.select_model('native-b', 'low', fast)
            state = controller.snapshot()
            self.assertEqual((state['selected_model'], state['selected_effort'], state['selected_fast']),
                             ('native-a', 'high', True))
        with self.assertRaises(HouseError):
            controller.select_model('legacy', None, True)
        self.assertEqual(controller.select_model('legacy', None, False)['selected_fast'], False)
        self.assertEqual(factory.instances[-1].calls, before)

    def test_fast_auto_keeps_compatible_current_tier_then_uses_catalog_default(self):
        factory = self.effort_factory()
        factory.current_service_tier = 'priority'
        factory.model_pages[None]['data'][1]['defaultServiceTier'] = 'express'
        controller, _ = self.controller(factory=factory)
        with controller.condition:
            controller.rooms['main']['status'] = 'working'
            controller.turn_id = 'existing-turn'
        first = controller.submit('main', '현재 Fast 유지')
        controller.select_model('native-a', None, False)
        second = controller.submit('main', '표준으로 해제')
        controller.select_model('native-b', None, None)
        third = controller.submit('main', '다른 모델의 catalog 기본 tier')
        controller.on_message({'method': 'thread/settings/updated', 'params': {
            'threadId': 'native-web', 'threadSettings': {'model': 'native-a', 'serviceTier': 'unsupported'}}})
        controller.select_model('native-a', None, None)
        fourth = controller.submit('main', '호환되지 않는 현재 tier는 기본 표준')
        self.assertEqual([i['service_tier'] for i in (first, second, third, fourth)],
                         ['priority', None, 'express', None])

    def test_fast_choices_are_captured_per_turn_and_null_is_sent_for_standard(self):
        controller, factory = self.controller(factory=self.effort_factory())
        client = factory.instances[-1]
        controller.select_model('native-a', None, True)
        controller.submit('main', 'Fast 작업')
        self.wait_for(lambda: any(method == 'turn/start' for method, _ in client.calls))
        controller.select_model('native-a', None, False)
        controller.submit('work', '다음 표준 작업')
        controller.select_model('native-b', None, True)
        controller.submit('literature', 'native id가 다른 Fast 작업')
        controller.select_model('native-b', None, False)
        self.assertEqual(sum(method == 'turn/start' for method, _ in client.calls), 1)
        for number in (1, 2):
            controller.on_message({'method': 'turn/completed', 'params': {'threadId': 'native-web',
                                   'turn': {'id': 'turn-' + str(number), 'status': 'completed'}}})
            self.wait_for(lambda: sum(method == 'turn/start' for method, _ in client.calls) == number + 1)
        turns = [params for method, params in client.calls if method == 'turn/start']
        self.assertEqual([i['serviceTier'] for i in turns], ['priority', None, 'express'])
        self.assertFalse(any(method in ('turn/interrupt', 'config/batchWrite') for method, _ in client.calls))

    def test_only_main_service_tier_notifications_update_current_and_null_clears(self):
        factory = self.effort_factory()
        factory.current_service_tier = 'priority'
        controller, _ = self.controller(factory=factory)
        controller.on_message({'method': 'thread/started', 'params': {'thread': {
            'id': 'work-agent', 'sessionId': 'native-web', 'parentThreadId': 'native-web',
            'agentRole': 'work', 'status': {'type': 'idle'}}}})
        controller.on_message({'method': 'thread/settings/updated', 'params': {
            'threadId': 'work-agent', 'threadSettings': {'model': 'native-b', 'serviceTier': 'express'}}})
        self.assertEqual(controller.snapshot()['current_service_tier'], 'priority')
        controller.on_message({'method': 'thread/settings/updated', 'params': {
            'threadId': 'native-web', 'threadSettings': {'model': 'native-a', 'serviceTier': None}}})
        self.assertIsNone(controller.snapshot()['current_service_tier'])

    def test_malformed_tier_catalog_preserves_default_work(self):
        cases = ({'serviceTiers': None}, {'serviceTiers': [{'id': '', 'name': 'Fast', 'description': 'x'}]},
                 {'serviceTiers': [{'id': 'priority', 'name': 7, 'description': 'x'}]},
                 {'serviceTiers': [{'id': 'priority', 'name': 'Fast', 'description': []}]},
                 {'defaultServiceTier': 7}, {'defaultServiceTier': 'not-advertised'})
        for changes in cases:
            with self.subTest(changes=changes):
                factory = self.effort_factory()
                factory.model_pages[None]['data'][0].update(changes)
                controller, _ = self.controller(factory=factory)
                self.assertTrue(controller.snapshot()['ready'])
                self.assertIsNotNone(controller.snapshot()['model_error'])
                self.assertEqual(controller.snapshot()['models'], [])
                item = controller.submit('main', '기본 작업 유지')
                self.assertIn('service_tier', item)
                self.assertIsNone(item['service_tier'])
                controller.close()

    def test_saved_queue_rejects_malformed_effort_and_service_tier_values(self):
        original, _ = self.controller()
        original.close()
        path = self.root / 'main/sessions/native-web/web-queue.json'
        for field, value in (('effort', []), ('service_tier', False)):
            path.write_text(json.dumps({'items': [{'id': 'invalid', 'room': 'main', 'text': 'check',
                                                  'status': 'queued', field: value}]}))
            with self.subTest(field=field), self.assertRaises(HouseError):
                self.controller(resume='native-web')


if __name__ == '__main__':
    unittest.main()
