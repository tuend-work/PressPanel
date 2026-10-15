import aiosqlite
import os
from app.core.config import settings

async def get_db_connection() -> aiosqlite.Connection:
    os.makedirs(os.path.dirname(settings.DB_PATH), exist_ok=True)
    db = await aiosqlite.connect(settings.DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode = WAL;")
    await db.execute("PRAGMA foreign_keys = ON;")
    return db
