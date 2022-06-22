import json
import logging

from pathlib import Path

# working with database
from sqlalchemy.orm import Session

# database engine
from db import engine

# database models
from db.models import User, Channel, Post, ArtWork

# get logger
log = logging.getLogger("yaminuichan.dumper")


def row2dict(row) -> dict:
    """Convert table rows to dictionary

    Args:
        row (_type_): a row of table

    Returns:
        dict: row as dictionary
    """
    return {
        column.name: getattr(row, column.name)
        for column in row.__table__.columns
    }


def dumper(table, filename: str) -> None:
    """Helper function for dumping tables into files

    Args:
        table (_type_): Table name for exporting
        filename (str): name for file to dump Table in
    """
    log.info("Dumping %s...", table.__class__)
    dst = Path(".dump")
    with Session(engine) as s:
        (dst / filename).with_suffix(".json").write_text(
            json.dumps(
                [row2dict(obj) for obj in s.query(table)],
                indent=4,
                default=str,
            )
        )


def dump_db() -> None:
    """Dump database as it is"""
    log.info("Dumping all the tables...")
    dumper(User, "users")
    dumper(Channel, "channels")
    dumper(Post, "posts")
    dumper(ArtWork, "artworks")
    log.info("Done!")
