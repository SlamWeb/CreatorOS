"""Add a project BGM pool alongside immutable account materials."""
from alembic import op
import sqlalchemy as sa

revision = "20261117_0031"
down_revision = "20261109_0030"
branch_labels = None
depends_on = None


def timestamps():
    return (sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))


def upgrade():
    op.create_table(
        "global_bgm_tracks",
        sa.Column("id", sa.String(68), primary_key=True),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("suffix", sa.String(10), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=False),
        sa.Column("source_type", sa.String(20), nullable=False),
        sa.Column("source_url", sa.Text()),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column("analysis_status", sa.String(20), nullable=False),
        sa.Column("analysis_message", sa.Text()),
        sa.Column("analysis_json", sa.JSON()),
        sa.Column("legacy_analysis_json", sa.JSON()),
        *timestamps(),
    )
    op.create_table(
        "global_bgm_legacy_sources",
        sa.Column("material_id", sa.String(64), sa.ForeignKey("managed_materials.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("track_id", sa.String(68), sa.ForeignKey("global_bgm_tracks.id", ondelete="RESTRICT"), nullable=False),
    )
    op.create_index("ix_global_bgm_legacy_sources_track_id", "global_bgm_legacy_sources", ["track_id"])
    op.create_table(
        "series_bgm_tracks",
        sa.Column("series_id", sa.String(80), sa.ForeignKey("series.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("track_id", sa.String(68), sa.ForeignKey("global_bgm_tracks.id", ondelete="RESTRICT"), primary_key=True),
        *timestamps(),
    )
    op.create_index("ix_series_bgm_tracks_track_id", "series_bgm_tracks", ["track_id"])
    op.create_table(
        "global_bgm_imports",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("media_url", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("message", sa.Text()),
        sa.Column("track_id", sa.String(68), sa.ForeignKey("global_bgm_tracks.id", ondelete="RESTRICT")),
        *timestamps(),
    )


def downgrade():
    for table in ("global_bgm_imports", "series_bgm_tracks", "global_bgm_legacy_sources", "global_bgm_tracks"):
        if op.get_bind().execute(sa.text(f"SELECT 1 FROM {table} LIMIT 1")).first():
            raise RuntimeError("Cannot downgrade a populated project BGM pool.")
    for table in ("global_bgm_imports", "series_bgm_tracks", "global_bgm_legacy_sources", "global_bgm_tracks"):
        op.drop_table(table)
