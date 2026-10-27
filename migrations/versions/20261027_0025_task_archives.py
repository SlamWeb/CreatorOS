"""Allow hiding completed production tasks while preserving their history."""
from alembic import op
import sqlalchemy as sa

revision = "20261027_0025"
down_revision = "20261027_0024"
branch_labels = depends_on = None


def upgrade():
    op.add_column("production_tasks", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT count(*) FROM production_tasks WHERE archived_at IS NOT NULL")).scalar():
        raise RuntimeError("存在已移除的制作任务，不能丢弃归档标记。")
    op.drop_column("production_tasks", "archived_at")
