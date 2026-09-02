"""add recoverable content runs"""

from alembic import op
import sqlalchemy as sa

revision = "20260902_0003"
down_revision = "20260902_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "content_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("topic_id", sa.String(length=80), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("status", sa.Enum("queued", "producing", "validating", "awaiting_approval", "approved", "interrupted", "failed", "cancelled", name="content_run_status", native_enum=False), nullable=False),
        sa.Column("active_revision_number", sa.Integer(), nullable=False),
        sa.Column("approved_revision_id", sa.String(length=36), nullable=True),
        sa.Column("approved_artifact_digest", sa.String(length=64), nullable=True),
        sa.Column("input_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("origin_session_id", sa.String(length=120), nullable=True),
        sa.Column("context_snapshot_ref", sa.String(length=500), nullable=True),
        sa.Column("producer_thread_id", sa.String(length=120), nullable=True),
        sa.Column("lease_owner", sa.String(length=120), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_stage", sa.String(length=40), nullable=True),
        sa.Column("error_type", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("active_revision_number > 0", name="active_revision_positive"),
        sa.CheckConstraint("version > 0", name="version_positive"),
        sa.CheckConstraint("status IN ('queued', 'producing', 'validating', 'awaiting_approval', 'approved', 'interrupted', 'failed', 'cancelled')", name="content_run_status_values"),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"], name="fk_content_runs_topic_id_topics", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_content_runs"),
        sa.UniqueConstraint("idempotency_key", name="uq_content_runs_idempotency_key"),
    )
    op.create_index("ix_content_runs_topic_id", "content_runs", ["topic_id"])

    op.create_table(
        "content_revisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("content_run_id", sa.String(length=36), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("instruction", sa.Text(), nullable=True),
        sa.Column("production_input_json", sa.JSON(), nullable=False),
        sa.Column("artifact_directory", sa.String(length=1000), nullable=True),
        sa.Column("manifest_path", sa.String(length=1000), nullable=True),
        sa.Column("artifact_digest", sa.String(length=64), nullable=True),
        sa.Column("validation_json", sa.JSON(), nullable=True),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("revision_number > 0", name="revision_number_positive"),
        sa.ForeignKeyConstraint(["content_run_id"], ["content_runs.id"], name="fk_content_revisions_content_run_id_content_runs", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_content_revisions"),
        sa.UniqueConstraint("content_run_id", "revision_number", name="uq_content_revisions_content_run_id"),
    )
    op.create_index("ix_content_revisions_content_run_id", "content_revisions", ["content_run_id"])

    op.create_table(
        "content_attempts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("revision_id", sa.String(length=36), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.Enum("running", "succeeded", "interrupted", "failed", "cancelled", name="content_attempt_status", native_enum=False), nullable=False),
        sa.Column("producer_thread_id", sa.String(length=120), nullable=True),
        sa.Column("output_directory", sa.String(length=1000), nullable=True),
        sa.Column("usage_json", sa.JSON(), nullable=True),
        sa.Column("trace_ref", sa.String(length=1000), nullable=True),
        sa.Column("error_type", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.CheckConstraint("attempt_number > 0", name="attempt_number_positive"),
        sa.CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="duration_nonnegative"),
        sa.CheckConstraint("status IN ('running', 'succeeded', 'interrupted', 'failed', 'cancelled')", name="content_attempt_status_values"),
        sa.ForeignKeyConstraint(["revision_id"], ["content_revisions.id"], name="fk_content_attempts_revision_id_content_revisions", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_content_attempts"),
        sa.UniqueConstraint("revision_id", "attempt_number", name="uq_content_attempts_revision_id"),
    )
    op.create_index("ix_content_attempts_revision_id", "content_attempts", ["revision_id"])

    op.create_table(
        "content_run_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("content_run_id", sa.String(length=36), nullable=False),
        sa.Column("revision_id", sa.String(length=36), nullable=True),
        sa.Column("attempt_id", sa.String(length=36), nullable=True),
        sa.Column("event_type", sa.Enum("created", "started", "produced", "validated", "approved", "revision_requested", "resumed", "interrupted", "failed", "cancelled", name="content_run_event_type", native_enum=False), nullable=False),
        sa.Column("from_status", sa.String(length=32), nullable=True),
        sa.Column("to_status", sa.String(length=32), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("event_type IN ('created', 'started', 'produced', 'validated', 'approved', 'revision_requested', 'resumed', 'interrupted', 'failed', 'cancelled')", name="content_run_event_type_values"),
        sa.ForeignKeyConstraint(["content_run_id"], ["content_runs.id"], name="fk_content_run_events_content_run_id_content_runs", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_content_run_events"),
    )
    op.create_index("ix_content_run_events_content_run_id", "content_run_events", ["content_run_id"])


def downgrade() -> None:
    op.drop_index("ix_content_run_events_content_run_id", table_name="content_run_events")
    op.drop_table("content_run_events")
    op.drop_index("ix_content_attempts_revision_id", table_name="content_attempts")
    op.drop_table("content_attempts")
    op.drop_index("ix_content_revisions_content_run_id", table_name="content_revisions")
    op.drop_table("content_revisions")
    op.drop_index("ix_content_runs_topic_id", table_name="content_runs")
    op.drop_table("content_runs")
