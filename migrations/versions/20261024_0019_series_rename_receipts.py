"""Allow successful Series rename receipts without changing historical records."""
from alembic import op
import sqlalchemy as sa

revision = "20261024_0019"
down_revision = "20261024_0018"
branch_labels = None
depends_on = None

_OLD = "'create_series', 'update_composition', 'assign_series', 'queue_topics', 'delete_series', 'edit_topic'"


def upgrade():
    with op.batch_alter_table("write_receipts", recreate="always") as batch:
        batch.drop_constraint("write_operation_values", type_="check")
        batch.create_check_constraint("write_operation_values", f"operation IN ({_OLD}, 'rename_series')")


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM write_receipts WHERE operation='rename_series'")):
        raise RuntimeError("Series rename receipts exist; this database cannot be downgraded safely.")
    with op.batch_alter_table("write_receipts", recreate="always") as batch:
        batch.drop_constraint("write_operation_values", type_="check")
        batch.create_check_constraint("write_operation_values", f"operation IN ({_OLD})")
