import time
import base64
import logging

from pathlib import Path

# http requests
import requests

# upload dictionary
from extra import upl_dict

# logger file handler
from extra.loggers import file_handler

# downloading media
from extra.download import download_media

# get logger
log = logging.getLogger("yaminuichan.upload")


def upload(file: Path, link: str, kind: str = "file") -> None:
    """Upload file of certain type to Google Drive

    Args:
        file (Path): file to upload
        kind (str, optional): file type description. Defaults to "file".
    """
    if not (file and isinstance(file, Path) and file.exists()):
        return log.error("No such file!")
    if not link:
        return log.error("No upload link!")
    UPLOAD_TIMEOUT = 3
    name, kind = file.name, kind.lower()
    for attempt in range(3):
        if attempt:
            log.info("Waiting for %d seconds...", UPLOAD_TIMEOUT)
            time.sleep(UPLOAD_TIMEOUT)
            log.info("Done. Current attempt: #%d.", attempt + 1)
        try:
            log.info("Uploading %s %r...", kind, name)
            r = requests.post(
                url=link,
                params={"name": name},
                data=base64.urlsafe_b64encode(file.read_bytes()),
            )
            if r.json()["ok"]:
                log.info("Done uploading %s %r.", kind, name)
            else:
                log.info("%s %r already exists.", kind.capitalize(), name)
            break
        except Exception as ex:
            log.error("Exception occured: %s.", ex)
    else:
        log.error("Error: Run out of attempts.")
        log.error("Couldn't upload %s %r.", kind, name)


def upload_media(info: dict, user: int = 0, order: list[int] = None) -> None:
    """Upload images to cloud

    Args:
        info (dict): art media dictionary
        user (int, optional): telegram user id. Defaults to 0.
        order (list[int], optional): which artworks to upload. Defaults to None.
    """
    if user != upl_dict["user"]:
        return  # silently exit
    if not upl_dict["media"]:
        return log.error("No media upload link.")
    for file in download_media(info, order=order):
        kind = f"file ({file.suffix})"
        match file.suffix:
            case ".mp4" | ".mov":
                kind = "video"
            case ".jpg" | ".jpeg" | ".png" | ".jiff":
                kind = "image"
            case ".gif":
                kind = "animated gif"
        upload(file, upl_dict["media"], kind)
        file.unlink()


def upload_log() -> None:
    """Upload log file to cloud"""
    if not file_handler:
        return  # silently exit
    if not upl_dict["log"]:
        return log.error("No log upload link.")
    upload(Path(file_handler.baseFilename), upl_dict["log"], "log file")
