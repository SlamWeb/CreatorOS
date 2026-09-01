from pathlib import Path
from tempfile import TemporaryDirectory

from creatoros.operations import PendingOperationRepository
from creatoros.storage import (
    Database,
    OperationEventType,
    PendingOperation,
    PendingOperationStatus,
    upgrade_database,
)


with TemporaryDirectory() as temporary_directory:
    database_path = Path(temporary_directory) / "creatoros.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    upgrade_database(database_url)
    database = Database(database_url)
    repository = PendingOperationRepository(database)
    pending = repository.create(
        PendingOperation(
            id="operation-1",
            request_text="增加 MCP 选题",
            decision_status="ready",
            status=PendingOperationStatus.AWAITING_APPROVAL,
            plan_json={"schema_version": 1, "operations": []},
            preview_json={"confirmation_token": "a" * 64, "changes": []},
            confirmation_token="a" * 64,
            usage_json={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
            revision=1,
        )
    )
    repository.add_event(
        pending.id,
        OperationEventType.PROPOSED,
        {"decision_status": "ready", "revision": 1},
    )
    assert [item.id for item in repository.list_actionable()] == [pending.id]
    database.close()

    restarted_database = Database(database_url)
    restarted_repository = PendingOperationRepository(restarted_database)
    restored = restarted_repository.get(pending.id)
    assert restored is not None
    assert restored.status is PendingOperationStatus.AWAITING_APPROVAL
    assert restored.plan_json == {"schema_version": 1, "operations": []}
    assert restored.usage_json["total_tokens"] == 120
    events = restarted_repository.list_events(pending.id)
    assert len(events) == 1
    assert events[0].event_type is OperationEventType.PROPOSED
    assert events[0].payload_json == {"decision_status": "ready", "revision": 1}
    restarted_database.close()

print("pending_operation_storage_smoke=passed restart=passed events=1")
