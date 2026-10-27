"""Durable agent plans with independent, dependency-aware steps."""
from alembic import op
import sqlalchemy as sa

revision = "20261027_0024"
down_revision = "20261027_0023"
branch_labels = depends_on = None


def upgrade():
    op.create_table("agent_plans",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("session_id", sa.String(120), nullable=False),
        sa.Column("creator_id", sa.String(80), sa.ForeignKey("creators.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("studio_url", sa.String(200), nullable=False),
        sa.Column("continuation_state", sa.String(30), nullable=False),
        sa.Column("continuation_version", sa.Integer(), nullable=False),
        sa.Column("continuation_token", sa.String(120), nullable=True),
        sa.Column("continuation_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_agent_plans_session_id", "agent_plans", ["session_id"])
    op.create_table("agent_plan_steps",
        sa.Column("id", sa.String(120), primary_key=True),
        sa.Column("plan_id", sa.String(64), sa.ForeignKey("agent_plans.id", ondelete="CASCADE"), nullable=False),
        sa.Column("step_key", sa.String(64), nullable=False),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("tool", sa.String(100), nullable=False),
        sa.Column("arguments_json", sa.JSON(), nullable=False),
        sa.Column("depends_on_json", sa.JSON(), nullable=False),
        sa.Column("wait_for_completion", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("plan_id", "step_key", name="uq_agent_plan_steps_plan_key"))
    op.create_index("ix_agent_plan_steps_plan_id", "agent_plan_steps", ["plan_id"])


def downgrade():
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT count(*) FROM agent_plans")).scalar():
        raise RuntimeError("请先保留并处理 Agent 计划记录。")
    op.drop_table("agent_plan_steps")
    op.drop_table("agent_plans")
