"""Build read-only evidence for exactly the production request sent by a plan."""
import json
from pathlib import Path

from creatoros.integrations.production_identity import (fresh_request_id, reference_marker_id,
    reference_request_id, task_request_id)
from creatoros.tools.production_arguments import StartRunArgs, production_tasks_payload


def submission_identity(arguments, context, chat_root):
    request = StartRunArgs.model_validate(arguments)
    sid, turn = context.agent_session_id, context.user_request_id
    if request.action == "tasks":
        return {"request_id": task_request_id(sid, turn, context.creator_id, request.topic_id,
                                              production_tasks_payload(request))}
    if request.action == "fresh":
        return {"request_id": fresh_request_id(sid, turn, request.source_run_id)}
    if request.action == "reference":
        key = reference_marker_id(sid, request.series_id, request.attachment_ids,
                                  request.instructions, request.reference_presentation)
        root = Path(chat_root).resolve()
        marker = root / "reference-requests" / f"{key}.json"
        try:
            if not marker.resolve().is_relative_to(root):
                return None
            with marker.open("r", encoding="utf-8") as stream:
                ref = json.loads(stream.read(4097))["reference_id"]
            if not isinstance(ref, str) or not ref:
                return None
        except (OSError, ValueError, TypeError, KeyError):
            return None
        return {"reference_id": ref,
                "request_id": reference_request_id(sid, request.series_id, ref, request.title)}
    return {}
