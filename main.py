"""Main module"""
import os
import re
import json
import logging
import threading

from pathlib import Path
from datetime import datetime
from functools import partial
from collections import namedtuple

# working with env
from dotenv import load_dotenv

# reading setings
import tomli

# working with database
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, aliased

# working with timezone
from dateutil import tz

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

# telegram errors
from telegram.error import Unauthorized

# escaping special markdown characters
from telegram.utils.helpers import escape_markdown

# http requests
import requests

# twitter api
import tweepy

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
TwitterMedia = namedtuple(
    "TwitterMedia",
    [
        "id",
        "type",
        "user_id",
        "user",
        "username",
        "date",
        "desc",
        "links",
        "thumbs",
    ],
)

################################################################################
# hardcode
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
        "link": "https://twitter.com/{author}/status/{id}",
        "full": "https://pbs.twimg.com/media/{id}?format={format}&name=orig",
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
        "link": "https://www.pixiv.net/artworks/{id}",
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

# events
not_busy = threading.Event()
not_busy.set()

# fake headers
fake_headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:97.0) Gecko/20100101 Firefox/97.0",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.5",
}

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
            _link = re_type["link"].format(**link.groupdict())
            log.info("Received %s link: '%s'.", re_key, _link)
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
                    "post_date": parse(message["date"]).astimezone(tz.tzutc()),
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

# escaping markdown v2
esc = partial(escape_markdown, version=2)


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


def forward(update: Update, channel: int) -> Message:
    """Forward message to channel"""
    notify(update, func="forward")
    return update.message.forward(
        chat_id=channel,
    )


