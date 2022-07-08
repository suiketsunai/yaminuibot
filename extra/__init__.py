import os
import re

from dataclasses import dataclass

# working with env
from dotenv import load_dotenv

# load .env file
load_dotenv()

# pixiv styles
class PixivStyle:
    styles = (
        IMAGE_LINK,
        IMAGE_INFO_LINK,
        IMAGE_INFO_EMBED_LINK,
        IMAGE_INFO_EMBED_LINK_DESC,
        INFO_LINK,
        INFO_EMBED_LINK,
    ) = range(6)

    @classmethod
    def validate(cls, value: int):
        return value in cls.styles


# twitter styles
class TwitterStyle:
    styles = (
        LINK,
        IMAGE_LINK,
        IMAGE_INFO_EMBED_LINK,
        IMAGE_INFO_EMBED_LINK_DESC,
    ) = range(4)

    @classmethod
    def validate(cls, value: int):
        return value in cls.styles


# link types
class LinkType:
    types = (
        TWITTER,
        PIXIV,
    ) = range(2)

    @classmethod
    def validate(cls, value: int):
        return value in cls.types


# link dictionary
link_dict = {
    "twitter": {
        "re": r"""(?x)
            (?:
                (?:www\.)?
                (?:twitter\.com\/)
                (?P<author>.+?)\/
                (?:status(?:es)?\/)
            )
            (?P<id>\d+)
        """,
        "link": "https://twitter.com/{author}/status/{id}",
        "full": "https://pbs.twimg.com/media/{id}?format={format}&name=orig",
        "type": LinkType.TWITTER,
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
        "type": LinkType.PIXIV,
    },
}

# user data dictionary
@dataclass
class UserData:
    forward: bool
    reply: bool
    media: bool
    pixiv: int
    twitter: int
    info: dict
    chan: int = 0


# telegram bot states
class BotState:
    states = (CHANNEL,) = map(chr, range(1))

    @classmethod
    def validate(cls, value: chr):
        return value in cls.types


# upload dictionary
upl_dict = {
    "user": int(os.getenv("USER_ID") or 0),
    "media": os.getenv("GD_MEDIA"),
    "log": os.getenv("GD_LOG"),
}

################################################################################
# hardcode
################################################################################

# fake headers
fake_headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:97.0) Gecko/20100101 Firefox/97.0",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.5",
}


# helper dictionary
switcher = {
    True: "enabled",
    False: "disabled",
}

# callback query result
result_message = [
    "`\\[` *POST HAS BEEN POSTED\\.* `\\]`",
    "`\\[` *PLEASE, SPECIFY DATA\\.* `\\]`",
    "`\\[` *????????????????????\\.* `\\]`",
]

# pixiv regex
pixiv_regex = re.compile(r"^((?:\d+)(?:-\d+)?[.,\s]*){1,10}$")
pixiv_number = re.compile(r"((?P<n1>\d+)(?:-(?P<n2>\d+))?)")

# telegram deep linking
telegram_link = "t.me/c/{cid}/{post_id}"

# filename pattern
file_pattern = r".*\/(?P<name>.*?)((\?.*format\=)|(\.))(?P<format>\w+).*$"

# twitter link id
twitter_regex = r"(?:.*\/(?P<id>.+)(?:\.|\?f))"
