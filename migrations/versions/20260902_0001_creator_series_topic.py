"""create creator series and topic tables"""

from alembic import op
import sqlalchemy as sa

revision = "20260902_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "creators",
        sa.Column("id", sa.String(length=80), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column(
            "platform",
            sa.Enum("xiaohongshu", name="creator_platform", native_enum=False),
            nullable=False,
        ),
        sa.Column("account_handle", sa.String(length=160), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("daily_content_limit", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "daily_content_limit IS NULL OR daily_content_limit > 0",
            name="daily_content_limit_positive",
        ),
        sa.CheckConstraint("platform IN ('xiaohongshu')", name="platform_values"),
        sa.PrimaryKeyConstraint("id", name="pk_creators"),
    )
    op.create_table(
        "series",
        sa.Column("id", sa.String(length=80), nullable=False),
        sa.Column("creator_id", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("audience", sa.Text(), nullable=False),
        sa.Column("skill_name", sa.String(length=120), nullable=False),
        sa.Column(
            "selection_policy",
            sa.Enum("approval", "auto", name="series_selection_policy", native_enum=False),
            nullable=False,
        ),
        sa.Column(
            "publish_policy",
            sa.Enum("approval", "auto", name="series_publish_policy", native_enum=False),
            nullable=False,
        ),
        sa.Column("replenish_threshold", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "replenish_threshold > 0",
            name="replenish_threshold_positive",
        ),
        sa.CheckConstraint(
            "selection_policy IN ('approval', 'auto')", name="selection_policy_values"
        ),
        sa.CheckConstraint(
            "publish_policy IN ('approval', 'auto')", name="publish_policy_values"
        ),
        sa.ForeignKeyConstraint(
            ["creator_id"], ["creators.id"], name="fk_series_creator_id_creators", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_series"),
        sa.UniqueConstraint("creator_id", "name", name="uq_series_creator_id"),
    )
    op.create_index("ix_series_creator_id", "series", ["creator_id"])
    op.create_table(
        "topics",
        sa.Column("id", sa.String(length=80), nullable=False),
        sa.Column("series_id", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("brief", sa.Text(), nullable=True),
        sa.Column(
            "source",
            sa.Enum("research", "manual", name="topic_source", native_enum=False),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "queued", "producing", "ready", "published", "failed", "skipped",
                name="topic_status",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("position > 0", name="position_positive"),
        sa.CheckConstraint("source IN ('research', 'manual')", name="source_values"),
        sa.CheckConstraint(
            "status IN ('queued', 'producing', 'ready', 'published', 'failed', 'skipped')",
            name="status_values",
        ),
        sa.ForeignKeyConstraint(
            ["series_id"], ["series.id"], name="fk_topics_series_id_series", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_topics"),
        sa.UniqueConstraint("series_id", "position", name="uq_topics_series_id"),
    )
    op.create_index("ix_topics_series_id", "topics", ["series_id"])


def downgrade() -> None:
    op.drop_index("ix_topics_series_id", table_name="topics")
    op.drop_table("topics")
    op.drop_index("ix_series_creator_id", table_name="series")
    op.drop_table("series")
    op.drop_table("creators")
