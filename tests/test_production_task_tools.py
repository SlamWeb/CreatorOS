"""Task tools use real loopback API and isolated SQLite, never formal services.

The production fixture is controlled local output, not real Codex image quality.
Only the lost-response test injects a transport failure after a real POST.
"""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import event, select

from creatoros.ai.types import ToolCall
from creatoros.context import RuntimeContext
from creatoros.integrations.studio import StudioClient, StudioClientError
from creatoros.storage import ContentRun, ProductionTask, Series, Topic, TopicRemoval, WriteReceipt
from creatoros.tools import execute_tool_call, tools
from creatoros.tools.definitions import tool_registry
from creatoros.tools.host_contract import model_tool_schemas
from creatoros.tools.model_projection import project_model_data
from creatoros.tools.results import ToolResult
from creatoros.web.chat import ACCOUNT_TOOLS
from tests import test_skill_version_scope as scope_fixture


class ProductionTaskToolContractTests(unittest.TestCase):
    def test_actual_model_schema_supports_tasks_without_model_request_identity(self):
        context = RuntimeContext(Path.cwd(), creator_id="review-lab", allowed_tools=ACCOUNT_TOOLS)
        schema = next(row["function"] for row in model_tool_schemas(tools, context)
                      if row["function"]["name"] == "start_content_run")
        branch = schema["parameters"]["$defs"]["StartProductionTasksArgs"]
        self.assertIn("tasks", branch["properties"])
        self.assertNotIn("request_id", branch["properties"])
        args = {"action": "tasks", "topic_id": "review-1", "tasks": [{"kind": "image", "title": " A ", "brief": " 全文 "}]}
        parsed = tool_registry["start_content_run"].parse_arguments(json.dumps(args))
        self.assertEqual(parsed["tasks"][0]["title"], "A")
        self.assertEqual(parsed["tasks"][0]["brief"], "全文")

    def test_invalid_task_modes_never_reach_transport(self):
        valid = {"action": "tasks", "topic_id": "review-1", "tasks": [{"kind": "image", "title": "A", "brief": "完整要求"}]}
        invalid = [{"tasks": valid["tasks"]}, {**valid, "tasks": []},
            {**valid, "topic_ids": ["review-2"]}, {**valid, "mode": "fresh"},
            {**valid, "source_run_id": "old"}, {**valid, "expected_version": 1},
            {**valid, "series_id": "s"}, {**valid, "attachment_ids": ["image"]},
            {**valid, "skill_ids": ["knowledge-to-carousel"]}, {**valid, "reference_presentation": True},
            {**valid, "request_id": "model-picked"},
            {**valid, "tasks": [{"kind": "image", "title": " ", "brief": "full"}]},
            {**valid, "tasks": [{"kind": "image", "title": "x", "brief": " "}]},
            {**valid, "tasks": [{"kind": "image", "title": "x", "brief": "full", "run_id": "forged"}]},
            {**valid, "tasks": [{"title": "x", "brief": "full"}]},
            {**valid, "skill_overrides": {"skill_id": "knowledge-to-carousel",
                "expected_digests": {"knowledge-to-carousel": "a" * 64}}}]
        context = RuntimeContext(Path.cwd(), creator_id="review-lab", allowed_tools=ACCOUNT_TOOLS)
        with patch.object(StudioClient, "request") as request:
            for arguments in invalid:
                with self.subTest(arguments=arguments):
                    result = execute_tool_call(ToolCall("bad", "start_content_run", json.dumps(arguments)),
                                               context, model_requested=True)
                    self.assertEqual(result.error_type, "invalid_arguments", result.content)
            request.assert_not_called()

    def test_task_projection_keeps_named_links_without_echoing_brief(self):
        data = {"topic_id": "topic", "collection_id": "collection", "creator_id": "account",
            "collection_url": "/?creator=account&view=materials&collection=collection", "items": [{
                "id": "task", "title": "开场图", "brief": "长正文" * 500, "position": 1,
                "run_id": "run", "run_version": 1, "status": "queued", "skill_summary": "绘画 v2",
                "skill_overrides": {"private": "trace only"}, "url": "/runs/run"}]}
        original = deepcopy(data)
        projected = project_model_data("start_content_run", data)
        self.assertEqual(projected["collection_url"], data["collection_url"])
        self.assertEqual(projected["items"][0]["title"], "开场图")
        self.assertEqual(projected["items"][0]["url"], "/runs/run")
        self.assertNotIn("brief", projected["items"][0])
        self.assertNotIn("skill_overrides", projected["items"][0])
        read = project_model_data("manage_topics", {"production_tasks": data})
        self.assertEqual(read["production_tasks"], projected)
        self.assertEqual(data, original)

    def test_chat_marks_only_unknown_production_write_as_unknown(self):
        from tests.test_plan_continuation_unknown_write import ContinuationUnknownWriteTests
        for error_type, expected in (('studio_outcome_unknown', 'unknown'),
                                     ('invalid_arguments', 'failed'),
                                     ('studio_read_failed', 'failed')):
            with self.subTest(error_type=error_type), ContinuationUnknownWriteTests.isolated_case() as case:
                case.responses = [[('start_content_run', {'action': 'tasks', 'topic_id': 'isolated-topic',
                    'tasks': [{'kind': 'image', 'title': 'One', 'brief': 'Create one'}]})], []]
                result = ToolResult(content='controlled result', is_error=True, error_type=error_type)
                with patch('creatoros.agent.loop.execute_tool_call', return_value=result):
                    view = case.chat.get(case.sid)
                    case.chat.submit(case.sid, 'production-unknown', '制作', view['version'], 'http://127.0.0.1:1')
                    thread = case.chat.threads.get(case.sid)
                    if thread is not None:
                        thread.join(20)
                entries = [entry for entry in case.chat._read(case.sid)['entries'] if entry['kind'] == 'tool']
                self.assertEqual([entry['status'] for entry in entries], [expected])
                self.assertEqual(case.providers[0].calls, 2)

    def test_fresh_and_reference_classify_only_dispatched_write_failures(self):
        context = RuntimeContext(Path.cwd(), studio_url='http://127.0.0.1:1', creator_id='creator',
                                 agent_session_id='session', user_request_id='turn', allowed_tools=ACCOUNT_TOOLS)
        for action, mode, expected in (('fresh', 'read', 'studio_read_failed'),
                                       ('fresh', 'server', 'studio_outcome_unknown'),
                                       ('fresh', 'lost', 'studio_outcome_unknown'),
                                       ('fresh', 'reject', 'invalid_request'),
                                       ('reference', 'server', 'studio_outcome_unknown'),
                                       ('reference', 'lost', 'studio_outcome_unknown'),
                                       ('reference', 'reject', 'invalid_request')):
            with self.subTest(action=action, mode=mode):
                posts = []
                class Client:
                    def __init__(self, base_url, **_kwargs):
                        self.base_url = base_url
                    def close(self):
                        pass
                    def get_run(self, _run_id):
                        if mode == 'read':
                            raise StudioClientError('read failed', 'studio_read_failed')
                        return {'topic_id': 'topic'}
                    def request(self, method, path, **kwargs):
                        if path.endswith('/image-actions/reference'):
                            return {'id': 'a' * 64}
                        posts.append((method, path, kwargs['payload']['request_id']))
                        if mode == 'lost':
                            raise StudioClientError('connection lost', 'studio_outcome_unknown')
                        if mode == 'reject':
                            raise StudioClientError('rejected', 'invalid_request', status_code=422)
                        raise StudioClientError('server failed', 'studio_request_failed', status_code=500)
                arguments = ({'action': 'fresh', 'source_run_id': 'run', 'expected_version': 1} if action == 'fresh'
                             else {'action': 'reference', 'series_id': 'series', 'title': 'One',
                                   'attachment_ids': ['attachment']})
                with patch('creatoros.tools.studio.StudioClient', Client):
                    result = execute_tool_call(ToolCall('production', 'start_content_run', json.dumps(arguments)),
                                               context, model_requested=True)
                self.assertEqual(result.error_type, expected, result.content)
                self.assertEqual(len(posts), 0 if mode == 'read' else 1)
                if posts:
                    self.assertEqual(json.loads(result.content)['request_id'], posts[0][2])


