import os
import re
import glob
import json
import time
import shutil
import secrets
import asyncio
import subprocess
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from pydantic import BaseModel

from app.api.deps import get_current_user
from app.db.session import get_db_connection
from app.executor.gatekeeper import run_gatekeeper

router = APIRouter(prefix="/wp", tags=["WordPress Manager"])

# ==============================================================================
# Helper Functions: Prerequisites Validation & WP Config Parser
# ==============================================================================

def check_mysql_service_available() -> tuple[bool, str]:
    """Kiểm tra nghiêm ngặt điều kiện tiên quyết: MariaDB / MySQL đã cài đặt và đang chạy hay chưa"""
    # 1. Tìm binary mysql hoặc mariadb
    mysql_bin = shutil.which("mysql") or shutil.which("mariadb")
    if not mysql_bin:
        for p in ["/usr/bin/mysql", "/usr/local/bin/mysql", "/opt/mariadb/bin/mysql", "/usr/bin/mariadb"]:
            if os.path.isfile(p) and os.access(p, os.X_OK):
                mysql_bin = p
                break

    if not mysql_bin:
        return False, "Máy chủ CHƯA CÀI ĐẶT MariaDB/MySQL. Vui lòng cài đặt dịch vụ MariaDB trong App Store trước khi cài đặt WordPress!"

    # 2. Kiểm tra dịch vụ có đang chạy và phản hồi hay không
    try:
        # Thử chạy SELECT 1
        res = subprocess.run([mysql_bin, "-e", "SELECT 1;"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=4)
        if res.returncode == 0:
            return True, ""

        # Thử mysqladmin ping
        admin_bin = shutil.which("mysqladmin") or "/usr/bin/mysqladmin"
        if os.path.isfile(admin_bin):
            res_ping = subprocess.run([admin_bin, "ping", "--silent"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=4)
            if res_ping.returncode == 0:
                return True, ""

        # Kiểm tra socket UNIX nếu có
        for sock in ["/var/run/mysqld/mysqld.sock", "/tmp/mysql.sock", "/var/lib/mysql/mysql.sock"]:
            if os.path.exists(sock):
                return True, ""

        err_out = res.stderr.decode("utf-8", errors="ignore").strip()
        return False, f"Không thể kết nối tới MariaDB/MySQL: {err_out or 'Dịch vụ MariaDB chưa được khởi động hoặc đang tắt'}"
    except Exception as e:
        return False, f"Lỗi kiểm tra cơ sở dữ liệu MariaDB: {str(e)}"

def extract_linux_user(docroot: Optional[str], domain: str, fallback: str = "webuser") -> str:
    """Trích xuất Linux user thực tế sở hữu thư mục website từ document_root hoặc tên miền"""
    if docroot:
        m = re.match(r"^/home/([^/]+)/", docroot.strip())
        if m and m.group(1):
            return m.group(1)
    clean_dom = re.sub(r"^(https?://)?(www\.)?", "", (domain or "").strip().lower()).rstrip("/")
    clean_linux = re.sub(r"[^a-z0-9]", "", clean_dom)[:30]
    if clean_linux and os.path.isdir(f"/home/{clean_linux}/domains/{clean_dom}/public_html"):
        return clean_linux
    return clean_linux or fallback

def resolve_target_dir(username: str, domain: str, docroot: Optional[str] = None) -> str:
    if docroot and os.path.isdir(docroot):
        return docroot
    clean_dom = re.sub(r"^(https?://)?(www\.)?", "", (domain or "").strip().lower()).rstrip("/")
    clean_linux = re.sub(r"[^a-z0-9]", "", clean_dom)[:30]
    candidates = [
        f"/home/{clean_linux}/domains/{domain}/public_html" if clean_linux else "",
        f"/home/{username}/domains/{domain}/public_html",
        f"/home/{username}/httpdocs/{domain}",
        f"/home/{clean_linux}/httpdocs/{domain}" if clean_linux else "",
        f"/home/{username}/public_html",
        f"/home/{clean_linux}/public_html" if clean_linux else ""
    ]
    for c in candidates:
        if c and os.path.isdir(c):
            return c
    if docroot:
        return docroot
    return f"/home/{clean_linux or username}/domains/{domain}/public_html"

def parse_wp_config_file(docroot: str) -> Dict[str, Any]:
    """Phân tích wp-config.php để trích xuất DB credentials và các constant WP defines"""
    config_path = os.path.join(docroot, "wp-config.php")
    res = {
        "exists": False,
        "db_name": "",
        "db_user": "",
        "db_password": "",
        "db_host": "localhost",
        "table_prefix": "wp_",
        "defines": {}
    }
    if not os.path.isfile(config_path):
        return res

    res["exists"] = True
    try:
        with open(config_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        # Trích xuất constants define('KEY', 'VAL')
        matches = re.findall(r"define\s*\(\s*['\"]([^'\"]+)['\"]\s*,\s*([^)]+)\)\s*;", content)
        for key, val in matches:
            val_clean = val.strip().strip("'\"")
            if key == "DB_NAME":
                res["db_name"] = val_clean
            elif key == "DB_USER":
                res["db_user"] = val_clean
            elif key == "DB_PASSWORD":
                res["db_password"] = val_clean
            elif key == "DB_HOST":
                res["db_host"] = val_clean
            else:
                res["defines"][key] = val_clean

        # Trích xuất $table_prefix
        p_match = re.search(r"\$table_prefix\s*=\s*['\"]([^'\"]+)['\"]\s*;", content)
        if p_match:
            res["table_prefix"] = p_match.group(1)

    except Exception:
        pass

    return res

async def run_mysql_query(db_config: Dict[str, Any], sql: str) -> List[Dict[str, str]]:
    """Thực thi SQL an toàn qua command line mysql client và trả về danh sách dict"""
    if not db_config.get("db_name"):
        return []

    host = db_config.get("db_host", "localhost")
    user = db_config.get("db_user", "")
    password = db_config.get("db_password", "")
    db_name = db_config.get("db_name", "")

    cmd = [
        "mysql",
        "-h", host,
        "-u", user,
        f"-p{password}",
        db_name,
        "--default-character-set=utf8mb4",
        "-B",  # Batch mode (tab-separated)
        "-e", sql
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            return []

        lines = stdout.decode("utf-8", errors="ignore").strip().split("\n")
        if not lines or not lines[0]:
            return []

        headers = lines[0].split("\t")
        results = []
        for line in lines[1:]:
            if not line:
                continue
            cols = line.split("\t")
            row = {}
            for i, h in enumerate(headers):
                row[h] = cols[i] if i < len(cols) else ""
            results.append(row)
        return results
    except Exception:
        return []

def get_dir_size_mb(path: str) -> float:
    """Tính dung lượng thư mục theo MB"""
    if not os.path.exists(path):
        return 0.0
    total = 0
    try:
        for entry in os.scandir(path):
            if entry.is_file(follow_symlinks=False):
                total += entry.stat().st_size
            elif entry.is_dir(follow_symlinks=False):
                total += int(get_dir_size_mb(entry.path) * 1024 * 1024)
    except Exception:
        pass
    return round(total / (1024 * 1024), 2)

# ==============================================================================
# Pydantic Request Models
# ==============================================================================

class WPInstallRequest(BaseModel):
    domain_id: Optional[int] = None
    domain: Optional[str] = None
    subdirectory: Optional[str] = ""
    apply_ssl: Optional[bool] = False
    site_title: str = "My WordPress Site"
    language: Optional[str] = "en_US"
    php_version: Optional[str] = "8.4"
    wp_version: Optional[str] = "latest"
    admin_user: str = "admin"
    admin_pass: str
    admin_email: str
    allow_weak_passwords: Optional[bool] = False
    db_name: Optional[str] = ""
    db_pass: Optional[str] = ""
    db_prefix: Optional[str] = "wp_"
    enable_cache: Optional[bool] = True
    create_html: Optional[bool] = False

class WPInstallZipRequest(BaseModel):
    domain_id: int
    zip_url: str

class WPPluginToggleRequest(BaseModel):
    plugin_file: str
    enable: bool

class WPPluginReinstallRequest(BaseModel):
    slug: str
    zip_url: Optional[str] = ""

class WPPluginInstallRequest(BaseModel):
    slug: str
    zip_url: Optional[str] = ""
    activate: bool = False

class WPThemeActionRequest(BaseModel):
    slug: str
    zip_url: Optional[str] = ""
    activate: bool = False

class WPUserCreateRequest(BaseModel):
    user_login: str
    user_email: str
    user_pass: str
    role: str = "administrator"

class WPUserModifyRequest(BaseModel):
    user_id: int
    user_email: Optional[str] = None
    user_pass: Optional[str] = None
    role: Optional[str] = None

class WPDefineSaveRequest(BaseModel):
    constant: str
    value: str
    enable: bool

class WPCOptimizeToggleRequest(BaseModel):
    file_name: str
    enable: bool

class WPSecurityToggleRequest(BaseModel):
    measure: str
    enable: bool

class WPCloneRequest(BaseModel):
    src_domain_id: int
    dest_domain_name: str
    is_staging: bool = False
    disallow_indexing: bool = True

# ==============================================================================
# Endpoints: Instances Management & Overview
# ==============================================================================

async def ensure_wp_instances_table(db):
    """Đảm bảo bảng wp_instances và các cột cần thiết luôn tồn tại đầy đủ"""
    try:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS wp_instances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                domain_id INTEGER NOT NULL,
                user_id INTEGER,
                site_title TEXT NOT NULL,
                admin_user TEXT NOT NULL,
                admin_email TEXT NOT NULL,
                wp_version TEXT NOT NULL,
                db_name TEXT NOT NULL,
                db_user TEXT NOT NULL,
                db_prefix TEXT DEFAULT 'wp_',
                document_root TEXT,
                auto_update_core INTEGER DEFAULT 1,
                auto_update_plugins INTEGER DEFAULT 0,
                auto_update_themes INTEGER DEFAULT 0,
                is_staging INTEGER DEFAULT 0,
                staging_source_id INTEGER DEFAULT NULL,
                security_score INTEGER DEFAULT 0,
                is_locked INTEGER DEFAULT 0,
                security_flags TEXT DEFAULT '{}',
                wp_defines TEXT DEFAULT '{}',
                wpc_optimize_flags TEXT DEFAULT '{}',
                site_size_mb REAL DEFAULT 0,
                db_size_mb REAL DEFAULT 0,
                uploads_size_mb REAL DEFAULT 0,
                last_scanned_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_backup_at DATETIME,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (domain_id) REFERENCES domains(id) ON DELETE CASCADE
            )
        """)
        cursor = await db.execute("PRAGMA table_info(wp_instances)")
        existing_cols = [r[1] for r in await cursor.fetchall()]
        needed_cols = [
            ("user_id", "INTEGER"),
            ("db_prefix", "TEXT DEFAULT 'wp_'"),
            ("document_root", "TEXT"),
            ("is_locked", "INTEGER DEFAULT 0"),
            ("security_flags", "TEXT DEFAULT '{}'"),
            ("wp_defines", "TEXT DEFAULT '{}'"),
            ("wpc_optimize_flags", "TEXT DEFAULT '{}'"),
            ("auto_update_themes", "INTEGER DEFAULT 0"),
            ("site_size_mb", "REAL DEFAULT 0"),
            ("db_size_mb", "REAL DEFAULT 0"),
            ("uploads_size_mb", "REAL DEFAULT 0"),
            ("last_scanned_at", "DATETIME DEFAULT CURRENT_TIMESTAMP")
        ]
        for col_name, col_type in needed_cols:
            if col_name not in existing_cols:
                try:
                    await db.execute(f"ALTER TABLE wp_instances ADD COLUMN {col_name} {col_type}")
                except Exception:
                    pass
        await db.commit()
    except Exception:
        pass

@router.get("/store/popular")
async def get_wp_store_popular(kind: str = "plugins", search: str = "", current_user: dict = Depends(get_current_user)):
    """Lấy danh sách Plugin hoặc Theme phổ biến từ WordPress.org API"""
    import urllib.request
    import urllib.parse
    import json

    is_theme = (kind == "themes")
    action = "query_themes" if is_theme else "query_plugins"
    params = [f"action={action}", "request[per_page]=24"]
    if search.strip():
        params.append("request[search]=" + urllib.parse.quote(search.strip()))
    else:
        params.append("request[browse]=popular")

    base = "https://api.wordpress.org/themes/info/1.2/" if is_theme else "https://api.wordpress.org/plugins/info/1.2/"
    url = base + "?" + "&".join(params)
    items = []
    try:
        req_u = urllib.request.Request(url, headers={"User-Agent": "PressPanel/1.0"})
        with urllib.request.urlopen(req_u, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
            raw_list = data.get("themes" if is_theme else "plugins") or []
            for it in raw_list:
                items.append({
                    "slug": it.get("slug", ""),
                    "name": re.sub(r"<[^>]+>", "", str(it.get("name", ""))),
                    "version": it.get("version", ""),
                    "description": re.sub(r"<[^>]+>", "", str(it.get("short_description") or it.get("description") or ""))[:180],
                    "author": re.sub(r"<[^>]+>", "", str(it.get("author", ""))),
                    "active_installs": it.get("active_installs", 0),
                    "download_link": it.get("download_link", ""),
                    "activate": True
                })
    except Exception as e:
        return {"status": "error", "items": [], "message": f"Không thể kết nối WordPress.org: {str(e)}"}

    return {"status": "ok", "items": items}


@router.get("/store/premium")
async def get_wp_store_premium(current_user: dict = Depends(get_current_user)):
    """Lấy danh sách Plugin & Theme Premium từ kho WPCloud"""
    import urllib.request
    import json

    url = "https://ultimate-wordpress-manager.wpcloud.vn/premium.json"
    base_url = "https://ultimate-wordpress-manager.wpcloud.vn/"
    plugins = []
    themes = []
    try:
        req_u = urllib.request.Request(url, headers={"User-Agent": "PressPanel/1.0"})
        with urllib.request.urlopen(req_u, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
            for p in (data.get("plugins") or []):
                f_url = p.get("zip_url") or p.get("url") or p.get("file") or ""
                if f_url and not f_url.startswith("http"):
                    f_url = base_url + f_url.lstrip("/")
                plugins.append({
                    "name": p.get("name", ""),
                    "description": p.get("description", ""),
                    "type": p.get("type", "zip"),
                    "slug": p.get("slug") or re.sub(r"[^a-z0-9_-]", "", str(p.get("name", "")).lower().replace(" ", "-")),
                    "zip_url": f_url if p.get("type") != "wporg" else "",
                    "file": p.get("file", ""),
                    "activate": True
                })
            for t in (data.get("themes") or []):
                f_url = t.get("zip_url") or t.get("url") or t.get("file") or ""
                if f_url and not f_url.startswith("http"):
                    f_url = base_url + f_url.lstrip("/")
                themes.append({
                    "name": t.get("name", ""),
                    "description": t.get("description", ""),
                    "type": t.get("type", "zip"),
                    "slug": t.get("slug") or re.sub(r"[^a-z0-9_-]", "", str(t.get("name", "")).lower().replace(" ", "-")),
                    "zip_url": f_url if t.get("type") != "wporg" else "",
                    "file": t.get("file", ""),
                    "activate": True
                })
    except Exception as e:
        return {"status": "error", "plugins": [], "themes": [], "message": f"Không thể tải danh sách Premium: {str(e)}"}

    return {"status": "ok", "plugins": plugins, "themes": themes}


@router.get("/list")
async def list_wordpress_instances(current_user: dict = Depends(get_current_user)):
    """Lấy danh sách các site WordPress thuộc quyền quản lý của User (hoặc tất cả nếu là admin)"""
    db = await get_db_connection()
    try:
        await ensure_wp_instances_table(db)
        if current_user["role"] == "admin":
            cursor = await db.execute("""
                SELECT w.*, d.domain_name, u.username as owner_username
                FROM wp_instances w
                JOIN domains d ON w.domain_id = d.id
                JOIN users u ON d.user_id = u.id
                ORDER BY w.id DESC
            """)
        elif current_user.get("web_domain_id"):
            cursor = await db.execute("""
                SELECT w.*, d.domain_name, u.username as owner_username
                FROM wp_instances w
                JOIN domains d ON w.domain_id = d.id
                JOIN users u ON d.user_id = u.id
                WHERE d.id = ?
                ORDER BY w.id DESC
            """, (current_user["web_domain_id"],))
        else:
            cursor = await db.execute("""
                SELECT w.*, d.domain_name, u.username as owner_username
                FROM wp_instances w
                JOIN domains d ON w.domain_id = d.id
                JOIN users u ON d.user_id = u.id
                WHERE d.user_id = ? OR u.parent_id = ?
                ORDER BY w.id DESC
            """, (current_user["id"], current_user["id"]))

        rows = await cursor.fetchall()
        instances = []
        for r in rows:
            item = dict(r)
            # Parse JSON fields
            for jf in ["security_flags", "wp_defines", "wpc_optimize_flags"]:
                val = item.get(jf)
                if isinstance(val, str):
                    try:
                        item[jf] = json.loads(val)
                    except Exception:
                        item[jf] = {}
                elif not val:
                    item[jf] = {}
            instances.append(item)

        return {"status": "ok", "instances": instances}
    except Exception as e:
        return {"status": "ok", "instances": [], "error": str(e)}
    finally:
        await db.close()

@router.post("/scan")
async def scan_wordpress_instances(current_user: dict = Depends(get_current_user)):
    """Tự động quét các thư mục domain trên máy chủ để phát hiện và đăng ký các site WordPress"""
    db = await get_db_connection()
    await ensure_wp_instances_table(db)
    if current_user["role"] == "admin":
        cursor = await db.execute("SELECT id, domain_name, user_id, document_root FROM domains WHERE COALESCE(is_system, 0) = 0")
    elif current_user.get("web_domain_id"):
        cursor = await db.execute("SELECT id, domain_name, user_id, document_root FROM domains WHERE id = ?", (current_user["web_domain_id"],))
    else:
        cursor = await db.execute("""
            SELECT d.id, d.domain_name, d.user_id, d.document_root
            FROM domains d
            JOIN users u ON d.user_id = u.id
            WHERE (d.user_id = ? OR u.parent_id = ?) AND COALESCE(d.is_system, 0) = 0
        """, (current_user["id"], current_user["id"]))

    domains = await cursor.fetchall()
    discovered = 0

    for d in domains:
        dom_id = d["id"]
        dom_name = d["domain_name"]
        u_id = d["user_id"]

        cur_u = await db.execute("SELECT username FROM users WHERE id = ?", (u_id,))
        user_row = await cur_u.fetchone()
        if not user_row:
            continue
        uname = user_row["username"]

        docroot = resolve_target_dir(uname, dom_name, d["document_root"])
        cfg = parse_wp_config_file(docroot)
        if cfg["exists"]:
            # Kiểm tra xem instance đã được lưu trong DB chưa
            chk = await db.execute("SELECT id FROM wp_instances WHERE domain_id = ?", (dom_id,))
            exists_inst = await chk.fetchone()

            # Lấy thông tin phiên bản từ version.php
            wp_version = "6.7"
            v_file = os.path.join(docroot, "wp-includes", "version.php")
            if os.path.isfile(v_file):
                try:
                    with open(v_file, "r", encoding="utf-8", errors="ignore") as vf:
                        v_content = vf.read()
                    vm = re.search(r"\$wp_version\s*=\s*['\"]([^'\"]+)['\"];", v_content)
                    if vm:
                        wp_version = vm.group(1)
                except Exception:
                    pass

            if not exists_inst:
                await db.execute("""
                    INSERT INTO wp_instances (
                        domain_id, user_id, site_title, admin_user, admin_email,
                        wp_version, db_name, db_user, db_prefix, document_root
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    dom_id, u_id, dom_name, "admin", f"admin@{dom_name}",
                    wp_version, cfg["db_name"], cfg["db_user"], cfg["table_prefix"], docroot
                ))
                discovered += 1
            else:
                await db.execute("""
                    UPDATE wp_instances SET
                        wp_version = ?, db_name = ?, db_user = ?, db_prefix = ?, document_root = ?
                    WHERE domain_id = ?
                """, (wp_version, cfg["db_name"], cfg["db_user"], cfg["table_prefix"], docroot, dom_id))

    await db.commit()
    await db.close()

    return {"status": "ok", "discovered": discovered, "message": f"Quét hoàn tất, phát hiện/cập nhật {discovered} website WordPress"}

async def _fetch_wp_instance_authorized(instance_id: int, current_user: dict) -> dict:
    """Lấy thông tin WordPress instance kèm kiểm tra chặt chẽ quyền sở hữu (Admin / Reseller / Hosting User / Web App)."""
    db = await get_db_connection()
    try:
        cursor = await db.execute("""
            SELECT w.*, d.domain_name, d.document_root, d.user_id as domain_user_id,
                   u.username as owner_username, u.parent_id as owner_parent_id
            FROM wp_instances w
            JOIN domains d ON w.domain_id = d.id
            JOIN users u ON d.user_id = u.id
            WHERE w.id = ?
        """, (instance_id,))
        row = await cursor.fetchone()
    finally:
        await db.close()

    if not row:
        raise HTTPException(status_code=404, detail="Không tìm thấy WordPress instance")

    inst = dict(row)
    role = current_user.get("role")
    if role == "admin":
        return inst
    if current_user.get("web_domain_id"):
        if int(inst["domain_id"]) != int(current_user["web_domain_id"]):
            raise HTTPException(status_code=403, detail="Bạn không có quyền truy cập WordPress instance này")
        return inst
    if role == "reseller":
        if inst["domain_user_id"] != current_user["id"] and inst["owner_parent_id"] != current_user["id"]:
            raise HTTPException(status_code=403, detail="Bạn không có quyền truy cập WordPress instance này")
        return inst
    if inst["domain_user_id"] != current_user["id"]:
        raise HTTPException(status_code=403, detail="Bạn không có quyền truy cập WordPress instance này")
    return inst

@router.get("/{instance_id}/detail")
async def get_wordpress_detail(instance_id: int, current_user: dict = Depends(get_current_user)):
    """Lấy thông tin chi tiết đầy đủ của 1 website WordPress (Site Weight, Security, Defines, Optimize)"""
    inst = await _fetch_wp_instance_authorized(instance_id, current_user)



    docroot = inst["document_root"] or resolve_target_dir(inst["owner_username"], inst["domain_name"])
    cfg = parse_wp_config_file(docroot)

    # 1. Tính toán Site Weight chi tiết
    core_size = get_dir_size_mb(os.path.join(docroot, "wp-admin")) + get_dir_size_mb(os.path.join(docroot, "wp-includes"))
    uploads_size = get_dir_size_mb(os.path.join(docroot, "wp-content", "uploads"))
    plugins_size = get_dir_size_mb(os.path.join(docroot, "wp-content", "plugins"))
    themes_size = get_dir_size_mb(os.path.join(docroot, "wp-content", "themes"))
    total_files_size = get_dir_size_mb(docroot)

    # Database Size
    db_size = 0.0
    if cfg["db_name"]:
        sql = f"SELECT ROUND(SUM(data_length + index_length) / 1024 / 1024, 2) AS db_size_mb FROM information_schema.TABLES WHERE table_schema = '{cfg['db_name']}';"
        rows = await run_mysql_query(cfg, sql)
        if rows and rows[0].get("db_size_mb"):
            try:
                db_size = float(rows[0]["db_size_mb"])
            except Exception:
                pass

    # 2. Kiểm tra Lockdown status
    is_locked = 0
    cfg_file = os.path.join(docroot, "wp-config.php")
    if os.path.isfile(cfg_file):
        try:
            # Kiểm tra quyền ghi hoặc thuộc tính bất biến
            st = os.stat(cfg_file)
            if not (st.st_mode & 0o200):  # Không có quyền ghi
                is_locked = 1
        except Exception:
            pass

    # 3. Trạng thái 7 WPC Optimize MU-Plugins
    mu_dir = os.path.join(docroot, "wp-content", "mu-plugins")
    wpc_optimize = {
        "admin": os.path.isfile(os.path.join(mu_dir, "wpc-optimize-admin.php")),
        "compatibility": os.path.isfile(os.path.join(mu_dir, "wpc-optimize-compatibility.php")),
        "editor": os.path.isfile(os.path.join(mu_dir, "wpc-optimize-editor.php")),
        "flatsome": os.path.isfile(os.path.join(mu_dir, "wpc-optimize-flatsome.php")),
        "frontend": os.path.isfile(os.path.join(mu_dir, "wpc-optimize-frontend.php")),
        "media": os.path.isfile(os.path.join(mu_dir, "wpc-optimize-media.php")),
        "security": os.path.isfile(os.path.join(mu_dir, "wpc-optimize-security.php")),
    }

    # 4. Trạng thái WP Defines
    defines = {
        "WP_DEBUG": cfg["defines"].get("WP_DEBUG", "false"),
        "WP_DEBUG_LOG": cfg["defines"].get("WP_DEBUG_LOG", "false"),
        "WP_CACHE": cfg["defines"].get("WP_CACHE", "false"),
        "WP_MEMORY_LIMIT": cfg["defines"].get("WP_MEMORY_LIMIT", "256M"),
        "AUTOSAVE_INTERVAL": cfg["defines"].get("AUTOSAVE_INTERVAL", "60"),
        "EMPTY_TRASH_DAYS": cfg["defines"].get("EMPTY_TRASH_DAYS", "30"),
        "DISALLOW_FILE_EDIT": cfg["defines"].get("DISALLOW_FILE_EDIT", "true"),
    }

    # 5. Đánh giá điểm bảo mật (0-100)
    sec_score = 0
    sec_checks = {
        "lockdown": is_locked == 1,
        "disallow_file_edit": cfg["defines"].get("DISALLOW_FILE_EDIT") in ["true", "1"],
        "debug_off": cfg["defines"].get("WP_DEBUG") in ["false", "0"],
        "mu_security": wpc_optimize["security"],
        "db_prefix": cfg["table_prefix"] != "wp_",
        "ssl": True
    }
    for k, v in sec_checks.items():
        if v:
            sec_score += 16
    if sec_score > 100:
        sec_score = 100

    data = dict(inst)
    data["weight"] = {
        "core_mb": core_size,
        "uploads_mb": uploads_size,
        "plugins_mb": plugins_size,
        "themes_mb": themes_size,
        "total_files_mb": total_files_size,
        "db_mb": db_size,
        "total_mb": round(total_files_size + db_size, 2)
    }
    data["is_locked"] = is_locked
    data["wpc_optimize"] = wpc_optimize
    data["defines"] = defines
    data["security_score"] = sec_score
    data["security_checks"] = sec_checks

    return {"status": "ok", "detail": data}

# ==============================================================================
# Endpoints: Install, Clone & Magic Login SSO
# ==============================================================================

@router.post("/install")
async def install_wordpress(req: WPInstallRequest, current_user: dict = Depends(get_current_user)):
    """Cài đặt WordPress mới hoàn toàn qua Direct ZIP từ wordpress.org (không nạp WP hook) với ràng buộc nghiêm ngặt"""
    # 1. RÀNG BUỘC TIÊN QUYẾT: KIỂM TRA MARIADB / MYSQL
    db_ok, db_err = check_mysql_service_available()
    if not db_ok:
        raise HTTPException(
            status_code=400,
            detail=f"Lỗi ràng buộc hệ thống: {db_err}"
        )

    # 2. XÁC THỰC VÀ CHUẨN HÓA TÊN MIỀN
    db = await get_db_connection()
    await ensure_wp_instances_table(db)
    domain_name = ""
    domain_docroot = ""
    owner_user_id = current_user["id"]
    domain_id = req.domain_id

    if domain_id:
        if current_user["role"] == "admin":
            cursor = await db.execute("SELECT id, domain_name, user_id, document_root FROM domains WHERE id = ?", (domain_id,))
        elif current_user.get("web_domain_id"):
            if int(domain_id) != int(current_user["web_domain_id"]):
                await db.close()
                raise HTTPException(status_code=403, detail="Bạn chỉ có quyền cài đặt WordPress trên website của mình")
            cursor = await db.execute("SELECT id, domain_name, user_id, document_root FROM domains WHERE id = ?", (domain_id,))
        else:
            cursor = await db.execute("""
                SELECT d.id, d.domain_name, d.user_id, d.document_root
                FROM domains d
                JOIN users u ON d.user_id = u.id
                WHERE d.id = ? AND (d.user_id = ? OR u.parent_id = ?)
            """, (domain_id, current_user["id"], current_user["id"]))
        domain_row = await cursor.fetchone()
        if not domain_row:
            await db.close()
            raise HTTPException(status_code=404, detail="Không tìm thấy tên miền chỉ định")
        domain_name = domain_row["domain_name"]
        domain_docroot = domain_row["document_root"] or ""
        owner_user_id = domain_row["user_id"]
    elif req.domain:
        clean_domain = re.sub(r"^(https?://)?(www\.)?", "", req.domain.strip().lower()).rstrip("/")
        if not clean_domain or not re.match(r"^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$", clean_domain):
            await db.close()
            raise HTTPException(status_code=400, detail="Tên miền không hợp lệ (ví dụ: mysite.com, blog.example.vn)")

        domain_name = clean_domain
        # Kiểm tra xem domain đã tồn tại trong database chưa
        cursor = await db.execute("SELECT id, domain_name, user_id, document_root FROM domains WHERE domain_name = ?", (domain_name,))
        existing_domain = await cursor.fetchone()
        if existing_domain:
            if current_user["role"] != "admin":
                if current_user.get("web_domain_id"):
                    if int(existing_domain["id"]) != int(current_user["web_domain_id"]):
                        await db.close()
                        raise HTTPException(status_code=403, detail="Bạn chỉ có quyền cài đặt WordPress trên website của mình")
                elif existing_domain["user_id"] != current_user["id"]:
                    u_chk = await db.execute("SELECT parent_id FROM users WHERE id = ?", (existing_domain["user_id"],))
                    u_chk_row = await u_chk.fetchone()
                    if not u_chk_row or u_chk_row["parent_id"] != current_user["id"]:
                        await db.close()
                        raise HTTPException(status_code=403, detail="Tên miền này đã được đăng ký bởi người dùng khác trên hệ thống")
            domain_id = existing_domain["id"]
            domain_docroot = existing_domain["document_root"] or ""
            owner_user_id = existing_domain["user_id"]
        else:
            # Tự động khởi tạo VirtualHost cho domain mới
            clean_linux_user = re.sub(r"[^a-z0-9]", "", clean_domain)[:30] or "webuser"
            hosting_username = current_user.get("username", clean_linux_user)
            ws_cur = await db.execute("SELECT webserver FROM domains ORDER BY id DESC LIMIT 1")
            last_ws = await ws_cur.fetchone()
            active_ws = last_ws["webserver"] if (last_ws and last_ws["webserver"]) else "ols"

            php_ver = req.php_version or "8.4"
            if php_ver.startswith("PHP-"):
                php_ver = php_ver.replace("PHP-", "")
                if len(php_ver) == 2:
                    php_ver = f"{php_ver[0]}.{php_ver[1]}"

            vhost_res = await run_gatekeeper("vhost", "add", clean_linux_user, domain_name, active_ws, php_ver, hosting_username)
            if vhost_res.get("status") == "error":
                await db.close()
                raise HTTPException(status_code=400, detail=f"Không thể khởi tạo VirtualHost cho {domain_name}: {vhost_res.get('message')}")

            domain_docroot = f"/home/{clean_linux_user}/domains/{domain_name}/public_html"
            ins_cur = await db.execute("""
                INSERT INTO domains (user_id, domain_name, document_root, webserver, php_version, allow_ssh)
                VALUES (?, ?, ?, ?, ?, 1)
            """, (current_user["id"], domain_name, domain_docroot, active_ws, php_ver))
            domain_id = ins_cur.lastrowid
            owner_user_id = current_user["id"]
            await db.commit()
    else:
        await db.close()
        raise HTTPException(status_code=400, detail="Vui lòng chọn hoặc nhập tên miền muốn cài đặt WordPress")

    # 3. VALIDATION DỮ LIỆU ĐẦU VÀO FORM
    if not re.match(r"^[a-zA-Z0-9_\-\.]{3,32}$", req.admin_user.strip()):
        await db.close()
        raise HTTPException(status_code=400, detail="Username Admin không hợp lệ (chỉ chấp nhận 3-32 ký tự chữ, số, gạch ngang, gạch dưới)")

    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", req.admin_email.strip()):
        await db.close()
        raise HTTPException(status_code=400, detail="Địa chỉ email quản trị không đúng định dạng")

    if not req.allow_weak_passwords and len(req.admin_pass) < 6:
        await db.close()
        raise HTTPException(status_code=400, detail="Mật khẩu Admin phải có ít nhất 6 ký tự hoặc đánh dấu 'Allow weak passwords'")

    if req.db_name and not re.match(r"^[a-zA-Z0-9_]{1,64}$", req.db_name):
        await db.close()
        raise HTTPException(status_code=400, detail="Tên cơ sở dữ liệu chỉ được chứa chữ cái, số và dấu gạch dưới")

    if req.db_prefix and not re.match(r"^[a-zA-Z0-9_]{1,20}$", req.db_prefix):
        await db.close()
        raise HTTPException(status_code=400, detail="Table Prefix chỉ được chứa chữ cái, số và dấu gạch dưới")

    sub_dir = (req.subdirectory or "").strip().strip("/")
    if sub_dir and not re.match(r"^[a-zA-Z0-9_\-]{1,64}$", sub_dir):
        await db.close()
        raise HTTPException(status_code=400, detail="Thư mục con (Subdirectory) chỉ được chứa ký tự chữ, số, gạch ngang và gạch dưới")

    linux_user = extract_linux_user(domain_docroot, domain_name, current_user["username"])
    full_prefix = re.sub(r"[^a-zA-Z0-9]", "", domain_name or linux_user).lower() or linux_user
    raw_wp_db = (req.db_name or "").strip()
    if raw_wp_db:
        if not raw_wp_db.startswith(f"{full_prefix}_"):
            final_wp_db = f"{full_prefix}_{raw_wp_db}"[:64]
        else:
            final_wp_db = raw_wp_db[:64]
    else:
        final_wp_db = ""

    # 4. GỌI GATEKEEPER THỰC THI SCRIPT CÀI ĐẶT
    res = await run_gatekeeper(
        "wp", "install",
        linux_user,
        domain_name,
        req.site_title or "Website title for wordpress",
        req.admin_user.strip(),
        req.admin_email.strip(),
        req.language or "en_US",
        final_wp_db,
        req.db_pass or "",
        req.db_prefix or "wp_",
        sub_dir,
        "1" if req.enable_cache else "0",
        req.wp_version or "latest",
        "1" if req.create_html else "0",
        stdin_data=req.admin_pass
    )

    if res.get("status") == "error":
        await db.close()
        err_msg = res.get("message", "Cài đặt WordPress thất bại. Kiểm tra MariaDB hoặc quyền thư mục!")
        raise HTTPException(status_code=400, detail=err_msg)

    docroot = resolve_target_dir(linux_user, domain_name, domain_docroot)
    if sub_dir:
        docroot = os.path.join(docroot, sub_dir)
    cfg = parse_wp_config_file(docroot)

    installed_wp_ver = req.wp_version or "latest"
    v_file = os.path.join(docroot, "wp-includes", "version.php")
    if os.path.isfile(v_file):
        try:
            with open(v_file, "r", encoding="utf-8", errors="ignore") as vf:
                vm = re.search(r"\$wp_version\s*=\s*['\"]([^'\"]+)['\"];", vf.read())
                if vm:
                    installed_wp_ver = vm.group(1)
        except Exception:
            pass

    created_db_name = cfg["db_name"] or final_wp_db or f"{full_prefix}_wp"
    created_db_user = cfg["db_user"] or created_db_name[:64]
    created_prefix = cfg["table_prefix"] or req.db_prefix or "wp_"

    # 5. LƯU THÔNG TIN INSTANCE VÀO DATABASE
    await db.execute("""
        INSERT INTO wp_instances (
            domain_id, user_id, site_title, admin_user, admin_email,
            wp_version, db_name, db_user, db_prefix, document_root
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        domain_id, owner_user_id, req.site_title or "Website title for wordpress",
        req.admin_user.strip(), req.admin_email.strip(),
        installed_wp_ver,
        created_db_name,
        created_db_user,
        created_prefix,
        docroot
    ))

    # Đồng bộ CSDL vừa tạo vào bảng databases để hiển thị ngay trong menu Databases & phpMyAdmin SSO
    try:
        db_chk = await db.execute("SELECT id FROM databases WHERE db_name = ?", (created_db_name,))
        if not await db_chk.fetchone():
            await db.execute("""
                INSERT INTO databases (user_id, domain_id, db_name, db_user, db_type, charset, description)
                VALUES (?, ?, ?, ?, 'mysql', 'utf8mb4_unicode_520_ci', ?)
            """, (owner_user_id, domain_id, created_db_name, created_db_user, f"WordPress DB ({domain_name})"))
    except Exception:
        pass

    # Nếu người dùng chọn Apply for SSL
    if req.apply_ssl:
        await db.execute("UPDATE domains SET ssl_enabled = 1 WHERE id = ?", (domain_id,))

    await db.commit()
    await db.close()

    site_path_info = f"{domain_name}/{sub_dir}" if sub_dir else domain_name
    return {
        "status": "ok",
        "message": f"Website WordPress cho {site_path_info} đã được cài đặt thành công!",
        "db_name": created_db_name,
        "db_user": created_db_user,
        "wp_version": installed_wp_ver,
        "document_root": docroot
    }

@router.post("/clone")
async def clone_wordpress(req: WPCloneRequest, current_user: dict = Depends(get_current_user)):
    """Clone WordPress sang tên miền mới (search-replace serialize an toàn với WP CLI)"""
    db = await get_db_connection()
    cursor = await db.execute("""
        SELECT w.*, d.domain_name, u.username as owner_username
        FROM wp_instances w
        JOIN domains d ON w.domain_id = d.id
        JOIN users u ON d.user_id = u.id
        WHERE d.id = ?
    """, (req.src_domain_id,))
    src_inst = await cursor.fetchone()
    if not src_inst:
        await db.close()
        raise HTTPException(status_code=404, detail="Không tìm thấy website nguồn")

    if current_user["role"] != "admin" and src_inst["user_id"] != current_user["id"]:
        await db.close()
        raise HTTPException(status_code=403, detail="Không có quyền sao chép website này")

    username = src_inst["owner_username"]
    src_domain = src_inst["domain_name"]
    dest_domain = req.dest_domain_name.strip().lower()

    # Chạy gatekeeper clone: wp clone <username> <src_domain> <dest_domain>
    res = await run_gatekeeper("wp", "clone", username, src_domain, dest_domain)

    if res.get("status") == "error":
        await db.close()
        raise HTTPException(status_code=400, detail=res.get("message", "Clone website thất bại"))

    # Nếu là Staging hoặc bật chế độ chống index, tắt search engine indexing (blog_public = 0)
    if req.disallow_indexing or req.is_staging:
        try:
            await run_gatekeeper("wp", "run_cli", username, dest_domain, "option", "update", "blog_public", "0")
        except Exception:
            pass

    # Đánh dấu is_staging trong DB nếu là môi trường thử nghiệm
    if req.is_staging:
        try:
            dest_cursor = await db.execute("SELECT id FROM domains WHERE domain_name = ?", (dest_domain,))
            dest_d = await dest_cursor.fetchone()
            if dest_d:
                await db.execute("""
                    UPDATE wp_instances 
                    SET is_staging = 1, staging_source_id = ?
                    WHERE domain_id = ?
                """, (req.src_domain_id, dest_d["id"]))
                await db.commit()
        except Exception:
            pass

    await db.close()

    msg = f"Website đã được nhân bản thành công sang {dest_domain}!"
    if req.is_staging:
        msg = f"Môi trường thử nghiệm Staging ({dest_domain}) đã sẵn sàng và được bảo vệ chống Google Bot index!"
    return {"status": "ok", "message": msg}

@router.get("/{instance_id}/sso")
async def get_magic_login_sso(instance_id: int, current_user: dict = Depends(get_current_user)):
    """Tạo liên kết Magic Login Single-Use (tự huỷ sau 60s) cho Administrator mặc định"""
    return await get_magic_login_user_sso(instance_id, 0, current_user)

@router.get("/{instance_id}/sso-user/{user_id}")
async def get_magic_login_user_sso(instance_id: int, user_id: int, current_user: dict = Depends(get_current_user)):
    """Tạo liên kết Magic Login Single-Use cho bất kỳ User ID nào trong WordPress"""
    db = await get_db_connection()
    cursor = await db.execute("""
        SELECT w.*, d.domain_name, d.document_root as domain_docroot, d.ssl_enabled, u.username as owner_username
        FROM wp_instances w
        JOIN domains d ON w.domain_id = d.id
        JOIN users u ON d.user_id = u.id
        WHERE w.id = ?
    """, (instance_id,))
    row = await cursor.fetchone()
    await db.close()

    if not row:
        raise HTTPException(status_code=404, detail="Không tìm thấy WordPress instance")

    if current_user["role"] == "admin":
        is_allowed = True
    elif current_user.get("web_domain_id"):
        is_allowed = int(current_user["web_domain_id"]) == int(row["domain_id"])
    else:
        is_allowed = (row["user_id"] == current_user["id"])
    if not is_allowed:
        raise HTTPException(status_code=403, detail="Bạn không có quyền truy cập website này")

    linux_user = extract_linux_user(row["document_root"] or row["domain_docroot"] or "", row["domain_name"], row["owner_username"])
    wp_docroot = (row["document_root"] or "").rstrip("/")
    dom_docroot = (row["domain_docroot"] or "").rstrip("/")
    sub_path = ""
    if wp_docroot and dom_docroot and wp_docroot.startswith(dom_docroot + "/"):
        sub_path = wp_docroot[len(dom_docroot) + 1:].strip("/")

    token = secrets.token_hex(16)
    # Gọi C gatekeeper: wp sso_create <linux_user> <domain> <target_user_id> <token> <admin_user> [subfolder]
    res = await run_gatekeeper("wp", "sso_create", linux_user, row["domain_name"], str(user_id), token, row["admin_user"] or "admin", sub_path)
    if res.get("status") == "error":
        raise HTTPException(status_code=400, detail=res.get("message", "Tạo liên kết Magic Login thất bại"))

    scheme = "https" if row["ssl_enabled"] else "http"
    url_prefix = f"{scheme}://{row['domain_name']}/{sub_path}" if sub_path else f"{scheme}://{row['domain_name']}"
    sso_url = f"{url_prefix}/?wpc_sso_token={token}"
    return {
        "status": "ok",
        "sso_url": sso_url,
        "token": token,
        "expires_in": 60,
        "message": "Liên kết Magic Login đã sẵn sàng, tự động hủy sau 60 giây hoặc ngay sau khi đăng nhập."
    }

# ==============================================================================
# Endpoints: Plugins Management (Direct ZIP & Clean Reinstall)
# ==============================================================================

@router.get("/{instance_id}/plugins")
async def list_plugins(instance_id: int, current_user: dict = Depends(get_current_user)):
    """Lấy danh sách Plugin đã cài đặt trực tiếp từ file headers và active_plugins trong database"""
    inst = await _fetch_wp_instance_authorized(instance_id, current_user)

    docroot = inst["document_root"] or resolve_target_dir(inst["owner_username"], inst["domain_name"])
    plugins_dir = os.path.join(docroot, "wp-content", "plugins")
    if not os.path.isdir(plugins_dir):
        return {"status": "ok", "plugins": []}

    cfg = parse_wp_config_file(docroot)

    # Đọc active_plugins từ MariaDB
    active_set = set()
    if cfg["db_name"]:
        sql = f"SELECT option_value FROM {cfg['table_prefix']}options WHERE option_name = 'active_plugins' LIMIT 1;"
        rows = await run_mysql_query(cfg, sql)
        if rows and rows[0].get("option_value"):
            val = rows[0]["option_value"]
            # Extract plugin file paths from PHP serialized string
            found = re.findall(r's:\d+:"([^"]+\.php)"', val)
            active_set = set(found)

    plugins = []
    try:
        for entry in os.scandir(plugins_dir):
            if entry.is_dir():
                slug = entry.name
                # Tìm main plugin file
                php_files = glob.glob(os.path.join(entry.path, "*.php"))
                for pf in php_files:
                    try:
                        with open(pf, "r", encoding="utf-8", errors="ignore") as f:
                            header_data = f.read(8192)
                        if "Plugin Name:" in header_data:
                            name_m = re.search(r"Plugin Name:\s*([^\r\n]+)", header_data)
                            ver_m = re.search(r"Version:\s*([^\r\n]+)", header_data)
                            desc_m = re.search(r"Description:\s*([^\r\n]+)", header_data)
                            auth_m = re.search(r"Author:\s*([^\r\n]+)", header_data)

                            rel_file = f"{slug}/{os.path.basename(pf)}"
                            plugins.append({
                                "slug": slug,
                                "file": rel_file,
                                "name": name_m.group(1).strip() if name_m else slug,
                                "version": ver_m.group(1).strip() if ver_m else "1.0",
                                "description": desc_m.group(1).strip() if desc_m else "",
                                "author": auth_m.group(1).strip() if auth_m else "",
                                "is_active": rel_file in active_set
                            })
                            break
                    except Exception:
                        pass
    except Exception:
        pass

    return {"status": "ok", "plugins": plugins}

@router.post("/{instance_id}/plugins/reinstall")
async def reinstall_plugin_clean(instance_id: int, req: WPPluginReinstallRequest, current_user: dict = Depends(get_current_user)):
    """
    CÀI ĐẶT LẠI / LÀM SẠCH PLUGIN BẰNG ZIP TRỰC TIẾP
    - Xoá sạch thư mục plugin cũ (diệt tận gốc mã độc/backdoor bị chèn)
    - Tải ZIP nguyên bản từ wordpress.org (hoặc custom ZIP URL)
    - Giải nén đè mới 100%
    - Phân quyền an toàn chuẩn Linux
    - Tuyệt đối không chạy WP hooks gây lỗi hay tái kích hoạt virus!
    """
    inst = await _fetch_wp_instance_authorized(instance_id, current_user)

    uname = inst["owner_username"]
    domain = inst["domain_name"]
    slug = req.slug.strip()
    zip_url = req.zip_url.strip() if req.zip_url else f"https://downloads.wordpress.org/plugin/{slug}.zip"

    res = await run_gatekeeper("wp", "download_zip", uname, domain, "plugin", slug, zip_url)
    if res.get("status") == "error":
        raise HTTPException(status_code=400, detail=res.get("message", f"Làm sạch và cài đặt lại plugin {slug} thất bại"))

    return {
        "status": "ok",
        "slug": slug,
        "message": f"Đã tải ZIP sạch và cài đặt lại Plugin [{slug}] thành công! Toàn bộ file mã độc cũ đã được làm sạch."
    }

@router.post("/{instance_id}/plugins/install")
async def install_plugin(instance_id: int, req: WPPluginInstallRequest, current_user: dict = Depends(get_current_user)):
    """Cài đặt Plugin mới từ slug repo WordPress.org hoặc từ URL ZIP tuỳ chọn"""
    return await reinstall_plugin_clean(instance_id, WPPluginReinstallRequest(slug=req.slug, zip_url=req.zip_url), current_user)

@router.post("/{instance_id}/plugins/toggle")
async def toggle_plugin(instance_id: int, req: WPPluginToggleRequest, current_user: dict = Depends(get_current_user)):
    """Bật / Tắt Plugin bằng cách cập nhật trực tiếp DB active_plugins mà không nạp WP hook"""
    inst = await _fetch_wp_instance_authorized(instance_id, current_user)

    docroot = inst["document_root"] or resolve_target_dir(inst["owner_username"], inst["domain_name"])
    cfg = parse_wp_config_file(docroot)
    if not cfg["db_name"]:
        raise HTTPException(status_code=400, detail="Không thể đọc cơ sở dữ liệu để bật/tắt plugin")

    plugin_file = req.plugin_file.strip()

    # Lấy danh sách plugin đang active
    sql = f"SELECT option_value FROM {cfg['table_prefix']}options WHERE option_name = 'active_plugins' LIMIT 1;"
    rows = await run_mysql_query(cfg, sql)
    current_active = []
    if rows and rows[0].get("option_value"):
        current_active = re.findall(r's:\d+:"([^"]+\.php)"', rows[0]["option_value"])

    active_set = set(current_active)
    if req.enable:
        active_set.add(plugin_file)
    else:
        active_set.discard(plugin_file)

    # Tạo lại chuỗi serialized PHP cơ bản
    items = list(active_set)
    serialized_parts = []
    for idx, item in enumerate(items):
        serialized_parts.append(f'i:{idx};s:{len(item)}:"{item}";')
    serialized_str = f'a:{len(items)}:{{{"" .join(serialized_parts)}}}'

    # Escape quotes
    escaped_str = serialized_str.replace("'", "\\'")
    update_sql = f"UPDATE {cfg['table_prefix']}options SET option_value = '{escaped_str}' WHERE option_name = 'active_plugins';"
    await run_mysql_query(cfg, update_sql)

    return {
        "status": "ok",
        "plugin_file": plugin_file,
        "is_active": req.enable,
        "message": f"Plugin đã {'bật' if req.enable else 'tắt'} thành công."
    }

@router.post("/{instance_id}/plugins/delete")
async def delete_plugin(instance_id: int, req: WPPluginReinstallRequest, current_user: dict = Depends(get_current_user)):
    """Xoá sạch thư mục Plugin khỏi máy chủ"""
    inst = await _fetch_wp_instance_authorized(instance_id, current_user)

    uname = inst["owner_username"]
    domain = inst["domain_name"]
    slug = req.slug.strip()

    res = await run_gatekeeper("wp", "delete_item", uname, domain, "plugin", slug)
    if res.get("status") == "error":
        raise HTTPException(status_code=400, detail=res.get("message", f"Xóa plugin {slug} thất bại"))

    return {"status": "ok", "message": f"Đã xóa hoàn toàn plugin {slug}."}

# ==============================================================================
# Endpoints: Themes Management
# ==============================================================================

@router.get("/{instance_id}/themes")
async def list_themes(instance_id: int, current_user: dict = Depends(get_current_user)):
    """Lấy danh sách Giao diện (Themes) đã cài đặt và trạng thái Active"""
    inst = await _fetch_wp_instance_authorized(instance_id, current_user)

    docroot = inst["document_root"] or resolve_target_dir(inst["owner_username"], inst["domain_name"])
    themes_dir = os.path.join(docroot, "wp-content", "themes")
    if not os.path.isdir(themes_dir):
        return {"status": "ok", "themes": []}

    cfg = parse_wp_config_file(docroot)

    # Đọc theme đang active
    active_theme = ""
    if cfg["db_name"]:
        sql = f"SELECT option_name, option_value FROM {cfg['table_prefix']}options WHERE option_name IN ('template', 'stylesheet');"
        rows = await run_mysql_query(cfg, sql)
        for r in rows:
            if r.get("option_name") == "stylesheet":
                active_theme = r.get("option_value", "")

    themes = []
    try:
        for entry in os.scandir(themes_dir):
            if entry.is_dir():
                slug = entry.name
                css_file = os.path.join(entry.path, "style.css")
                theme_info = {
                    "slug": slug,
                    "name": slug,
                    "version": "1.0",
                    "author": "",
                    "description": "",
                    "is_active": (slug == active_theme)
                }
                if os.path.isfile(css_file):
                    try:
                        with open(css_file, "r", encoding="utf-8", errors="ignore") as f:
                            header_data = f.read(8192)
                        name_m = re.search(r"Theme Name:\s*([^\r\n]+)", header_data)
                        ver_m = re.search(r"Version:\s*([^\r\n]+)", header_data)
                        auth_m = re.search(r"Author:\s*([^\r\n]+)", header_data)
                        desc_m = re.search(r"Description:\s*([^\r\n]+)", header_data)

                        if name_m: theme_info["name"] = name_m.group(1).strip()
                        if ver_m: theme_info["version"] = ver_m.group(1).strip()
                        if auth_m: theme_info["author"] = auth_m.group(1).strip()
                        if desc_m: theme_info["description"] = desc_m.group(1).strip()
                    except Exception:
                        pass
                themes.append(theme_info)
    except Exception:
        pass

    return {"status": "ok", "themes": themes}

@router.post("/{instance_id}/themes/activate")
async def activate_theme(instance_id: int, req: WPThemeActionRequest, current_user: dict = Depends(get_current_user)):
    """Kích hoạt Theme bằng cách cập nhật template và stylesheet trong MariaDB"""
    inst = await _fetch_wp_instance_authorized(instance_id, current_user)

    docroot = inst["document_root"] or resolve_target_dir(inst["owner_username"], inst["domain_name"])
    cfg = parse_wp_config_file(docroot)
    slug = req.slug.strip()

    sql = f"""
    UPDATE {cfg['table_prefix']}options SET option_value = '{slug}' WHERE option_name = 'template';
    UPDATE {cfg['table_prefix']}options SET option_value = '{slug}' WHERE option_name = 'stylesheet';
    """
    await run_mysql_query(cfg, sql)

    return {"status": "ok", "message": f"Đã kích hoạt Theme [{slug}] thành công!"}

@router.post("/{instance_id}/themes/reinstall")
async def reinstall_theme_clean(instance_id: int, req: WPThemeActionRequest, current_user: dict = Depends(get_current_user)):
    """Làm sạch và cài đặt lại Theme từ ZIP trực tiếp"""
    inst = await _fetch_wp_instance_authorized(instance_id, current_user)

    uname = inst["owner_username"]
    domain = inst["domain_name"]
    slug = req.slug.strip()
    zip_url = req.zip_url.strip() if req.zip_url else f"https://downloads.wordpress.org/theme/{slug}.zip"

    res = await run_gatekeeper("wp", "download_zip", uname, domain, "theme", slug, zip_url)
    if res.get("status") == "error":
        raise HTTPException(status_code=400, detail=res.get("message", f"Cài đặt lại Theme {slug} thất bại"))

    return {"status": "ok", "message": f"Đã làm sạch và tải cài đặt Theme [{slug}] thành công!"}

# ==============================================================================
# Endpoints: Users Management (Direct MariaDB Query)
# ==============================================================================

@router.get("/{instance_id}/users")
async def list_wp_users(instance_id: int, current_user: dict = Depends(get_current_user)):
    """Lấy danh sách người dùng WordPress trực tiếp từ MariaDB không nạp WP Core"""
    inst = await _fetch_wp_instance_authorized(instance_id, current_user)

    docroot = inst["document_root"] or resolve_target_dir(inst["owner_username"], inst["domain_name"])
    cfg = parse_wp_config_file(docroot)
    if not cfg["db_name"]:
        return {"status": "ok", "users": []}

    prefix = cfg["table_prefix"]
    sql = f"""
    SELECT u.ID, u.user_login, u.user_email, u.display_name, u.user_registered,
           m.meta_value as capabilities
    FROM {prefix}users u
    LEFT JOIN {prefix}usermeta m ON u.ID = m.user_id AND m.meta_key = '{prefix}capabilities'
    ORDER BY u.ID ASC;
    """
    rows = await run_mysql_query(cfg, sql)
    users = []
    for r in rows:
        caps = r.get("capabilities", "")
        role = "subscriber"
        if "administrator" in caps:
            role = "administrator"
        elif "editor" in caps:
            role = "editor"
        elif "author" in caps:
            role = "author"
        elif "contributor" in caps:
            role = "contributor"

        users.append({
            "id": r.get("ID"),
            "login": r.get("user_login"),
            "email": r.get("user_email"),
            "display_name": r.get("display_name"),
            "registered": r.get("user_registered"),
            "role": role
        })

    return {"status": "ok", "users": users}

@router.post("/{instance_id}/users/create")
async def create_wp_user(instance_id: int, req: WPUserCreateRequest, current_user: dict = Depends(get_current_user)):
    """Tạo tài khoản WordPress mới trực tiếp trong MariaDB"""
    user_login = req.user_login.strip()
    user_email = req.user_email.strip()
    if not re.match(r"^[a-zA-Z0-9_\-.]{2,60}$", user_login):
        raise HTTPException(status_code=400, detail="Tên đăng nhập chỉ được chứa chữ, số, _, -, . (2-60 ký tự)")
    if not user_email or "@" not in user_email:
        raise HTTPException(status_code=400, detail="Email không hợp lệ")
    if not req.user_pass or len(req.user_pass) < 6:
        raise HTTPException(status_code=400, detail="Mật khẩu phải có ít nhất 6 ký tự")

    inst = await _fetch_wp_instance_authorized(instance_id, current_user)

    docroot = inst["document_root"] or resolve_target_dir(inst["owner_username"], inst["domain_name"])
    cfg = parse_wp_config_file(docroot)
    if not cfg["db_name"]:
        raise HTTPException(status_code=400, detail="Không thể đọc cấu hình CSDL WordPress")

    prefix = cfg["table_prefix"]
    esc_login = user_login.replace("'", "\\'")
    esc_email = user_email.replace("'", "\\'")
    existing = await run_mysql_query(cfg, f"SELECT ID FROM {prefix}users WHERE user_login = '{esc_login}' OR user_email = '{esc_email}' LIMIT 1;")
    if existing:
        raise HTTPException(status_code=400, detail="Username hoặc Email đã tồn tại trong WordPress này")

    import hashlib
    md5_pass = hashlib.md5(req.user_pass.encode("utf-8")).hexdigest()
    role = (req.role or "administrator").strip().lower()
    if role not in ("administrator", "editor", "author", "contributor", "subscriber"):
        role = "subscriber"
    level_map = {"administrator": 10, "editor": 7, "author": 2, "contributor": 1, "subscriber": 0}
    user_level = level_map.get(role, 0)
    role_serialized = f'a:1:{{s:{len(role)}:"{role}";b:1;}}'

    sql_insert = f"""
    INSERT INTO {prefix}users (user_login, user_pass, user_nicename, user_email, user_registered, user_status, display_name)
    VALUES ('{esc_login}', '{md5_pass}', '{esc_login.lower()}', '{esc_email}', NOW(), 0, '{esc_login}');
    SET @new_uid = LAST_INSERT_ID();
    INSERT INTO {prefix}usermeta (user_id, meta_key, meta_value) VALUES (@new_uid, '{prefix}capabilities', '{role_serialized}');
    INSERT INTO {prefix}usermeta (user_id, meta_key, meta_value) VALUES (@new_uid, '{prefix}user_level', '{user_level}');
    """
    await run_mysql_query(cfg, sql_insert)
    return {"status": "ok", "message": f"Đã tạo tài khoản WordPress '{user_login}' ({role}) thành công!"}


@router.post("/{instance_id}/users/modify")
async def modify_wp_user(instance_id: int, req: WPUserModifyRequest, current_user: dict = Depends(get_current_user)):
    """Chỉnh sửa thông tin người dùng (Email, Password, Role) trực tiếp trong MariaDB"""
    inst = await _fetch_wp_instance_authorized(instance_id, current_user)

    docroot = inst["document_root"] or resolve_target_dir(inst["owner_username"], inst["domain_name"])
    cfg = parse_wp_config_file(docroot)
    prefix = cfg["table_prefix"]

    updates = []
    if req.user_email:
        updates.append(f"user_email = '{req.user_email.strip()}'")

    if req.user_pass:
        # Sử dụng MD5 fallback hoặc cập nhật pass
        # WordPress tự nâng cấp MD5 lên phpass khi user đăng nhập lần đầu
        import hashlib
        md5_pass = hashlib.md5(req.user_pass.encode("utf-8")).hexdigest()
        updates.append(f"user_pass = '{md5_pass}'")

    if updates:
        sql = f"UPDATE {prefix}users SET {', '.join(updates)} WHERE ID = {req.user_id};"
        await run_mysql_query(cfg, sql)

    if req.role:
        role = req.role.strip().lower()
        role_serialized = f'a:1:{{s:{len(role)}:"{role}";b:1;}}'
        sql_role = f"""
        UPDATE {prefix}usermeta SET meta_value = '{role_serialized}'
        WHERE user_id = {req.user_id} AND meta_key = '{prefix}capabilities';
        """
        await run_mysql_query(cfg, sql_role)

    return {"status": "ok", "message": f"Đã cập nhật thông tin User #{req.user_id} thành công!"}

# ==============================================================================
# Endpoints: Security, Lockdown, WP Defines & WPC Optimize
# ==============================================================================

@router.post("/{instance_id}/lockdown")
async def toggle_lockdown(instance_id: int, current_user: dict = Depends(get_current_user)):
    """Bật / Tắt chế độ WordPress Lockdown (chattr +i/-i và chmod khoá file core/config)"""
    inst = await _fetch_wp_instance_authorized(instance_id, current_user)
    db = await get_db_connection()

    is_currently_locked = inst["is_locked"]
    new_locked = 0 if is_currently_locked else 1
    action = "lock" if new_locked else "unlock"

    res = await run_gatekeeper("wp", action, inst["owner_username"], inst["domain_name"])
    if res.get("status") == "error":
        await db.close()
        raise HTTPException(status_code=400, detail=res.get("message", "Thay đổi trạng thái Lockdown thất bại"))

    await db.execute("UPDATE wp_instances SET is_locked = ? WHERE id = ?", (new_locked, instance_id))
    await db.commit()
    await db.close()

    return {
        "status": "ok",
        "is_locked": new_locked,
        "message": f"Đã {'khóa' if new_locked else 'mở khóa'} WordPress Lockdown thành công!"
    }

@router.post("/{instance_id}/fixperms")
async def fix_permissions(instance_id: int, current_user: dict = Depends(get_current_user)):
    """Khôi phục phân quyền chuẩn Linux (755 dir, 644 file, 600 wp-config.php)"""
    inst = await _fetch_wp_instance_authorized(instance_id, current_user)

    res = await run_gatekeeper("wp", "fixperms", inst["owner_username"], inst["domain_name"])
    if res.get("status") == "error":
        raise HTTPException(status_code=400, detail=res.get("message", "Phân quyền thất bại"))

    return {"status": "ok", "message": "Đã chuẩn hóa phân quyền toàn bộ thư mục và tệp tin website!"}

@router.post("/{instance_id}/defines/save")
async def save_wp_define(instance_id: int, req: WPDefineSaveRequest, current_user: dict = Depends(get_current_user)):
    """Cập nhật giá trị WP Define trong wp-config.php an toàn"""
    inst = await _fetch_wp_instance_authorized(instance_id, current_user)

    enable_str = "1" if req.enable else "0"
    res = await run_gatekeeper(
        "wp", "config_define", inst["owner_username"], inst["domain_name"],
        req.constant, req.value, enable_str
    )
    if res.get("status") == "error":
        raise HTTPException(status_code=400, detail=res.get("message", f"Cập nhật define {req.constant} thất bại"))

    return {"status": "ok", "message": f"Đã lưu cấu hình {req.constant} vào wp-config.php"}

@router.post("/{instance_id}/wpc-optimize/toggle")
async def toggle_wpc_optimize(instance_id: int, req: WPCOptimizeToggleRequest, current_user: dict = Depends(get_current_user)):
    """Bật / Tắt từng module trong bộ WPC Optimize MU-Plugins"""
    inst = await _fetch_wp_instance_authorized(instance_id, current_user)

    file_name = req.file_name.strip()
    if not file_name.startswith("wpc-optimize-") or not file_name.endswith(".php"):
        raise HTTPException(status_code=400, detail="Tên file MU-Plugin không hợp lệ")

    enable_str = "1" if req.enable else "0"
    res = await run_gatekeeper(
        "wp", "wpc_optimize", inst["owner_username"], inst["domain_name"],
        file_name, enable_str
    )
    if res.get("status") == "error":
        raise HTTPException(status_code=400, detail=res.get("message", "Cập nhật WPC Optimize thất bại"))

    return {"status": "ok", "file_name": file_name, "is_enabled": req.enable, "message": f"Đã {'bật' if req.enable else 'tắt'} module {file_name}!"}

@router.post("/{instance_id}/db-cleanup")
async def cleanup_database(instance_id: int, current_user: dict = Depends(get_current_user)):
    """Dọn dẹp rác cơ sở dữ liệu: Revisions, Auto-drafts, Spam comments, Transients và Tối ưu bảng"""
    inst = await _fetch_wp_instance_authorized(instance_id, current_user)

    res = await run_gatekeeper("wp", "db_cleanup", inst["owner_username"], inst["domain_name"])
    if res.get("status") == "error":
        raise HTTPException(status_code=400, detail=res.get("message", "Dọn dẹp database thất bại"))

    return {"status": "ok", "message": "Đã dọn dẹp revisions, spam, auto-drafts và tối ưu hóa database thành công!"}

# ==============================================================================
# Endpoints: Realtime PHP Error Logs
# ==============================================================================

@router.get("/{instance_id}/logs")
async def get_php_error_logs(
    instance_id: int,
    period: str = Query("all", pattern="^(15m|1h|24h|all)$"),
    level: str = Query("all", pattern="^(fatal|warning|notice|all)$"),
    current_user: dict = Depends(get_current_user)
):
    """Đọc và lọc log lỗi PHP / debug.log của website theo thời gian và mức độ lỗi"""
    inst = await _fetch_wp_instance_authorized(instance_id, current_user)

    docroot = inst["document_root"] or resolve_target_dir(inst["owner_username"], inst["domain_name"])
    log_candidates = [
        os.path.join(docroot, "wp-content", "debug.log"),
        f"/var/log/ols/{inst['domain_name']}.error.log",
        f"/home/{inst['owner_username']}/domains/{inst['domain_name']}/logs/error.log"
    ]

    log_file = None
    for lc in log_candidates:
        if os.path.isfile(lc):
            log_file = lc
            break

    if not log_file:
        return {
            "status": "ok",
            "logs": [],
            "message": "Không tìm thấy file debug.log. Hãy bật WP_DEBUG và WP_DEBUG_LOG trong tab WP Defines."
        }

    now = datetime.now()
    time_limit = None
    if period == "15m":
        time_limit = now - timedelta(minutes=15)
    elif period == "1h":
        time_limit = now - timedelta(hours=1)
    elif period == "24h":
        time_limit = now - timedelta(days=1)

    parsed_logs = []
    try:
        with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
            # Đọc 200 dòng cuối
            lines = f.readlines()[-300:]

        for line in lines:
            line_str = line.strip()
            if not line_str:
                continue

            # Parse log date: [18-Oct-2026 10:20:30 UTC] PHP Fatal error: ...
            log_time = None
            date_m = re.match(r"^\[([0-9a-zA-Z:\- ]+)\]", line_str)
            if date_m:
                try:
                    raw_dt = date_m.group(1).split(" ")[0] + " " + date_m.group(1).split(" ")[1]
                    log_time = datetime.strptime(raw_dt, "%d-%b-%Y %H:%M:%S")
                except Exception:
                    pass

            if time_limit and log_time and log_time < time_limit:
                continue

            # Phân loại level
            line_lower = line_str.lower()
            detected_level = "notice"
            if "fatal error" in line_lower or "parse error" in line_lower:
                detected_level = "fatal"
            elif "warning" in line_lower:
                detected_level = "warning"
            elif "notice" in line_lower or "deprecated" in line_lower:
                detected_level = "notice"

            if level != "all" and detected_level != level:
                continue

            parsed_logs.append({
                "time": date_m.group(1) if date_m else "",
                "level": detected_level,
                "message": line_str
            })

    except Exception:
        pass

    return {
        "status": "ok",
        "log_path": log_file,
        "count": len(parsed_logs),
        "logs": parsed_logs[-100:]  # Giới hạn 100 bản ghi mới nhất
    }

# ==============================================================================
# Endpoints: Admin Server-Wide Plugins & Themes Management
# ==============================================================================

@router.get("/admin/plugins")
async def get_all_server_plugins(current_user: dict = Depends(get_current_user)):
    """Dành riêng cho Admin: Gom nhóm tất cả Plugins trên toàn bộ server, xem site nào đang cài đặt"""
    if current_user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Chỉ có Quản trị viên mới được truy cập tính năng này")

    db = await get_db_connection()
    cursor = await db.execute("""
        SELECT w.*, d.domain_name, u.username as owner_username
        FROM wp_instances w
        JOIN domains d ON w.domain_id = d.id
        JOIN users u ON d.user_id = u.id
    """)
    sites = await cursor.fetchall()
    await db.close()

    plugins_map = {}
    for s in sites:
        docroot = s["document_root"] or resolve_target_dir(s["owner_username"], s["domain_name"])
        p_dir = os.path.join(docroot, "wp-content", "plugins")
        if not os.path.isdir(p_dir):
            continue

        try:
            for entry in os.scandir(p_dir):
                if entry.is_dir():
                    slug = entry.name
                    php_files = glob.glob(os.path.join(entry.path, "*.php"))
                    for pf in php_files:
                        try:
                            with open(pf, "r", encoding="utf-8", errors="ignore") as f:
                                h = f.read(4096)
                            if "Plugin Name:" in h:
                                nm = re.search(r"Plugin Name:\s*([^\r\n]+)", h)
                                vm = re.search(r"Version:\s*([^\r\n]+)", h)
                                p_name = nm.group(1).strip() if nm else slug
                                p_ver = vm.group(1).strip() if vm else "1.0"

                                if slug not in plugins_map:
                                    plugins_map[slug] = {
                                        "slug": slug,
                                        "name": p_name,
                                        "sites": []
                                    }
                                plugins_map[slug]["sites"].append({
                                    "instance_id": s["id"],
                                    "domain": s["domain_name"],
                                    "version": p_ver
                                })
                                break
                        except Exception:
                            pass
        except Exception:
            pass

    return {"status": "ok", "plugins": list(plugins_map.values())}

@router.post("/admin/plugins/bulk-update")
async def bulk_update_plugin(slug: str, current_user: dict = Depends(get_current_user)):
    """Cập nhật / Làm sạch hàng loạt Plugin trên toàn server qua Direct ZIP"""
    if current_user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Chỉ có Quản trị viên mới được cập nhật toàn server")

    db = await get_db_connection()
    cursor = await db.execute("""
        SELECT w.*, d.domain_name, u.username as owner_username
        FROM wp_instances w
        JOIN domains d ON w.domain_id = d.id
        JOIN users u ON d.user_id = u.id
    """)
    sites = await cursor.fetchall()
    await db.close()

    updated = 0
    zip_url = f"https://downloads.wordpress.org/plugin/{slug}.zip"

    for s in sites:
        docroot = s["document_root"] or resolve_target_dir(s["owner_username"], s["domain_name"])
        target_p = os.path.join(docroot, "wp-content", "plugins", slug)
        if os.path.isdir(target_p):
            await run_gatekeeper("wp", "download_zip", s["owner_username"], s["domain_name"], "plugin", slug, zip_url)
            updated += 1

    return {"status": "ok", "updated_count": updated, "message": f"Đã cập nhật làm sạch Plugin [{slug}] trên {updated} website."}
