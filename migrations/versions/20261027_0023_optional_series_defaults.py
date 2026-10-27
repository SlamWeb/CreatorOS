"""Allow series with only a stage Skill pool, without legacy fixed bindings."""
from alembic import op
import sqlalchemy as sa

revision = "20261027_0023"
down_revision = "20261027_0022"
branch_labels = None
depends_on = None

OLD = "(skill_name IS NOT NULL AND mind_skill_id IS NULL AND production_skill_id IS NULL) OR (skill_name IS NULL AND mind_skill_id IS NOT NULL AND production_skill_id IS NOT NULL)"
NEW = OLD + " OR (skill_name IS NULL AND mind_skill_id IS NULL AND production_skill_id IS NULL)"


def upgrade():
    with op.batch_alter_table("series") as batch:
        batch.drop_constraint(op.f("ck_series_skill_binding_shape"), type_="check")
        batch.create_check_constraint(op.f("ck_series_skill_binding_shape"), NEW)


def downgrade():
    count = op.get_bind().scalar(sa.text("SELECT count(*) FROM series WHERE skill_name IS NULL AND mind_skill_id IS NULL AND production_skill_id IS NULL"))
    if count:
        raise RuntimeError("Stage-only series exist; select legacy defaults before downgrade.")
    with op.batch_alter_table("series") as batch:
        batch.drop_constraint(op.f("ck_series_skill_binding_shape"), type_="check")
        batch.create_check_constraint(op.f("ck_series_skill_binding_shape"), OLD)
