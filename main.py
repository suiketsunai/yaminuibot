"""Main module"""
import os
import re
import time
import json
import base64
import logging

from pathlib import Path
from datetime import datetime
from functools import partial
from dataclasses import dataclass

# working with env
from dotenv import load_dotenv

# reading setings
import tomli

# working with database
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

# working with images
from PIL import Image

# database engine
from db import engine

# database models
from db.models import User, Channel, ArtWork

# import pixiv styles and link types
from extra import *

# settings
from extra.loggers import root_log

# namedtuples
from extra.namedtuples import ArtWorkMedia, Link

# twitter
from extra.twitter import get_twitter_links

# pixiv
from extra.pixiv import get_pixiv_links

# current timestamp & this file directory
date_run = datetime.now()
file_dir = Path(__file__).parent

# load .env file & get config
load_dotenv()

# upload dictionary
upl_dict = {
    "user": int(os.getenv("USER_ID") or 0),
    "link": os.getenv("GG_URL"),
}

# setup loggers
log = logging.getLogger("yaminuichan.main")
sys_log = logging.getLogger("yaminuichan.system")
upl_log = logging.getLogger("yaminuichan.upload")

################################################################################
# file operations functions
################################################################################


def extract_media_ids(art: dict) -> list[str]:
    if art["type"] == LinkType.TWITTER:
        return [re.search(twi_id, link).group("id") for link in art["links"]]
    if art["type"] == LinkType.PIXIV:
        return [str(art["id"])]
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
    dumper(User, "users")
    dumper(Channel, "channels")
    dumper(ArtWork, "artworks")


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
            s.add(User(**user))
        s.commit()

        for channel in channels:
            s.add(Channel(**channel))
        s.commit()
    # get directories
    dirs = [cid for cid in src.iterdir() if cid.is_dir()]
    with Session(engine) as s:
        chans = {str(channel.cid): channel for channel in s.query(Channel)}
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


################################################################################
# telegram bot helpers section
################################################################################

# states
states = (CHANNEL,) = map(chr, range(1))

# helper dictionary
_switch = {
    True: "enabled",
    False: "disabled",
}

# user data dictionary
@dataclass
class UserData:
    forward: bool
    reply: bool
    media: bool
    pixiv: int
    info: dict
    channel: Channel = None
    channel_id: int = 0


# escaping markdown v2
esc = partial(escape_markdown, version=2)


def _reply(update: Update, text: str, **kwargs) -> Message:
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


def _error(update: Update, text: str, **kwargs) -> Message:
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


def _warn(update: Update, link: Link, **kwargs) -> Message:
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
    caption = ""
    match style:
        case PixivStyle.IMAGE_LINK:
            caption = esc(info["link"])
        case PixivStyle.IMAGE_INFO_LINK:
            caption = esc(f'{info["desc"]} | {info["user"]}\n{info["link"]}')
        case PixivStyle.IMAGE_INFO_EMBED_LINK:
            temp = esc(f'{info["desc"]} | {info["user"]}\n')
            caption = f'[{temp}]({esc(info["link"])})'
        case PixivStyle.INFO_LINK:
            caption = esc(f'{info["desc"]} | {info["user"]}\n{info["link"]}')
            return context.bot.send_message(
                text=caption,
                parse_mode=ParseMode.MARKDOWN_V2,
                **kwargs,
            )
        case PixivStyle.INFO_EMBED_LINK:
            temp = esc(f'{info["desc"]} | {info["user"]}\n')
            caption = f'[{temp}]({esc(info["link"])})'
            return context.bot.send_message(
                text=caption,
                parse_mode=ParseMode.MARKDOWN_V2,
                **kwargs,
            )
        case _:
            caption = esc(info["link"])
    media = []
    for file in download_media(info, full=False, order=order):
        media.append(InputMediaPhoto(file.read_bytes()))
        file.unlink()
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
    style: int = None,
    **kwargs,
) -> Message:
    if not info:
        return log.error("send_media_doc: No info supplied.")
    if media_filter and info["media"] not in media_filter:
        return log.debug("send_media_doc: Didn't pass media filter.")
    log.debug("send_media_doc: Passed media filter.")
    for file in download_media(info, full=True, order=order):
        context.bot.send_document(
            document=file.read_bytes(),
            filename=file.name,
            **kwargs,
        )
        file.unlink()