class ProductionTaskToolHttpTests(unittest.TestCase):
    setUp = scope_fixture.SkillVersionScopeTests.setUp
    skill = scope_fixture.SkillVersionScopeTests.skill
    header = scope_fixture.SkillVersionScopeTests.header
    candidate = scope_fixture.SkillVersionScopeTests.candidate

    def context(self):
        return RuntimeContext(self.root, studio_url=self.url, creator_id="review-lab",
            agent_session_id=self.account, allowed_tools=ACCOUNT_TOOLS,
            session_file=self.app.state.chat.root / self.account / "messages.json",
            user_request_id=uuid4().hex, archive_only_reads=True)

    def call(self, context, **arguments):
        return execute_tool_call(ToolCall(uuid4().hex, "start_content_run", json.dumps(arguments)),
                                 context, model_requested=True)

    def counts(self):
        with self.database.session() as db:
            return tuple(len(list(db.scalars(select(model)))) for model in (Topic, ProductionTask, ContentRun))

    def test_real_tool_replay_readback_and_distinct_payload_receipts(self):
        context = self.context()
        tasks = [{"kind": "image", "title": "第一组", "brief": "第一组独立完整要求"},
                 {"kind": "image", "title": "第二组", "brief": "第二组要求"}]
        original = self.counts()
        created = self.call(context, action="tasks", topic_id="review-1", tasks=tasks)
        self.assertFalse(created.is_error, created.content)
        receipt = json.loads(created.content)
        self.assertEqual(self.counts(), (original[0], original[1] + 2, original[2] + 2))
        self.assertEqual([item["title"] for item in receipt["items"]], [item["title"] for item in tasks])
        self.assertEqual(receipt["collection_url"],
            f"/?creator=review-lab&view=materials&collection={receipt['collection_id']}")
        for item in receipt["items"]:
            self.assertEqual(item["url"], f"/runs/{item['run_id']}")
        replay = self.call(context, action="tasks", topic_id="review-1", tasks=tasks)
        self.assertFalse(replay.is_error, replay.content)
        self.assertEqual([item["id"] for item in json.loads(replay.content)["items"]],
                         [item["id"] for item in receipt["items"]])
        different = self.call(context, action="tasks", topic_id="review-1", tasks=[{
            "kind": "image", "title": "另一次", "brief": "same turn changed"}])
        self.assertFalse(different.is_error, different.content)
        self.assertEqual(self.counts(), (original[0], original[1] + 3, original[2] + 3))
        read = execute_tool_call(ToolCall("read", "manage_topics", json.dumps({"action": "get", "topic_id": "review-1"})),
                                replace(context, user_request_id=uuid4().hex), model_requested=True)
        self.assertFalse(read.is_error, read.content)
        self.assertEqual(len(json.loads(read.content)["production_tasks"]["items"]), 3)
        self.assertNotIn("brief", json.loads(created.to_model_content())["items"][0])

    def test_foreign_parent_or_any_foreign_override_rejects_whole_batch(self):
        source = self.catalog.read_skill_file(self.foreign["id"], "SKILL.md")
        candidate = self.client.put(f"/api/producer-skills/{self.foreign['id']}/files/content",
            headers=self.header(self.other), json={"path": "SKILL.md", "expected_digest": source["digest"],
                "content": source["content"] + "\n另一账号的私有候选。\n"})
        self.assertEqual(candidate.status_code, 200, candidate.text)
        foreign_candidate = candidate.json()
        before = self.counts()
        path = "/api/topics/review-1/production-tasks"
        tasks = [{"title": "有效项", "brief": "不应部分创建"}, {"title": "越权项", "brief": "private", "skill_overrides": {
            "skill_id": self.foreign["id"], "expected_digests": {self.foreign["id"]: self.foreign["digest"]}}}]
        for headers, body in ((self.header(self.other), {"tasks": tasks[:1]}),
                              (self.header(), {"tasks": tasks}),
                              (self.header(), {"tasks": [tasks[0], {**tasks[1], "skill_overrides": {
                                  "skill_id": foreign_candidate["skill_id"], "expected_digests": {
                                      foreign_candidate["skill_id"]: foreign_candidate["digest"]}}}]})):
            response = self.client.post(path, headers=headers, json={"request_id": uuid4().hex, **body})
            self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(self.client.get(path, headers=self.header(self.other)).status_code, 403)
        self.assertEqual(self.counts(), before)

    def test_owned_candidate_can_be_selected_per_task_without_rebinding(self):
        candidate = self.candidate(self.legacy)
        result = self.call(self.context(), action="tasks", topic_id="review-1", tasks=[{
            "kind": "image", "title": "候选试做", "brief": "仅本次候选", "skill_ids": [candidate["skill_id"]]}])
        self.assertFalse(result.is_error, result.content)
        item = json.loads(result.content)["items"][0]
        with self.database.session() as db:
            run = db.get(ContentRun, item["run_id"])
            selected = run.input_snapshot_json["execution_spec"]["skills"]
            self.assertEqual([(skill["skill_id"], skill["digest"], skill["role"]) for skill in selected],
                             [(candidate["skill_id"], candidate["digest"], "selected")])
            self.assertEqual(db.get(Series, "agent-notes").skill_name, self.legacy["id"])

    def test_removed_parent_and_frozen_foreign_run_are_denied(self):
        with self.database.session() as db:
            db.add(TopicRemoval(topic_id="review-1", series_id="agent-notes", creator_id="review-lab",
                request_id=uuid4().hex, status="archived"))
        path = "/api/topics/review-1/production-tasks"
        self.assertEqual(self.client.get(path, headers=self.header()).status_code, 403)
        self.assertEqual(self.client.post(path, headers=self.header(), json={"request_id": uuid4().hex,
            "tasks": [{"title": "blocked", "brief": "blocked"}]}).status_code, 403)
        # Frozen run identity remains an account boundary after a Series move.
        run = self.runs.create("review-2")
        with self.database.session() as db:
            db.get(ContentRun, run.id).input_snapshot_json = {**run.input_snapshot_json, "creator_id": "other"}
        self.assertEqual(self.client.get("/api/topics/review-2/production-tasks", headers=self.header()).status_code, 403)

    def test_lost_response_reuses_host_identity_without_new_task_or_run(self):
        context = self.context()
        actual = StudioClient.request
        seen_keys = []
        lost = True
        def request(client, method, path, **kwargs):
            nonlocal lost
            if method == "POST" and path.endswith("/production-tasks"):
                seen_keys.append(kwargs["payload"]["request_id"])
                data = actual(client, method, path, **kwargs)
                if lost:
                    lost = False
                    raise StudioClientError("injected response loss", code="studio_outcome_unknown")
                return data
            return actual(client, method, path, **kwargs)
        with patch.object(StudioClient, "request", new=request):
            args = {"action": "tasks", "topic_id": "review-1", "tasks": [{
                "kind": "image", "title": "已落库", "brief": "仅响应丢失"}]}
            first = self.call(context, **args)
            self.assertTrue(first.is_error)
            unknown = json.loads(first.content)
            self.assertEqual(first.error_type, 'studio_outcome_unknown')
            self.assertEqual(unknown['request_id'], seen_keys[0])
            self.assertTrue(unknown['receipt_url'].endswith('/receipts/' + seen_keys[0]))
            model_unknown = json.loads(first.to_model_content().split('\n', 1)[1])
            self.assertEqual(model_unknown['request_id'], seen_keys[0])
            self.assertEqual(model_unknown['topic_id'], 'review-1')
            after = self.counts()
            read = execute_tool_call(ToolCall('read-receipt', 'manage_topics', json.dumps({
                'action': 'get', 'topic_id': 'review-1', 'production_request_id': seen_keys[0]})),
                replace(context, user_request_id=uuid4().hex), model_requested=True)
            self.assertFalse(read.is_error, read.content)
            self.assertEqual(json.loads(read.to_model_content())['production_receipt']['status'], 'accepted')
            self.assertEqual(self.counts(), after)
            second = self.call(context, **args)
            self.assertFalse(second.is_error, second.content)
        self.assertEqual(seen_keys[0], seen_keys[1])
        self.assertEqual(self.counts(), after)

    def test_receipt_scope_and_missing_result_remain_read_only(self):
        request_id = uuid4().hex
        path = f'/api/topics/review-1/production-tasks/receipts/{request_id}'
        before = self.counts()
        missing = self.client.get(path, headers=self.header())
        self.assertEqual(missing.status_code, 200, missing.text)
        self.assertEqual(missing.json()['status'], 'unknown')
        self.assertEqual(self.client.get(path, headers=self.header(self.other)).status_code, 403)
        self.assertEqual(self.client.get(path, params={'creator_id': 'other'}).status_code, 403)
        context = self.context()
        result = execute_tool_call(ToolCall('unknown-receipt', 'manage_topics', json.dumps({
            'action': 'get', 'topic_id': 'review-1', 'production_request_id': request_id})), context, model_requested=True)
        self.assertFalse(result.is_error, result.content)
        view = json.loads(result.to_model_content())['production_receipt']
        self.assertEqual(view['status'], 'unknown')
        self.assertEqual(view['request_id'], request_id)
        self.assertIn('不确定', view['message'])
        self.assertEqual(self.counts(), before)

    def test_http_commit_ack_loss_returns_unknown_and_original_receipt(self):
        factory = self.database._session_factory
        session_type = factory.class_
        def mark_receipt(session, _context):
            if any(isinstance(item, WriteReceipt) and item.response_json.get('kind') == 'production_tasks'
                   for item in session.new):
                session.info['production_receipt_test'] = True
        def lose_ack_factory():
            session = factory()
            commit = session.commit
            def commit_then_raise():
                commit()
                if session.info.get('production_receipt_test'):
                    raise OSError('injected committed acknowledgement loss')
            session.commit = commit_then_raise
            return session
        request_id = uuid4().hex
        path = '/api/topics/review-1/production-tasks'
        event.listen(session_type, 'after_flush', mark_receipt)
        try:
            with patch.object(self.database, '_session_factory', side_effect=lose_ack_factory):
                response = self.client.post(path, headers=self.header(), json={'request_id': request_id,
                    'tasks': [{'title': 'Committed once', 'brief': 'No retry'}]})
        finally:
            event.remove(session_type, 'after_flush', mark_receipt)
        self.assertEqual(response.status_code, 409, response.text)
        error = response.json()['error']
        self.assertEqual(error['code'], 'studio_outcome_unknown')
        self.assertEqual(error['request_id'], request_id)
        after = self.counts()
        receipt = self.client.get(error['receipt_url'], headers=self.header())
        self.assertEqual(receipt.status_code, 200, receipt.text)
        self.assertEqual(receipt.json()['status'], 'accepted')
        self.assertEqual(len(receipt.json()['items']), 1)
        self.assertEqual(self.client.get(error['receipt_url'], headers=self.header(self.other)).status_code, 403)
        self.assertEqual(self.counts(), after)
        with self.database.session() as session:
            session.get(Series, 'agent-notes').creator_id = 'other'
        self.assertEqual(self.client.get(error['receipt_url'], headers=self.header()).status_code, 403)
        self.assertEqual(self.client.get(error['receipt_url'], headers=self.header(self.other)).status_code, 403)

    def test_agent_conservatively_classifies_5xx_and_bad_receipts_only_for_writes(self):
        context = self.context()
        args = {'action': 'tasks', 'topic_id': 'review-1', 'tasks': [{
            'kind': 'image', 'title': 'Response check', 'brief': 'Test'}]}
        for failure in (StudioClientError('server error', 'studio_request_failed', status_code=500),
                        StudioClientError('bad JSON', 'studio_invalid_response')):
            with self.subTest(failure=failure.code), patch.object(StudioClient, 'request', side_effect=failure):
                result = self.call(context, **args)
                self.assertEqual(result.error_type, 'studio_outcome_unknown')
                data = json.loads(result.content)
                self.assertTrue(data['outcome_unknown'])
                self.assertTrue(data['request_id'])
        with patch.object(StudioClient, 'request', side_effect=StudioClientError('rejected', 'invalid_request', status_code=422)):
            result = self.call(context, **args)
            self.assertEqual(result.error_type, 'invalid_request')
        with patch.object(StudioClient, 'request', return_value={}):
            result = self.call(context, **args)
            self.assertEqual(result.error_type, 'studio_outcome_unknown')
        with patch.object(StudioClient, 'request', side_effect=StudioClientError('read failed', 'studio_read_failed')):
            result = execute_tool_call(ToolCall('read-failed', 'manage_topics', json.dumps({
                'action': 'get', 'topic_id': 'review-1'})), context, model_requested=True)
            self.assertEqual(result.error_type, 'studio_read_failed')


if __name__ == "__main__":
    unittest.main()
