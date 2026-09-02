from io import StringIO
from types import SimpleNamespace

from creatoros.runs.cli import ContentRunCLI
from creatoros.storage import ContentRunStatus
from creatoros.terminal import RichConsole


class FakeRunService:
    def __init__(self):
        self.run = SimpleNamespace(
            id="run-12345678",
            topic_id="topic-1",
            status=ContentRunStatus.AWAITING_APPROVAL,
            active_revision_number=1,
            input_snapshot_json={"topic_title": "Agent State"},
            retryable=False,
            error_message=None,
            failure_stage=None,
            version=3,
        )
        self.revision = SimpleNamespace(
            id="revision-1",
            revision_number=1,
            artifact_directory="D:/outputs/run/revision-001/attempt-001",
            artifact_digest="a" * 64,
        )

    def recover_inflight(self):
        return 0, 0, 0

    def list_runs(self):
        return (self.run,)

    def get(self, run_id):
        assert run_id == self.run.id
        return self.run

    def get_active_revision(self, run_id):
        assert run_id == self.run.id
        return self.revision

    def approve(self, run_id, *, revision_id, artifact_digest, expected_version):
        assert (run_id, revision_id, artifact_digest, expected_version) == (
            self.run.id,
            self.revision.id,
            self.revision.artifact_digest,
            3,
        )
        self.run.status = ContentRunStatus.APPROVED
        self.run.version = 4
        return self.run


output = StringIO()
inputs = iter(("1", "1", "1", "3"))
console = RichConsole(input_fn=lambda _prompt: next(inputs), output=output)
cli = ContentRunCLI(console, FakeRunService(), SimpleNamespace())
cli.run()
rendered = output.getvalue()
assert "Agent State  ·  待批准" in rendered
assert "Revision 1  ·  version 3" in rendered
assert "Digest：aaaaaaaaaaaaaaaa…" in rendered
assert "已批准当前产物" in rendered
assert "Agent State  ·  已批准" in rendered

print("content_run_cli_smoke=passed approval=versioned")
