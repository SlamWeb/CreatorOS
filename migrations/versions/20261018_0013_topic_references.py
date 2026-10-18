"""Keep topic reference ownership without rewriting existing production inputs."""
from alembic import op
import sqlalchemy as sa

revision = "20261018_0013"
down_revision = "20261017_0012"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("topics", sa.Column("reference_id", sa.String(64), nullable=True))


def downgrade():
    op.drop_column("topics", "reference_id")
