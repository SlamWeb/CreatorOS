"""Stable identities shared by production adapters and their observers."""
import hashlib
import json


def _hash(value, **kwargs):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, **kwargs).encode()).hexdigest()


def task_request_id(session_id, turn_id, creator_id, topic_id, tasks):
    return _hash(["production-tasks-v2", session_id, turn_id, creator_id, topic_id, tasks],
                 sort_keys=True, separators=(",", ":"))


def fresh_request_id(session_id, turn_id, source_run_id):
    return _hash(["fresh-production-v1", session_id, turn_id, source_run_id])


def reference_marker_id(session_id, series_id, attachment_ids, instructions, reference_presentation):
    payload = {"series_id": series_id, "attachment_ids": attachment_ids, "instructions": instructions,
               "reference_presentation": reference_presentation}
    return _hash([session_id, payload], sort_keys=True)


def reference_request_id(session_id, series_id, reference_id, title):
    return _hash(["reference-production-v1", session_id, series_id, reference_id, title])


def reference_topic_id(series_id, request_id):
    # Existing operation topic ids use json's ASCII escaping and default spacing.
    return "reference-" + hashlib.sha256(json.dumps([series_id, request_id]).encode()).hexdigest()[:24]