def download_media(
    info: dict,
    *,
    full: bool = True,
    user: int = 0,
    order: list[int] = None,
) -> list[Path] | None:
    if not info:
        return log.error("download_media: No info supplied.")
    if info["type"] == LinkType.PIXIV:
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
        links = [info["links"][index - 1] for index in order]
    elif len(info["links"]) <= 10:
        links = info["links"]
    else:
        links = info["links"][:10]
    files = []
    for link in links:
        file = requests.get(
            link,
            headers=headers,
            allow_redirects=True,
        )
        reg = re.search(file_pattern, link)
        if not reg:
            log.error("download_media: Couldn't get name or format: %s.", link)
            continue
        name = reg.group("name") + "." + reg.group("format")
        if user == upl_dict["user"] and upl_dict["link"]:
            attempt = 1
            while attempt <= 3:
                upl_log.info("Uploading file '%s'...", name)
                r = requests.post(
                    url=os.getenv("GG_URL"),
                    params={"name": name},
                    data=base64.urlsafe_b64encode(file.content),
                )
                try:
                    if r.json()["ok"]:
                        upl_log.info("Done uploading file '%s'.", name)
                    else:
                        upl_log.info("File '%s' already exists.", name)
                    break
                except Exception as ex:
                    upl_log.error("Exception occured: %s", ex)
                    upl_log.info("Waiting for 3 seconds...")
                    time.sleep(3)
                    attempt += 1
                    upl_log.info("Done. Current attempt: #%s.", attempt)
            continue
        image_file = Path(name)
        image_file.write_bytes(file.content)
        if not full:
            image = Image.open(image_file)
            image.thumbnail([1280, 1280])
            image.save(image_file)
        files.append(image_file)
    return files


def forward(update: Update, channel: int) -> Message:
    """Forward message to channel

    Args:
        update (Update): current update
        channel (int): a channel to forward to.
    """
    notify(update, func="forward")
    return update.effective_message.forward(
        chat_id=channel,
    )


