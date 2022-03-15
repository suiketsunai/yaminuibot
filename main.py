"""Main module"""
import os
import re
import json
import base64
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
from telegram import (
    InlineKeyboardMarkup,
    Message,
    ParseMode,
    Update,
    InputMediaPhoto,
    InlineKeyboardButton,
)

# telegram core bot api extension
from telegram.ext import (
    Updater,
    CallbackContext,
    MessageHandler,
    CommandHandler,
    ConversationHandler,
    CallbackQueryHandler,
    Filters,
)

# telegram errors
from telegram.error import Unauthorized

# escaping special markdown characters
from telegram.utils.helpers import escape_markdown

# http requests
import requests

# pixiv api
from pixivpy3 import AppPixivAPI

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
    os.environ["SB_CNT"].format(password=os.environ["SB_PSW"]),
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
ArtWorkMedia = namedtuple(
    "ArtWorkMedia",
    [
        "link",
        "type",
        "id",
        "media",
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
states = (CHANNEL,) = map(chr, range(1))

# events
not_busy = threading.Event()
not_busy.set()

# fake headers
fake_headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:97.0) Gecko/20100101 Firefox/97.0",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.5",
}

# pixiv tokens
pixiv_api = {
    "ACCESS_TOKEN": os.environ["PX_ACCESS"],
    "REFRESH_TOKEN": os.environ["PX_REFRESH"],
}

# pixiv regex
pixiv_regex = r"^(?:\s*\d+\s*)+$"

# telegram deep linking
telegram_link = "t.me/c/{cid}/{post_id}"

# filename pattern
file_pattern = r".*\/(?P<name>.*?)((\?.*format\=)|(\.))(?P<format>\w+).*$"

# twitter link id
twi_id = r"(?:.*\/(?P<id>.+)(?:\.|\?f))"

################################################################################
# file operations functions
################################################################################


def extract_media_ids(art: dict) -> list[str]:
    if art["type"] == db.TWITTER:
        return [re.search(twi_id, link).group("id") for link in art["links"]]
    if art["type"] == db.PIXIV:
        return [art["id"]]
    return None


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
    if not query:
        return None
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


def send_reply(update: Update, text: str, **kwargs) -> Message:
    """Reply to current message

    Args:
        update (Update): current update
        text (str): text to send in markdown v2

    Returns:
        Message: Telegram Message
    """
    return update.effective_message.reply_markdown_v2(
        reply_to_message_id=update.effective_message.message_id,
        text=text,
        **kwargs,
    )


def send_warning(update: Update, link: Link, **kwargs) -> Message:
    """Reply to current message

    Args:
        update (Update): current update
        text (str): text to send in markdown v2

    Returns:
        Message: Telegram Message
    """
    posted = get_other_links(link.id, link.type)
    text = ", and ".join([f"[here]({esc(post)})" for post in posted])
    return update.effective_message.reply_markdown_v2(
        f"This [artwork]({esc(link.link)}) was already posted\\: {text}\\.\n\n"
        "`\\[` ⚠️ *POST IT ANYWAY\\?* ⚠️ `\\]`",
        reply_markup=InlineKeyboardMarkup.from_button(
            InlineKeyboardButton(text="Post!", callback_data="post")
        ),
    )


def send_post(
    context: CallbackContext,
    info: dict,
    **kwargs,
):
    return context.bot.send_message(
        text=esc(info["link"]),
        parse_mode=ParseMode.MARKDOWN_V2,
        **kwargs,
    )


