"""Persist bounded Skill iterations without modifying existing business rows."""
from alembic import op
import sqlalchemy as sa

revision = "20261022_0016"
down_revision = "20261020_0015"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("skill_iterations",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("request_id", sa.String(128), unique=True, nullable=False),
        sa.Column("input_digest", sa.String(64), nullable=False),
        sa.Column("creator_id", sa.String(80), nullable=False),
        sa.Column("series_id", sa.String(80), nullable=False),
        sa.Column("skill_id", sa.String(120), nullable=False),
        sa.Column("topic_id", sa.String(80), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("state_json", sa.JSON(), nullable=False),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    for field in ("creator_id", "series_id", "status"):
        op.create_index(f"ix_skill_iterations_{field}", "skill_iterations", [field])


def downgrade():
    for field in ("creator_id", "series_id", "status"):
        op.drop_index(f"ix_skill_iterations_{field}", table_name="skill_iterations")
    op.drop_table("skill_iterations")
