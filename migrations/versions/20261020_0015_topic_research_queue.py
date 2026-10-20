"""Queue explicitly requested research without replaying interrupted paid work."""
from alembic import op
import sqlalchemy as sa

revision = "20261020_0015"
down_revision = "20261020_0014"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "topic_research_queue_items",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("batch_id", sa.String(32), unique=True, nullable=False),
        sa.Column("series_id", sa.String(80), sa.ForeignKey("series.id"), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.CheckConstraint(
            "status IN ('queued','researching','ready','failed','interrupted','stale')",
            name="status_values",
        ),
    )
    op.create_index("ix_topic_research_queue_pending", "topic_research_queue_items",
                    ["status", "submitted_at", "id"])


def downgrade():
    op.drop_index("ix_topic_research_queue_pending", table_name="topic_research_queue_items")
    op.drop_table("topic_research_queue_items")
