"""Persist explicitly authorized serial production submissions; never backfill old Runs."""
from alembic import op
import sqlalchemy as sa

revision = "20261017_0012"
down_revision = "20261016_0011"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("production_queue_items",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("content_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("revision_id", sa.String(36), sa.ForeignKey("content_revisions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("expected_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.UniqueConstraint("run_id", "revision_id"),
        sa.CheckConstraint("expected_version > 0", name="expected_version_positive"),
        sa.CheckConstraint("status IN ('pending','running','succeeded','failed','needs_attention','cancelled')", name="status_values"))
    op.create_index("ix_production_queue_pending", "production_queue_items", ["status", "submitted_at", "id"])


def downgrade():
    op.drop_index("ix_production_queue_pending", table_name="production_queue_items")
    op.drop_table("production_queue_items")
