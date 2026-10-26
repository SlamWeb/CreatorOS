"""Independent Topic production tasks; historical Run rows retain a null scope."""
from alembic import op
import sqlalchemy as sa

revision = "20261027_0020"
down_revision = "20261024_0019"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("production_tasks",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("topic_id", sa.String(80), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("brief", sa.Text(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("collection_id", sa.String(36), nullable=True),
        sa.Column("create_request_id", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("position >= 0", name="position_nonnegative"),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["collection_id"], ["media_collections.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"))
    op.create_index("ix_production_tasks_topic_id", "production_tasks", ["topic_id"])
    op.create_index("ix_production_tasks_create_request_id", "production_tasks", ["create_request_id"])
    # SQLite permits an additive nullable REFERENCES column without rebuilding
    # content_runs and its existing Revision/Attempt/queue foreign-key graph.
    if op.get_bind().dialect.name == "sqlite":
        op.execute("ALTER TABLE content_runs ADD COLUMN task_id VARCHAR(36) REFERENCES production_tasks(id)")
    else:
        op.add_column("content_runs", sa.Column("task_id", sa.String(36), sa.ForeignKey("production_tasks.id")))
    op.create_index("ix_content_runs_task_id", "content_runs", ["task_id"])


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM production_tasks")):
        raise RuntimeError("Production tasks exist; downgrade would discard production history.")
    op.drop_index("ix_content_runs_task_id", table_name="content_runs")
    op.drop_column("content_runs", "task_id")
    op.drop_index("ix_production_tasks_create_request_id", table_name="production_tasks")
    op.drop_index("ix_production_tasks_topic_id", table_name="production_tasks")
    op.drop_table("production_tasks")
