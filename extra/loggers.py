import os
import logging

from pathlib import Path

# working with env
from dotenv import load_dotenv

# reading setings
import tomli

################################################################################
# logger
################################################################################

# load .env file & get config
load_dotenv()
config = tomli.load(Path(os.getenv("PATH_SETTINGS")).open("rb"))

# set basic config to logger
logging.basicConfig(
    format=config["log"]["form"],
    level=config["log"]["level"],
)

# get root logger
root_log = logging.getLogger()


def get_file_handler():
    """Create file handler"""
    file_log = config["log"]["file"]
    if file_log["enable"]:
        root_log.info("Logging to file enabled.")
        log_name = "log.log"
        log_file = Path(log_name)
        root_log.info("Logging to file: '%s'.", log_name)
        # add file handler
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(logging.Formatter(file_log["form"]))
        fh.setLevel(file_log["level"])
        return fh
    root_log.info("Logging to file disabled.")
    return None


# get file handler
file_handler = get_file_handler()


def setup_logging(name: str):
    """Set up logger"""
    log = logging.getLogger(name)
    if file_handler:
        log.addHandler(file_handler)
    return log


# setup root logger
root_log = setup_logging("root")

# setup sqlalchemy loggers
for name, module in config["log"]["sqlalchemy"].items():
    if module["enable"]:
        logging.getLogger(f"sqlalchemy.{name}").setLevel(module["level"])
