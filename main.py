"""Main module"""
import os
import re
import json
import logging

from pathlib import Path
from datetime import datetime
from itertools import cycle, islice, dropwhile
from collections import namedtuple

# working with env
from dotenv import load_dotenv

# reading setings
import tomli

# working with database
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, aliased

# parsing datetime
from dateutil.parser import parse

# telegram core bot api
from telegram import Message, Update

# telegram core bot api extension
from telegram.ext import (
    Updater,
    CallbackContext,
    MessageHandler,
    CommandHandler,
    ConversationHandler,
    Filters,
)

# database models
import db.models as db

# current timestamp & this file directory
date_run = datetime.now()
file_dir = Path(__file__).parent

# load .env file & get config
load_dotenv()
config = tomli.load(Path(os.environ["PATH_SETTINGS"]).open("rb"))

# session settings
engine = create_engine(
    os.environ["DATABASE_URI"],
    echo=True,
    echo_pool="debug",
    future=True,
)

################################################################################
# logger
################################################################################

# get logger
log = logging.getLogger("yaminuichan")


def setup_logging():
    """Set up logger"""
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
                log.info("Created log directory: '%s'.", log_dir.resolve())
            except Exception as ex:
                log.error("Exception occured: %s", ex)
                log.info("Can't execute program.")
                quit()
        log_date = date_run.strftime(config["log"]["file"]["date"])
        log_name = f'{config["log"]["file"]["pref"]}{log_date}.log'
        log_file = log_dir / log_name
        log.info("Logging to file: '%s'.", log_name)
        # add file handler
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(logging.Formatter(config["log"]["file"]["form"]))
        fh.setLevel(logging.getLevelName(config["log"]["file"]["level"]))
        logging.getLogger().addHandler(fh)
    else:
        log.info("Logging to file disabled.")


################################################################################
# named tuples
################################################################################

Link = namedtuple("Link", ["type", "link", "id"])


################################################################################
# links
################################################################################

# link dictionary
link_dict = {
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
        "type": db.TWITTER,
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
        "type": db.PIXIV,
    },
}

_switch = {
    True: "enabled",
    False: "disabled",
}

# states
states = (
    CHANNEL,
    TEST,
) = map(chr, range(2))

################################################################################
# file operations functions
################################################################################


def row2dict(row) -> dict:
    """Convert table row to dictionary

    Args:
        row (_type_): a row of table

    Returns:
        dict: row as dictionary
    """
    return {
        column.name: getattr(row, column.name)
        for column in row.__table__.columns
    }


def dumper(table, filename: str) -> None:
    """Helper function for dumping Tables into files

    Args:
        table (_type_): Table name for exporting
        filename (str): name for file to dump Table in
    """
    src = Path(".src")
    with Session(engine) as s:
        (src / filename).with_suffix(".json").write_text(
            json.dumps(
                [row2dict(obj) for obj in s.query(table)],
                indent=4,
                default=str,
            )
        )


def dump_db() -> None:
    """Dump database as it is"""
    dumper(db.User, "users")
    dumper(db.Channel, "channels")
    dumper(db.ArtWork, "artworks")


def formatter(query: str) -> list[Link]:
    """Exctract and format links in text

    Args:
        query (str): text

    Returns:
        list[Link]: list of Links
    """
    response = []
    for re_key, re_type in link_dict.items():
        for link in re.finditer(re_type["re"], query):
            # dictionary keys = format args
            _link = "https://" + re_type["link"].format(**link.groupdict())
            log.info("Inline: Received %s link: '%s'.", re_key, _link)
            # add to response list
            response.append(Link(re_type["type"], _link, int(link.group("id"))))
    return response


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
    users = json.loads((src / "users.json").read_bytes())
    channels = json.loads((src / "channels.json").read_bytes())
    # migrate all users and channels
    with Session(engine) as s:
        for user in users:
            s.add(db.User(**user))
        s.commit()

        for channel in channels:
            s.add(db.Channel(**channel))
        s.commit()
    # get directories
    dirs = [cid for cid in src.iterdir() if cid.is_dir()]
    with Session(engine) as s:
        chans = {str(channel.cid): channel for channel in s.query(db.Channel)}
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
                                "forwarded_channel": s.query(db.Channel)
                                .filter(db.Channel.name == f)
                                .first(),
                            }
                        )
                    s.add(db.ArtWork(**data))
            channel.last_post = messages[-1]["id"]
            s.commit()
    # find all first-posted artworks
    with Session(engine) as s:
        artl, artr = aliased(db.ArtWork), aliased(db.ArtWork)
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


