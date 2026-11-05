"""Controlled recovery fixtures bound to the active benchmark session.

The harness injects persisted plan rows after a first user turn, just like a
separately performed UI action. These are synthetic inputs, never Agent calls
or claims that any plan step was actually executed by the model.
"""
from copy import deepcopy

SCENARIOS = {
    "recovery_paused_plan": "paused",
    "recovery_blocked_plan": "needs_attention",
    "recovery_unknown_plan": "needs_attention",
    "recovery_complete_plan": "completed",
}
PLAN_ID = "bench-recovery-plan"
EVENT = "seed_recovery_plan"


def seed(world, case):
    """No pre-session writes; the real session is only known after turn one."""
    return None


def host_event(world, name, session_id):
    from creatoros.agent.plans import CONTRACT_PREFIX
    from creatoros.storage.models import AgentPlan, AgentPlanStep
    if name != EVENT or world.case.get("scenario") not in SCENARIOS:
        raise ValueError("Unfrozen recovery event")
    doc = world.app.state.chat.get(session_id)
    if doc.get("id") != session_id or doc.get("creator_id") != world.variables["creator_a"]:
        raise ValueError("Session does not belong to the benchmark account")
    scenario = world.case["scenario"]
    status = SCENARIOS[scenario]
    rows = [
        {"id":"collect", "title":"查看素材集", "tool":"manage_media_collection",
         "arguments":{"action":"get", "creator_id":world.variables["creator_a"],
                      "collection_id":world.variables["collection_a"]},
         "depends_on":[], "status":"succeeded", "result":{"collection_id":world.variables["collection_a"]}},
        {"id":"deliver", "title":"打包素材", "tool":"manage_media_collection",
         "arguments":{"action":"export", "creator_id":world.variables["creator_a"],
                      "collection_id":world.variables["collection_a"]},
         "depends_on":["collect"], "status":"pending", "result":None},
    ]
    if scenario == "recovery_blocked_plan":
        rows[0].update(status="failed", result=None, error="测试注入：素材读取失败")
        rows[1].update(status="blocked", error="前置步骤失败")
    elif scenario == "recovery_unknown_plan":
        rows[1].update(status="unknown", error="测试注入：提交回执未知，须先核对原请求")
    elif scenario == "recovery_complete_plan":
        rows = [{"id":"inspect", "title":"查看已有作品", "tool":"get_content_run",
                 "arguments":{"run_id":world.variables["run_a"]}, "depends_on":[], "status":"succeeded",
                 "result":{"run_id":world.variables["run_a"], "status":"awaiting_approval"}}]
    with world.database.session() as db:
        if db.get(AgentPlan, PLAN_ID) is not None:
            raise ValueError("Controlled plan event must be applied only once")
        db.add(AgentPlan(id=PLAN_ID, session_id=session_id, creator_id=world.variables["creator_a"],
            request_id="bench-recovery-fixture", request_digest=CONTRACT_PREFIX+"0"*30,
            title="测试素材整理计划", status=status, version=1, studio_url="http://127.0.0.1",
            continuation_state="none", continuation_version=0))
        for position, row in enumerate(rows):
            db.add(AgentPlanStep(id=PLAN_ID+":"+row["id"], plan_id=PLAN_ID, step_key=row["id"],
                title=row["title"], position=position, tool=row["tool"], arguments_json=row["arguments"],
                depends_on_json=row["depends_on"], wait_for_completion=False, status=row["status"],
                result_json=row.get("result"), error=row.get("error")))
    # Public API must be able to retrieve the exact controlled fixture through
    # the real account/session scope before the model is asked to inspect it.
    visible = world.app.state.agent_plans.get(session_id, PLAN_ID)
    if visible["creator_id"] != world.variables["creator_a"] or visible["status"] != status:
        raise ValueError("Controlled plan scope or status differs")
    snapshot = world.snapshot()["database"]
    control = {"name":EVENT, "table":"agent_plans", "id":PLAN_ID, "preserve_in_grading":True,
               "session_id":session_id, "creator_id":world.variables["creator_a"],
               "initial_plans":deepcopy(snapshot["agent_plans"]),
               "initial_steps":deepcopy(snapshot["agent_plan_steps"]),
               "fixture_note":"Synthetic host event: plan statuses are input data, not Agent actions."}
    return {"variables":{"recovery_plan":PLAN_ID}, "controls":[control], "declared_control":True}


def check(expectation, evidence):
    """Check plans after Agent response against the host's pre-turn fixture."""
    from creatoros.evaluation.benchmark.grader import MissingEvidence
    controls=evidence.get("controls")
    matches=[x for x in controls if isinstance(x,dict) and x.get("name")==EVENT] if isinstance(controls,list) else []
    if len(matches)!=1:
        raise MissingEvidence("Unique recovery control event missing")
    control=matches[0]
    after_snapshot=evidence.get("after",{})
    before_snapshot=evidence.get("before",{})
    after=after_snapshot.get("database",{})
    before=before_snapshot.get("database",{})
    plans, steps = after.get("agent_plans"), after.get("agent_plan_steps")
    initial_plans, initial_steps=control.get("initial_plans"),control.get("initial_steps")
    if not all(isinstance(x,list) for x in (plans,steps,initial_plans,initial_steps,before.get("agent_plans"),before.get("agent_plan_steps"))):
        raise MissingEvidence("Complete plan and step snapshots required")
    target=[x for x in initial_plans if isinstance(x,dict) and x.get("id")==PLAN_ID]
    initial_target=[x for x in before["agent_plans"] if isinstance(x,dict) and x.get("id")==PLAN_ID]
    if (len(target)!=1 or initial_target or target[0].get("creator_id")!=control.get("creator_id")
            or target[0].get("session_id")!=control.get("session_id")
            or control.get("creator_id")!=evidence.get("variables",{}).get("creator_a")):
        raise MissingEvidence("Controlled plan ownership or original absence cannot be verified")
    if (not isinstance(before,dict) or not isinstance(after,dict) or not before or set(before)!=set(after)
            or not isinstance(before_snapshot.get("files"),dict) or not isinstance(after_snapshot.get("files"),dict)):
        raise MissingEvidence("Complete matching database and file snapshots required")
    other_before={key:value for key,value in before.items() if key not in {"agent_plans","agent_plan_steps"}}
    other_after={key:value for key,value in after.items() if key not in {"agent_plans","agent_plan_steps"}}
    ok=(plans == initial_plans and steps == initial_steps and other_before == other_after
        and before_snapshot["files"] == after_snapshot["files"])
    return ok, "Plan rows match the host fixture and all other database rows and files remain unchanged"
