"""Explicit producer barriers for isolated browser tests, never production code."""
from threading import Event, Lock
from time import monotonic


class ProductionGates:
    def __init__(self):
        self._lock = Lock()
        self._topics = {}

    def arm(self, topic_id, outcome="complete"):
        if outcome not in {"complete", "fail"}:
            raise ValueError("Use complete or fail")
        with self._lock:
            if topic_id in self._topics:
                raise ValueError("Use a new topic; an existing producer barrier cannot be reset")
            self._topics[topic_id] = dict(outcome=outcome, release=Event(), calls={"image": 0, "video": 0}, waiting=False)
        return self.snapshot(topic_id)

    def snapshot(self, topic_id):
        with self._lock:
            state = self._topics[topic_id]
            return dict(outcome=state["outcome"], released=state["release"].is_set(),
                        calls=dict(state["calls"]), image_waiting=state["waiting"])

    def release(self, topic_id):
        with self._lock:
            self._topics[topic_id]["release"].set()
        return self.snapshot(topic_id)

    def before_produce(self, request):
        topic_id = request["topic_id"]
        kind = getattr(request.get("execution_spec"), "kind", "image")
        with self._lock:
            state = self._topics.get(topic_id)
            if state is None:
                return
            state["calls"][kind] += 1
            if kind != "image":
                return
            state["waiting"] = True
        deadline = monotonic() + 120
        try:
            while not state["release"].wait(0.1):
                cancel = request.get("cancel_event")
                if (cancel is not None and cancel.is_set()) or monotonic() >= deadline:
                    raise RuntimeError("Controlled producer barrier stopped before release")
            if state["outcome"] == "fail":
                from creatoros.integrations.codex import CodexProducerError
                raise CodexProducerError("Controlled image dependency failed", error_type="controlled_image_failure")
        finally:
            with self._lock:
                state["waiting"] = False
