"""Fault/competition tests intentionally inject deterministic model outputs."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Barrier, Lock, get_ident
from unittest.mock import patch
from creatoros.operations import OperationPlanParser, PendingOperationRepository, PendingOperationError
from creatoros.operations.parser import OperationParserUnavailable
from creatoros.storage import Database, Series, PendingOperationStatus, OperationEventType
from creatoros.web import create_app
from tests.studio_operation_fixtures import Client, Provider, catalog, decision, version, approval

with catalog() as (db, repo, url):
    provider = Provider()
    factory_calls = []
    def factory():
        factory_calls.append(1)
        return OperationPlanParser(provider, repo)
    app = create_app(database=db, operation_parser_factory=factory)
    client = Client(app)
    def post(path, payload):
        return client.request("POST", "/api/operations" + path, json=payload)
    def propose():
        result = post("/propose", {"request_text": "加 MCP", "series_id": "series-1"})
        assert result.status_code == 201, result.text
        return result.json()
    assert client.request("GET", "/api/health").status_code == 200 and not factory_calls
    assert post("/propose", {"request_text": "加 MCP", "series_id": "missing"}).status_code == 404
    with db.session() as session:
        session.get(Series, "series-2").is_active = False
    assert post("/propose", {"request_text": "加 MCP", "series_id": "series-2"}).status_code == 409
    assert not factory_calls
    with db.session() as session:
        session.get(Series, "series-2").is_active = True
    provider.result = decision(series="series-2")
    outside = propose()
    assert outside["status"] == "needs_clarification" and outside["preview"] is None
    provider.result = {"status": "unsupported", "plan": None, "message": "不支持发布"}
    unsupported = propose()
    assert post("/" + unsupported["id"] + "/cancel", version(unsupported)).status_code == 200
    provider.result = decision()
    p = propose()
    assert len(repo.list_topics("series-1")) == 2
    assert "confirmation_token" not in p["preview"]
    count = provider.calls
    restarted = Database(url)
    try:
        restored = Client(create_app(database=restarted)).request("GET", "/api/operations/" + p["id"])
        assert restored.status_code == 200 and restored.json() == p
    finally:
        restarted.close()
    assert provider.calls == count
    provider.result = decision("tools")
    edited = post("/" + p["id"] + "/edit", {**version(p), "instruction": "改成 Tools"})
    assert edited.status_code == 200, edited.text
    e = edited.json()
    assert (e["id"], e["revision"], e["version"]) == (p["id"], 2, 2)
    assert "当前完整计划" in provider.inputs[-1]["user_request"]
    assert post("/" + p["id"] + "/confirm", approval(p)).status_code == 409
    assert post("/" + p["id"] + "/confirm", approval(e)).status_code == 200
    assert post("/" + p["id"] + "/confirm", approval(e)).status_code == 200
    assert post("/" + p["id"] + "/confirm", {**approval(e), "expected_version": 999}).status_code == 409
    assert len(repo.list_topics("series-1")) == 3
    count = provider.calls
    assert post("/" + p["id"] + "/edit", {**version(e), "instruction": "改成别的"}).status_code == 409
    assert provider.calls == count
    service = app.state.writes.pending_operations
    try:
        service.confirm(p["id"])
        raise AssertionError("tokenless confirm accepted")
    except PendingOperationError:
        pass
    # Invalid output and timeout must preserve an existing plan.
    provider.result = decision("other")
    p = propose()
    for result, status in (("not json", 502), ({"status": "ready", "plan": {"operations": [
        {"action": "reorder_topics", "series_id": "series-1", "ordered_topic_ids": ["state"]}]}}, 502)):
        provider.result = result
        assert post("/" + p["id"] + "/edit", {**version(p), "instruction": "调整"}).status_code == status
        assert service.get(p["id"]).version == p["version"]
    def timeout():
        raise TimeoutError("credential-must-not-leak")
    provider.result = timeout
    response = post("/propose", {"request_text": "增加", "series_id": "series-1"})
    assert response.status_code == 503 and "credential" not in response.text
    with patch.dict("os.environ", {"DEEPSEEK_API_KEY": ""}):
        no_key = Client(create_app(database=db))
        assert not no_key.request("GET", "/api/health").json()["operation_parser_configured"]
        assert no_key.request("POST", "/api/operations/propose", json={"request_text": "增加"}).status_code == 503
        form = no_key.request("POST", "/api/operations/preview", json={"request_text": "表单", "plan": decision("form")["plan"]})
        assert form.status_code == 201
        assert no_key.request("POST", "/api/operations/" + form.json()["id"] + "/confirm", json=approval(form.json())).status_code == 200
    # Pause model completion so cancellation definitively wins; late edit cannot overwrite.
    started, release = Event(), Event()
    def slow():
        started.set()
        assert release.wait(10)
        return decision("late")
    provider.result = slow
    with ThreadPoolExecutor(2) as pool:
        future = pool.submit(post, "/" + p["id"] + "/edit", {**version(p), "instruction": "稍后改"})
        assert started.wait(5)
        assert post("/" + p["id"] + "/cancel", version(p)).status_code == 200
        release.set()
        assert future.result().status_code == 409
    assert service.get(p["id"]).status is PendingOperationStatus.CANCELLED
    # Two edits start from the same snapshot. Exactly one revision survives.
    provider.result = decision("race")
    p = propose()
    barrier = Barrier(2)
    def competing():
        barrier.wait(5)
        return decision("race-edited")
    provider.result = competing
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(post, "/" + p["id"] + "/edit", {**version(p), "instruction": "修改"}) for _ in range(2)]
        assert sorted(f.result().status_code for f in futures) == [200, 409]
    current = client.request("GET", "/api/operations/" + p["id"]).json()
    snapshot = service.get(p["id"])
    assert post("/" + p["id"] + "/confirm", approval(current)).status_code == 200
    try:
        service._mark_terminal(p["id"], PendingOperationStatus.FAILED, OperationEventType.FAILED, "late failure", expected=snapshot)
        raise AssertionError("late failure overwrote success")
    except PendingOperationError:
        pass
    assert service.get(p["id"]).status is PendingOperationStatus.SUCCEEDED
    assert repo.get_topic("late") is None
    # Confirmation and cancellation claim the same observed version concurrently.
    provider.result = decision("confirm-race")
    p = propose()
    rendezvous, seen, mutex = Barrier(2), set(), Lock()
    original_get = PendingOperationRepository.get
    def synchronized_get(repository, operation_id):
        result = original_get(repository, operation_id)
        with mutex:
            first = get_ident() not in seen
            seen.add(get_ident())
        if first:
            rendezvous.wait(5)
        return result
    with patch.object(PendingOperationRepository, "get", synchronized_get), ThreadPoolExecutor(2) as pool:
        confirm = pool.submit(post, "/" + p["id"] + "/confirm", approval(p))
        cancel = pool.submit(post, "/" + p["id"] + "/cancel", version(p))
        assert sorted([confirm.result().status_code, cancel.result().status_code]) == [200, 409]
    final = service.get(p["id"])
    assert (repo.get_topic("confirm-race") is not None) == (final.status is PendingOperationStatus.SUCCEEDED)
print("studio_operation_workflow_smoke=passed scope=hard_guard restart=edit replay=guarded faults=503/502 concurrency=409 late_failure=blocked")
