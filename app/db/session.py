import aiosqlite
import os
import shutil
import glob
from app.core.config import settings

async def get_db_connection() -> aiosqlite.Connection:
    data_dir = os.path.dirname(settings.DB_PATH)
    os.makedirs(data_dir, exist_ok=True)

    # Đảm bảo phân quyền đọc/ghi cho user wpc nếu đang chạy dưới quyền root
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        try:
            shutil.chown(data_dir, user="wpc", group="wpc")
            for f in glob.glob(f"{data_dir}/panel.db*"):
                try:
                    shutil.chown(f, user="wpc", group="wpc")
                    os.chmod(f, 0o664)
                except Exception:
                    pass
        except Exception:
            pass

    db = await aiosqlite.connect(settings.DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode = WAL;")
    await db.execute("PRAGMA foreign_keys = ON;")
    return db
