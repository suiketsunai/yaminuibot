from sqlalchemy import (
    ForeignKey,
    Integer,
    Column,
    BigInteger,
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

# base class
Base = declarative_base(cls=RepresentableBase)


class Channel(Base):
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
    # if bot is admin
    is_admin = Column(Boolean, default=False, nullable=False)
    # 1-1 Admin with Channels
    admin = relationship("User", back_populates="channel")
    # FK: admin user
    admin_id = Column(BigInteger, ForeignKey("user.id"))
    # last post number
    last_post = Column(BigInteger)


class User(Base):
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
    # 1-1 Admin with Channel
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
    # TODO: replace 1 and 2 with values
    @validates("pixiv_style")
    def validate_forwarding(self, key, value):
        if value < 0 or value > 1:
            raise ValueError(f"Invalid value for field {key!r}.")
        return value

    # all info about the last link, depending on the type
    last_info = Column(JSON)
    # in case if user should be banned
    is_banned = Column(Boolean, default=False, nullable=False)
