"""Typed task waiting state and account-owned input materials."""
from alembic import op
import sqlalchemy as sa

revision = "20261027_0022"
down_revision = "20261027_0021"
branch_labels = depends_on = None


def upgrade():
    op.add_column("production_tasks", sa.Column("kind", sa.String(20), nullable=False, server_default="image"))
    op.add_column("production_tasks", sa.Column("definition_json", sa.JSON(), nullable=False, server_default="{}"))
    op.add_column("production_tasks", sa.Column("dependency_json", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("production_tasks", sa.Column("dispatch_state", sa.String(30), nullable=False, server_default="dispatched"))
    op.add_column("production_tasks", sa.Column("last_error", sa.Text(), nullable=True))
    op.create_table("managed_materials",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("creator_id", sa.String(80), sa.ForeignKey("creators.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False), sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False), sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_managed_materials_creator_id", "managed_materials", ["creator_id"])


def downgrade():
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT count(*) FROM managed_materials")).scalar() or bind.execute(
            sa.text("SELECT count(*) FROM production_tasks WHERE definition_json != '{}' OR kind != 'image'")).scalar():
        raise RuntimeError("保留制作任务和素材记录，请先导出并处理新增数据后再降级。")
    op.drop_table("managed_materials")
    for name in ("last_error", "dispatch_state", "dependency_json", "definition_json", "kind"):
        op.drop_column("production_tasks", name)
