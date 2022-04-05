import json
import logging

from pathlib import Path

# working with database
from sqlalchemy.orm import Session, aliased

# working with timezone
from dateutil import tz

# parsing datetime
from dateutil.parser import parse

# database engine
from db import engine

# database models
from db.models import User, Channel, ArtWork

# namedtuples
from extra.namedtuples import Link

# formatter
from extra.helpers import formatter

# get logger
log = logging.getLogger("yaminuichan.migrate")


def check_message(message: dict) -> list[Link]:
    """Check if message has appropriate link in it

    Args:
        message (dict): Telegram channel message from exported json

    Returns:
        list[Link]: list of Links
    """
    result = []
    if message["type"] == "message":
        for item in message["text"]:
            if isinstance(item, dict) and item.get("type") == "link":
                result += formatter(item["text"])
    return result


def migrate_db() -> None:
    """Read exported jsons and insert data in database"""
    src = Path(".src")
    log.info("Reading User and Channel json files...")
    users = json.loads((src / "users.json").read_bytes())
    channels = json.loads((src / "channels.json").read_bytes())
    log.info("Done!")

    log.info("Inserting Users and Channels to database...")
    with Session(engine) as s:
        for user in users:
            s.add(User(**user))
        s.commit()

        for channel in channels:
            s.add(Channel(**channel))
        s.commit()
    log.info("Done!")

    log.info("Reading Channel directories...")
    dirs = [cid for cid in src.iterdir() if cid.is_dir()]
    log.info("Getting Channel list...")
    with Session(engine) as s:
        chans = {str(channel.cid): channel for channel in s.query(Channel)}
    log.info("Done!")

    log.info("Inserting ArtWorks to database...")
    for path in dirs:
        channel = chans[path.name]
        messages = json.loads((path / "result.json").read_bytes())["messages"]
        with Session(engine) as s:
            log.info("Channel: %s...", channel.name)
            for message in messages:
                data = {
                    "post_id": message["id"],
                    "post_date": parse(message["date"]).astimezone(tz.tzutc()),
                    "channel": channel,
                }
                for artwork in check_message(message):
                    data.update({"aid": artwork.id, "type": artwork.type})
                    if ch := message.get("forwarded_from", None):
                        data.update(
                            {
                                "is_forwarded": True,
                                "is_original": False,
                                "forwarded_channel": s.query(Channel)
                                .filter(Channel.name == ch)
                                .first(),
                            }
                        )
                    s.add(ArtWork(**data))
            channel.last_post = messages[-1]["id"]
            s.commit()
    log.info("Done!")

    log.info("Finding all not first-posted ArtWorks...")
    with Session(engine) as s:
        artl, artr = aliased(ArtWork), aliased(ArtWork)
        q = (
            s.query(artr)
            .join(
                artl,
                (artl.aid == artr.aid)
                & (artl.type == artr.type)
                & (artl.id != artr.id)
                & (artl.post_date < artr.post_date),
            )
            .order_by(artl.type, artl.aid, artl.post_date)
        )
        for post in q.all():
            post.is_original = False
        s.commit()
    log.info("Done!")
