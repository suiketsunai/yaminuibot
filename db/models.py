"""Database"""
from sqlalchemy import (
    UniqueConstraint,
    ForeignKey,
    Integer,
    Column,
    BigInteger,
    DateTime,
    String,
    Boolean,
    JSON,
)
from sqlalchemy.orm import (
    relationship,
    declarative_base,
    validates,
    column_property,
)

# pretty __repr__ and __str__
from sqlalchemy_repr import RepresentableBase

# import pixiv and styles
from extra import PixivStyle, LinkType

# base class
Base = declarative_base(cls=RepresentableBase)


class Channel(Base):
    """Table for storing telegram channel data"""

    __tablename__ = "channel"
    # channel public id
    id = Column(BigInteger, primary_key=True, autoincrement=False)

    @validates("id")
    def _write_once_id(self, key, value):
        if self.id:
            raise ValueError(f"Field {key!r} is write-once.")
        elif not value < 0:
            raise ValueError(f"Field {key!r} can't be positive number.")
        return value

    # channel internal id
    cid = column_property(-(id + 10**12))

    @validates("cid")
    def _read_only_cid(self, key):
        raise ValueError(f"Field {key!r} is read-only.")

    # channel name
    name = Column(String)
    # channel link
    link = Column(String)
    # if bot is admin
    is_admin = Column(Boolean, default=False, nullable=False)
    # RL: 1-1 Admin with Channels
    admin = relationship("User", back_populates="channel")
    # FK: admin user
    admin_id = Column(BigInteger, ForeignKey("user.id"))
    # last post number
    last_post = Column(BigInteger)
    # RL: 1-M Channnel with artworks
    artworks = relationship(
        "ArtWork",
        back_populates="channel",
        foreign_keys="ArtWork.channel_id",
    )
    # RL: 1-M Channnel reposts
    reposts = relationship(
        "ArtWork",
        back_populates="forwarded_channel",
        foreign_keys="ArtWork.forwarded_channel_id",
    )
    # if channel was deleted
    is_deleted = Column(Boolean, default=False, nullable=False)


class User(Base):
    """Table for storing telegram user data"""

    __tablename__ = "user"
    # telegram account id
    id = Column(BigInteger, primary_key=True, autoincrement=False)

    @validates("id")
    def _write_id_once(self, key, value):
        if getattr(self, key):
            raise ValueError(f"Field {key!r} is write-once.")
        return value

    # full name = first name + last name
    full_name = Column(String)
    # nick name if available
    nick_name = Column(String)
    # RL: 1-1 Admin with Channel
    channel = relationship("Channel", back_populates="admin", uselist=False)
    # enable posting video and gifs
    media_mode = Column(Boolean, default=False, nullable=False)
    # enable replying to sent links?
    reply_mode = Column(Boolean, default=True, nullable=False)
    # enable forwarding to channel?
    forward_mode = Column(Boolean, default=False, nullable=False)

    @validates("forward_mode")
    def validate_forwarding(self, key, value):
        if value and not self.channel:
            raise ValueError(f"Field 'channel' is empty. Can't update {key!r}.")
        return value

    # pixiv style
    pixiv_style = Column(Integer, default=1, nullable=False)

    @validates("pixiv_style")
    def validate_pixiv_style(self, key, value):
        if PixivStyle.validate(value):
            return value
        raise ValueError(f"Invalid value {value!r} for field {key!r}.")

    # all info about the last link, depending on the type
    last_info = Column(JSON)
    # in case if user should be banned
    is_banned = Column(Boolean, default=False, nullable=False)
    # if user was deleted
    is_deleted = Column(Boolean, default=False, nullable=False)


class ArtWork(Base):
    """Table for storing channel post with artwork data"""

    __tablename__ = "artwork"
    # artwork record id
    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    # twitter or pixiv?
    type = Column(Integer, nullable=False)

    @validates("type")
    def validate_type(self, key, value):
        if LinkType.validate(value):
            return value
        raise ValueError(f"Invalid value {value!r} for field {key!r}.")

    # artwork id
    aid = Column(BigInteger, nullable=False)
    # FK: channel that post is from
    channel_id = Column(BigInteger, ForeignKey("channel.id"), nullable=False)
    # RL: M-1 Artwork in Channels
    channel = relationship(
        "Channel",
        back_populates="artworks",
        foreign_keys=[channel_id],
    )
    # channel post id
    post_id = Column(BigInteger, nullable=False)
    # dote when artwork posted
    post_date = Column(DateTime(timezone=True))
    # is this post original or not?
    is_original = Column(Boolean, default=True, nullable=False)
    # if forwarded and not in db
    is_forwarded = Column(Boolean, default=False, nullable=False)
    # FK: channel that post is forwarded from
    forwarded_channel_id = Column(BigInteger, ForeignKey("channel.id"))
    # RL: M-1 Forwarded Artworks from Channel
    forwarded_channel = relationship(
        "Channel",
        back_populates="reposts",
        foreign_keys=[forwarded_channel_id],
    )
    # files
    files = Column(JSON)

    # add unique constraints
    __table_args__ = (
        # no double posts
        UniqueConstraint("channel_id", "post_id", name="uix_post"),
        # ideally:
        # UniqueConstraint("type", "aid", name="uix_artwork"),
    )
