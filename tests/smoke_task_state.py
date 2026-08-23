from datetime import datetime, timedelta, timezone

from creatoros.agent import AgentState, TaskHealth, TaskRecord, TaskStatus


def main():
    base = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    record = TaskRecord(
        task_id="job-1",
        kind="author_index",
        created_at=base,
        deadline_at=base + timedelta(seconds=40),
    )

    assert record.status is TaskStatus.QUEUED
    assert record.health(now=base + timedelta(seconds=20)) is TaskHealth.HEALTHY

    record.mark_running(now=base + timedelta(seconds=21))
    record.heartbeat("抓取回答", now=base + timedelta(seconds=25))
    assert record.health(
        now=base + timedelta(seconds=30),
        heartbeat_timeout=timedelta(seconds=10),
    ) is TaskHealth.HEALTHY
    assert record.health(
        now=base + timedelta(seconds=36),
        heartbeat_timeout=timedelta(seconds=10),
    ) is TaskHealth.STALLED
    assert record.health(now=base + timedelta(seconds=41)) is TaskHealth.DEADLINE_EXCEEDED

    record.timeout(now=base + timedelta(seconds=41))
    assert record.status is TaskStatus.TIMED_OUT
    assert record.health(now=base + timedelta(seconds=42)) is TaskHealth.TERMINAL

    state = AgentState(messages=[], tasks={record.task_id: record})
    assert state.tasks["job-1"].status is TaskStatus.TIMED_OUT
    print("task_state_smoke=passed")


if __name__ == "__main__":
    main()
