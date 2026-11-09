"""Optional series ownership for newly created media collections."""
from alembic import op
import sqlalchemy as sa

revision = "20261109_0030"
down_revision = "20261107_0029"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("media_collections", sa.Column("series_id", sa.String(80), nullable=True))


def downgrade():
    if op.get_bind().execute(sa.text(
        "SELECT 1 FROM media_collections WHERE series_id IS NOT NULL LIMIT 1"
    )).first():
        raise RuntimeError("Cannot downgrade: media collections have explicit series ownership. Preserve or migrate their ownership first.")
    op.drop_column("media_collections", "series_id")
