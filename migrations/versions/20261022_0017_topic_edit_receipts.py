"""Allow successful Topic edit receipts; preserve existing business records."""
from alembic import op
import sqlalchemy as sa

revision = "20261022_0017"
down_revision = "20261022_0016"
branch_labels = None
depends_on = None

_OLD = "'create_series', 'update_composition', 'assign_series', 'queue_topics', 'delete_series'"


def upgrade():
    with op.batch_alter_table("write_receipts", recreate="always") as batch:
        batch.drop_constraint("write_operation_values", type_="check")
        batch.create_check_constraint("write_operation_values", f"operation IN ({_OLD}, 'edit_topic')")


def downgrade():
    # Never silently erase edit evidence or rewrite an unsupported receipt.
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM write_receipts WHERE operation='edit_topic'")):
        raise RuntimeError("Topic edit receipts exist; this database cannot be downgraded safely.")
    with op.batch_alter_table("write_receipts", recreate="always") as batch:
        batch.drop_constraint("write_operation_values", type_="check")
        batch.create_check_constraint("write_operation_values", f"operation IN ({_OLD})")
