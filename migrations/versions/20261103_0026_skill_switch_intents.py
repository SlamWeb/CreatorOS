"""Preserve intent across Skill file publication and series activation."""
from alembic import op
import sqlalchemy as sa

revision = "20261103_0026"
down_revision = "20261027_0025"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "skill_switch_intents",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("signature", sa.String(64), nullable=False),
        sa.Column("series_id", sa.String(80), nullable=False),
        sa.Column("creator_id", sa.String(80), nullable=True),
        sa.Column("skill_id", sa.String(120), nullable=False),
        sa.Column("expected_revision", sa.Integer(), nullable=False),
        sa.Column("expected_digest", sa.String(64), nullable=False),
        sa.Column("previous_skill_id", sa.String(120), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("scope_json", sa.JSON(), nullable=False),
        sa.Column("phase", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("phase IN ('prepared', 'committed')", name="skill_switch_intent_phase"),
    )
    op.create_index("ix_skill_switch_intents_series_id", "skill_switch_intents", ["series_id"])


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM skill_switch_intents")):
        raise RuntimeError("存在 Skill 切版意图，不允许删除恢复证据。")
    op.drop_index("ix_skill_switch_intents_series_id", table_name="skill_switch_intents")
    op.drop_table("skill_switch_intents")