################################################################################
# telegram helper function
################################################################################


def reply(update: Update, text: str, **kwargs) -> Message:
    """Reply to current message

    Args:
        update (Update): current update
        text (str): text to send in markdown v2

    Returns:
        Message: Telegram Message
    """
    return update.message.reply_markdown_v2(
        reply_to_message_id=update.message.message_id,
        text=text,
        **kwargs,
    )


def error(update: Update, text: str, **kwargs) -> Message:
    """Reply to current message with error

    Args:
        update (Update): current update
        text (str): text to send in markdown v2

    Returns:
        Message: Telegram Message
    """
    return update.message.reply_markdown_v2(
        reply_to_message_id=update.message.message_id,
        text="\\[`ERROR`\\] " + text,
        **kwargs,
    )


def notify(update: Update, *, command: str = None) -> None:
    """Log that something hapened

    Args:
        update (Update): current update
        command (str, optional): called command. Defaults to None.
    """
    if command:
        log.info(
            "%s command was called by %s [%s].",
            command,
            update.effective_user.full_name,
            update.effective_user.id,
        )


def toggler(update: Update, attr: str) -> bool:
    """Toggle state between True and False

    Args:
        update (Update): current update
        attr (str): attribute to change

    Returns:
        bool: new state
    """
    with Session(engine) as s:
        u = s.get(db.User, update.effective_chat.id)
        state = getattr(u, attr)
        setattr(u, attr, not state)
        s.commit()
        return not state


def channel_check(update: Update, context: CallbackContext) -> int:
    """Checks if channel is a valid choice"""
    mes = update.message
    if getattr(mes, "forward_from_chat"):
        channel = mes.forward_from_chat
        if channel.type == "supergroup":
            error(update, "This message is from a supergroup\\.")
        else:
            with Session(engine) as s:
                if (c := s.get(db.Channel, channel.id)) and c.admin:
                    error(update, "This channel is *already* owned\\.")
                else:
                    reply(
                        update,
                        "*Seems fine\\!* ✨\n"
                        "Checking for *admin rights*\\.\\.\\.",
                    )
                    if (
                        (
                            member_bot := context.bot.get_chat_member(
                                channel.id,
                                int(os.environ["TOKEN"].split(":")[0]),
                            )
                        )
                        and getattr(member_bot, "can_post_messages")
                        and (
                            member_user := context.bot.get_chat_member(
                                channel.id,
                                update.effective_chat.id,
                            )
                        )
                        and member_user.status in ["creator", "administrator"]
                    ):
                        # get current user
                        u = s.get(db.User, update.effective_chat.id)
                        # remove old channel
                        if c:
                            # channel already exist
                            u.channel = c
                        else:
                            # channel doesn't exist
                            u.channel = None
                            # create new channel
                            db.Channel(
                                id=channel.id,
                                name=channel.title,
                                is_admin=True,
                                admin=u,
                            )
                        # commit changes to database
                        s.commit()
                        reply(
                            update,
                            "*Done\\!* 🎉\n"
                            "*Your channel* is added to the database\\!",
                        )
                        del context.user_data[CHANNEL]
                        return ConversationHandler.END
                    else:
                        error(
                            update,
                            "Either *the bot* or *you* "
                            "are not an admin of this channel\\!",
                        )
    else:
        error(update, "Please, *forward* a message from *your channel*\\.")

    return CHANNEL


################################################################################
# telegram bot commands
################################################################################


def command_start(update: Update, _) -> None:
    """Start the bot"""
    notify(update, command="/start")
    with Session(engine) as s:
        if not s.get(db.User, update.effective_chat.id):
            s.add(
                db.User(
                    id=update.effective_chat.id,
                    full_name=update.effective_chat.full_name,
                    nick_name=update.effective_chat.username,
                )
            )
            s.commit()
    update.message.reply_markdown_v2(
        text=f"Hello, {update.effective_user.mention_markdown_v2()}\\!\n"
        "Nice to meet you\\! My name is *Nuiko Hayami*\\. ❄️\n"
        "Please, see \\/help to learn more about me\\!",
    )


