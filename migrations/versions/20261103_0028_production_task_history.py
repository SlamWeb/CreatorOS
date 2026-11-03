"""Keep staged task dispatch evidence independent of frozen inputs."""
from alembic import op
import sqlalchemy as sa

revision = "20261103_0028"
down_revision = "20261103_0027"
branch_labels = depends_on = None


def upgrade():
    op.create_table("production_task_history",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("production_tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("last_error", sa.Text()),
        sa.Column("dependencies", sa.JSON(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_production_task_history_task_id", "production_task_history", ["task_id"])


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM production_task_history")):
        raise RuntimeError("存在阶段任务历史证据，不允许降级删除。")
    op.drop_index("ix_production_task_history_task_id", table_name="production_task_history")
    op.drop_table("production_task_history")
