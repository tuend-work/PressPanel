import aiosqlite
import os
import shutil
import glob
from app.core.config import settings

async def get_db_connection() -> aiosqlite.Connection:
    data_dir = os.path.dirname(settings.DB_PATH)
    os.makedirs(data_dir, exist_ok=True)

    # Đảm bảo phân quyền đọc/ghi cho user presspanel nếu đang chạy dưới quyền root
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        for u in ["presspanel", "wpc"]:
            try:
                shutil.chown(data_dir, user=u, group=u)
                for f in glob.glob(f"{data_dir}/panel.db*"):
                    try:
                        shutil.chown(f, user=u, group=u)
                        os.chmod(f, 0o664)
                    except Exception:
                        pass
                break
            except Exception:
                pass

    db = await aiosqlite.connect(settings.DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode = WAL;")
    await db.execute("PRAGMA foreign_keys = ON;")
    return db
