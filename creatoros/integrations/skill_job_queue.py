"""Single-consumer durable queue for independent Skill workspaces."""
from __future__ import annotations

import json
import logging
import threading

from .producer_skills import _write
from .skill_extraction import now

LOG = logging.getLogger(__name__)


def queued_record(job, operation, key=None):
    """Build the complete authorization before its single authoritative write."""
    job["queue"] = {"state": "queued", "operation": operation, "key": key,
                    "submitted_at": now()}
    job.update(operation=operation, updated_at=now())
    return job


def enqueue(service, job, operation, key=None):
    queued_record(job, operation, key)
    _write(service._path("jobs", job["id"]) / "job.json", job)
    kick(service)


def pending(service):
    jobs = []
    for path in (service.root / "jobs").glob("*/job.json"):
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(job, dict) or not isinstance(job.get("queue", {}), dict):
                raise ValueError("Invalid Skill queue record")
            if job.get("queue", {}).get("state") == "queued":
                if (job.get("id") != path.parent.name or not isinstance(job["queue"].get("submitted_at"), str)
                        or job["queue"].get("operation") not in {"extract", "revise", "trial"}):
                    raise ValueError("Invalid queued Skill operation")
                jobs.append(job)
        except (OSError, ValueError):
            LOG.exception("Cannot read queued Skill job %s", path.parent.name)
    return sorted(jobs, key=lambda job: (job["queue"]["submitted_at"], job["id"]))


def kick(service):
    """Caller owns service.lock; one thread drains all accepted operations."""
    if service.closed or (service._draining and service.worker and service.worker.is_alive()):
        return
    if not pending(service):
        return
    service.worker = threading.Thread(target=drain, args=(service,), daemon=True, name="creatoros-skill-queue")
    service._draining = True
    try:
        service.worker.start()
    except RuntimeError:
        # The accepted request remains queued and can be retried or restarted.
        service.worker = None
        service._draining = False
        LOG.exception("Skill queue consumer did not start; queued requests retained")


def drain(service):
    try:
        _drain(service)
    finally:
        with service.lock:
            if service.worker is threading.current_thread():
                service._draining = False
                service.active_id = None


def _drain(service):
    unreadable = set()
    while True:
        with service.lock:
            jobs = [] if service.closed else [job for job in pending(service) if job["id"] not in unreadable]
            if not jobs:
                # Clear under the same lock as submissions: no lost wake-up.
                service.active_id = None
                service._draining = False
                return
            job = jobs[0]
            identifier = job["id"]
            queue = {**job["queue"], "state": "running", "started_at": now()}
            service.cancel_event = threading.Event()
            service.active_id = identifier
            changes = {"queue": queue, "cancel_requested": False}
            if queue["operation"] == "extract":
                changes["status"] = "running"
            if queue["operation"] == "trial":
                for trial in job.get("trials", []):
                    if trial["id"] == queue["key"]:
                        trial["status"] = "running"
                changes["trials"] = job["trials"]
            try:
                service._update(identifier, **changes)
            except Exception:
                # Do not make a paid call without a durable claim. Other jobs
                # remain independent; a later kick/restart can retry this claim.
                unreadable.add(identifier)
                service.active_id = None
                LOG.exception("Cannot claim Skill job %s; kept queued", identifier)
                continue
        execution_failed = False
        try:
            if queue["operation"] == "extract":
                from .skill_workbench import references
                service._run(identifier, references(service, job))
            elif queue["operation"] == "revise":
                from .skill_workbench import run_revision
                run_revision(service, identifier, queue["key"])
            elif queue["operation"] == "trial":
                from .skill_workbench import run_trial
                run_trial(service, identifier, queue["key"])
            else:
                raise ValueError("Unknown Skill queue operation")
        except Exception as error:
            execution_failed = True
            # Also contains failures outside the model helper (e.g. bad snapshot).
            from .skill_extraction import extraction_failure
            kind, message = extraction_failure(error, service.cancel_event.is_set())
            try:
                changes = {"operation": None, "error": message, "error_type": kind}
                if queue["operation"] == "extract":
                    changes["status"] = "failed"
                if queue["operation"] == "trial":
                    current = service._read(identifier)
                    for trial in current.get("trials", []):
                        if trial["id"] == queue["key"]:
                            trial.update(status="failed", error=message)
                    changes["trials"] = current.get("trials", [])
                service._update(identifier, **changes)
            except Exception:
                LOG.exception("Cannot save failure of Skill job %s; files retained", identifier)
        finally:
            with service.lock:
                try:
                    current = service._read(identifier)
                    state = ("interrupted" if service.cancel_event.is_set() else
                             "failed" if execution_failed or current.get("error") else "completed")
                    service._update(identifier, queue={**queue, "state": state, "finished_at": now()})
                except Exception:
                    LOG.exception("Cannot finish Skill queue receipt %s; not retried", identifier)
                service.active_id = None