def send_media_group(
    context: CallbackContext,
    info: dict,
    *,
    order: list[int] = None,
    style: int = None,
    **kwargs,
):
    if order:
        media = [InputMediaPhoto(info["thumbs"][i - 1]) for i in order]
    else:
        limit = len(info["links"]) if len(info["links"]) <= 10 else 10
        media = [InputMediaPhoto(info["thumbs"][i]) for i in range(limit)]
    caption = ""
    match style:
        case db.IMAGE_LINK:
            caption = esc(info["link"])
        case db.IMAGE_INFO_LINK:
            caption = esc(f'{info["desc"]} | {info["user"]}\n{info["link"]}')
        case db.IMAGE_INFO_EMBED_LINK:
            temp = esc(f'{info["desc"]} | {info["user"]}\n')
            caption = f'[{temp}]({esc(info["link"])})'
        case db.INFO_LINK:
            caption = esc(f'{info["desc"]} | {info["user"]}\n{info["link"]}')
            return context.bot.send_message(
                text=caption,
                parse_mode=ParseMode.MARKDOWN_V2,
                **kwargs,
            )
        case db.INFO_EMBED_LINK:
            temp = esc(f'{info["desc"]} | {info["user"]}\n')
            caption = f'[{temp}]({esc(info["link"])})'
            return context.bot.send_message(
                text=caption,
                parse_mode=ParseMode.MARKDOWN_V2,
                **kwargs,
            )
        case _:
            caption = esc(info["link"])
    media[0].caption = caption
    media[0].parse_mode = ParseMode.MARKDOWN_V2
    return context.bot.send_media_group(
        media=media,
        **kwargs,
    )


def send_media_doc(
    context: CallbackContext,
    info: dict,
    *,
    media_filter: list[str] = None,
    order: list[int] = None,
    **kwargs,
) -> Message:
    if media_filter:
        if info["media"] not in media_filter:
            return
    if order:
        for index in order:
            context.bot.send_document(
                document=info["links"][index - 1],
                **kwargs,
            )
    else:
        for link in info["links"]:
            context.bot.send_document(
                document=link,
                **kwargs,
            )


def download_media(info: dict, *, order: list[int] = None) -> None:
    if info["type"] == db.PIXIV:
        headers = {
            "user-agent": "PixivIOSApp/7.13.3 (iOS 14.6; iPhone13,2)",
            "app-os-version": "14.6",
            "app-os": "ios",
            "referer": "https://www.pixiv.net/",
            "referrer-policy": "strict-origin-when-cross-origin",
        }
    else:
        headers = fake_headers
    links = []
    if order:
        for index in order:
            links.append(info["links"][index - 1])
    else:
        links = info["links"]

    for link in links:
        file = requests.get(
            link,
            headers=headers,
            allow_redirects=True,
        )
        reg = re.search(file_pattern, link)
        if not reg:
            log.error("Couldn't get name or format: %s.", link)
            continue
        name = reg.group("name") + "." + reg.group("format")
        if os.environ["GG_URL"]:
            log.info("Uploading file '%s'...", name)
            r = requests.post(
                url=os.environ["GG_URL"],
                params={"name": name},
                data=base64.urlsafe_b64encode(file.content),
            )
            if r.json()["ok"]:
                log.info("Done uploading file '%s'.", name)
            else:
                log.info("File '%s' already exists.", name)


def send_error(update: Update, text: str, **kwargs) -> Message:
    """Reply to current message with error

    Args:
        update (Update): current update
        text (str): text to send in markdown v2

    Returns:
        Message: Telegram Message
    """
    return update.effective_message.reply_markdown_v2(
        reply_to_message_id=update.effective_message.message_id,
        text="\\[`ERROR`\\] " + text,
        **kwargs,
    )


