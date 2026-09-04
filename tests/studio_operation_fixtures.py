"""Isolated S6 fixtures; no production executor or real operational data."""
import asyncio
import json
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
import httpx
from creatoros.ai import ModelResponse, ModelUsage
from creatoros.storage import ContentRepository, Database, TopicSource, upgrade_database

class Client:
    def __init__(self, app):
        self.app = app
    def request(self, method, path, **kwargs):
        async def send():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://testserver") as client:
                return await client.request(method, path, **kwargs)
        return asyncio.run(send())

@contextmanager
def catalog():
    with TemporaryDirectory() as directory:
        url = f"sqlite:///{(Path(directory) / 'creatoros.db').as_posix()}"
        upgrade_database(url)
        db = Database(url)
        try:
            repo = ContentRepository(db)
            for n in (1, 2):
                repo.create_creator(creator_id=f"creator-{n}", display_name=f"实验账号{n}")
                repo.create_series(series_id=f"series-{n}", creator_id=f"creator-{n}", name="Agent 每日一题",
                                   description="", audience="初学者", skill_name="knowledge-to-carousel")
            for tid, title in (("state", "AgentState"), ("context", "AgentContext")):
                repo.add_topic(topic_id=tid, series_id="series-1", title=title, source=TopicSource.MANUAL)
            yield db, repo, url
        finally:
            db.close()

def decision(topic="mcp", series="series-1"):
    return {"status": "ready", "plan": {"operations": [{"action": "add_topics", "series_id": series,
            "topics": [{"topic_id": topic, "title": topic.upper(), "brief": "面向初学者"}]}]}}

class Provider:
    def __init__(self, result=None):
        self.result = result or decision()
        self.calls = 0
        self.inputs = []
    def complete_structured(self, **kwargs):
        self.calls += 1
        self.inputs.append(json.loads(kwargs["input_text"]))
        value = self.result() if callable(self.result) else self.result
        return ModelResponse(content=value if isinstance(value, str) else json.dumps(value),
                             tool_calls=[], usage=ModelUsage(12, 8, 20))

def version(body):
    return {"expected_version": body["version"], "expected_revision": body["revision"]}

def approval(body):
    return {**version(body), "confirmation_token": body["confirmation_token"]}
