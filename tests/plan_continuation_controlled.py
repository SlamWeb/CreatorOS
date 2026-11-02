"""Deterministic provider for transport/scheduler tests, never real-model evidence."""
import json
from uuid import uuid4


def initial_steps(scenario):
    first = ({"id": "review", "title": "判断后再调整", "tool": "request_review", "arguments": {"question": "把后续步骤改成查询BGM材料后继续"}}
             if scenario == "review" else {"id": "missing", "title": "确定的只读失败", "tool": "get_producer_skill",
                 "arguments": {"skill_id": "missing-skill--0123456789abcdef"}})
    return [first, {"id": "next", "title": "初始查询选题", "tool": "manage_topics",
                   "arguments": {"action": "list", "series_id": "plan-series"}, "depends_on": [first["id"]]}]


class ControlledPlanProvider:
    context_window = 128000
    reserve_output_tokens = 4096

    def __init__(self, state, scenario):
        self.state, self.scenario = state, scenario
        self.client = self

    def close(self):
        pass

    def complete(self, _context):
        from creatoros.ai.types import ModelResponse, ToolCall
        service, sid = self.state["app"].state.agent_plans, self.state["session_id"]
        plans = service.list(sid)["items"]
        chat = self.state["app"].state.chat
        with chat.lock:
            continuation = chat._read(sid)["requests"][-1].get("kind") == "plan_continuation"
        arguments = None
        if not plans and self.scenario in {"review", "checkpoint"}:
            arguments = {"action": "create", "title": "受控检查点", "steps": initial_steps("review")}
        elif plans and continuation:
            plan = service.get(sid, plans[0]["id"])
            first, next_step = plan["steps"]
            common = {"plan_id": plan["id"], "expected_version": plan["version"]}
            if self.scenario == "recovery" and first["status"] == "failed":
                arguments = {**common, "action": "skip", "step_id": first["id"]}
            elif (plan["status"] == "paused" or first["status"] == "skipped") and next_step["tool"] != "manage_material":
                fields = ("id", "title", "tool", "arguments", "depends_on", "wait_for_completion")
                steps = [{key: step[key] for key in fields} for step in plan["steps"]]
                steps[1].update(tool="manage_material", arguments={"action": "list"},
                                depends_on=[first["id"]] if self.scenario != "recovery" else [])
                arguments = {**common, "action": "update", "steps": steps}
            elif plan["status"] == "paused" and next_step["tool"] == "manage_material":
                arguments = {**common, "action": "resume"}
        if arguments:
            return ModelResponse(None, [ToolCall(uuid4().hex, "manage_plan", json.dumps(arguments, ensure_ascii=False))])
        return ModelResponse("受控测试：已记录当前计划结果。", [])

    def stream(self, context):
        from creatoros.ai.types import ToolCallDelta, TextDelta, StreamEnd
        response = self.complete(context)
        for index, call in enumerate(response.tool_calls):
            yield ToolCallDelta(index, call.id, call.name, call.arguments)
        if response.content:
            yield TextDelta(response.content)
        yield StreamEnd("tool_calls" if response.tool_calls else "stop")