def notify(
    update: Update,
    *,
    command: str = None,
    func: str = None,
    art: ArtWorkMedia = None,
    toggle: tuple[str, bool] = None,
) -> None:
    """Log that something hapened

    Args:
        update (Update): current update
        command (str, optional): called command. Defaults to None.
        func (str, optional): called function. Defaults to None.
        art (ArtWorkMedia, optional): art object. Defaults to None.
        toggle (tuple[str, bool]m optional): toggler info. Defaults to None.
    """
    if command:
        sys_log.info(
            "[%s] '%s' called command: '%s'.",
            update.effective_chat.id,
            update.effective_chat.full_name or update.effective_chat.title,
            command,
        )
    if func:
        sys_log.info(
            "[%s] '%s' called function: '%s'.",
            update.effective_chat.id,
            update.effective_chat.full_name or update.effective_chat.title,
            func,
        )
    if art:
        sys_log.info(
            "[%s] '%s' received content: [%s/%s] '%s' by [%s/@%s] '%s' | %s.",
            update.effective_chat.id,
            update.effective_chat.full_name or update.effective_chat.title,
            art.id,
            art.media,
            art.desc,
            art.user_id,
            art.username,
            art.user,
            art.date,
        )
    if toggle:
        sys_log.info(
            "[%s] '%s' called toggler: '%s' is now %s.",
            update.effective_chat.id,
            update.effective_chat.full_name or update.effective_chat.title,
            toggle[0],
            _switch[toggle[1]],
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
        u = s.get(User, update.effective_chat.id)
        state = not getattr(u, attr)
        setattr(u, attr, state)
        s.commit()
        notify(update, toggle=(attr, state))
        return state


def channel_check(update: Update, context: CallbackContext) -> int:
    """Checks if channel is a valid choice"""
    mes = update.effective_message
    if getattr(mes, "forward_from_chat"):
        channel = mes.forward_from_chat
        if channel.type == "supergroup":
            _error(update, "This message is from a supergroup\\.")
        else:
            with Session(engine) as s:
                if (c := s.get(Channel, channel.id)) and c.admin:
                    _error(update, "This channel is *already* owned\\.")
                else:
                    _reply(
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
                            u = s.get(User, update.effective_chat.id)
                            # remove old channel
                            if c:
                                # channel already exist
                                u.channel = c
                            else:
                                # channel doesn't exist
                                u.channel = None
                                # create new channel
                                Channel(
                                    id=channel.id,
                                    name=channel.title,
                                    link=channel.username,
                                    is_admin=True,
                                    admin=u,
                                )
                            # commit changes to database
                            s.commit()
                            _reply(
                                update,
                                "*Done\\!* 🎉\n"
                                "*Your channel* is added to the database\\!",
                            )
                            del context.user_data[CHANNEL]
                            return ConversationHandler.END
                        else:
                            _error(
                                update,
                                "Either *the bot* or *you* "
                                "are not an admin of this channel\\!",
                            )
                    except Unauthorized as ex:
                        _error(
                            update,
                            "The bot *was kicked* from this channel\\!",
                        )
    else:
        _error(update, "Please, *forward* a message from *your channel*\\.")

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
    if media.type == LinkType.TWITTER:
        return get_twitter_links(media.id)
    if media.type == LinkType.PIXIV:
        return get_pixiv_links(media.id)
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
            s.query(ArtWork)
            .where(ArtWork.aid == aid)
            .where(ArtWork.type == type)
            .count()
        )


def get_other_links(aid: int, type: int) -> list[str]:
    with Session(engine) as s:
        return [
            telegram_link.format(**item)
            for item in (
                s.query(ArtWork.post_id, Channel.cid)
                .where(ArtWork.channel_id == Channel.id)
                .where(ArtWork.aid == aid)
                .where(ArtWork.type == type)
                .order_by(ArtWork.post_date.asc())
                .all()
            )
        ]


def get_user_data(update: Update):
    with Session(engine) as s:
        if u := s.get(User, update.effective_chat.id):
            data = UserData(
                u.forward_mode,
                u.reply_mode,
                u.media_mode,
                u.pixiv_style,
                u.last_info,
            )
            if u.forward_mode:
                if not (channel := u.channel):
                    _error(update, "You have no channel\\! Send /channel\\.")
                    return None
                data.channel, data.channel_id = channel, channel.id
            return data
        _error(update, "The bot doesn\\'t know you\\! Send /start\\.")
        return None


################################################################################
# telegram bot commands
################################################################################


def command_start(update: Update, _) -> None:
    """Start the bot"""
    notify(update, command="/start")
    with Session(engine) as s:
        if not s.get(User, update.effective_chat.id):
            s.add(
                User(
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
    notify(update, command="/help")
    _reply(update, Path(os.getenv("HELP_FILE")).read_text(encoding="utf-8"))


def command_channel(update: Update, context: CallbackContext) -> int:
    """Starts process of adding user's channel to their profile"""
    notify(update, command="/channel")
    if context.user_data.get(CHANNEL, None):
        _reply(
            update,
            "*Ehm\\.\\.\\.*\n"
            "Please, forward a post from *your channel* already\\.",
        )
        return CHANNEL
    context.user_data[CHANNEL] = True
    _reply(
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
        _reply(update, "*Okay\\!* 👌\nYou can add *your channel* any time\\.")
        return ConversationHandler.END
    else:
        _reply(update, "*Yeah, sure\\.* 👀\nCancel all you want\\.")


def command_forward(update: Update, _) -> None:
    """Enables/Disables forwarding to channel"""
    notify(update, command="/forward")
    _reply(
        update,
        f"Forwarding mode is *{_switch[toggler(update, 'forward_mode')]}*\\.",
    )


def command_reply(update: Update, _) -> None:
    """Enables/Disables replying to messages"""
    notify(update, command="/reply")
    _reply(
        update,
        f"Replying mode is *{_switch[toggler(update, 'reply_mode')]}*\\.",
    )


def command_media(update: Update, _) -> None:
    """Enables/Disables adding video/gif to links"""
    notify(update, command="/media")
    _reply(
        update,
        f"Media mode is *{_switch[toggler(update, 'media_mode')]}*\\.",
    )


def command_style(update: Update, _) -> None:
    """Change pixiv style."""
    notify(update, command="/style")
    # get old and new styles
    with Session(engine) as s:
        u = s.get(User, update.effective_chat.id)
        old_style = u.pixiv_style
        new_style = PixivStyle.styles[(old_style + 1) % len(PixivStyle.styles)]
        u.pixiv_style = new_style
        s.commit()
    # demonstrate new style
    link = esc("https://www.pixiv.net/")
    match new_style:
        case PixivStyle.IMAGE_LINK:
            style = "\\[ `Image(s)` \\]\n\nLink"
        case PixivStyle.IMAGE_INFO_LINK:
            style = "\\[ `Image(s)` \\]\n\nArtwork \\| Author\nLink"
        case PixivStyle.IMAGE_INFO_EMBED_LINK:
            style = f"\\[ `Image(s)` \\]\n\n[Artwork \\| Author]({link})"
        case PixivStyle.INFO_LINK:
            style = "Artwork \\| Author\nLink"
        case PixivStyle.INFO_EMBED_LINK:
            style = f"[Artwork \\| Author]({link})"
        case _:
            style = "Unknown"
    _reply(update, f"_Style has been changed to_\\:\n\n{style}")


################################################################################
# telegram text message handlers
################################################################################


def pixiv_parse(
    update: Update,
    context: CallbackContext,
    data: dict,
    text: str,
) -> None:
    notify(update, func="pixiv_parse")
    # speed up
    last_info = data.info
    # initial data
    count = len(last_info["thumbs"])
    ids = []
    for number in re.finditer(pixiv_number, text):
        n1 = int(number.group("n1"))
        if n2 := number.group("n2"):
            n2 = int(n2)
        else:
            n2 = n1
        if n1 > n2:
            ids += reversed(range(n2, n1 + 1))
        else:
            ids += range(n1, n2 + 1)
    ids = list(dict.fromkeys(ids))
    if len(ids) > 10:
        return _error(update, "You *can\\'t* choose more than 10 files\\!")
    if max(ids) > count or min(ids) < 1:
        return _error(update, f"*Not within* range: \\[`1`\\-`{count}`\\]\\!")
    # save for reuse
    common_data = {
        "context": context,
        "info": last_info,
        "style": data.pixiv,
        "order": ids,
    }
    reply_data = {
        **common_data,
        "chat_id": update.effective_chat.id,
        "reply_to_message_id": update.effective_message.message_id,
    }

    if data.forward:
        art = {
            "aid": last_info["id"],
            "type": last_info["type"],
            "channel": data.channel,
        }
        post = send_media_group(**common_data, chat_id=data.channel_id)
        if not post:
            return _error(update, "Coudn't post\\!")
        if not isinstance(post, Message):
            post = post[0]
        art.update(
            {
                "post_id": post.message_id,
                "post_date": post.date,
                "is_original": check_original(
                    last_info["id"],
                    last_info["type"],
                ),
                "is_forwarded": False,
            }
        )
        with Session(engine) as s:
            s.add(ArtWork(**art, files=extract_media_ids(last_info)))
            s.commit()
        if data.reply:
            send_media_group(**reply_data)
            _reply(update, f'Posted\\!\n{esc(last_info["link"])}')
    else:
        if data.reply:
            send_media_group(**reply_data)
            _reply(update, f"Sending files\\.\\.\\.")
        send_media_doc(**reply_data)
    # upload to cloud
    download_media(info=last_info, order=ids, user=update.effective_chat.id)
    # clean last_info for user
    with Session(engine) as s:
        u = s.get(User, update.effective_chat.id)
        u.last_info = None
        s.commit()


def no_forwarding(
    update: Update,
    context: CallbackContext,
    data: dict,
    links: list[Link],
) -> None:
    notify(update, func="no_forwarding")
    # process links
    for link in links:
        if not (art := get_links(link)):
            log.error("Couldn't get content: '%s'.", link.link)
            _error(update, "Couldn't get this content\\!")
            continue
        notify(update, art=art)
        art = art._asdict()
        common_data = {
            "context": context,
            "chat_id": update.effective_chat.id,
            "info": art,
            "reply_to_message_id": update.effective_message.message_id,
        }
        match link.type:
            # twitter links
            case LinkType.TWITTER:
                if data.reply:
                    send_post(**common_data)
                send_media_doc(**common_data)
            # one pixiv link
            case LinkType.PIXIV:
                if len(art["links"]) > 1:
                    log.info("There's more than 1 artwork.")
                    with Session(engine) as s:
                        u = s.get(User, update.effective_chat.id)
                        u.last_info = art
                        s.commit()
                    _reply(
                        update,
                        "Please, choose illustrations to download\\: "
                        f'\\[`1`\\-`{len(art["links"])}`\\]\\.',
                    )
                    return
                log.info("There's only 1 artwork.")
                if data.reply:
                    send_media_group(**common_data, style=data.pixiv)
                    _reply(update, f"Sending a file\\.\\.\\.")
                send_media_doc(**common_data)
        # upload to cloud
        download_media(art, user=update.effective_chat.id)


def just_forwarding(
    update: Update,
    context: CallbackContext,
    data: dict,
    links: list[Link],
) -> None:
    notify(update, func="forward_forwarding")
    # check if media group message
    if getattr(update.effective_message, "media_group_id"):
        log.error("Forward: Bots can't forward media groups.")
        return _error(
            update,
            "Unfortunately, bots can\\'t *forward* messages with "
            "more than 1 media \\(photo/video\\) just yet\\. But "
            "they can *post* them\\! So, please, *for now*, "
            "forward this kind of messages yourself\\. This may "
            "change in the future Telegram Bot API updates\\.",
        )
    # check if more than 1 link in message
    if len(links) > 1:
        log.error("Forward: More than 1 link.")
        return _error(update, "Only *one link* is allowed for forwarding\\!")
    # and so there's one link
    link = links[0]
    artwork = {
        "aid": link.id,
        "type": link.type,
        "channel": data.channel,
    }
    # can be ignored for this one
    if not (art := get_links(link)):
        log.warning("Forward: Couldn't get content: '%s'.", link.link)
    else:
        notify(update, art=art)
        art = art._asdict()
        artwork["files"] = extract_media_ids(art)
    # check if it's forwarded from channel in database
    with Session(engine) as s:
        if src := update.effective_message.forward_from_chat:
            if c := s.get(Channel, src.id):
                artwork["forwarded_channel_id"] = c.id
                log.info("Forward: Source: '%s' [%s].", c.name, c.cid)
                if c.id == data.channel_id:
                    log.error("Forward: Self-forwarding is no allowed.")
                    return _error(update, "You shouldn't *self\\-forward*\\!")
            else:
                log.info("Forward: Spurce: unknown.")
        else:
            log.info("Forward: Source: not a channel.")
    # just forward it
    if post := forward(update, data.channel_id):
        log.info("Forward: Successfully forwarded to channel.")
        artwork.update(
            {
                "post_id": post.message_id,
                "post_date": post.date,
                "is_original": False,
                "is_forwarded": True,
            }
        )
        with Session(engine) as s:
            s.add(ArtWork(**artwork))
            s.commit()
            log.debug("Forward: Inserted ArtWork: %s.", artwork)
        if data.reply:
            _reply(update, f"Forwarded\\!\n{esc(link.link)}")
        if data.media:
            if art:
                if send_media_doc(
                    context=context,
                    info=art,
                    media_filter=["video", "animated_gif"],
                    chat_id=data.channel_id,
                    reply_to_message_id=post.message_id,
                ):
                    log.info("Forward: Successfully replied with media.")
            else:
                _error(update, "*Media mode*\\: Couldn't get this content\\!")
                log.warning("Forward: Couldn't reply with media.")
    # upload to cloud
    download_media(art, user=update.effective_chat.id)


def just_posting(
    update: Update,
    context: CallbackContext,
    data: dict,
    links: list[Link],
) -> None:
    notify(update, func="just_posting")
    # process links
    for link in links:
        is_orig = check_original(link.id, link.type)
        if not is_orig:
            _warn(update, link)
            continue
        if not (art := get_links(link)):
            log.error("Post: Couldn't get content: '%s'.", link.link)
            _error(update, "Couldn't get this content\\!")
            continue
        notify(update, art=art)
        art = art._asdict()
        artwork = {
            "aid": link.id,
            "type": link.type,
            "channel": data.channel,
            "is_original": True,
            "is_forwarded": False,
        }
        common_data = {
            "context": context,
            "info": art,
        }
        reply_data = {
            **common_data,
            "chat_id": update.effective_chat.id,
            "reply_to_message_id": update.effective_message.message_id,
        }
        match link.type:
            # twitter links
            case LinkType.TWITTER:
                if post := send_post(**common_data, chat_id=data.channel_id):
                    log.info("Post: Successfully forwarded to channel.")
                    artwork.update(
                        {
                            "post_id": post.message_id,
                            "post_date": post.date,
                            "files": extract_media_ids(art),
                        }
                    )
                    with Session(engine) as s:
                        s.add(ArtWork(**artwork))
                        s.commit()
                        log.debug("Post: Inserted ArtWork: %s.", artwork)
                    if data.reply:
                        _reply(update, f'Posted\\!\n{esc(art["link"])}')
                    if data.media:
                        send_media_doc(
                            **common_data,
                            media_filter=["video", "animated_gif"],
                            chat_id=data.channel_id,
                            reply_to_message_id=post.message_id,
                        )
            # pixiv links
            case LinkType.PIXIV:
                if (
                    len(art["links"]) == 1
                    or data.pixiv == PixivStyle.INFO_LINK
                    or data.pixiv == PixivStyle.INFO_EMBED_LINK
                ):
                    if post := send_media_group(
                        **common_data,
                        style=data.pixiv,
                        chat_id=data.channel_id,
                    ):
                        if not isinstance(post, Message):
                            post = post[0]
                        log.info("Post: Successfully forwarded to channel.")
                        artwork.update(
                            {
                                "post_id": post.message_id,
                                "post_date": post.date,
                                "files": extract_media_ids(art),
                            }
                        )
                        with Session(engine) as s:
                            s.add(ArtWork(**artwork))
                            s.commit()
                            log.debug("Post: Inserted ArtWork: %s.", artwork)
                        if data.reply:
                            send_media_group(**reply_data, style=data.pixiv)
                            _reply(update, f'Posted\\!\n{esc(art["link"])}')
                else:
                    with Session(engine) as s:
                        u = s.get(User, update.effective_chat.id)
                        u.last_info = art
                        s.commit()
                    _reply(
                        update,
                        "Please, choose illustrations to download\\: "
                        f'\\[`1`\\-`{len(art["links"])}`\\]\\.',
                    )
                    continue
        # upload to cloud
        download_media(art, user=update.effective_chat.id)


def universal(update: Update, context: CallbackContext) -> None:
    """Universal function for handling posting

    Args:
        update (Update): current update
        context (CallbackContext): current context
    """
    notify(update, func="universal")
    # get data
    if not (data := get_user_data(update)):
        return log.error("No data for user: %s.", update.effective_chat.id)
    # check for text
    if not (text := update.effective_message.text):
        # check for caption
        if not (text := update.effective_message.caption):
            # no text found!
            return log.error("No text.")
    # check for links
    if links := formatter(text):
        if len(links) > 1:
            if any(link.type == LinkType.PIXIV for link in links):
                _error(update, "Can't process pixiv links in *batch* mode\\.")
            links = [link for link in links if link.type == LinkType.TWITTER]
        if not data.forward:
            no_forwarding(update, context, data, links)
        else:
            if update.effective_message.forward_date:
                just_forwarding(update, context, data, links)
            else:
                just_posting(update, context, data, links)
    elif data.info and re.search(pixiv_regex, text):
        pixiv_parse(update, context, data, text)
    else:
        log.info("No idea what to do with message: '%s'.", text)


def answer_query(update: Update, context: CallbackContext) -> None:
    notify(update, func="answer_query")
    chat_id = update.effective_chat.id
    if not (data := get_user_data(update)):
        return
    if not data.forward:
        _error(
            update,
            "Forwarding mode is turned off\\! Please, turn it on to proceed\\.",
        )
    update.callback_query.answer()
    links = update.effective_message.entities
    link, posted = links[0], links[1:-3]
    text = ", and ".join([f"[here]({esc(post['url'])})" for post in posted])
    if not (art := get_links(formatter(link["url"])[0])):
        log.error("Couldn't get content: '%s'.", link.link)
        _error(update, "Couldn't get this content\\!")
        return
    notify(update, art=art)
    art = art._asdict()
    artwork = {
        "aid": art["id"],
        "type": art["type"],
        "channel": data.channel,
        "is_original": False,
        "is_forwarded": False,
    }
    if art["type"] == LinkType.TWITTER:
        if post := send_post(
            context,
            art,
            chat_id=data.channel_id,
        ):
            with Session(engine) as s:
                s.add(
                    ArtWork(
                        **artwork,
                        post_id=post.message_id,
                        post_date=post.date,
                        files=extract_media_ids(art),
                    )
                )
                s.commit()
            if data.reply:
                _reply(update, f'Posted\\!\n{esc(art["link"])}')
            if data.media:
                send_media_doc(
                    context,
                    art,
                    media_filter=["video", "animated_gif"],
                    chat_id=data.channel_id,
                    reply_to_message_id=post.message_id,
                )
            download_media(art, user=chat_id)
        result = "`\\[` *POST HAS BEEN POSTED\\.* `\\]`"
    elif art["type"] == LinkType.PIXIV:
        if (
            len(art["links"]) == 1
            or data.pixiv == PixivStyle.INFO_LINK
            or data.pixiv == PixivStyle.INFO_EMBED_LINK
        ):
            if post := send_media_group(
                context,
                art,
                style=data.pixiv,
                chat_id=data.channel_id,
            ):
                with Session(engine) as s:
                    if not isinstance(post, Message):
                        post = post[0]
                    s.add(
                        ArtWork(
                            **artwork,
                            post_id=post.message_id,
                            post_date=post.date,
                            files=extract_media_ids(art),
                        )
                    )
                    s.commit()
                if data.reply:
                    send_media_group(
                        context,
                        art,
                        style=data.pixiv,
                        reply_to_message_id=update.effective_message.message_id,
                        chat_id=chat_id,
                    )
                    _reply(update, f'Posted\\!\n{esc(art["link"])}')
                download_media(art, user=chat_id)
                result = "`\\[` *POST HAS BEEN POSTED\\.* `\\]`"
        else:
            with Session(engine) as s:
                u = s.get(User, update.effective_message.chat_id)
                u.last_info = art
                s.commit()
            _reply(
                update,
                "Please, choose illustrations to download\\: "
                f'\\[`1`\\-`{len(art["links"])}`\\]\\.',
            )
            result = "`\\[` *PLEASE, SPECIFY DATA\\.* `\\]`"

    return update.effective_message.edit_text(
        f'~This [artwork]({esc(art["link"])}) was already posted\\: {text}~\\.'
        f"\n\n{result}",
        parse_mode=ParseMode.MARKDOWN_V2,
    )


def handle_post(update: Update, context: CallbackContext) -> None:
    notify(update, func="handle_post")
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
                if (
                    s.query(ArtWork)
                    .where(ArtWork.channel_id == update.effective_chat.id)
                    .where(ArtWork.post_id == mes.message_id)
                    .count()
                ):
                    log.info("Already in database. Skipping...")
                    return
                c = None
                if getattr(mes, "forward_from_chat"):
                    c = s.get(Channel, mes.forward_from_chat.id)
                    log.info("Forwarded channel: '%s'.", c.name)
                s.add(ArtWork(**artwork, forwarded_channel=c))
                s.commit()
    return


################################################################################
# main body
################################################################################


def main() -> None:
    """Set up and run the bot"""
    # create updater & dispatcher
    updater = Updater(os.getenv("TOKEN"))

    # start bot
    webhook = "".join(
        "https://",
        os.getenv("APP_NAME"),
        ".herokuapp.com/",
        os.getenv("TOKEN"),
    )
    updater.start_webhook(
        listen="0.0.0.0",
        port=int(os.getenv.get("PORT", "8443")),
        url_path=os.getenv("TOKEN"),
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

    # stop bot
    updater.idle()


if __name__ == "__main__":
    main()
