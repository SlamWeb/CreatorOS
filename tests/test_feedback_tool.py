"""Projection edge cases + real isolated HTTP/SQLite scope (no model mocks)."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import httpx
from pydantic import ValidationError

from creatoros.ai.types import ToolCall
from creatoros.context import RuntimeContext
from creatoros.storage import ContentRepository, CreatorPlatform
from creatoros.tools import execute_tool_call, tools
from creatoros.tools.feedback import QueryFeedbackArgs, feedback_summary, query_feedback
from creatoros.tools.host_contract import model_tool_schemas
from creatoros.web.chat import ACCOUNT_TOOLS, STUDIO_TOOLS
from tests.agent_studio_support import serve
from tests.studio_review_fixtures import make_fixture


class FeedbackToolTests(unittest.TestCase):
    def test_projection_ties_nulls_pagination_and_latest_sample(self):
        data = {"creator_id": "a", "window_days": 7, "last_updated_at": "latest",
                "summary": {"notes": 4}, "notes": [
                    {"title": "missing", "samples": []},
                    {"title": "zero", "samples": [{"favorites": 0}]},
                    {"title": "first", "samples": [{"favorites": 100}, {"favorites": 3, "measured_at": "t"}]},
                    {"title": "tied", "samples": [{"favorites": 3}]},
                ]}
        before = deepcopy(data)
        result = feedback_summary(data, limit=2)
        self.assertEqual([n["title"] for n in result["items"]], ["first", "tied"])
        self.assertEqual(result["items"][0]["measured_at"], "t")
        self.assertIsNone(result["items"][0]["views"])
        self.assertEqual(result["known_metric_notes"], 3)
        self.assertEqual(result["page"]["next_offset"], 2)
        tail = feedback_summary(data, offset=2, limit=2)
        self.assertEqual([n["favorites"] for n in tail["items"]], [0, None])
        self.assertIsNone(tail["page"]["next_offset"])
        self.assertEqual(data, before)

    def test_schema_validation_and_host_binding(self):
        for args in ({"limit": 21}, {"limit": "3"}, {"offset": -1}, {"metric": "best"}, {"refresh": True}):
            with self.assertRaises(ValidationError):
                QueryFeedbackArgs(creator_id="a", **args)
        self.assertIn("query_feedback", STUDIO_TOOLS & ACCOUNT_TOOLS)
        ctx = RuntimeContext(Path.cwd(), creator_id="a", allowed_tools=ACCOUNT_TOOLS)
        schema = next(s["function"] for s in model_tool_schemas(tools, ctx)
                      if s["function"]["name"] == "query_feedback")
        self.assertNotIn("creator_id", schema["parameters"]["properties"])
        result = execute_tool_call(ToolCall("test", "query_feedback", '{"creator_id":"b"}'), ctx, model_requested=True)
        self.assertEqual(result.error_type, "agent_scope_rejected")

    def test_real_scoped_loopback_is_read_only(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            db, _, _, app = make_fixture(root)
            try:
                ContentRepository(db).create_creator(creator_id="other", display_name="其他", platform=CreatorPlatform.XIAOHONGSHU)
                requests = []
                @app.middleware("http")
                async def record(request, call_next):
                    requests.append((request.method, request.url.path))
                    return await call_next(request)

                with serve(app) as url, httpx.Client(base_url=url, trust_env=False) as web:
                    sid = web.post("/api/agent/sessions", json={"creator_id": "review-lab"}).json()["id"]
                    ctx = RuntimeContext(root, studio_url=url, creator_id="review-lab",
                                         agent_session_id=sid, allowed_tools=ACCOUNT_TOOLS)
                    before = (root / "review.db").read_bytes()
                    requests.clear()
                    result = execute_tool_call(ToolCall("test", "query_feedback", "{}"), ctx, model_requested=True)
                    self.assertFalse(result.is_error, result.content)
                    payload = json.loads(result.model_content or result.content)
                    self.assertEqual(payload["items"], [])
                    self.assertEqual(payload["page"]["total"], 0)
                    self.assertTrue(query_feedback("other", context=ctx).is_error)
                    self.assertEqual(requests, [("GET", "/api/creators/review-lab/analytics"),
                                                ("GET", "/api/creators/other/analytics")])
                    self.assertEqual(before, (root / "review.db").read_bytes())
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
