import os
import re
import logging

from pathlib import Path
from datetime import datetime
from collections import namedtuple

# working with env
from dotenv import load_dotenv

# reading setings
import tomli

# current timestamp & this file directory
date_run = datetime.now()
file_dir = Path(__file__).parent

# load .env file & get config
load_dotenv()
config = tomli.load(Path(os.environ["PATH_SETTINGS"]).open("rb"))

# get logger
log = logging.getLogger("yaminuichan")

################################################################################
# named tuples
################################################################################

Link = namedtuple("Link", ["type", "link", "id"])


################################################################################
# links
################################################################################

# link types
TWITTER = 0
PIXIV = 1

# link dictionary
src = {
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
        "link": "twitter.com/{author}/status/{id}",
        "type": TWITTER,
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
        "link": "www.pixiv.net/artworks/{id}",
        "type": PIXIV,
    },
}


def setup_logging():
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
                log.info(f"Created log directory: {log_dir.resolve()}.")
            except Exception as ex:
                log.error(f"Exception occured: {ex}")
                log.info("Can't execute program.")
                quit()
        log_date = date_run.strftime(config["log"]["file"]["date"])
        log_name = f'{config["log"]["file"]["pref"]}{log_date}.log'
        log_file = log_dir / log_name
        log.info(f"Logging to file: {log_name}")
        # add file handler
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(logging.Formatter(config["log"]["file"]["form"]))
        fh.setLevel(logging.getLevelName(config["log"]["file"]["level"]))
        logging.getLogger().addHandler(fh)
    else:
        log.info("Logging to file disabled.")


def formatter(query: str):
    """Exctracts and formates links in text

    Args:
        query (str): text

    Returns:
        list: list of Links
    """
    response = []
    for re_key, re_type in src.items():
        for link in re.finditer(re_type["re"], query):
            # dictionary keys = format args
            _link = "https://" + re_type["link"].format(**link.groupdict())
            log.info(f"Inline: Received link {re_key}: {_link}.")
            # add to response list
            response.append(Link(re_type["type"], _link, int(link.group("id"))))
    return response


def main():
    setup_logging()


if __name__ == "__main__":
    main()
