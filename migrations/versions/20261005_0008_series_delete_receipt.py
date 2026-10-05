"""allow idempotent series deletion receipts"""

from alembic import op


revision = "20261005_0008"
down_revision = "20260930_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("write_receipts", recreate="always") as batch_op:
        batch_op.drop_constraint("write_operation_values", type_="check")
        batch_op.create_check_constraint(
            "write_operation_values",
            "operation IN ('create_series', 'update_composition', 'assign_series', 'queue_topics', 'delete_series')",
        )


def downgrade() -> None:
    with op.batch_alter_table("write_receipts", recreate="always") as batch_op:
        batch_op.drop_constraint("write_operation_values", type_="check")
        batch_op.create_check_constraint(
            "write_operation_values",
            "operation IN ('create_series', 'update_composition', 'assign_series', 'queue_topics')",
        )
