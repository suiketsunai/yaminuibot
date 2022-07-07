"""Add twitter_style column to User table

Revision ID: 983997dbb0e4
Revises: 02d6cb723028
Create Date: 2022-07-07 18:03:51.013823

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "983997dbb0e4"
down_revision = "02d6cb723028"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "user",
        sa.Column(
            "twitter_style",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade():
    op.drop_column("user", "twitter_style")
