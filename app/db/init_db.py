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
    plan_type TEXT NOT NULL DEFAULT 'user',
    max_php_sites INTEGER NOT NULL DEFAULT 10,
    max_wp_sites INTEGER NOT NULL DEFAULT 5,
    max_nodejs_sites INTEGER NOT NULL DEFAULT 2,
    max_python_sites INTEGER NOT NULL DEFAULT 2,
    max_go_sites INTEGER NOT NULL DEFAULT 2,
    max_inodes INTEGER NOT NULL DEFAULT 250000,
    max_mysql_dbs INTEGER NOT NULL DEFAULT 10,
    max_mongo_dbs INTEGER NOT NULL DEFAULT 2,
    bandwidth_mb INTEGER NOT NULL DEFAULT 0,
    max_ftp_accounts INTEGER NOT NULL DEFAULT 5,
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
    is_system INTEGER NOT NULL DEFAULT 0,
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

-- 13. Bảng Cấu Hình Hệ Thống Toàn Cục (System Settings)
CREATE TABLE IF NOT EXISTS system_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 14. Bảng Ghi Nhận Lịch Sử Tài Nguyên Chi Tiết Từng Phút (Resource Metrics History)
CREATE TABLE IF NOT EXISTS resource_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_type TEXT NOT NULL,  -- 'server', 'user', 'domain'
    target_id INTEGER NOT NULL DEFAULT 0,  -- 0 cho server, user_id hoặc domain_id
    cpu_pct REAL NOT NULL DEFAULT 0.0,
    mem_mb INTEGER NOT NULL DEFAULT 0,
    disk_mb INTEGER NOT NULL DEFAULT 0,
    bw_in_kb REAL NOT NULL DEFAULT 0.0,
    bw_out_kb REAL NOT NULL DEFAULT 0.0,
    pps_in INTEGER NOT NULL DEFAULT 0,
    pps_out INTEGER NOT NULL DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_metrics_target_created ON resource_metrics(target_type, target_id, created_at);
