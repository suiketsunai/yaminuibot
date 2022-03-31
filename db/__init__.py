import os

# working with env
from dotenv import load_dotenv

# create engine
from sqlalchemy import create_engine

# load .env file & get config
load_dotenv()

# session settings
engine = create_engine(
    os.environ["SB_CNT"].format(password=os.environ["SB_PSW"]),
    future=True,
)
