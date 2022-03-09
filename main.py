import os
import logging

from pathlib import Path

# working with env
from dotenv import load_dotenv

# reading setings
import tomli

# load .env file & get config
load_dotenv()
config = tomli.load(Path(os.environ["PATH_SETTINGS"]).open("rb"))

# get logger
log = logging.getLogger("yaminuichan")


def setup_logging():
    # set basic config to logger
    logging.basicConfig(
        format=config["log"]["form"],
        level=logging.getLevelName(config["log"]["level"]),
    )


def main():
    setup_logging()


if __name__ == "__main__":
    main()
