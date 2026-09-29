"""manual publication receipt and append-only metric snapshots"""

from alembic import op
import sqlalchemy as sa

revision = "20260930_0007"
down_revision = "20260923_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "manual_publications",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("content_run_id", sa.String(36), sa.ForeignKey("content_runs.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("revision_id", sa.String(36), nullable=False),
        sa.Column("artifact_digest", sa.String(64), nullable=False),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("post_url", sa.String(1000), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("platform = 'xiaohongshu'", name="platform_values"),
    )
    op.create_table(
        "publication_metrics",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("publication_id", sa.String(36), sa.ForeignKey("manual_publications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=False, unique=True),
        sa.Column("views", sa.Integer()),
        sa.Column("likes", sa.Integer()),
        sa.Column("favorites", sa.Integer()),
        sa.Column("comments", sa.Integer()),
        sa.Column("shares", sa.Integer()),
        sa.Column("measured_at", sa.DateTime(timezone=True), nullable=False),
        *(sa.CheckConstraint(f"{name} IS NULL OR {name} >= 0", name=f"{name}_nonnegative")
          for name in ("views", "likes", "favorites", "comments", "shares")),
    )
    op.create_index("ix_publication_metrics_publication_id", "publication_metrics", ["publication_id"])


def downgrade() -> None:
    op.drop_index("ix_publication_metrics_publication_id", table_name="publication_metrics")
    op.drop_table("publication_metrics")
    op.drop_table("manual_publications")
