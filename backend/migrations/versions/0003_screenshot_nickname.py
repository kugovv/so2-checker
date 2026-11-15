"""Remember each Telegram user's confirmed screenshot nickname."""
from alembic import op
import sqlalchemy as sa

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('screenshot_nickname', sa.String(80), nullable=True))


def downgrade():
    op.drop_column('users', 'screenshot_nickname')