CREATE INDEX IF NOT EXISTS idx_metrics_created_at ON resource_metrics(created_at);
"""

async def init_database():
    import os
    os.makedirs(os.path.dirname(settings.DB_PATH), exist_ok=True)
    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute("PRAGMA journal_mode = WAL;")
        await db.executescript(SCHEMA_SQL)

        # Seed system_settings mặc định cho tính năng lưu trữ lịch sử metrics
        try:
            await db.execute("INSERT OR IGNORE INTO system_settings (key, value) VALUES ('metrics_history_enabled', 'true')")
            await db.execute("INSERT OR IGNORE INTO system_settings (key, value) VALUES ('metrics_retention_days', '7')")
            await db.commit()
        except Exception:
            pass
        
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
            await db.execute("ALTER TABLE packages ADD COLUMN max_php_sites INTEGER NOT NULL DEFAULT 10")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE packages ADD COLUMN max_wp_sites INTEGER NOT NULL DEFAULT 5")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE packages ADD COLUMN max_nodejs_sites INTEGER NOT NULL DEFAULT 2")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE packages ADD COLUMN max_python_sites INTEGER NOT NULL DEFAULT 2")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE packages ADD COLUMN max_go_sites INTEGER NOT NULL DEFAULT 2")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE packages ADD COLUMN max_inodes INTEGER NOT NULL DEFAULT 250000")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE packages ADD COLUMN max_mysql_dbs INTEGER NOT NULL DEFAULT 10")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE packages ADD COLUMN max_mongo_dbs INTEGER NOT NULL DEFAULT 2")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE packages ADD COLUMN bandwidth_mb INTEGER NOT NULL DEFAULT 0")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE packages ADD COLUMN max_ftp_accounts INTEGER NOT NULL DEFAULT 5")
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
            await db.execute("ALTER TABLE domains ADD COLUMN php_extensions TEXT DEFAULT NULL")
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
        try:
            await db.execute("ALTER TABLE domains ADD COLUMN is_system INTEGER DEFAULT 0")
        except Exception:
            pass

        # Cập nhật schema cho WordPress Manager
        wp_cols = [
            ("user_id", "INTEGER DEFAULT 1"),
            ("db_prefix", "TEXT DEFAULT 'wp_'"),
            ("document_root", "TEXT DEFAULT ''"),
            ("is_locked", "INTEGER DEFAULT 0"),
            ("security_score", "INTEGER DEFAULT 0"),
            ("security_flags", "TEXT DEFAULT '{}'"),
            ("wp_defines", "TEXT DEFAULT '{}'"),
            ("wpc_optimize_flags", "TEXT DEFAULT '{}'"),
            ("auto_update_themes", "INTEGER DEFAULT 0"),
            ("site_size_mb", "REAL DEFAULT 0"),
            ("db_size_mb", "REAL DEFAULT 0"),
            ("uploads_size_mb", "REAL DEFAULT 0"),
            ("last_scanned_at", "DATETIME DEFAULT CURRENT_TIMESTAMP"),
        ]
        for col_name, col_type in wp_cols:
            try:
                await db.execute(f"ALTER TABLE wp_instances ADD COLUMN {col_name} {col_type}")
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

        # Tự động quét và chuẩn hóa toàn bộ file vhost OpenLiteSpeed để loại bỏ triệt để lỗi shell metacharacters / {{PHP_VERSION}}
        await auto_fix_ols_vhosts(db)

    print("[+] SQLite Database initialized successfully with 12 tables!")

    # Đảm bảo phân quyền đọc/ghi cho user wpc trên database và thư mục data
    try:
        import shutil, glob
        data_dir = os.path.dirname(settings.DB_PATH)
        os.chmod(data_dir, 0o775)
        for f in glob.glob(f"{data_dir}/panel.db*"):
            try:
                os.chmod(f, 0o664)
            except Exception:
                pass
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            try:
                shutil.chown(data_dir, user="wpc", group="wpc")
                for f in glob.glob(f"{data_dir}/panel.db*"):
                    try:
                        shutil.chown(f, user="wpc", group="wpc")
                    except Exception:
                        pass
            except Exception:
                pass
    except Exception:
        pass


async def auto_fix_ols_vhosts(db):
    """Tự động quét và chuẩn hóa toàn bộ file cấu hình vhost OLS, loại bỏ hoàn toàn shell metacharacters / {{PHP_VERSION}}"""
    try:
        import glob, re
        ols_vhosts_dir = "/usr/local/lsws/conf/vhosts"
        if not os.path.isdir(ols_vhosts_dir):
            return

        # Lấy map domain_name -> php_version từ database
        dom_map = {}
        try:
            cursor = await db.execute("SELECT domain_name, php_version FROM domains")
            rows = await cursor.fetchall()
            for r in rows:
                dom_map[r[0]] = r[1]
        except Exception:
            pass

        for vh_conf in glob.glob(f"{ols_vhosts_dir}/*/vhconf.conf"):
            try:
                dom_name = os.path.basename(os.path.dirname(vh_conf))
                p_ver = dom_map.get(dom_name) or "8.2"
                p_num = str(p_ver).replace(".", "").strip() or "82"

                with open(vh_conf, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()

                content = content.replace("\r\n", "\n").replace("\r", "\n")
                new_content = re.sub(r"/usr/local/lsws/lsphp\{\{[^}]+\}\}/bin/lsphp", f"/usr/local/lsws/lsphp{p_num}/bin/lsphp", content)
                new_content = new_content.replace("{{PHP_VERSION}}", p_num).replace("{{PHP_NUM}}", p_num)

                # Kiểm tra binary lsphp
                m = re.search(r"^\s*path\s+(\S+)", new_content, re.MULTILINE)
                if m:
                    bin_path = m.group(1)
                    if not os.path.exists(bin_path):
                        for fb in ["84", "83", "82", "81", "74"]:
                            if os.path.exists(f"/usr/local/lsws/lsphp{fb}/bin/lsphp"):
                                new_content = re.sub(r"^\s*path\s+\S+", f"  path                    /usr/local/lsws/lsphp{fb}/bin/lsphp", new_content, flags=re.MULTILINE)
                                break

                if new_content != content:
                    with open(vh_conf, "w", encoding="utf-8") as f:
                        f.write(new_content)
                    print(f"[+] Da chuan hoa vhost OLS: {dom_name} (PHP {p_num})")
            except Exception:
                pass
    except Exception:
        pass

if __name__ == "__main__":
    asyncio.run(init_database())


