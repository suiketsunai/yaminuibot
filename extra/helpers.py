import re
import logging

import requests

# import pixiv styles and link types
from extra import (
    LinkType,
    link_dict,
    fake_headers,
    twitter_regex,
    telegram_link,
)

# namedtuples
from extra.namedtuples import ArtWorkMedia, Link

# twitter
from extra.twitter import get_twitter_links

# pixiv
from extra.pixiv import get_pixiv_links

# get logger
log = logging.getLogger("yaminuichan.helper")


def extract_media_ids(art: dict) -> list[str]:
    if art["type"] == LinkType.TWITTER:
        return [re.search(twitter_regex, link)["id"] for link in art["links"]]
    if art["type"] == LinkType.PIXIV:
        return [str(art["id"])]
    return None


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
            log.info("Formatter: Received %s link: %r.", re_key, _link)
            # add to response list
            response.append(Link(re_type["type"], _link, int(link.group("id"))))
    return response


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


def get_post_link(cid: int, post_id: int) -> str:
    return telegram_link.format(cid=-(cid + 10**12), post_id=post_id)
