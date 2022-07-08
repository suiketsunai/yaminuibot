"""Pixiv module"""
import os
import logging

# working with env
from dotenv import load_dotenv

# pixiv api
from pixivpy3 import AppPixivAPI

# http requests
import requests

# link types, link dictionary
from extra import LinkType, link_dict

# ArtWorkMedia
from extra.namedtuples import ArtWorkMedia

# load .env file
load_dotenv()

# pixiv tokens
pixiv_api = {
    "ACCESS_TOKEN": os.environ["PX_ACCESS"],
    "REFRESH_TOKEN": os.environ["PX_REFRESH"],
}

# get logger
log = logging.getLogger("yaminuichan.pixiv")

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
        LinkType.PIXIV,
        illust.id,
        illust.type,  # 'ugoira' or 'illust'
        illust.user.id,
        illust.user.name,
        illust.user.account,
        illust.create_date,
        illust.title,
        illust.caption,
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
            log.debug("Response: %r.", json_result.illust)
            return get_pixiv_media(json_result.illust)
    return None
