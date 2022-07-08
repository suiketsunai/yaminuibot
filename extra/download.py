import re
import logging

from pathlib import Path

# http requests
import requests

# working with images
from PIL import Image

# link types, fake headers, file pattern
from extra import LinkType, fake_headers, file_pattern

# get logger
log = logging.getLogger("yaminuichan.download")

# max image side length
IMAGE_LIMIT = 2560


def download_media(
    info: dict,
    *,
    full: bool = True,
    order: list[int] = None,
) -> Path | None:
    """Download files using art media dictionary depending on order list and
    yield downloaded files in full size or resized to 1280px at max size

    Args:
        info (dict): art media dictionary
        full (bool, optional): yield full size or not. Defaults to True.
        order (list[int], optional): which artworks to upload. Defaults to None.

    Yields:
        Iterator[Path | None]: downloaded file
    """
    if not info:
        return log.error("Download Media: No info supplied.")
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
    for link in links:
        file = requests.get(
            link,
            headers=headers,
            allow_redirects=True,
        )
        reg = re.search(file_pattern, link)
        if not reg:
            log.error("Download Media: Couldn't get name or format: %s.", link)
            continue
        name = ".".join([reg.group("name"), reg.group("format")])
        media_file = Path(name)
        media_file.write_bytes(file.content)
        if not full and info["media"] in ["illust", "photo"]:
            log.debug(
                "Download Media: Fitting into %d x %d size...",
                IMAGE_LIMIT,
                IMAGE_LIMIT,
            )
            try:
                image = Image.open(media_file)
                image.thumbnail([IMAGE_LIMIT, IMAGE_LIMIT])
                image.save(media_file, format="png", optimize=True)
            except Exception as ex:
                log.error("Download Media: Exception occured: %s", ex)
        yield media_file
