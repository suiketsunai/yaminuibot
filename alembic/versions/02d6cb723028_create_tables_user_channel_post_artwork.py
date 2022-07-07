"""Create tables User, Channel, Post, ArtWork

Revision ID: 02d6cb723028
Revises: 
Create Date: 2022-06-21 21:13:12.531592

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "02d6cb723028"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "artwork",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            nullable=False,
        ),
        sa.Column("aid", sa.BigInteger(), nullable=False),
        sa.Column("type", sa.Integer(), nullable=False),
        sa.Column("files", sa.ARRAY(sa.String(), dimensions=1), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("type", "aid", name="uix_artwork"),
    )
    op.create_table(
        "user",
        sa.Column("id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("full_name", sa.String(), nullable=True),
        sa.Column("nick_name", sa.String(), nullable=True),
        sa.Column("media_mode", sa.Boolean(), nullable=False),
        sa.Column("reply_mode", sa.Boolean(), nullable=False),
        sa.Column("forward_mode", sa.Boolean(), nullable=False),
        sa.Column("pixiv_style", sa.Integer(), nullable=False),
        sa.Column("last_info", sa.JSON(), nullable=True),
        sa.Column("is_banned", sa.Boolean(), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "channel",
        sa.Column("id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("link", sa.String(), nullable=True),
        sa.Column("is_admin", sa.Boolean(), nullable=False),
        sa.Column("admin_id", sa.BigInteger(), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["admin_id"],
            ["user.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "post",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            nullable=False,
        ),
        sa.Column("artwork_id", sa.BigInteger(), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("post_id", sa.BigInteger(), nullable=False),
        sa.Column("post_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_original", sa.Boolean(), nullable=False),
        sa.Column("is_forwarded", sa.Boolean(), nullable=False),
        sa.Column("forwarded_channel_id", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(
            ["artwork_id"],
            ["artwork.id"],
        ),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["channel.id"],
        ),
        sa.ForeignKeyConstraint(
            ["forwarded_channel_id"],
            ["channel.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("channel_id", "post_id", name="uix_post"),
    )


def downgrade():
    op.drop_table("post")
    op.drop_table("channel")
    op.drop_table("user")
    op.drop_table("artwork")
