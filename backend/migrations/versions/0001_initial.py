"""Initial normalized schema.

Revision ID: 0001
Revises:
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("players", sa.Column("uid", sa.String(32), primary_key=True),
                    sa.Column("source_id", sa.String(128), unique=True, nullable=False),
                    sa.Column("nickname", sa.String(128), nullable=False), sa.Column("avatar_url", sa.String(2048)),
                    sa.Column("current_mmr", sa.JSON(), nullable=False),
                    sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("users", sa.Column("telegram_id", sa.BigInteger(), primary_key=True),
                    sa.Column("primary_uid", sa.String(32), sa.ForeignKey("players.uid")),
                    sa.Column("mode", sa.String(20), nullable=False),
                    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
                    sa.Column("last_active", sa.DateTime(timezone=True), nullable=False))
    op.create_table("auth_sessions", sa.Column("token_hash", sa.String(64), primary_key=True),
                    sa.Column("telegram_id", sa.BigInteger(), sa.ForeignKey("users.telegram_id"), nullable=False),
                    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_auth_sessions_telegram_id", "auth_sessions", ["telegram_id"])
    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])
    op.create_table("matches", sa.Column("id", sa.String(128), primary_key=True),
                    sa.Column("mode", sa.String(20), nullable=False), sa.Column("map", sa.String(128)),
                    sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
                    sa.Column("score", sa.JSON()), sa.Column("completed", sa.Boolean(), nullable=False))
    op.create_index("ix_matches_mode", "matches", ["mode"])
    op.create_index("ix_matches_ended_at", "matches", ["ended_at"])
    op.create_table("participants", sa.Column("id", sa.Integer(), primary_key=True),
                    sa.Column("match_id", sa.String(128), sa.ForeignKey("matches.id", ondelete="CASCADE"), nullable=False),
                    sa.Column("player_uid", sa.String(32), sa.ForeignKey("players.uid"), nullable=False),
                    sa.Column("mode", sa.String(20), nullable=False),
                    sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False), sa.Column("team", sa.String(32)),
                    sa.Column("result", sa.String(12)),
                    *[sa.Column(key, sa.Integer()) for key in ("kills", "deaths", "assists", "headshots", "rounds", "damage", "rank", "game_score", "mmr_before", "mmr_after")],
                    sa.Column("rating", sa.Numeric(14, 8)), sa.Column("rating_tier", sa.String(32)),
                    sa.Column("formula_version", sa.String(32), nullable=False),
                    sa.Column("rating_inputs", sa.JSON(), nullable=False),
                    sa.Column("rating_parameters", sa.JSON(), nullable=False),
                    sa.Column("rating_factors", sa.JSON(), nullable=False),
                    sa.UniqueConstraint("match_id", "player_uid", name="uq_participant_match_player"))
    op.create_index("ix_participant_recent", "participants", ["player_uid", "mode", "ended_at"])
    op.create_table("observations", sa.Column("id", sa.Integer(), primary_key=True),
                    sa.Column("player_uid", sa.String(32), sa.ForeignKey("players.uid"), nullable=False),
                    sa.Column("mode", sa.String(20), nullable=False),
                    *[sa.Column(key, sa.DateTime(timezone=True)) for key in ("updated_at", "oldest_seen", "newest_seen")],
                    sa.Column("last_match_ids", sa.JSON(), nullable=False),
                    sa.Column("history_complete", sa.Boolean(), nullable=False),
                    sa.Column("source_error", sa.Text()), sa.Column("next_cursor", sa.String(256)),
                    sa.UniqueConstraint("player_uid", "mode", name="uq_observation_player_mode"))
    op.create_table("history_gaps", sa.Column("id", sa.Integer(), primary_key=True),
                    sa.Column("player_uid", sa.String(32), sa.ForeignKey("players.uid"), nullable=False),
                    sa.Column("mode", sa.String(20), nullable=False),
                    sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
                    sa.Column("after_at", sa.DateTime(timezone=True)), sa.Column("before_at", sa.DateTime(timezone=True)),
                    sa.Column("reason", sa.String(256), nullable=False))
    op.create_index("ix_history_gaps_player_uid", "history_gaps", ["player_uid"])
    op.create_table("refresh_jobs", sa.Column("id", sa.Integer(), primary_key=True),
                    sa.Column("player_uid", sa.String(32), sa.ForeignKey("players.uid"), nullable=False),
                    sa.Column("mode", sa.String(20), nullable=False), sa.Column("state", sa.String(16), nullable=False),
                    sa.Column("attempts", sa.Integer(), nullable=False),
                    sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
                    sa.Column("started_at", sa.DateTime(timezone=True)), sa.Column("finished_at", sa.DateTime(timezone=True)),
                    sa.Column("error", sa.Text()),
                    sa.UniqueConstraint("player_uid", "mode", name="uq_refresh_player_mode"))
    op.create_index("ix_refresh_jobs_state", "refresh_jobs", ["state"])


def downgrade():
    for table in ("refresh_jobs", "history_gaps", "observations", "participants", "matches", "auth_sessions", "users", "players"):
        op.drop_table(table)
