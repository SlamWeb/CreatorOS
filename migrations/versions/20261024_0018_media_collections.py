"""Add reference-only account media collections without rewriting business records."""
from alembic import op
import sqlalchemy as sa

revision = "20261024_0018"
down_revision = "20261022_0017"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("media_collections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("creator_id", sa.String(80), sa.ForeignKey("creators.id", ondelete="CASCADE"), nullable=False),
        sa.Column("topic_id", sa.String(80)),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("create_digest", sa.String(64), nullable=False),
        sa.Column("run_ids_json", sa.JSON(), nullable=False),
        sa.Column("order_ids_json", sa.JSON(), nullable=False),
        sa.Column("excluded_ids_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("creator_id", "request_id"),
        sa.CheckConstraint("version > 0", name="version_positive"))
    op.create_index("ix_media_collections_creator_id", "media_collections", ["creator_id"])


def downgrade():
    op.drop_index("ix_media_collections_creator_id", table_name="media_collections")
    op.drop_table("media_collections")
