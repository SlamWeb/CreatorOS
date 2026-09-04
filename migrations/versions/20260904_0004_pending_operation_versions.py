"""add operation scope and optimistic version"""

from alembic import op
import sqlalchemy as sa


revision = "20260904_0004"
down_revision = "20260902_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("pending_operations", sa.Column("scope_series_id", sa.String(length=80), nullable=True))
    op.add_column("pending_operations", sa.Column("version", sa.Integer(), nullable=True))
    op.execute("UPDATE pending_operations SET version = 1 WHERE version IS NULL")
    with op.batch_alter_table("pending_operations") as batch:
        batch.alter_column("version", existing_type=sa.Integer(), nullable=False)
        batch.create_index("ix_pending_operations_scope_series_id", ["scope_series_id"])
        batch.create_check_constraint("version_positive", "version > 0")


def downgrade() -> None:
    with op.batch_alter_table("pending_operations") as batch:
        batch.drop_constraint("version_positive", type_="check")
        batch.drop_index("ix_pending_operations_scope_series_id")
        batch.alter_column("version", existing_type=sa.Integer(), server_default=None)
    op.drop_column("pending_operations", "version")
    op.drop_column("pending_operations", "scope_series_id")
