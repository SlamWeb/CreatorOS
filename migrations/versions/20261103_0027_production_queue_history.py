"""Keep failed queue receipts before explicit reauthorization."""
from alembic import op
import sqlalchemy as sa

revision = "20261103_0027"
down_revision = "20261103_0026"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "production_queue_history",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("queue_item_id", sa.Integer(), sa.ForeignKey("production_queue_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("expected_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_production_queue_history_queue_item_id", "production_queue_history", ["queue_item_id"])


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM production_queue_history")):
        raise RuntimeError("存在生产队列历史证据，不允许降级删除。")
    op.drop_index("ix_production_queue_history_queue_item_id", table_name="production_queue_history")
    op.drop_table("production_queue_history")
