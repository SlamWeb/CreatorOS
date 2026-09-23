"""series skill composition contract: nullable creator, mind/production pair, revision

- 栏目可以先不分配账号（creator_id 可空），生产前再归属。
- 栏目绑定要么是旧单 Skill（skill_name），要么是完整 mind+production 组合，
  禁止只有半套；旧栏目保持 skill_name，不伪造组合。
- revision 作为栏目配置的乐观并发版本，由 ORM version_id_col 自增。
- SQLite 唯一约束视 NULL 互不相同；未分配栏目的同名去重由部分唯一索引兜底。
"""

from alembic import op
import sqlalchemy as sa


revision = "20260923_0005"
down_revision = "20260904_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("series") as batch:
        batch.alter_column("creator_id", existing_type=sa.String(length=80), nullable=True)
        batch.alter_column("skill_name", existing_type=sa.String(length=120), nullable=True)
        batch.add_column(sa.Column("mind_skill_id", sa.String(length=120), nullable=True))
        batch.add_column(sa.Column("production_skill_id", sa.String(length=120), nullable=True))
        batch.add_column(sa.Column("revision", sa.Integer(), nullable=True))
    op.execute("UPDATE series SET revision = 1 WHERE revision IS NULL")
    with op.batch_alter_table("series") as batch:
        batch.alter_column("revision", existing_type=sa.Integer(), nullable=False)
        batch.create_check_constraint(
            "skill_binding_shape",
            "(skill_name IS NOT NULL AND mind_skill_id IS NULL AND production_skill_id IS NULL) "
            "OR (skill_name IS NULL AND mind_skill_id IS NOT NULL AND production_skill_id IS NOT NULL)",
        )
        batch.create_check_constraint("revision_positive", "revision > 0")
    op.create_index(
        "uq_series_unassigned_name",
        "series",
        ["name"],
        unique=True,
        sqlite_where=sa.text("creator_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_series_unassigned_name", table_name="series")
    with op.batch_alter_table("series") as batch:
        batch.drop_constraint("skill_binding_shape", type_="check")
        batch.drop_constraint("revision_positive", type_="check")
        batch.drop_column("revision")
        batch.drop_column("production_skill_id")
        batch.drop_column("mind_skill_id")
    # 未分配或双 Skill 栏目无法落回旧非空结构，降级时明确删除而不是留下脏数据。
    op.execute("DELETE FROM series WHERE creator_id IS NULL OR skill_name IS NULL")
    with op.batch_alter_table("series") as batch:
        batch.alter_column("creator_id", existing_type=sa.String(length=80), nullable=False)
        batch.alter_column("skill_name", existing_type=sa.String(length=120), nullable=False)
