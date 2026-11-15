"""Private confirmed screenshot archive and personal best per mode.

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "screenshot_matches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("telegram_id", sa.BigInteger(), sa.ForeignKey("users.telegram_id", ondelete="CASCADE"), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("dedup_key", sa.String(64), nullable=False),
        sa.Column("match_id", sa.String(128)),
        sa.Column("nickname", sa.String(80), nullable=False),
        *[sa.Column(field, sa.Integer(), nullable=False) for field in ("kills", "deaths", "assists")],
        sa.Column("rating", sa.Numeric(14, 8), nullable=False),
        sa.Column("formula_version", sa.String(32), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("telegram_id", "mode", "dedup_key", name="uq_screenshot_owner_mode_key"),
    )
    op.create_index("ix_screenshot_owner_mode_created", "screenshot_matches", ["telegram_id", "mode", "created_at"])
    op.create_table(
        "personal_bests",
        sa.Column("telegram_id", sa.BigInteger(), sa.ForeignKey("users.telegram_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("mode", sa.String(20), primary_key=True),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("source_key", sa.String(256), nullable=False),
        sa.Column("screenshot_id", sa.Integer(), sa.ForeignKey("screenshot_matches.id", ondelete="SET NULL")),
        sa.Column("rating", sa.Numeric(14, 8), nullable=False),
        sa.Column("formula_version", sa.String(32), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("personal_bests")
    op.drop_table("screenshot_matches")
