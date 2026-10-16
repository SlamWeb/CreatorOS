"""Confirmed, at-most-once dispatched publication requests."""
from alembic import op
import sqlalchemy as sa

revision = "20261016_0010"
down_revision = "20261009_0009"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("publication_accounts",
        sa.Column("creator_id", sa.String(80), sa.ForeignKey("creators.id"), primary_key=True),
        sa.Column("account_id", sa.String(120), nullable=False, unique=True),
        sa.Column("account_name", sa.String(200), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("publication_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("content_run_id", sa.String(36), sa.ForeignKey("content_runs.id"), nullable=False, unique=True),
        sa.Column("creator_id", sa.String(80), sa.ForeignKey("creators.id"), nullable=False),
        sa.Column("revision_id", sa.String(36), nullable=False),
        sa.Column("artifact_digest", sa.String(64), nullable=False),
        sa.Column("account_id", sa.String(120), nullable=False),
        sa.Column("account_name", sa.String(200), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("content_digest", sa.String(64), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("post_url", sa.String(1000)),
        sa.Column("last_error", sa.Text()),
        sa.Column("events_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("account_id", "content_digest"),
        sa.CheckConstraint("status IN ('draft','sending','verifying','unknown','published')", name="status_values"))


def downgrade():
    op.drop_table("publication_requests")
    op.drop_table("publication_accounts")
