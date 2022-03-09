import os
import logging

from pathlib import Path
from datetime import datetime

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


def main():
    setup_logging()


if __name__ == "__main__":
    main()
