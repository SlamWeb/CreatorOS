"""Keep calibration evidence separate from live shared Skills and production."""
from alembic import op
import sqlalchemy as sa

revision = "20261020_0014"
down_revision = "20261018_0013"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("skill_calibrations",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("request_id", sa.String(128), unique=True, nullable=False),
        sa.Column("input_digest", sa.String(64), nullable=False),
        sa.Column("series_id", sa.String(80), sa.ForeignKey("series.id"), nullable=False),
        sa.Column("creator_id", sa.String(80)),
        sa.Column("skill_id", sa.String(120), nullable=False),
        sa.Column("base_skill_digest", sa.String(64), nullable=False),
        sa.Column("job_id", sa.String(64), unique=True, nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("apply_json", sa.JSON()),
        sa.Column("applied_digest", sa.String(64)),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('draft','applying','applied')", name="status_values"))
    op.create_index("ix_skill_calibrations_series_id", "skill_calibrations", ["series_id"])


def downgrade():
    op.drop_index("ix_skill_calibrations_series_id", table_name="skill_calibrations")
    op.drop_table("skill_calibrations")
