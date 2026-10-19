import sys
import os

# Tự động thêm thư mục gốc dự án vào sys.path để chạy trực tiếp không bị ModuleNotFoundError
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import asyncio
import aiosqlite
from app.core.config import settings
from app.core.security import get_password_hash

SCHEMA_SQL = """
-- 1. Bảng Gói Tài Nguyên (Đã bổ sung disk_mb cho Disk Quota)
CREATE TABLE IF NOT EXISTS packages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER NOT NULL DEFAULT 0,
    name TEXT NOT NULL,
    package_type TEXT NOT NULL CHECK(package_type IN ('reseller', 'user')),
    cpu_pct INTEGER NOT NULL,
    mem_mb INTEGER NOT NULL,
    disk_mb INTEGER NOT NULL DEFAULT 5120,
    io_weight INTEGER NOT NULL DEFAULT 100,
    pids_max INTEGER NOT NULL DEFAULT 100,
    max_domains INTEGER NOT NULL DEFAULT 10,
    max_databases INTEGER NOT NULL DEFAULT 10,
    max_sub_users INTEGER DEFAULT 0,
    allow_ssh INTEGER NOT NULL DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 2. Bảng Người Dùng (3 Cấp Phân Quyền RBAC: Admin -> Reseller -> User)
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id INTEGER DEFAULT NULL,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    full_name TEXT DEFAULT '',
    phone TEXT DEFAULT '',
    email TEXT UNIQUE NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin', 'reseller', 'user')),
    system_uid INTEGER UNIQUE,
    package_id INTEGER NOT NULL,
    cpu_pct INTEGER NOT NULL,
    mem_mb INTEGER NOT NULL,
    disk_mb INTEGER NOT NULL DEFAULT 5120,
    io_weight INTEGER NOT NULL,
    pids_max INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'suspended')),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (package_id) REFERENCES packages(id),
    FOREIGN KEY (parent_id) REFERENCES users(id) ON DELETE CASCADE
);

-- 3. Bảng Quản Lý Website / Tên Miền
CREATE TABLE IF NOT EXISTS domains (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    domain_name TEXT UNIQUE NOT NULL,
    document_root TEXT NOT NULL,
    webserver TEXT NOT NULL CHECK(webserver IN ('ols', 'nginx')),
    php_version TEXT NOT NULL,
    ssl_enabled INTEGER DEFAULT 0,
    ssl_expires_at DATETIME,
    allow_ssh INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'suspended')),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- 4. Bảng Quản Lý WordPress Instances (WP Toolkit)
CREATE TABLE IF NOT EXISTS wp_instances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain_id INTEGER NOT NULL,
    site_title TEXT NOT NULL,
    admin_user TEXT NOT NULL,
    admin_email TEXT NOT NULL,
    wp_version TEXT NOT NULL,
    db_name TEXT NOT NULL,
    db_user TEXT NOT NULL,
    auto_update_core INTEGER DEFAULT 1,
    auto_update_plugins INTEGER DEFAULT 0,
    is_staging INTEGER DEFAULT 0,
    staging_source_id INTEGER DEFAULT NULL,
    security_score INTEGER DEFAULT 0,
    last_backup_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (domain_id) REFERENCES domains(id) ON DELETE CASCADE
);

-- 5. Bảng Cơ Sở Dữ Liệu MariaDB
CREATE TABLE IF NOT EXISTS databases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    db_name TEXT UNIQUE NOT NULL,
    db_user TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- 6. Bảng Quản Lý Ứng Dụng Đã Cài (WPC App Store)
CREATE TABLE IF NOT EXISTS installed_apps (
    app_name TEXT NOT NULL,
    version TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('installing', 'active', 'failed', 'removing')),
    installed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (app_name, version)
);

-- 7. Bảng Hàng Đợi Tác Vụ Bất Đồng Bộ (Task Queue & Log Streaming)
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending', 'running', 'completed', 'failed')),
    log_file TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    completed_at DATETIME
);

-- 8. Bảng API Keys
CREATE TABLE IF NOT EXISTS api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    key_name TEXT NOT NULL,
    key_hash TEXT NOT NULL,
    scopes TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- 9. Bảng Quản Lý Cronjob của User (Thực thi trong Cage)
CREATE TABLE IF NOT EXISTS cron_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    schedule TEXT NOT NULL,
    command TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'disabled')),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- 10. Bảng Chi Tiết Chứng Chỉ SSL
CREATE TABLE IF NOT EXISTS ssl_certificates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain_id INTEGER NOT NULL,
    provider TEXT NOT NULL CHECK(provider IN ('letsencrypt', 'custom', 'self_signed')),
    cert_path TEXT NOT NULL,
    key_path TEXT NOT NULL,
    auto_renew INTEGER DEFAULT 1,
    expires_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (domain_id) REFERENCES domains(id) ON DELETE CASCADE
);

-- 11. Bảng Quản Lý Bản Sao Lưu Toàn Diện (Backups)
CREATE TABLE IF NOT EXISTS backups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    domain_id INTEGER DEFAULT NULL,
    backup_type TEXT NOT NULL CHECK(backup_type IN ('full', 'files', 'db')),
    storage_type TEXT NOT NULL DEFAULT 'local' CHECK(storage_type IN ('local', 's3', 'ftp')),
    file_path TEXT NOT NULL,
    file_size_bytes INTEGER DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- 12. Bảng Luật Tường Lửa & Chặn IP (Firewall Rules)
CREATE TABLE IF NOT EXISTS firewall_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    port INTEGER,
    protocol TEXT NOT NULL DEFAULT 'tcp' CHECK(protocol IN ('tcp', 'udp')),
    action TEXT NOT NULL CHECK(action IN ('allow', 'deny')),
    ip_source TEXT DEFAULT '0.0.0.0/0',
    comment TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""

async def init_database():
    import os
    os.makedirs(os.path.dirname(settings.DB_PATH), exist_ok=True)
    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute("PRAGMA journal_mode = WAL;")
        await db.executescript(SCHEMA_SQL)
        
        # Migration: Tự động bổ sung cột allow_ssh, plan_type cho packages và domains nếu đã tạo từ trước
        try:
            await db.execute("ALTER TABLE packages ADD COLUMN allow_ssh INTEGER NOT NULL DEFAULT 1")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE packages ADD COLUMN plan_type TEXT NOT NULL DEFAULT 'user'")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE domains ADD COLUMN allow_ssh INTEGER NOT NULL DEFAULT 1")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE domains ADD COLUMN package_id INTEGER DEFAULT NULL")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE domains ADD COLUMN status TEXT DEFAULT 'active'")
        except Exception:
            pass
        try:
            # Website Plan: Mỗi gói là 1 web duy nhất
            await db.execute("UPDATE packages SET max_domains = 1 WHERE plan_type = 'website'")
        except Exception:
            pass
        try:
            # Chuẩn hoá các tài khoản Hosting User thành role 'reseller'
            await db.execute("UPDATE users SET role = 'reseller' WHERE role = 'user' AND username != 'admin'")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN full_name TEXT DEFAULT ''")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN phone TEXT DEFAULT ''")
        except Exception:
            pass
        try:
            await db.execute("UPDATE users SET full_name = 'Administrator' WHERE username = 'admin' AND (full_name IS NULL OR full_name = '' OR full_name = 'Nguyễn Đức Tuệ')")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE databases ADD COLUMN db_type TEXT DEFAULT 'mysql'")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE databases ADD COLUMN domain_id INTEGER DEFAULT NULL")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE databases ADD COLUMN charset TEXT DEFAULT 'utf8mb4_unicode_ci'")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE databases ADD COLUMN description TEXT DEFAULT ''")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE databases ADD COLUMN backup_enabled INTEGER DEFAULT 0")
        except Exception:
            pass

        # Tạo các gói Service Plans mẫu (Hosting Plans) nếu chưa có
        cursor = await db.execute("SELECT id FROM packages WHERE id = 1")
        pkg1 = await cursor.fetchone()
        if not pkg1:
            await db.execute("""
                INSERT INTO packages (id, owner_id, name, package_type, cpu_pct, mem_mb, disk_mb, io_weight, pids_max, max_domains, max_databases, allow_ssh)
                VALUES (1, 0, 'Admin Unlimited Plan', 'reseller', 400, 8192, 102400, 100, 500, 100, 100, 1)
            """)

        # Gói 2: Standard Plan (Mặc định cho Customer)
        cursor = await db.execute("SELECT id FROM packages WHERE id = 2")
        pkg2 = await cursor.fetchone()
        if not pkg2:
            await db.execute("""
                INSERT INTO packages (id, owner_id, name, package_type, cpu_pct, mem_mb, disk_mb, io_weight, pids_max, max_domains, max_databases, allow_ssh)
                VALUES (2, 0, 'Standard Web Hosting', 'user', 100, 1024, 5120, 100, 100, 5, 5, 1)
            """)

        # Gói 3: Pro WordPress Plan
        cursor = await db.execute("SELECT id FROM packages WHERE id = 3")
        pkg3 = await cursor.fetchone()
        if not pkg3:
            await db.execute("""
                INSERT INTO packages (id, owner_id, name, package_type, cpu_pct, mem_mb, disk_mb, io_weight, pids_max, max_domains, max_databases, allow_ssh)
                VALUES (3, 0, 'Pro WordPress Turbo', 'user', 200, 2048, 20480, 150, 200, 15, 15, 1)
            """)

        # Gói 4: Starter Mini Plan
        cursor = await db.execute("SELECT id FROM packages WHERE id = 4")
        pkg4 = await cursor.fetchone()
        if not pkg4:
            await db.execute("""
                INSERT INTO packages (id, owner_id, name, package_type, cpu_pct, mem_mb, disk_mb, io_weight, pids_max, max_domains, max_databases, allow_ssh)
                VALUES (4, 0, 'Starter Mini', 'user', 50, 512, 2048, 80, 50, 1, 1, 0)
            """)

        # Tạo tài khoản Admin mặc định nếu chưa có
        cursor = await db.execute("SELECT id FROM users WHERE username = 'admin'")
        admin = await cursor.fetchone()
        if not admin:
            admin_pass_hash = get_password_hash("admin123456")
            await db.execute("""
                INSERT INTO users (username, password_hash, email, role, package_id, cpu_pct, mem_mb, disk_mb, io_weight, pids_max)
                VALUES ('admin', ?, 'admin@wpc.local', 'admin', 1, 400, 8192, 102400, 100, 500)
            """, (admin_pass_hash,))

        await db.commit()

    print("[+] SQLite Database initialized successfully with 12 tables!")

if __name__ == "__main__":
    asyncio.run(init_database())


