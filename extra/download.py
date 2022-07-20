import re
import logging

from pathlib import Path

# http requests
import requests

# file extension check
from magic import from_buffer as mfb

# working with images
from PIL import Image

# link types, fake headers, file pattern
from extra import LinkType, fake_headers, file_pattern

# get logger
log = logging.getLogger("yaminuichan.download")

# max image side length
IM_MAX = (2560, 2560)

# shrinked max image side length
IM_SHR = (2240, 2240)


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
        return log.error("No info supplied.")
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
        media = requests.get(
            link,
            headers=headers,
            allow_redirects=True,
        )
        reg = re.search(file_pattern, link)
        if not reg:
            log.error("Couldn't get name or format: %s.", link)
            continue
        name = f"{reg['name']}.{mfb(media.content, mime=True).split('/')[1]}"
        file = Path(name)
        file.write_bytes(media.content)
        if not full and info["media"] in ["illust", "photo"]:
            try:
                im = Image.open(file)
                log.debug("Original size: %d x %d.", *im.size)
                log.debug("Fitting into %d x %d...", *IM_MAX)
                im.thumbnail(IM_MAX)
                log.debug("New size: %d x %d.", *im.size)
                im.save(file, format="webp", lossless=True, optimize=True)
                if (size := file.stat().st_size) > 10 << 20:
                    log.warning("File is bigger 10 MB: %d.", size)
                    file.write_bytes(media.content)
                    im = Image.open(file)
                    log.debug("Fitting into %d x %d...", *IM_SHR)
                    im.thumbnail(IM_SHR)
                    log.debug("New size: %d x %d.", *im.size)
                    im.save(file, format="webp", lossless=True, optimize=True)
            except Exception as ex:
                log.error("Exception occured: %s.", ex)
        yield file