def forward(update: Update, channel: int) -> Message:
    """Forward message to channel"""
    notify(update, func="forward")
    return update.effective_message.forward(
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
    mes = update.effective_message
    if getattr(mes, "forward_from_chat"):
        channel = mes.forward_from_chat
        if channel.type == "supergroup":
            send_error(update, "This message is from a supergroup\\.")
        else:
            with Session(engine) as s:
                if (c := s.get(db.Channel, channel.id)) and c.admin:
                    send_error(update, "This channel is *already* owned\\.")
                else:
                    send_reply(
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
                                    link=channel.username,
                                    is_admin=True,
                                    admin=u,
                                )
                            # commit changes to database
                            s.commit()
                            send_reply(
                                update,
                                "*Done\\!* 🎉\n"
                                "*Your channel* is added to the database\\!",
                            )
                            del context.user_data[CHANNEL]
                            return ConversationHandler.END
                        else:
                            send_error(
                                update,
                                "Either *the bot* or *you* "
                                "are not an admin of this channel\\!",
                            )
                    except Unauthorized as ex:
                        send_error(
                            update,
                            "The bot *was kicked* from this channel\\!",
                        )
    else:
        send_error(update, "Please, *forward* a message from *your channel*\\.")

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


def get_links(media: Link) -> ArtWorkMedia:
    if media.type == db.TWITTER:
        return get_twitter_links(media.id)
    elif media.type == db.PIXIV:
        return get_pixiv_links(media.id)
    else:
        log.warning("Error: Unknown media type: %s.", media.type)
        return None


def unduplicate(arr):
    seen = set()
    seen_add = seen.add
    return [i for i in arr if not (i in seen or seen_add(i))]


################################################################################
# database retrieve functions
################################################################################


def check_original(aid: int, type: int) -> bool:
    with Session(engine) as s:
        return not bool(
            s.query(db.ArtWork)
            .where(db.ArtWork.aid == aid)
            .where(db.ArtWork.type == type)
            .count()
        )


def get_other_links(aid: int, type: int) -> list[str]:
    with Session(engine) as s:
        return [
            telegram_link.format(**item)
            for item in (
                s.query(db.ArtWork.post_id, db.Channel.cid)
                .where(db.ArtWork.channel_id == db.Channel.id)
                .where(db.ArtWork.aid == aid)
                .where(db.ArtWork.type == type)
                .order_by(db.ArtWork.post_date.asc())
                .all()
            )
        ]


def get_user_data(update: Update):
    with Session(engine) as s:
        not_busy.wait()
        if u := s.get(db.User, update.effective_chat.id):
            data = {
                "forward_mode": u.forward_mode,
                "reply_mode": u.reply_mode,
                "media_mode": u.media_mode,
                "pixiv_style": u.pixiv_style,
                "last_info": u.last_info,
            }
            if u.forward_mode:
                if not (channel := u.channel):
                    send_error(
                        update, "You have no channel\\! Send /channel\\."
                    )
                    return None
                data.update({"channel": channel, "channel_id": channel.id})
        else:
            send_error(update, "The bot doesn\\'t know you\\! Send /start\\.")
            return None
    return data


################################################################################
# twitter
################################################################################


def get_twitter_media(
    tweet_id: int,
    media_type: str = None,
    image_list: list[str] = None,
) -> list[list[str], list[str]]:
    """Collect media links from tweet data

    Args:
        tweet_id (int): tweet id
        media_type (str, optional): "photo", "video" or "animated_gif".
        Defaults to None.
        image_list (list[str], optional): list of image links. Defaults to None.

    Returns:
        list[list[str], list[str]]: media links
    """
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
        for url in image_list:
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


def get_twitter_links(tweet_id: int) -> ArtWorkMedia:
    """Get illustration info with twitter api by id

    Args:
        tweet_id (int): tweet id

    Returns:
        ArtWorkMedia: artwork object
    """
    log.debug("Starting Twitter API client...")
    client = tweepy.Client(os.environ["TW_TOKEN"])
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
            return ArtWorkMedia(
                link_dict["twitter"]["link"].format(
                    id=tweet_id, author=user.username
                ),
                db.TWITTER,
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
# pixiv
################################################################################


def get_pixiv_media(illust: dict) -> ArtWorkMedia:
    """Collect information about pixiv artwork

    Args:
        illust (dict): dictionary of illustration

    Returns:
        ArtWorkMedia: artwork object
    """
    if illust.meta_single_page:
        links = [
            [illust.meta_single_page.original_image_url],
            [illust.image_urls.large],
        ]
    else:
        links = [
            [page.image_urls.original for page in illust.meta_pages],
            [page.image_urls.large for page in illust.meta_pages],
        ]
    return ArtWorkMedia(
        link_dict["pixiv"]["link"].format(id=illust.id),
        db.PIXIV,
        illust.id,
        illust.type,  # 'ugoira' or 'illust'
        illust.user.id,
        illust.user.name,
        illust.user.account,
        illust.create_date,
        illust.title,
        links[0],
        links[1],
    )


def get_pixiv_token(refresh_token: str) -> list[str, str]:
    """Get new pixiv API access and refresh token

    Args:
        refresh_token (str): old refresh token

    Returns:
        list[str, str]: access and refresh token
    """
    res = requests.post(
        url="https://oauth.secure.pixiv.net/auth/token",
        headers={
            "User-Agent": "PixivIOSApp/7.13.3 (iOS 14.6; iPhone13,2)",
            "App-OS-Version": "14.6",
            "App-OS": "ios",
        },
        data={
            "client_id": "MOBrBDS8blbauoSck0ZfDbtuzpyT",
            "client_secret": "lsACyCD94FhDUtGTXi3QzcFE2uU1hqtDaKeqrdwj",
            "grant_type": "refresh_token",
            "include_policy": "true",
            "refresh_token": refresh_token,
        },
    )
    try:
        data = res.json()
        return [data["access_token"], data["refresh_token"]]
    except Exception as ex:
        log.error("Exception occured: %s", ex)
        return None


def get_pixiv_links(pixiv_id: int) -> ArtWorkMedia:
    """Get illustration info with pixiv api by id

    Args:
        pixiv_id (int): pixiv id

    Returns:
        ArtWorkMedia: artwork object
    """
    log.debug("Starting Pixiv API client...")
    api = AppPixivAPI()
    tries = 0
    while tries < 3:
        log.debug("Setting authentication...")
        api.set_auth(pixiv_api["ACCESS_TOKEN"], pixiv_api["REFRESH_TOKEN"])
        log.debug("Trying to fetch artwork...")
        json_result = api.illust_detail(pixiv_id)
        if json_result.error:
            if json_result.error.user_message:
                log.error("Error: %s", json_result.error.user_message)
                return None
            else:
                log.warning("Warning: %s", json_result.error.message)
                log.debug("Getting new access token...")
                token = get_pixiv_token(pixiv_api["REFRESH_TOKEN"])
                if token:
                    log.debug("Setting new access token...")
                    pixiv_api["ACCESS_TOKEN"] = token[0]
                else:
                    log.warning("Warning: No token received!")
                    tries += 1
                    log.debug("Trying again [%s]...", tries)
        else:
            return get_pixiv_media(json_result.illust)
    return None


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
    update.effective_message.reply_markdown_v2(
        text=f"Hello, {update.effective_user.mention_markdown_v2()}\\!\n"
        "Nice to meet you\\! My name is *Nuiko Hayami*\\. ❄️\n"
        "Please, see \\/help to learn more about me\\!",
    )


def command_help(update: Update, _) -> None:
    """Send a message when the command /help is issued."""
    send_reply(
        update, Path(os.environ["HELP_FILE"]).read_text(encoding="utf-8")
    )


def command_channel(update: Update, context: CallbackContext) -> int:
    """Starts process of adding user's channel to their profile"""
    notify(update, command="/channel")
    if context.user_data.get(CHANNEL, None):
        send_reply(
            update,
            "*Ehm\\.\\.\\.*\n"
            "Please, forward a post from *your channel* already\\.",
        )
        return CHANNEL
    context.user_data[CHANNEL] = True
    send_reply(
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
        send_reply(
            update, "*Okay\\!* 👌\nYou can add *your channel* at any time\\."
        )
        return ConversationHandler.END
    else:
        send_reply(update, "*Yeah, sure\\.* 👀\nCancel all you want\\.")


def command_forward(update: Update, _) -> None:
    """Enables/Disables forwarding to channel"""
    not_busy.wait()
    not_busy.clear()
    notify(update, command="/forward")
    send_reply(
        update,
        f"Forwarding mode is *{_switch[toggler(update, 'forward_mode')]}*\\.",
    )
    not_busy.set()


def command_reply(update: Update, _) -> None:
    """Enables/Disables replying to messages"""
    not_busy.wait()
    not_busy.clear()
    notify(update, command="/reply")
    send_reply(
        update,
        f"Replying mode is *{_switch[toggler(update, 'reply_mode')]}*\\.",
    )
    not_busy.set()


def command_media(update: Update, _) -> None:
    """Enables/Disables adding video/gif to links"""
    not_busy.wait()
    not_busy.clear()
    notify(update, command="/media")
    send_reply(
        update,
        f"Media mode is *{_switch[toggler(update, 'media_mode')]}*\\.",
    )
    not_busy.set()


def command_style(update: Update, _) -> None:
    """Change pixiv style."""
    not_busy.wait()
    not_busy.clear()
    notify(update, command="/style")
    with Session(engine) as s:
        u = s.get(db.User, update.effective_chat.id)
        old_style = u.pixiv_style
        new_style = db.pixiv[(old_style + 1) % len(db.pixiv)]
        u.pixiv_style = new_style
        s.commit()
    link = esc("https://www.pixiv.net/")
    match new_style:
        case db.IMAGE_LINK:
            style = "\\[ `Image(s)` \\]\n\nLink"
        case db.IMAGE_INFO_LINK:
            style = "\\[ `Image(s)` \\]\n\nArtwork \\| Author\nLink"
        case db.IMAGE_INFO_EMBED_LINK:
            style = f"\\[ `Image(s)` \\]\n\n[Artwork \\| Author]({link})"
        case db.INFO_LINK:
            style = "Artwork \\| Author\nLink"
        case db.INFO_EMBED_LINK:
            style = f"[Artwork \\| Author]({link})"
        case _:
            style = "Unknown"
    send_reply(update, f"_Style has been changed to_\\:\n\n{style}")
    not_busy.set()


################################################################################
# telegram text message handlers
################################################################################


def universal(update: Update, context: CallbackContext) -> None:
    """Universal function for handling posting

    Args:
        update (Update): current update
        context (CallbackContext): current context
    """
    notify(update, func="universal")
    chat_id = update.effective_chat.id
    # get data
    if not (data := get_user_data(update)):
        return
    # get message
    mes = update.effective_message
    # if no text
    if not ((text := mes.text) or (text := mes.caption)):
        return
    # check for links
    if links := formatter(text):
        if len(links) > 1:
            if any(link.type == db.PIXIV for link in links):
                send_error(update, "Can't process pixiv links in batch mode\\.")
            links = [link for link in links if link.type == db.TWITTER]
        if not data["forward_mode"]:
            for link in links:
                if link.type == db.TWITTER:
                    if not (art := get_twitter_links(link.id)):
                        send_error(update, "Couldn't get this content\\!")
                        return
                    if data["reply_mode"]:
                        send_post(
                            context,
                            art._asdict(),
                            chat_id=chat_id,
                        )
                    send_media_doc(
                        context,
                        art._asdict(),
                        reply_to_message_id=mes.message_id,
                        chat_id=mes.chat_id,
                    )
                    if int(os.environ["USER_ID"]) == chat_id:
                        download_media(art._asdict())
                    continue
                if link.type == db.PIXIV:
                    if not (art := get_pixiv_links(link.id)):
                        send_error(update, "Couldn't get this content\\!")
                        return
                    elif len(art.links) == 1:
                        if data["reply_mode"]:
                            send_media_group(
                                context,
                                art._asdict(),
                                style=data["pixiv_style"],
                                reply_to_message_id=mes.message_id,
                                chat_id=chat_id,
                            )
                            send_reply(update, f"Sending a file\\.\\.\\.")
                        send_media_doc(
                            context,
                            art._asdict(),
                            reply_to_message_id=mes.message_id,
                            chat_id=mes.chat_id,
                        )
                        if int(os.environ["USER_ID"]) == chat_id:
                            download_media(art._asdict())
                        continue
                    else:
                        with Session(engine) as s:
                            u = s.get(db.User, mes.chat_id)
                            u.last_info = art._asdict()
                            s.commit()
                        send_reply(
                            update,
                            "Please, choose illustrations to download\\: "
                            f"*\\[*`1`\\-`{len(art.links)}`*\\]*\\.",
                        )
        else:
            if mes.forward_date:
                if getattr(mes, "media_group_id"):
                    send_error(
                        update,
                        "Unfortunately, bots can\\'t *forward* messages with "
                        "more than 1 media \\(photo/video\\) just yet\\. But "
                        "they can *post* them\\! So, please, *for now*, "
                        "forward this kind of messages yourself\\. This may "
                        "change in the future Telegram Bot API updates\\.",
                    )
                    return
                if len(links) > 1:
                    send_error(
                        update, "Only *one link* is allowed for forwarding\\!"
                    )
                    return
                link = links[0]
                artwork = {
                    "aid": link.id,
                    "type": link.type,
                    "channel": data["channel"],
                }
                if post := forward(update, data["channel_id"]):
                    artwork.update(
                        {
                            "post_id": post.message_id,
                            "post_date": post.date,
                            "is_original": False,
                            "is_forwarded": True,
                        }
                    )
                    with Session(engine) as s:
                        c = None
                        if getattr(mes, "forward_from_chat"):
                            c = s.get(db.Channel, mes.forward_from_chat.id)
                            log.info("Forwarded channel: '%s'.", c.name)
                        s.add(db.ArtWork(**artwork, forwarded_channel=c))
                        s.commit()
                    if data["reply_mode"]:
                        send_reply(
                            update,
                            f"Forwarded\\!\n{esc(link.link)}",
                        )
                    if data["media_mode"]:
                        send_media_doc(
                            context,
                            get_links(link)._asdict(),
                            media_filter=["video", "animated_gif"],
                            chat_id=data["channel_id"],
                            reply_to_message_id=post.message_id,
                        )
                    if int(os.environ["USER_ID"]) == chat_id:
                        download_media(get_links(link)._asdict())
            else:
                for link in links:
                    is_orig = check_original(link.id, link.type)
                    if not is_orig:
                        send_warning(update, link)
                        continue
                    if not (art := get_links(link)):
                        send_error(update, "Couldn't get this content\\!")
                        continue
                    artwork = {
                        "aid": link.id,
                        "type": link.type,
                        "channel": data["channel"],
                        "is_original": True,
                        "is_forwarded": False,
                    }
                    if link.type == db.TWITTER:
                        if post := send_post(
                            context,
                            art._asdict(),
                            chat_id=data["channel_id"],
                        ):
                            with Session(engine) as s:
                                s.add(
                                    db.ArtWork(
                                        **artwork,
                                        post_id=post.message_id,
                                        post_date=post.date,
                                        files=extract_media_ids(art._asdict()),
                                    )
                                )
                                s.commit()
                            if data["reply_mode"]:
                                send_reply(
                                    update,
                                    f"Posted\\!\n{esc(art.link)}",
                                )
                            if data["media_mode"]:
                                send_media_doc(
                                    context,
                                    art._asdict(),
                                    media_filter=["video", "animated_gif"],
                                    chat_id=data["channel_id"],
                                    reply_to_message_id=post.message_id,
                                )
                            if int(os.environ["USER_ID"]) == chat_id:
                                download_media(art._asdict())
                        continue
                    if link.type == db.PIXIV:
                        if (
                            len(art.links) == 1
                            or data["pixiv_style"] == db.INFO_LINK
                            or data["pixiv_style"] == db.INFO_EMBED_LINK
                        ):
                            if post := send_media_group(
                                context,
                                art._asdict(),
                                style=data["pixiv_style"],
                                chat_id=data["channel_id"],
                            ):
                                with Session(engine) as s:
                                    s.add(
                                        db.ArtWork(
                                            **artwork,
                                            post_id=post[0].message_id,
                                            post_date=post[0].date,
                                            files=extract_media_ids(
                                                art._asdict()
                                            ),
                                        )
                                    )
                                    s.commit()
                                if data["reply_mode"]:
                                    send_media_group(
                                        context,
                                        art._asdict(),
                                        style=data["pixiv_style"],
                                        reply_to_message_id=mes.message_id,
                                        chat_id=chat_id,
                                    )
                                    send_reply(
                                        update,
                                        f"Posted\\!\n{esc(art.link)}",
                                    )
                                if int(os.environ["USER_ID"]) == chat_id:
                                    download_media(art._asdict())
                        else:
                            with Session(engine) as s:
                                u = s.get(db.User, mes.chat_id)
                                u.last_info = art._asdict()
                                s.commit()
                            send_reply(
                                update,
                                "Please, choose illustrations to download\\: "
                                f"*\\[*`1`\\-`{len(art.links)}`*\\]*\\.",
                            )
                        continue
    elif data["last_info"] and re.search(pixiv_regex, text):
        count = len(data["last_info"]["thumbs"])
        ids = unduplicate([int(i.group()) for i in re.finditer(r"\d+", text)])
        if len(ids) > 10:
            send_error(update, "You *can\\'t* choose more than 10 files\\!")
            return
        if max(ids) > count or min(ids) < 1:
            send_error(
                update,
                f"*Not within* range *\\[*`1`\\-`{count}`*\\]*\\!",
            )
            return
        if data["forward_mode"]:
            artwork = {
                "aid": data["last_info"]["id"],
                "type": data["last_info"]["type"],
                "channel": data["channel"],
            }
            post = send_media_group(
                context,
                data["last_info"],
                order=ids,
                style=data["pixiv_style"],
                chat_id=data["channel_id"],
            )
            if post:
                artwork.update(
                    {
                        "post_id": post[0].message_id,
                        "post_date": post[0].date,
                        "is_original": check_original(
                            data["last_info"]["id"],
                            data["last_info"]["type"],
                        ),
                        "is_forwarded": False,
                    }
                )
                with Session(engine) as s:
                    s.add(
                        db.ArtWork(
                            **artwork,
                            files=extract_media_ids(data["last_info"]),
                        )
                    )
                    s.commit()
                if data["reply_mode"]:
                    send_media_group(
                        context,
                        data["last_info"],
                        order=ids,
                        style=data["pixiv_style"],
                        reply_to_message_id=mes.message_id,
                        chat_id=chat_id,
                    )
                    send_reply(
                        update,
                        f"Posted\\!\n{esc(art.link)}",
                    )
                if int(os.environ["USER_ID"]) == chat_id:
                    download_media(data["last_info"], order=ids)
        else:
            if data["reply_mode"]:
                send_media_group(
                    context,
                    data["last_info"],
                    order=ids,
                    style=data["pixiv_style"],
                    reply_to_message_id=mes.message_id,
                    chat_id=chat_id,
                )
                send_reply(update, f"Sending files\\.\\.\\.")
            send_media_doc(
                context,
                data["last_info"],
                order=ids,
                reply_to_message_id=mes.message_id,
                chat_id=chat_id,
            )
            if int(os.environ["USER_ID"]) == chat_id:
                download_media(data["last_info"], order=ids)
        with Session(engine) as s:
            u = s.get(db.User, mes.chat_id)
            u.last_info = None
            s.commit()
    else:
        return


def answer_query(update: Update, context: CallbackContext) -> None:
    chat_id = update.effective_chat.id
    if not (data := get_user_data(update)):
        return
    if not data["forward_mode"]:
        send_error(
            update,
            "Forwarding mode is turned off\\! Please, turn it on to proceed\\.",
        )
    # print it in readable form
    update.callback_query.answer()
    links = update.effective_message.entities
    link, posted = links[0], links[1:-3]
    text = ", and ".join([f"[here]({esc(post['url'])})" for post in posted])
    if not (art := get_links(formatter(link["url"])[0])):
        send_error(update, "Couldn't get this content\\!")
        return
    artwork = {
        "aid": art.id,
        "type": art.type,
        "channel": data["channel"],
        "is_original": False,
        "is_forwarded": False,
    }
    if art.type == db.TWITTER:
        if post := send_post(
            context,
            art._asdict(),
            chat_id=data["channel_id"],
        ):
            with Session(engine) as s:
                s.add(
                    db.ArtWork(
                        **artwork,
                        post_id=post.message_id,
                        post_date=post.date,
                        files=extract_media_ids(art._asdict()),
                    )
                )
                s.commit()
            if data["reply_mode"]:
                send_reply(
                    update,
                    f"Posted\\!\n{esc(art.link)}",
                )
            if data["media_mode"]:
                send_media_doc(
                    context,
                    art._asdict(),
                    media_filter=["video", "animated_gif"],
                    chat_id=data["channel_id"],
                    reply_to_message_id=post.message_id,
                )
            if int(os.environ["USER_ID"]) == chat_id:
                download_media(art._asdict())
        result = "`\\[` *POST HAS BEEN POSTED\\.* `\\]`"
    elif art.type == db.PIXIV:
        if (
            len(art.links) == 1
            or data["pixiv_style"] == db.INFO_LINK
            or data["pixiv_style"] == db.INFO_EMBED_LINK
        ):
            if post := send_media_group(
                context,
                art._asdict(),
                style=data["pixiv_style"],
                chat_id=data["channel_id"],
            ):
                with Session(engine) as s:
                    s.add(
                        db.ArtWork(
                            **artwork,
                            post_id=post[0].message_id,
                            post_date=post[0].date,
                            files=extract_media_ids(art._asdict()),
                        )
                    )
                    s.commit()
                if data["reply_mode"]:
                    send_media_group(
                        context,
                        data["last_info"],
                        style=data["pixiv_style"],
                        reply_to_message_id=update.effective_message.message_id,
                        chat_id=chat_id,
                    )
                    send_reply(
                        update,
                        f"Posted\\!\n{esc(art.link)}",
                    )
                result = "`\\[` *POST HAS BEEN POSTED\\.* `\\]`"
        else:
            with Session(engine) as s:
                u = s.get(db.User, update.effective_message.chat_id)
                u.last_info = art._asdict()
                s.commit()
            send_reply(
                update,
                "Please, choose illustrations to download\\: "
                f"*\\[*`1`\\-`{len(art.links)}`*\\]*\\.",
            )
            result = "`\\[` *PLEASE, SPECIFY DATA\\.* `\\]`"

    return update.effective_message.edit_text(
        f"~This [artwork]({esc(art.link)}) was already posted\\: {text}~\\.\n\n"
        + result,
        parse_mode=ParseMode.MARKDOWN_V2,
    )


def handle_post(update: Update, context: CallbackContext) -> None:
    print(f"\n\n{json.dumps(update.to_dict(), indent=2, default=str)}\n\n")

    mes = update.effective_message
    if not ((text := mes.text) or (text := mes.caption)):
        return
    if links := formatter(text):
        if len(links) > 1:
            return
        else:
            link = links[0]
            artwork = {
                "aid": link.id,
                "type": link.type,
                "is_original": check_original(link.id, link.type),
                "is_forwarded": bool(mes.forward_date),
                "post_id": mes.message_id,
                "post_date": mes.date,
                "channel_id": update.effective_chat.id,
            }
            with Session(engine) as s:
                c = None
                if getattr(mes, "forward_from_chat"):
                    c = s.get(db.Channel, mes.forward_from_chat.id)
                    log.info("Forwarded channel: '%s'.", c.name)
                s.add(db.ArtWork(**artwork, forwarded_channel=c))
                s.commit()
    return


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
    updater = Updater(os.environ["TOKEN"])

    # start bot
    webhook = (
        "https://"
        + os.environ["APP_NAME"]
        + ".herokuapp.com/"
        + os.environ["TOKEN"]
    )
    updater.start_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", "8443")),
        url_path=os.environ["TOKEN"],
        webhook_url=webhook,
    )
    dispatcher = updater.dispatcher

    # start the bot
    dispatcher.add_handler(
        CommandHandler(
            "start",
            command_start,
        )
    )

    # get help
    dispatcher.add_handler(
        CommandHandler(
            "help",
            command_help,
        )
    )

    # toggle forwarding mode
    dispatcher.add_handler(
        CommandHandler(
            "forward",
            command_forward,
        )
    )

    # toggle replying mode
    dispatcher.add_handler(
        CommandHandler(
            "reply",
            command_reply,
        )
    )

    # toggle media media
    dispatcher.add_handler(
        CommandHandler(
            "media",
            command_media,
        )
    )

    # cycle through pixiv styles
    dispatcher.add_handler(
        CommandHandler(
            "style",
            command_style,
        )
    )

    channel_handler = CommandHandler("channel", command_channel)
    cancel_handler = CommandHandler("cancel", command_cancel)

    # add your channel
    dispatcher.add_handler(
        ConversationHandler(
            entry_points=[
                channel_handler,
                cancel_handler,
            ],
            states={
                CHANNEL: [
                    MessageHandler(
                        Filters.chat_type.private & ~Filters.command,
                        channel_check,
                    ),
                ]
            },
            fallbacks=[
                channel_handler,
                cancel_handler,
            ],
        )
    )

    # handle text messages
    dispatcher.add_handler(
        MessageHandler(
            Filters.chat_type.private & ~Filters.command,
            universal,
        )
    )

    # handle force posting
    dispatcher.add_handler(CallbackQueryHandler(answer_query))

    # handle channels posts
    dispatcher.add_handler(
        MessageHandler(
            Filters.chat_type.channel & ~Filters.command,
            handle_post,
        )
    )

    # start bot
    updater.start_polling()

    # stop bot
    updater.idle()


if __name__ == "__main__":
    main()
