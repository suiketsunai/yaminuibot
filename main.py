"""Main module"""
import os
import re
import logging

from pathlib import Path
from functools import partial

# working with env
from dotenv import load_dotenv

# working with database
from sqlalchemy.orm import Session

# telegram core bot api
from telegram import (
    InlineKeyboardMarkup,
    Message,
    Update,
    InputMediaPhoto,
    InputMediaVideo,
    InputMediaDocument,
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

# telegram constants
from telegram.constants import PARSEMODE_MARKDOWN_V2 as MDV2

# escaping special markdown characters
from telegram.utils.helpers import escape_markdown

# database engine
from db import engine

# database models
from db.models import User, Post, Channel, ArtWork

# pixiv styles, link types, user data dataclass
from extra import PixivStyle, LinkType, TwitterStyle, UserData

# expressions
from extra import pixiv_number, pixiv_regex, telegram_link

# dictionaries
from extra import switcher, result_message

# bot states
from extra import BotState

# settings
from extra.loggers import root_log

# namedtuples
from extra.namedtuples import ArtWorkMedia, Link

# helpers
from extra.helpers import formatter, get_links, get_post_link, extract_media_ids

# downloading media
from extra.download import download_media

# uploading media
from extra.upload import upload_media, upload_log

# dumping db
from db.dump_db import dump_db

# migrating db
from db.migrate_db import migrate_db

# load .env file
load_dotenv()

# setup loggers
log = logging.getLogger("yaminuichan.app")
sys_log = logging.getLogger("yaminuichan.system")

################################################################################
# telegram bot helpers section
################################################################################

# escaping markdown v2
esc = partial(escape_markdown, version=2)


def rep(update: Update) -> dict:
    """Get current chat and message for bot to reply to

    Args:
        update (Update): current update

    Returns:
        dict: current chat and message
    """
    return {
        "chat_id": update.effective_chat.id,
        "reply_to_message_id": update.effective_message.message_id,
    }


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


def _post(update: Update, text: str, cid: int, pid: int, link: str) -> Message:
    """Reply to current message with link to posted content

    Args:
        update (Update): current update
        text (str): description of action
        cid (int): channel internal id
        pid (int): channel post id
        link (str): content original link

    Returns:
        Message: Telegram Message
    """
    text, post, link = esc(text), esc(get_post_link(cid, pid)), esc(link)
    _reply(update, f"*[Artwork]({link})* was *[{text}]({post})*\\!")


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
    """Reply to current message with warning

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
        reply_to_message_id=update.effective_message.message_id,
        reply_markup=InlineKeyboardMarkup.from_button(
            InlineKeyboardButton(text="Post!", callback_data="post")
        ),
        **kwargs,
    )


def send_post(
    context: CallbackContext,
    *,
    info: dict = None,
    text: str = None,
    **kwargs,
) -> Message | None:
    """Send post to channel

    Args:
        context (CallbackContext): current context
        info (dict): art media dictionary
        text (str): text to send

    Returns:
        Message | None: Telegram Message
    """
    if info:
        text = esc(info["link"])
    if text:
        return context.bot.send_message(
            text=text,
            parse_mode=MDV2,
            **kwargs,
        )
    return log.error("Send Post: No text or info supplied.")


def send_media(
    context: CallbackContext,
    info: dict,
    *,
    order: list[int] = None,
    style: int = None,
    **kwargs,
) -> Message | None:
    """Send media as media group

    Args:
        context (CallbackContext): current context
        info (dict): art media dictionary
        order (list[int], optional): which artworks to upload. Defaults to None.
        style (int, optional): pixiv sryle. Defaults to None.

    Returns:
        Message | None: Telegram Message
    """
    if not info:
        return log.error("Send Media: No info supplied.")
    caption = ""
    match info["type"]:
        case LinkType.PIXIV:
            match style:
                case PixivStyle.IMAGE_LINK:
                    caption = esc(info["link"])
                case PixivStyle.IMAGE_INFO_LINK:
                    caption = esc(
                        f'{info["desc"]} | {info["user"]}\n{info["link"]}'
                    )
                case PixivStyle.IMAGE_INFO_EMBED_LINK:
                    temp = esc(f'{info["desc"]} | {info["user"]}\n')
                    caption = f'[{temp}]({esc(info["link"])})'
                case PixivStyle.INFO_LINK:
                    caption = esc(
                        f'{info["desc"]} | {info["user"]}\n{info["link"]}'
                    )
                    return send_post(context, text=caption, **kwargs)
                case PixivStyle.INFO_EMBED_LINK:
                    temp = esc(f'{info["desc"]} | {info["user"]}\n')
                    caption = f'[{temp}]({esc(info["link"])})'
                    return send_post(context, text=caption, **kwargs)
                case _:
                    caption = esc(info["link"])
        case LinkType.TWITTER:
            match style:
                case TwitterStyle.LINK:
                    caption = esc(info["link"])
                    return send_post(context, text=caption, **kwargs)
                case TwitterStyle.IMAGE_LINK:
                    caption = esc(info["link"])
                case TwitterStyle.IMAGE_INFO_EMBED_LINK:
                    temp = esc(f'{info["user"]} | @{info["username"]}\n')
                    caption = f'[{temp}]({esc(info["link"])})'
                case TwitterStyle.IMAGE_INFO_EMBED_LINK_DESC:
                    temp = esc(f'{info["user"]} | @{info["username"]}\n')
                    desc = esc(info["desc"])
                    caption = f'[{temp}]({esc(info["link"])})\n\n{desc}'
                case _:
                    caption = esc(info["link"])
    media = []
    for file in download_media(info, full=False, order=order):
        match info["media"]:
            case "video" | "animated_gif":
                media.append(InputMediaVideo(file.read_bytes()))
            case _:
                media.append(InputMediaPhoto(file.read_bytes()))
        file.unlink()
    media[0].caption = caption
    media[0].parse_mode = MDV2
    # answer to pixiv artwork
    if "reply_to_message_id" in kwargs and "message_id" in info:
        kwargs["reply_to_message_id"] = info["message_id"]
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
) -> Message | None:
    """Send media as documents

    Args:
        context (CallbackContext): current context
        info (dict): art media dictionary
        media_filter (list[str], optional): types to send. Defaults to None.
        order (list[int], optional): which artworks to upload. Defaults to None.

    Returns:
        Message | None: Telegram Message
    """
    if not info:
        return log.error("Send Media Doc: No info supplied.")
    if media_filter and info["media"] not in media_filter:
        return log.debug("Send Media Doc: Didn't pass media filter.")
    log.debug("Send Media Doc: Passed media filter.")
    documents = []
    for file in download_media(info, order=order):
        documents.append(
            InputMediaDocument(
                media=file.read_bytes(),
                filename=file.name,
                disable_content_type_detection=True,
            )
        )
        file.unlink()
    # answer to pixiv artwork
    if "reply_to_message_id" in kwargs and "message_id" in info:
        kwargs["reply_to_message_id"] = info["message_id"]
    context.bot.send_media_group(
        media=documents,
        **kwargs,
    )


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
    art: dict = None,
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
            "[%d] %r called command: %r.",
            update.effective_chat.id,
            update.effective_chat.full_name or update.effective_chat.title,
            command,
        )
    if func:
        sys_log.debug(
            "[%d] %r called function: %r.",
            update.effective_chat.id,
            update.effective_chat.full_name or update.effective_chat.title,
            func,
        )
    if art:
        sys_log.info(
            "[%d] %r received content: [%d/%s] %r by [%d/@%s] %r | %s.",
            update.effective_chat.id,
            update.effective_chat.full_name or update.effective_chat.title,
            art["id"],
            art["media"],
            art["desc"],
            art["user_id"],
            art["username"],
            art["user"],
            art["date"],
        )
    if toggle:
        sys_log.info(
            "[%d] %r called toggler: %r is now %s.",
            update.effective_chat.id,
            update.effective_chat.full_name or update.effective_chat.title,
            toggle[0],
            switcher[toggle[1]],
        )


def channel_check(update: Update, context: CallbackContext) -> int | None:
    """Checks if channel is a valid choice

    Args:
        update (Update): current update
        context (CallbackContext): current context

    Returns:
        int | None: ConversationHandler state
    """
    mes = update.effective_message
    if channel := mes.forward_from_chat:
        if channel.type == "supergroup":
            _error(update, "This message is from a supergroup\\.")
            return log.error("Channel: This message is from a supergroup.")
        with Session(engine) as s:
            if (c := s.get(Channel, channel.id)) and c.admin:
                _error(update, "This channel is *already* owned\\.")
                return log.error("Channel: [%s] is already owned.", channel.id)
        _reply(
            update,
            "*Seems fine\\!* ✨\nChecking for *admin rights*\\.\\.\\.",
        )
        bot_id = int(os.getenv("TOKEN").split(":")[0])
        chat_id = update.effective_chat.id
        chan_id = channel.id
        try:
            if not (
                (_bot := context.bot.get_chat_member(chan_id, bot_id))
                and getattr(_bot, "can_post_messages")
                and (_user := context.bot.get_chat_member(channel.id, chat_id))
                and _user.status in ["creator", "administrator"]
            ):
                _error(
                    update,
                    "Either *bot* or *you* are not admin of this channel\\!",
                )
                return log.error("Channel: No admin rights for user or bot.")
            with Session(engine) as s:
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
                "*Done\\!* 🎉\n*Your channel* is added to the database\\!",
            )
            del context.user_data[BotState.CHANNEL]
            return ConversationHandler.END
        except Unauthorized as ex:
            _error(update, "The bot *was kicked* from this channel\\!")
            return log.error("Channel: The bot was kicked from this channel.")
    _error(update, "Please, *forward* a message from *your channel*\\.")
    return log.error("Channel: This message is from a user.")


################################################################################
# database functions
################################################################################


def get_artwork(aid: int, type: int) -> ArtWork:
    """Gets artwork if it is already in database

    Args:
        aid (int): artwork id
        type (int): artwork type

    Returns:
        ArtWork: found artwork
    """
    with Session(engine) as s:
        return s.query(ArtWork).filter_by(aid=aid, type=type).first()


def get_other_links(aid: int, type: int) -> list[str]:
    """Get already posted instances of artwork

    Args:
        aid (int): artwork id
        type (int): artwork type

    Returns:
        list[str]: list of links to posts
    """
    with Session(engine) as s:
        return [
            telegram_link.format(**item)
            for item in (
                s.query(Post.post_id, Channel.cid)
                .filter(Post.channel_id == Channel.id)
                .filter(Post.artwork == get_artwork(aid, type))
                .order_by(Post.post_date.asc())
                .all()
            )
        ]


def get_user_data(update: Update) -> UserData | None:
    """Get current user's current data

    Args:
        update (Update): current update

    Returns:
        UserData | None: current user's current data
    """
    with Session(engine) as s:
        if u := s.get(User, update.effective_chat.id):
            data = UserData(
                u.forward_mode,
                u.reply_mode,
                u.media_mode,
                u.pixiv_style,
                u.twitter_style,
                u.last_info,
            )
            if u.forward_mode:
                if not (channel := u.channel):
                    _error(update, "You have no channel\\! Send /channel\\.")
                    return None
                data.chan = channel.id
            return data
        _error(update, "The bot doesn\\'t know you\\! Send /start\\.")
        return None


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


def pixiv_save(update: Update, art: dict) -> None:
    """Save current art media data to user's last_info

    Args:
        update (Update): current update
        art (dict): art media dictionary
    """
    notify(update, func="pixiv_save")
    with Session(engine) as s:
        u = s.get(User, update.effective_chat.id)
        art["message_id"] = update.effective_message.message_id
        u.last_info = art
        s.commit()
    log.debug("Added last info to user [%d].", update.effective_chat.id)
    # prompt user to choose illustrations
    _reply(
        update,
        "Please, choose illustrations to download\\: "
        f'\\[`1`\\-`{len(art["links"])}`\\]\\.',
    )


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
    if context.user_data.get(BotState.CHANNEL, None):
        _reply(
            update,
            "*Ehm\\.\\.\\.*\n"
            "Please, forward a post from *your channel* already\\.",
        )
        return BotState.CHANNEL
    context.user_data[BotState.CHANNEL] = True
    _reply(
        update,
        "*Sure\\!* 💫\n"
        "Please, add *this bot* to *your channel* as admin\\.\n"
        "Then, forward a message from *your channel* to me\\.",
    )
    return BotState.CHANNEL


def command_cancel(update: Update, context: CallbackContext) -> int:
    """Cancels and ends the conversation"""
    notify(update, command="/cancel")
    if context.user_data.get(BotState.CHANNEL, None):
        context.user_data[BotState.CHANNEL] = False
        _reply(update, "*Okay\\!* 👌\nYou can add *your channel* any time\\.")
        return ConversationHandler.END
    else:
        _reply(update, "*Yeah, sure\\.* 👀\nCancel all you want\\.")


def command_forward(update: Update, _) -> None:
    """Enables/Disables forwarding to channel"""
    notify(update, command="/forward")
    _reply(
        update,
        f"Forwarding mode is *{switcher[toggler(update, 'forward_mode')]}*\\.",
    )


def command_reply(update: Update, _) -> None:
    """Enables/Disables replying to messages"""
    notify(update, command="/reply")
    _reply(
        update,
        f"Replying mode is *{switcher[toggler(update, 'reply_mode')]}*\\.",
    )


def command_media(update: Update, _) -> None:
    """Enables/Disables adding video/gif to links"""
    notify(update, command="/media")
    _reply(
        update,
        f"Media mode is *{switcher[toggler(update, 'media_mode')]}*\\.",
    )


def command_pixiv_style(update: Update, _) -> None:
    """Change pixiv style."""
    notify(update, command="/pixiv_style")
    # get old and new styles
    with Session(engine) as s:
        u = s.get(User, update.effective_chat.id)
        style = PixivStyle.styles[(u.pixiv_style + 1) % len(PixivStyle.styles)]
        u.pixiv_style = style
        s.commit()
    # demonstrate new style
    link = esc("https://www.pixiv.net/")
    match style:
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
    _reply(update, f"_Pixiv style has been changed to_\\:\n\n{style}")


def command_twitter_style(update: Update, _) -> None:
    """Change twitter style."""
    notify(update, command="/twitter_style")
    # get old and new styles
    with Session(engine) as s:
        u = s.get(User, update.effective_chat.id)
        style = TwitterStyle.styles[
            (u.twitter_style + 1) % len(TwitterStyle.styles)
        ]
        u.twitter_style = style
        s.commit()
    # demonstrate new style
    link = esc("https://twitter.com/")
    match style:
        case TwitterStyle.LINK:
            style = "Link"
        case TwitterStyle.IMAGE_LINK:
            style = "\\[ `Image(s)` \\]\n\nLink"
        case TwitterStyle.IMAGE_INFO_EMBED_LINK:
            style = f"\\[ `Image(s)` \\]\n\n[Author \\| @Username]({link})"
        case TwitterStyle.IMAGE_INFO_EMBED_LINK_DESC:
            style = f"\\[ `Image(s)` \\]\n\n[Author \\| @Username]({link})\n\nDescription"
        case _:
            style = "Unknown"
    _reply(update, f"_Twitter style has been changed to_\\:\n\n{style}")


################################################################################
# telegram text message handlers
################################################################################


def get_text(update: Update):
    return "|".join(
        text
        for text in [
            update.effective_message.text,
            update.effective_message.caption,
        ]
        + [
            entity.url
            for entity in update.effective_message.entities
            + update.effective_message.caption_entities
        ]
        if text
    )


def check_message_media(update: Update):
    return bool(
        update.effective_message.animation
        or update.effective_message.document
        or update.effective_message.photo
        or update.effective_message.video
    )


def pixiv_parse(
    update: Update,
    context: CallbackContext,
    data: UserData,
    text: str,
) -> None:
    notify(update, func="pixiv_parse")
    # speed up
    art = data.info
    # initial data
    count = len(art["links"])
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
        _error(update, "You *can\\'t* choose more than 10 files\\!")
        return log.error("Pixiv: Can't choose more than 10 files.")
    if max(ids) > count or min(ids) < 1:
        _error(update, f"*Not within* range: \\[`1`\\-`{count}`\\]\\!")
        return log.error("Pixiv: Not within range: [1-%d].", count)
    log.debug("Pixiv: Chosen artworks: %r.", ids)
    # save for reuse
    com = {"context": context, "info": art, "order": ids}
    post = {
        "channel_id": data.chan,
        "is_original": False,
        "is_forwarded": False,
    }
    artwork = {
        "aid": art["id"],
        "type": art["type"],
    }
    if not (a := get_artwork(**artwork)):
        notify(update, art=art)
        artwork["files"] = extract_media_ids(art)
        a = ArtWork(**artwork)
        post["is_original"] = True
        log.debug("Pixiv: ArtWork to insert: %s.", artwork)
    if data.forward:
        if not (
            posted := send_media(**com, style=data.pixiv, chat_id=data.chan)
        ):
            _error(update, "Coudn't post\\!")
            return log.error("Pixiv: Couldn't post.")
        log.info("Pixiv: Successfully posted to channel.")
        if not isinstance(posted, Message):
            posted = posted[0]
        post.update(
            {
                "post_id": posted.message_id,
                "post_date": posted.date,
            }
        )
        with Session(engine) as s:
            s.add(Post(**post, artwork=a))
            s.commit()
            log.debug("Pixiv: Inserted Post: %s.", post)
        if data.reply:
            send_media(**com, **rep(update), style=data.pixiv)
            _post(
                update,
                "posted",
                data.chan,
                posted.message_id,
                art["link"],
            )
    else:
        if data.reply:
            send_media(**com, **rep(update), style=data.pixiv)
        send_media_doc(**com, **rep(update))
    # upload to cloud
    upload_media(info=art, order=ids, user=update.effective_chat.id)
    # clean last_info for user
    with Session(engine) as s:
        u = s.get(User, update.effective_chat.id)
        u.last_info = None
        s.commit()


def no_forwarding(
    update: Update,
    context: CallbackContext,
    data: UserData,
    links: list[Link],
) -> None:
    notify(update, func="no_forwarding")
    # process links
    for link in links:
        if not (art := get_links(link)):
            log.error("Couldn't get content: %r.", link.link)
            _error(update, "Couldn't get this content\\!")
            continue
        art = art._asdict()
        notify(update, art=art)
        com = {"context": context, "info": art, **rep(update)}
        match link.type:
            # twitter links
            case LinkType.TWITTER:
                if data.reply:
                    send_media(**com, style=data.twitter)
                send_media_doc(**com)
            # one pixiv link
            case LinkType.PIXIV:
                if len(art["links"]) > 1:
                    log.info("There's more than 1 artwork.")
                    pixiv_save(update, art)
                    return
                log.info("There's only 1 artwork.")
                if data.reply:
                    send_media(**com, style=data.pixiv)
                send_media_doc(**com)
        # upload to cloud
        upload_media(art, user=update.effective_chat.id)


def just_forwarding(
    update: Update,
    context: CallbackContext,
    data: UserData,
    links: list[Link],
) -> None:
    notify(update, func="just_forwarding")
    # check if media group message
    if getattr(update.effective_message, "media_group_id"):
        log.error("Forward: Bots can't forward media groups.")
        return _error(
            update,
            "Unfortunately, bots can\\'t *forward* messages with more than 1 "
            "media \\(photo/video\\) just yet\\. But they can *post* them\\! "
            "So, please, *for now*, forward this kind of messages yourself\\. "
            "This may change in the future Telegram Bot API updates\\.",
        )
    # check if more than 1 link in message
    if len(links) > 1:
        log.error("Forward: More than 1 link.")
        return _error(update, "Only *one link* is allowed for forwarding\\!")
    # and so there's one link
    link = links[0]
    post = {
        "channel_id": data.chan,
        "is_original": False,
        "is_forwarded": True,
    }
    artwork = {"aid": link.id, "type": link.type}
    # can be ignored for this one
    if art := get_links(link):
        art = art._asdict()
        notify(update, art=art)
    if not (a := get_artwork(**artwork)):
        if art:
            artwork["files"] = extract_media_ids(art)
        else:
            log.warning("Forward: Couldn't get content: %r.", link.link)
        a = ArtWork(**artwork)
        log.debug("Forward: ArtWork to insert: %s.", artwork)
    # check if it's forwarded from channel in database
    with Session(engine) as s:
        if src := update.effective_message.forward_from_chat:
            if c := s.get(Channel, src.id):
                post["forwarded_channel_id"] = c.id
                log.info("Forward: Source: %r [%d].", c.name, c.cid)
                if c.id == data.chan:
                    log.error("Forward: Self-forwarding is no allowed.")
                    return _error(update, "You shouldn't *self\\-forward*\\!")
            else:
                log.info("Forward: Source: unknown.")
        else:
            log.info("Forward: Source: not a channel.")
    # just forward it
    if posted := forward(update, data.chan):
        log.info("Forward: Successfully forwarded to channel.")
        post.update(
            {
                "post_id": posted.message_id,
                "post_date": posted.date,
            }
        )
        with Session(engine) as s:
            s.add(Post(**post, artwork=a))
            s.commit()
            log.debug("Forward: Inserted Post: %s.", post)
        if data.reply:
            _post(
                update,
                "forwarded",
                data.chan,
                posted.message_id,
                art["link"] if art else link.link,
            )
        if data.media and not check_message_media(update):
            if art:
                if send_media_doc(
                    context=context,
                    info=art,
                    media_filter=["video", "animated_gif"],
                    chat_id=data.chan,
                    reply_to_message_id=posted.message_id,
                ):
                    log.info("Forward: Successfully replied with media.")
            else:
                _error(update, "*Media mode*\\: Couldn't get this content\\!")
                log.warning("Forward: Couldn't reply with media.")
    # upload to cloud
    upload_media(art, user=update.effective_chat.id)


def just_posting(
    update: Update,
    context: CallbackContext,
    data: UserData,
    links: list[Link],
) -> None:
    notify(update, func="just_posting")
    # process links
    for link in links:
        if get_artwork(link.id, link.type):
            log.warning("Post: Content is not original: %r.", link.link)
            _warn(update, link)
            continue
        post = {
            "channel_id": data.chan,
            "is_original": True,
            "is_forwarded": False,
        }
        if not (art := get_links(link)):
            log.error("Post: Couldn't get content: %r.", link.link)
            _error(update, "Couldn't get this content\\!")
            continue
        art = art._asdict()
        notify(update, art=art)
        artwork = {
            "aid": link.id,
            "type": link.type,
            "files": extract_media_ids(art),
        }
        com = {"context": context, "info": art}
        match link.type:
            # twitter links
            case LinkType.TWITTER:
                if posted := send_media(
                    **com, style=data.twitter, chat_id=data.chan
                ):
                    log.info("Post: Successfully forwarded to channel.")
                    if not isinstance(posted, Message):
                        posted = posted[0]
                    post.update(
                        {
                            "post_id": posted.message_id,
                            "post_date": posted.date,
                        }
                    )
                    with Session(engine) as s:
                        s.add(Post(**post, artwork=ArtWork(**artwork)))
                        s.commit()
                        log.debug("Post: Inserted ArtWork: %s.", artwork)
                        log.debug("Post: Inserted Post: %s.", post)
                    if data.reply:
                        _post(
                            update,
                            "posted",
                            data.chan,
                            posted.message_id,
                            art["link"],
                        )
                    if data.media and data.twitter == TwitterStyle.LINK:
                        send_media_doc(
                            **com,
                            media_filter=["video", "animated_gif"],
                            chat_id=data.chan,
                            reply_to_message_id=posted.message_id,
                        )
            # pixiv links
            case LinkType.PIXIV:
                if (
                    len(art["links"]) == 1
                    or data.pixiv == PixivStyle.INFO_LINK
                    or data.pixiv == PixivStyle.INFO_EMBED_LINK
                ):
                    if posted := send_media(
                        **com, style=data.pixiv, chat_id=data.chan
                    ):
                        log.info("Post: Successfully forwarded to channel.")
                        if not isinstance(posted, Message):
                            posted = posted[0]
                        post.update(
                            {
                                "post_id": posted.message_id,
                                "post_date": posted.date,
                            }
                        )
                        with Session(engine) as s:
                            s.add(Post(**post, artwork=ArtWork(**artwork)))
                            s.commit()
                            log.debug("Post: Inserted ArtWork: %s.", artwork)
                            log.debug("Post: Inserted Post: %s.", post)
                        if data.reply:
                            send_media(**com, **rep(update), style=data.pixiv)
                            _post(
                                update,
                                "posted",
                                data.chan,
                                posted.message_id,
                                art["link"],
                            )
                else:
                    pixiv_save(update, art)
                    continue
        # upload to cloud
        upload_media(art, user=update.effective_chat.id)


def universal(update: Update, context: CallbackContext) -> None:
    """Universal function for handling posting

    Args:
        update (Update): current update
        context (CallbackContext): current context
    """
    notify(update, command="universal")
    # get user data
    if not (data := get_user_data(update)):
        return log.error("Universal: No data: [%d].", update.effective_chat.id)
    # check for text
    if not (text := get_text(update)):
        # no text found!
        return log.error("Universal: No text.")
    log.debug("Received text: %r.", text)
    # check for links
    if links := formatter(text):
        if len(links) > 1:
            if any(link.type == LinkType.PIXIV for link in links):
                _error(update, "Can't process pixiv links in *batch* mode\\.")
                log.warning("Universal: Pixiv links are not allowed.")
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
        log.info("Universal: No idea what to do with message: %r.", text)


def answer_query(update: Update, context: CallbackContext) -> None:
    notify(update, command="answer_query")
    # get user data
    if not (data := get_user_data(update)):
        return log.error("Query: No data: [%d].", update.effective_chat.id)
    # check for forward mode
    if not data.forward:
        _error(update, "Forwarding mode is *off*\\! Turn it *on* to proceed\\.")
        return log.error("Query: Forwarding mode is turned off!")
    # answer callback query
    update.callback_query.answer()
    # get message info
    links = update.effective_message.entities
    link, posted = links[0], links[1:-3]
    text = ", and ".join([f"[here]({esc(post['url'])})" for post in posted])
    post = {
        "channel_id": data.chan,
        "is_original": False,
        "is_forwarded": False,
    }
    if not (art := get_links(formatter(link["url"])[0])):
        _error(update, "Couldn't get this content\\!")
        return log.error("Query: Couldn't get content: %r.", link.link)
    art = art._asdict()
    notify(update, art=art)
    post["artwork"] = get_artwork(art["id"], art["type"])
    com = {"context": context, "info": art}
    match art["type"]:
        # twitter links
        case LinkType.TWITTER:
            if posted := send_media(
                **com, style=data.twitter, chat_id=data.chan
            ):
                log.info("Query: Successfully posted to channel.")
                if not isinstance(posted, Message):
                    posted = posted[0]
                post.update(
                    {
                        "post_id": posted.message_id,
                        "post_date": posted.date,
                    }
                )
                with Session(engine) as s:
                    s.add(Post(**post))
                    s.commit()
                    log.debug("Query: Inserted Post: %s.", post)
                if data.reply:
                    _post(
                        update,
                        "posted",
                        data.chan,
                        posted.message_id,
                        art["link"],
                    )
                if data.media and data.twitter == TwitterStyle.LINK:
                    send_media_doc(
                        **com,
                        media_filter=["video", "animated_gif"],
                        chat_id=data.chan,
                        reply_to_message_id=posted.message_id,
                    )
                result = 0
            else:
                result = 2
        # pixiv links
        case LinkType.PIXIV:
            if (
                len(art["links"]) == 1
                or data.pixiv == PixivStyle.INFO_LINK
                or data.pixiv == PixivStyle.INFO_EMBED_LINK
            ):
                if posted := send_media(
                    **com, style=data.pixiv, chat_id=data.chan
                ):
                    log.info("Query: Successfully posted to channel.")
                    if not isinstance(posted, Message):
                        posted = posted[0]
                    post.update(
                        {
                            "post_id": posted.message_id,
                            "post_date": posted.date,
                        }
                    )
                    with Session(engine) as s:
                        s.add(Post(**post))
                        s.commit()
                        log.debug("Query: Inserted Post: %s.", post)
                    if data.reply:
                        send_media(**com, **rep(update), style=data.pixiv)
                        _post(
                            update,
                            "posted",
                            data.chan,
                            posted.message_id,
                            art["link"],
                        )
                    result = 0
                else:
                    result = 2
            else:
                pixiv_save(update, art)
                result = 1
    update.effective_message.edit_text(
        f'~This [artwork]({esc(art["link"])}) was already posted\\: {text}~\\.'
        f"\n\n{result_message[result]}",
        parse_mode=MDV2,
    )
    # upload to cloud
    if not result:
        upload_media(art, user=update.effective_chat.id)


def handle_post(update: Update, _) -> None:
    notify(update, command="handle_post")
    # speed up
    message = update.effective_message
    # check for text
    if not (text := get_text(update)):
        # no text found!
        return log.error("Handle Post: No text.")
    log.debug("Received text: %r.", text)
    if links := formatter(text):
        if len(links) > 1:
            return log.error("Handle Post: More than 1 link in post.")
        else:
            link = links[0]
            post = {
                "channel_id": update.effective_chat.id,
                "is_original": False,
                "is_forwarded": bool(message.forward_date),
                "post_id": message.message_id,
                "post_date": message.date,
            }
            artwork = {"aid": link.id, "type": link.type}
            # can be ignored for this one
            if art := get_links(link):
                art = art._asdict()
                notify(update, art=art)
            if not (a := get_artwork(**artwork)):
                if art:
                    artwork["files"] = extract_media_ids(art)
                else:
                    log.warning(
                        "Handle Post: Couldn't get content: %r.", link.link
                    )
                a = ArtWork(**artwork)
                log.debug("Handle Post: ArtWork to insert: %s.", artwork)
                post["is_original"] = True
            with Session(engine) as s:
                if (
                    s.query(Post)
                    .filter_by(channel_id=update.effective_chat.id)
                    .filter_by(post_id=message.message_id)
                    .first()
                ):
                    log.info("Handle Post: Already in database. Skipping...")
                    return
                if src := message.forward_from_chat:
                    if c := s.get(Channel, src.id):
                        post["forwarded_channel_id"] = c.id
                        log.info("Handle Post: Source: %r [%d].", c.name, c.cid)
                    else:
                        log.info("Handle Post: Source: unknown.")
                else:
                    log.info("Handle Post: Source: not a channel.")
                s.add(Post(**post, artwork=a))
                s.commit()
                log.debug("Handle Post: Inserted ArtWork: %s.", artwork)
                log.debug("Handle Post: Inserted Post: %s.", post)
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
        [
            "https://",
            os.getenv("APP_NAME"),
            ".herokuapp.com/",
            os.getenv("TOKEN"),
        ]
    )
    updater.start_webhook(
        listen="0.0.0.0",
        port=int(os.getenv("PORT", "8443")),
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
            "pixiv_style",
            command_pixiv_style,
        )
    )

    # cycle through twitter styles
    dispatcher.add_handler(
        CommandHandler(
            "twitter_style",
            command_twitter_style,
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
                BotState.CHANNEL: [
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
    root_log.info("Starting the bot...")
    # start the bot
    main()
    # upload log
    upload_log()
