"""Persist opt-in metric collection and retry windows; preserve all snapshots."""
from alembic import op
import sqlalchemy as sa

revision = "20261016_0011"
down_revision = "20261016_0010"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("publication_feedback_settings",
        sa.Column("creator_id", sa.String(80), sa.ForeignKey("creators.id"), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()))
    op.create_table("publication_feedback_states",
        sa.Column("request_id", sa.String(36), sa.ForeignKey("publication_requests.id"), primary_key=True),
        sa.Column("attempted_at", sa.DateTime(timezone=True)),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text()))


def downgrade():
    op.drop_table("publication_feedback_states")
    op.drop_table("publication_feedback_settings")
