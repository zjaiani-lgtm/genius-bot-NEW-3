from sqlalchemy import create_engine, text
from .config import DB_URL

engine = create_engine(DB_URL, echo=False)

def init_db():
    with engine.begin() as c:
        c.execute(text("""
        CREATE TABLE IF NOT EXISTS trades(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            qty REAL,
            price REAL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """))
