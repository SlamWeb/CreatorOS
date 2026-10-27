"""Account Skill references and series pools without rewriting legacy bindings."""
from alembic import op
import sqlalchemy as sa

revision = "20261027_0021"
down_revision = "20261027_0020"
branch_labels = None
depends_on = None


def upgrade():
    for name, owner, target, extra in (
        ("creator_skill_refs", "creator_id", "creators.id", "request_ids_json"),
        ("series_skill_refs", "series_id", "series.id", "defaults_json"),
    ):
        op.create_table(name,
            sa.Column(owner, sa.String(80), nullable=False),
            sa.Column("skill_ids_json", sa.JSON(), nullable=False),
            sa.Column(extra, sa.JSON(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint("version > 0", name="version_positive"),
            sa.ForeignKeyConstraint([owner], [target], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint(owner))


def downgrade():
    for name in ("series_skill_refs", "creator_skill_refs"):
        if op.get_bind().scalar(sa.text(f"SELECT count(*) FROM {name}")):
            raise RuntimeError("Skill library references exist; downgrade would discard them.")
        op.drop_table(name)
