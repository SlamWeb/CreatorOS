"""write receipts: idempotency anchor for direct writes

同一 request_id 的重复提交返回首次成功结果，不生成两份记录；
失败不留回执，客户端可用同一 request_id 安全重试。
"""

from alembic import op
import sqlalchemy as sa


revision = "20260923_0006"
down_revision = "20260923_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "write_receipts",
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("operation", sa.String(length=40), nullable=False),
        sa.Column("resource_id", sa.String(length=80), nullable=True),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.Column("response_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "operation IN ('create_series', 'update_composition', 'assign_series', 'queue_topics')",
            name="write_operation_values",
        ),
        sa.PrimaryKeyConstraint("request_id", name="pk_write_receipts"),
    )
    op.create_index("ix_write_receipts_resource_id", "write_receipts", ["resource_id"])


def downgrade() -> None:
    op.drop_index("ix_write_receipts_resource_id", table_name="write_receipts")
    op.drop_table("write_receipts")
