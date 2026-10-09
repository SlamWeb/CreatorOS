"""Preserve explicit topic removals independently of production history."""
from alembic import op
import sqlalchemy as sa

revision = "20261009_0009"
down_revision = "20261005_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "topic_removals",
        sa.Column("topic_id", sa.String(80), primary_key=True),
        sa.Column("series_id", sa.String(80), sa.ForeignKey("series.id", ondelete="CASCADE"), nullable=False),
        sa.Column("creator_id", sa.String(80)),
        sa.Column("request_id", sa.String(64), nullable=False, unique=True),
        sa.Column("batch_id", sa.String(32)),
        sa.Column("candidate_id", sa.String(80)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('deleted', 'archived', 'dismissed')", name="removal_status_values"),
    )
    op.create_index("ix_topic_removals_series_id", "topic_removals", ["series_id"])


def downgrade() -> None:
    op.drop_index("ix_topic_removals_series_id", table_name="topic_removals")
    op.drop_table("topic_removals")
