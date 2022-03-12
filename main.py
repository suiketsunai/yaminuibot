import os
import re
import json
import logging

from pathlib import Path
from datetime import datetime
from collections import namedtuple

# working with env
from dotenv import load_dotenv

# reading setings
import tomli

# working with database
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, aliased

# database models
from db.models import User, Channel, ArtWork

# parsing datetime
from dateutil.parser import parse

# telegram core bot api extension
from telegram.ext import Updater

# current timestamp & this file directory
date_run = datetime.now()
file_dir = Path(__file__).parent

# load .env file & get config
load_dotenv()
config = tomli.load(Path(os.environ["PATH_SETTINGS"]).open("rb"))

# get logger
log = logging.getLogger("yaminuichan")

# session settings
engine = create_engine(
    os.environ["DATABASE_URI"],
    echo=True,
    echo_pool="debug",
    future=True,
)

################################################################################
# named tuples
################################################################################

Link = namedtuple("Link", ["type", "link", "id"])


################################################################################
# links
################################################################################

# link types
TWITTER = 0
PIXIV = 1

# link dictionary
src = {
    "twitter": {
        "re": r"""(?x)
            (?:
                (?:www\.)?
                (?:twitter\.com\/)
                (?P<author>.+?)\/
                (?:status\/)
            )
            (?P<id>\d+)
        """,
        "link": "twitter.com/{author}/status/{id}",
        "type": TWITTER,
    },
    "pixiv": {
        "re": r"""(?x)
            (?:
                (?:www\.)?
                (?:pixiv\.net\/)
                (?:\w{2}\/)?
                (?:artworks\/)
            )
            (?P<id>\d+)
        """,
        "link": "www.pixiv.net/artworks/{id}",
        "type": PIXIV,
    },
}


def setup_logging():
    # set basic config to logger
    logging.basicConfig(
        format=config["log"]["form"],
        level=logging.getLevelName(config["log"]["level"]),
    )
    # setup logging to file
    if config["log"]["file"]["enable"]:
        log.info("Logging to file enabled.")
        log_dir = file_dir / config["log"]["file"]["path"]
        if not log_dir.is_dir():
            log.warning("Log directory doesn't exist.")
            try:
                log.info("Creating log directory...")
                log_dir.mkdir()
                log.info(f"Created log directory: {log_dir.resolve()}.")
            except Exception as ex:
                log.error(f"Exception occured: {ex}")
                log.info("Can't execute program.")
                quit()
        log_date = date_run.strftime(config["log"]["file"]["date"])
        log_name = f'{config["log"]["file"]["pref"]}{log_date}.log'
        log_file = log_dir / log_name
        log.info(f"Logging to file: {log_name}")
        # add file handler
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(logging.Formatter(config["log"]["file"]["form"]))
        fh.setLevel(logging.getLevelName(config["log"]["file"]["level"]))
        logging.getLogger().addHandler(fh)
    else:
        log.info("Logging to file disabled.")


def row2dict(row):
    return {
        column.name: getattr(row, column.name)
        for column in row.__table__.columns
    }


def dumper(table, filename):
    src = Path(".src")
    with Session(engine) as s:
        (src / filename).with_suffix(".json").write_text(
            json.dumps(
                [row2dict(obj) for obj in s.query(table)],
                indent=4,
                default=str,
            )
        )


def dump_db():
    dumper(User, "users")
    dumper(Channel, "channels")
    dumper(ArtWork, "artworks")


def formatter(query: str):
    """Exctracts and formates links in text

    Args:
        query (str): text

    Returns:
        list: list of Links
    """
    response = []
    for re_key, re_type in src.items():
        for link in re.finditer(re_type["re"], query):
            # dictionary keys = format args
            _link = "https://" + re_type["link"].format(**link.groupdict())
            log.info(f"Inline: Received link {re_key}: {_link}.")
            # add to response list
            response.append(Link(re_type["type"], _link, int(link.group("id"))))
    return response


def check_message(message: dict):
    if message["type"] == "message":
        for item in message["text"]:
            if isinstance(item, dict) and item.get("type") == "link":
                return formatter(item["text"])
    return []


def migrate_db():
    src = Path(".src")
    users = json.loads((src / "users.json").read_bytes())
    channels = json.loads((src / "channels.json").read_bytes())
    # migrate all users and channels
    with Session(engine) as s:
        for user in users:
            s.add(User(**user))
        s.commit()

        for channel in channels:
            s.add(Channel(**channel))
        s.commit()
    # get directories
    dirs = [cid for cid in src.iterdir() if cid.is_dir()]
    with Session(engine) as s:
        chans = {str(channel.cid): channel for channel in s.query(Channel)}
        forwarded = {str(channel.cid): [] for channel in s.query(Channel)}
    # migrate all artworks
    for path in dirs:
        channel = chans[path.name]
        messages = json.loads((path / "result.json").read_bytes())["messages"]
        with Session(engine) as s:
            for message in messages:
                data = {
                    "post_id": message["id"],
                    "post_date": parse(message["date"]),
                    "channel": channel,
                }
                for artwork in check_message(message):
                    data.update({"aid": artwork.id, "type": artwork.type})
                    if f := message.get("forwarded_from", None):
                        data.update(
                            {
                                "is_forwarded": True,
                                "is_original": False,
                                "forwarded_channel": s.query(Channel)
                                .filter(Channel.name == f)
                                .first(),
                            }
                        )
                    s.add(ArtWork(**data))
            channel.last_post = messages[-1]["id"]
            s.commit()
    # find all first-posted artworks
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


def main() -> None:
    """Set up and run it"""
    # setup logging
    setup_logging()

    # migrate db if needed
    migrate_db()

    # create updater & dispatcher
    updater = Updater(
        os.environ["TOKEN"],
        request_kwargs={
            "read_timeout": 6,
            "connect_timeout": 7,
        },
    )

    # start bot
    updater.start_polling()

    # stop bot
    updater.idle()


if __name__ == "__main__":
    main()
