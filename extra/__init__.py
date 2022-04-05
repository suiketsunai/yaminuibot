import re

# pixiv styles
class PixivStyle:
    styles = (
        IMAGE_LINK,
        IMAGE_INFO_LINK,
        IMAGE_INFO_EMBED_LINK,
        INFO_LINK,
        INFO_EMBED_LINK,
    ) = range(5)

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

################################################################################
# hardcode
################################################################################

# fake headers
fake_headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:97.0) Gecko/20100101 Firefox/97.0",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.5",
}

# pixiv regex
pixiv_regex = re.compile(r"^((?:\d+)(?:-\d+)?[.,\s]*){1,10}$")
pixiv_number = re.compile(r"((?P<n1>\d+)(?:-(?P<n2>\d+))?)")

# telegram deep linking
telegram_link = "t.me/c/{cid}/{post_id}"

# filename pattern
file_pattern = r".*\/(?P<name>.*?)((\?.*format\=)|(\.))(?P<format>\w+).*$"

# twitter link id
twitter_regex = r"(?:.*\/(?P<id>.+)(?:\.|\?f))"