def notify(update: Update, *, command: str = None, func: str = None) -> None:
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
    if func:
        log.info(
            "%s function was called by %s [%s].",
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
                    try:
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
                            and member_user.status
                            in ["creator", "administrator"]
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
                    except Unauthorized as ex:
                        error(
                            update,
                            "The bot *was kicked* from this channel\\!",
                        )
    else:
        error(update, "Please, *forward* a message from *your channel*\\.")

    return CHANNEL


def get_file_size(link: str, session: requests.Session = None) -> int:
    """Gets file size

    Args:
        link (str): downloadable file

    Returns:
        int: size of file
    """
    if not session:
        session = requests
    r = session.head(
        url=link,
        headers=fake_headers,
        allow_redirects=True,
    )
    if r.ok and (size := r.headers.get("Content-Length", None)):
        return int(size)
    return 0


################################################################################
# twitter
################################################################################


def get_twitter_media(tweet_id, media_type=None, source_url=None) -> list:
    if media_type == "photo":
        pat = r"""(?x)
            (?:
                (?:media\/)
                (?P<id>[^\.\?]+)
                (?:
                    (?:\?.*format\=)|(?:\.)
                )
            )
            (?P<format>\w+)
        """
        links = []
        for url in source_url:
            reg = re.search(pat, url)
            links.append(link_dict["twitter"]["full"].format(**reg.groupdict()))
        return [links, [link.replace("orig", "large") for link in links]]
    else:
        base = "https://tweetpik.com/twitter-downloader/"
        api = f"https://tweetpik.com/api/tweets/{tweet_id}/video"
        log.debug(f"Sending request to API: {api}...")
        s = requests.session()
        res = s.post(
            url=api,
            headers={
                **fake_headers,
                "Referer": base,
            },
        )
        if res.status_code != 200:
            log.warning("Service is unavailable.")
            return None
        log.debug("Received json: %s.", res.json())
        var = res.json()["variants"]
        return [
            [var[-1 % len(var)]["url"]],
            [var[-2 % len(var)]["url"]],
        ]


def get_twitter_links(tweet_id: int) -> TwitterMedia:
    # start client
    log.debug("Starting Twitter API client...")
    client = tweepy.Client(os.environ["TWITTER_TOKEN"])
    res = client.get_tweet(
        id=tweet_id,
        expansions=[
            "attachments.media_keys",
            "author_id",
        ],
        tweet_fields=[
            "id",
            "text",
            "created_at",
            "entities",
        ],
        user_fields=[
            "id",
            "name",
            "username",
        ],
        media_fields=[
            "type",
            "width",
            "height",
            "preview_image_url",
            "url",
            "duration_ms",
        ],
    )
    log.debug("Response: %s.", res)
    error = res.errors
    if error:
        log.warning("%s: %s", error["title"], error["detail"])
    else:
        media = [media for media in res.includes["media"]]
        user = res.includes["users"][0]
        kind = media[0].type
        if kind == "photo":
            links = get_twitter_media(tweet_id, kind, [e.url for e in media])
        else:
            links = get_twitter_media(tweet_id, kind)
        if not links[0]:
            log.warning("Unexpected error occured: no links.")
            return None
        else:
            text = res.data.text
            for url in res.data.entities["urls"][:-1]:
                text = text.replace(url["url"], url["expanded_url"])
            text = text.replace(res.data.entities["urls"][-1]["url"], "")
            return TwitterMedia(
                tweet_id,
                kind,
                user.id,
                user.name,
                user.username,
                res.data.created_at,
                text.strip(),
                links[0],
                links[1],
            )


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
    not_busy.clear()
    notify(update, command="/forward")
    reply(
        update,
        f"Forwarding mode is *{_switch[toggler(update, 'forward_mode')]}*\\.",
    )
    not_busy.set()


def command_reply(update: Update, _) -> None:
    """Enables/Disables replying to messages"""
    not_busy.clear()
    notify(update, command="/reply")
    reply(
        update,
        f"Replying mode is *{_switch[toggler(update, 'reply_mode')]}*\\.",
    )
    not_busy.set()


def command_media(update: Update, _) -> None:
    """Enables/Disables adding video/gif to links"""
    not_busy.clear()
    notify(update, command="/media")
    reply(
        update,
        f"Media mode is *{_switch[toggler(update, 'media_mode')]}*\\.",
    )
    not_busy.set()


def command_style(update: Update, _) -> None:
    """Change pixiv style."""
    not_busy.clear()
    notify(update, command="/style")
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
    not_busy.set()


################################################################################
# telegram text message handlers
################################################################################


def forward_post(update: Update, _) -> None:
    """Forward post if it's allowed"""
    notify(update, command="forward_post")
    if not (text := update.message.text):
        text = update.message.caption
    if links := formatter(text):
        if len(links) > 1:
            error(update, "Only *one link* is allowed for forwarding\\!")
            return
        link = links[0]
        with Session(engine) as s:
            not_busy.wait()
            if u := s.get(db.User, update.effective_chat.id):
                f, r, m = u.forward_mode, u.reply_mode, u.media_mode
                if f and not (channel := u.channel.id):
                    error(update, "You have no channel\\! Send /channel\\.")
                    return
            else:
                error(update, "The bot doesn\\'t know you\\! Send /start\\.")
                return
        if f:
            p = forward(update, channel)
            if p:
                if r:
                    reply(update, "Forwarded\\!")
                if m:
                    pass
        else:
            pass


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
    dispatcher.add_handler(
        CommandHandler(
            "start",
            command_start,
            run_async=True,
        )
    )

    # get help
    dispatcher.add_handler(
        CommandHandler(
            "help",
            command_help,
            run_async=True,
        )
    )

    # toggle forwarding mode
    dispatcher.add_handler(
        CommandHandler(
            "forward",
            command_forward,
            run_async=True,
        )
    )

    # toggle replying mode
    dispatcher.add_handler(
        CommandHandler(
            "reply",
            command_reply,
            run_async=True,
        )
    )

    # toggle media media
    dispatcher.add_handler(
        CommandHandler(
            "media",
            command_media,
            run_async=True,
        )
    )

    # cycle through pixiv styles
    dispatcher.add_handler(
        CommandHandler(
            "style",
            command_style,
            run_async=True,
        )
    )

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
            run_async=True,
        )
    )

    dispatcher.add_handler(
        MessageHandler(
            Filters.forwarded & ~Filters.command,
            forward_post,
            run_async=True,
        )
    )

    # start bot
    updater.start_polling()

    # stop bot
    updater.idle()


if __name__ == "__main__":
    main()
