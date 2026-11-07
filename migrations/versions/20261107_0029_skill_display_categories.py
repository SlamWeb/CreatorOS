"""Local Skill library categories are independent from frozen versions."""
from alembic import op
import sqlalchemy as sa

revision = "20261107_0029"
down_revision = "20261103_0028"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "skill_display_categories",
        sa.Column("family_id", sa.String(120), primary_key=True),
        sa.Column("categories_json", sa.JSON(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.CheckConstraint("revision > 0", name="revision_positive"),
    )


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM skill_display_categories")):
        raise RuntimeError("存在 Skill 分类数据，不允许降级删除。")
    op.drop_table("skill_display_categories")