def command_help(update: Update, _) -> None:
    """Send a message when the command /help is issued."""
    reply(update, Path(os.environ["HELP_FILE"]).read_text(encoding="utf-8"))


def command_channel(update: Update, context: CallbackContext) -> int:
    """Starts process of adding user's channel to their profile"""
    notify(update, command="/channel")
    if context.user_data.get(CHANNEL, None):
        reply(
            update,
            "*Ehm\\.\\.\\.*\n"
            "Please, forward a post from *your channel* already\\.",
        )
        return CHANNEL
    context.user_data[CHANNEL] = True
    reply(
        update,
        "*Sure\\!* 💫\n"
        "Please, add *this bot* to *your channel* as admin\\.\n"
        "Then, forward a message from *your channel* to me\\.",
    )
    return CHANNEL


def command_cancel(update: Update, context: CallbackContext) -> int:
    """Cancels and ends the conversation"""
    notify(update, command="/cancel")

    if context.user_data.get(CHANNEL, None):
        context.user_data[CHANNEL] = False
        reply(update, "*Okay\\!* 👌\nYou can add *your channel* at any time\\.")
        return ConversationHandler.END
    else:
        reply(update, "*Yeah, sure\\.* 👀\nCancel all you want\\.")


def command_forward(update: Update, _) -> None:
    """Enables/Disables forwarding to channel"""
    notify(update, command="/forward")
    reply(
        update,
        f"Forwarding mode is *{_switch[toggler(update, 'forward_mode')]}*\\.",
    )


def command_reply(update: Update, _) -> None:
    """Enables/Disables replying to messages"""
    notify(update, command="/reply")
    reply(
        update,
        f"Replying mode is *{_switch[toggler(update, 'reply_mode')]}*\\.",
    )


def command_media(update: Update, _) -> None:
    """Enables/Disables adding video/gif to links"""
    notify(update, command="/media")
    reply(
        update,
        f"Media mode is *{_switch[toggler(update, 'media_mode')]}*\\.",
    )


def command_style(update: Update, _) -> None:
    """Change pixiv style."""
    with Session(engine) as s:
        u = s.get(db.User, update.effective_chat.id)
        old_style = u.pixiv_style
        new_style = db.pixiv[(old_style + 1) % len(db.pixiv)]
        u.pixiv_style = new_style
        s.commit()
    match new_style:
        case 0:
            style = "\\[ `Image(s)` \\]\n\nLink"
        case 1:
            style = "\\[ `Image(s)` \\]\n\nArtwork \\| Author\nLink"
        case 2:
            style = "Artwork \\| Author\nLink"
    reply(update, f"Style has been changed to\\:\n\n{style}\\.")


################################################################################
# main body
################################################################################


def main() -> None:
    """Set up and run the bot"""
    # setup logging
    setup_logging()

    # migrate db if needed
    # migrate_db()

    # create updater & dispatcher
    updater = Updater(
        os.environ["TOKEN"],
        request_kwargs={
            "read_timeout": 6,
            "connect_timeout": 7,
        },
    )
    dispatcher = updater.dispatcher

    # start the bot
    dispatcher.add_handler(CommandHandler("start", command_start))

    # get help
    dispatcher.add_handler(CommandHandler("help", command_help))

    # toggle forwarding mode
    dispatcher.add_handler(CommandHandler("forward", command_forward))

    # toggle replying mode
    dispatcher.add_handler(CommandHandler("reply", command_reply))

    # toggle media media
    dispatcher.add_handler(CommandHandler("media", command_media))

    # cycle through pixiv styles
    dispatcher.add_handler(CommandHandler("style", command_style))

    # add your channel
    dispatcher.add_handler(
        ConversationHandler(
            entry_points=[
                CommandHandler("channel", command_channel),
                CommandHandler("cancel", command_cancel),
            ],
            states={
                CHANNEL: [
                    MessageHandler(~Filters.command, channel_check),
                ]
            },
            fallbacks=[
                CommandHandler("channel", command_channel),
                CommandHandler("cancel", command_cancel),
            ],
        )
    )

    # start bot
    updater.start_polling()

    # stop bot
    updater.idle()


if __name__ == "__main__":
    main()
