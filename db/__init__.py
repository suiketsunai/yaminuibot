import os

# working with env
from dotenv import load_dotenv

# create engine
from sqlalchemy import create_engine

# load .env file & get config
load_dotenv()

# database connection string
DB_URI = os.environ["SB_CNT"].format(password=os.environ["SB_PSW"])

# session settings
engine = create_engine(
    DB_URI,
    future=True,
)
