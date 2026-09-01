"""add persistent operation approval state"""

from alembic import op
import sqlalchemy as sa

revision = "20260902_0002"
down_revision = "20260902_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pending_operations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("request_text", sa.Text(), nullable=False),
        sa.Column("decision_status", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "awaiting_approval",
                "needs_clarification",
                "unsupported",
                "succeeded",
                "failed",
                "cancelled",
                "stale",
                name="pending_operation_status",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("plan_json", sa.JSON(), nullable=True),
        sa.Column("preview_json", sa.JSON(), nullable=True),
        sa.Column("confirmation_token", sa.String(length=64), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("usage_json", sa.JSON(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "decision_status IN ('ready', 'needs_clarification', 'unsupported')",
            name="decision_status_values",
        ),
        sa.CheckConstraint(
            "status IN ('awaiting_approval', 'needs_clarification', 'unsupported', "
            "'succeeded', 'failed', 'cancelled', 'stale')",
            name="pending_operation_status_values",
        ),
        sa.CheckConstraint("revision > 0", name="revision_positive"),
        sa.PrimaryKeyConstraint("id", name="pk_pending_operations"),
    )
    op.create_table(
        "operation_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("pending_operation_id", sa.String(length=36), nullable=False),
        sa.Column(
            "event_type",
            sa.Enum(
                "proposed",
                "edited",
                "confirmed",
                "succeeded",
                "failed",
                "cancelled",
                "stale",
                name="operation_event_type",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "event_type IN ('proposed', 'edited', 'confirmed', 'succeeded', "
            "'failed', 'cancelled', 'stale')",
            name="event_type_values",
        ),
        sa.ForeignKeyConstraint(
            ["pending_operation_id"],
            ["pending_operations.id"],
            name="fk_operation_events_pending_operation_id_pending_operations",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_operation_events"),
    )
    op.create_index(
        "ix_operation_events_pending_operation_id",
        "operation_events",
        ["pending_operation_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_operation_events_pending_operation_id",
        table_name="operation_events",
    )
    op.drop_table("operation_events")
    op.drop_table("pending_operations")
