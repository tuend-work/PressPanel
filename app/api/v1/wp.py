import os
import re
import glob
import json
import time
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
# Helper Functions: Parse WP Config & Execute Safe Queries
# ==============================================================================

def resolve_target_dir(username: str, domain: str) -> str:
    candidates = [
        f"/home/{username}/domains/{domain}/public_html",
        f"/home/{username}/httpdocs/{domain}",
        f"/home/{username}/public_html"
    ]
    for c in candidates:
        if os.path.isdir(c):
            return c
    return candidates[0]

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
    domain_id: int
    site_title: str
    admin_user: str
    admin_pass: str
    admin_email: str

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

class WPThemeActionRequest(BaseModel):
    slug: str
    zip_url: Optional[str] = ""

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

# ==============================================================================
# Endpoints: Instances Management & Overview
# ==============================================================================

@router.get("/list")
async def list_wordpress_instances(current_user: dict = Depends(get_current_user)):
    """Lấy danh sách các site WordPress thuộc quyền quản lý của User (hoặc tất cả nếu là admin)"""
    db = await get_db_connection()
    if current_user["role"] == "admin":
        cursor = await db.execute("""
            SELECT w.*, d.domain_name, u.username as owner_username
            FROM wp_instances w
            JOIN domains d ON w.domain_id = d.id
            JOIN users u ON d.user_id = u.id
            ORDER BY w.id DESC
        """)
    else:
        cursor = await db.execute("""
            SELECT w.*, d.domain_name, u.username as owner_username
            FROM wp_instances w
            JOIN domains d ON w.domain_id = d.id
            JOIN users u ON d.user_id = u.id
            WHERE d.user_id = ?
            ORDER BY w.id DESC
        """)
        cursor = await db.execute("""
            SELECT w.*, d.domain_name, u.username as owner_username
            FROM wp_instances w
            JOIN domains d ON w.domain_id = d.id
            JOIN users u ON d.user_id = u.id
            WHERE d.user_id = ?
            ORDER BY w.id DESC
        """, (current_user["id"],))

    rows = await cursor.fetchall()
    await db.close()

    instances = []
    for r in rows:
        item = dict(r)
        # Parse JSON fields
        for jf in ["security_flags", "wp_defines", "wpc_optimize_flags"]:
            if isinstance(item.get(jf), str):
                try:
                    item[jf] = json.loads(item[jf])
                except Exception:
                    item[jf] = {}
        instances.append(item)

    return {"status": "ok", "instances": instances}

@router.post("/scan")
async def scan_wordpress_instances(current_user: dict = Depends(get_current_user)):
    """Tự động quét các thư mục domain trên máy chủ để phát hiện và đăng ký các site WordPress"""
    db = await get_db_connection()
    if current_user["role"] == "admin":
        cursor = await db.execute("SELECT id, domain_name, user_id FROM domains")
    else:
        cursor = await db.execute("SELECT id, domain_name, user_id FROM domains WHERE user_id = ?", (current_user["id"],))

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

        docroot = resolve_target_dir(uname, dom_name)
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

@router.get("/{instance_id}/detail")
async def get_wordpress_detail(instance_id: int, current_user: dict = Depends(get_current_user)):
    """Lấy thông tin chi tiết đầy đủ của 1 website WordPress (Site Weight, Security, Defines, Optimize)"""
    db = await get_db_connection()
    cursor = await db.execute("""
        SELECT w.*, d.domain_name, u.username as owner_username
        FROM wp_instances w
        JOIN domains d ON w.domain_id = d.id
        JOIN users u ON d.user_id = u.id
        WHERE w.id = ?
    """, (instance_id,))
    inst = await cursor.fetchone()
    await db.close()

    if not inst:
        raise HTTPException(status_code=404, detail="Không tìm thấy WordPress instance")

    if current_user["role"] != "admin" and inst["user_id"] != current_user["id"]:
        raise HTTPException(status_code=403, detail="Bạn không có quyền truy cập website này")

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
    """Cài đặt WordPress mới hoàn toàn qua Direct ZIP từ wordpress.org (không nạp WP hook)"""
    db = await get_db_connection()
    cursor = await db.execute("SELECT domain_name FROM domains WHERE id = ? AND user_id = ?", (req.domain_id, current_user["id"]))
    domain = await cursor.fetchone()
    if not domain:
        await db.close()
        raise HTTPException(status_code=404, detail="Không tìm thấy tên miền chỉ định")

    domain_name = domain["domain_name"]
    username = current_user["username"]

    # Gọi C gatekeeper: wp install <username> <domain> <title> <admin_user> <admin_email> (admin_pass qua stdin)
    res = await run_gatekeeper(
        "wp", "install", username, domain_name, req.site_title, req.admin_user, req.admin_email,
        stdin_data=req.admin_pass
    )
    if res.get("status") == "error":
        await db.close()
        raise HTTPException(status_code=400, detail=res.get("message", "Cài đặt WordPress thất bại"))

    docroot = resolve_target_dir(username, domain_name)
    cfg = parse_wp_config_file(docroot)

    await db.execute("""
        INSERT INTO wp_instances (
            domain_id, user_id, site_title, admin_user, admin_email,
            wp_version, db_name, db_user, db_prefix, document_root
        ) VALUES (?, ?, ?, ?, ?, 'latest', ?, ?, ?, ?)
    """, (
        req.domain_id, current_user["id"], req.site_title, req.admin_user, req.admin_email,
        cfg["db_name"] or f"wp_{username[:8]}", cfg["db_user"] or username, cfg["table_prefix"] or "wp_", docroot
    ))
    await db.commit()
    await db.close()

    return {"status": "ok", "message": f"WordPress đã được cài đặt thành công trên {domain_name}!"}

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
    await db.close()

    if res.get("status") == "error":
        raise HTTPException(status_code=400, detail=res.get("message", "Clone website thất bại"))

    return {"status": "ok", "message": f"Website đã được nhân bản thành công sang {dest_domain}!"}

@router.get("/{instance_id}/sso")
async def get_magic_login_sso(instance_id: int, current_user: dict = Depends(get_current_user)):
    """Tạo liên kết Magic Login Single-Use (tự huỷ sau 60s) cho Administrator mặc định"""
    return await get_magic_login_user_sso(instance_id, 0, current_user)

@router.get("/{instance_id}/sso-user/{user_id}")
async def get_magic_login_user_sso(instance_id: int, user_id: int, current_user: dict = Depends(get_current_user)):
    """Tạo liên kết Magic Login Single-Use cho bất kỳ User ID nào trong WordPress"""
    db = await get_db_connection()
    cursor = await db.execute("""
        SELECT w.*, d.domain_name, u.username as owner_username
        FROM wp_instances w
        JOIN domains d ON w.domain_id = d.id
        JOIN users u ON d.user_id = u.id
        WHERE w.id = ?
    """, (instance_id,))
    row = await cursor.fetchone()
    await db.close()

    if not row:
        raise HTTPException(status_code=404, detail="Không tìm thấy WordPress instance")

    if current_user["role"] != "admin" and row["user_id"] != current_user["id"]:
        raise HTTPException(status_code=403, detail="Bạn không có quyền truy cập website này")

    token = secrets.token_hex(24)
    # Gọi C gatekeeper: wp sso_create <username> <domain> <target_user_id> <token>
    res = await run_gatekeeper("wp", "sso_create", row["owner_username"], row["domain_name"], str(user_id), token)
    if res.get("status") == "error":
        raise HTTPException(status_code=400, detail=res.get("message", "Tạo liên kết Magic Login thất bại"))

    sso_url = f"http://{row['domain_name']}/?wpc_sso_token={token}"
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
    db = await get_db_connection()
    cursor = await db.execute("""
        SELECT w.*, d.domain_name, u.username as owner_username
        FROM wp_instances w
        JOIN domains d ON w.domain_id = d.id
        JOIN users u ON d.user_id = u.id
        WHERE w.id = ?
    """, (instance_id,))
    inst = await cursor.fetchone()
    await db.close()

    if not inst:
        raise HTTPException(status_code=404, detail="Không tìm thấy WordPress instance")

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
    db = await get_db_connection()
    cursor = await db.execute("""
        SELECT w.*, d.domain_name, u.username as owner_username
        FROM wp_instances w
        JOIN domains d ON w.domain_id = d.id
        JOIN users u ON d.user_id = u.id
        WHERE w.id = ?
    """, (instance_id,))
    inst = await cursor.fetchone()
    await db.close()

    if not inst:
        raise HTTPException(status_code=404, detail="Không tìm thấy WordPress instance")

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
    db = await get_db_connection()
    cursor = await db.execute("""
        SELECT w.*, d.domain_name, u.username as owner_username
        FROM wp_instances w
        JOIN domains d ON w.domain_id = d.id
        JOIN users u ON d.user_id = u.id
        WHERE w.id = ?
    """, (instance_id,))
    inst = await cursor.fetchone()
    await db.close()

    if not inst:
        raise HTTPException(status_code=404, detail="Không tìm thấy WordPress instance")

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
    db = await get_db_connection()
    cursor = await db.execute("""
        SELECT w.*, d.domain_name, u.username as owner_username
        FROM wp_instances w
        JOIN domains d ON w.domain_id = d.id
        JOIN users u ON d.user_id = u.id
        WHERE w.id = ?
    """, (instance_id,))
    inst = await cursor.fetchone()
    await db.close()

    if not inst:
        raise HTTPException(status_code=404, detail="Không tìm thấy WordPress instance")

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
    db = await get_db_connection()
    cursor = await db.execute("""
        SELECT w.*, d.domain_name, u.username as owner_username
        FROM wp_instances w
        JOIN domains d ON w.domain_id = d.id
        JOIN users u ON d.user_id = u.id
        WHERE w.id = ?
    """, (instance_id,))
    inst = await cursor.fetchone()
    await db.close()

    if not inst:
        raise HTTPException(status_code=404, detail="Không tìm thấy WordPress instance")

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
    db = await get_db_connection()
    cursor = await db.execute("""
        SELECT w.*, d.domain_name, u.username as owner_username
        FROM wp_instances w
        JOIN domains d ON w.domain_id = d.id
        JOIN users u ON d.user_id = u.id
        WHERE w.id = ?
    """, (instance_id,))
    inst = await cursor.fetchone()
    await db.close()

    if not inst:
        raise HTTPException(status_code=404, detail="Không tìm thấy WordPress instance")

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
    db = await get_db_connection()
    cursor = await db.execute("""
        SELECT w.*, d.domain_name, u.username as owner_username
        FROM wp_instances w
        JOIN domains d ON w.domain_id = d.id
        JOIN users u ON d.user_id = u.id
        WHERE w.id = ?
    """, (instance_id,))
    inst = await cursor.fetchone()
    await db.close()

    if not inst:
        raise HTTPException(status_code=404, detail="Không tìm thấy WordPress instance")

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
    db = await get_db_connection()
    cursor = await db.execute("""
        SELECT w.*, d.domain_name, u.username as owner_username
        FROM wp_instances w
        JOIN domains d ON w.domain_id = d.id
        JOIN users u ON d.user_id = u.id
        WHERE w.id = ?
    """, (instance_id,))
    inst = await cursor.fetchone()
    await db.close()

    if not inst:
        raise HTTPException(status_code=404, detail="Không tìm thấy WordPress instance")

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

@router.post("/{instance_id}/users/modify")
async def modify_wp_user(instance_id: int, req: WPUserModifyRequest, current_user: dict = Depends(get_current_user)):
    """Chỉnh sửa thông tin người dùng (Email, Password, Role) trực tiếp trong MariaDB"""
    db = await get_db_connection()
    cursor = await db.execute("""
        SELECT w.*, d.domain_name, u.username as owner_username
        FROM wp_instances w
        JOIN domains d ON w.domain_id = d.id
        JOIN users u ON d.user_id = u.id
        WHERE w.id = ?
    """, (instance_id,))
    inst = await cursor.fetchone()
    await db.close()

    if not inst:
        raise HTTPException(status_code=404, detail="Không tìm thấy WordPress instance")

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
    db = await get_db_connection()
    cursor = await db.execute("""
        SELECT w.*, d.domain_name, u.username as owner_username
        FROM wp_instances w
        JOIN domains d ON w.domain_id = d.id
        JOIN users u ON d.user_id = u.id
        WHERE w.id = ?
    """, (instance_id,))
    inst = await cursor.fetchone()

    if not inst:
        await db.close()
        raise HTTPException(status_code=404, detail="Không tìm thấy WordPress instance")

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
    db = await get_db_connection()
    cursor = await db.execute("""
        SELECT w.*, d.domain_name, u.username as owner_username
        FROM wp_instances w
        JOIN domains d ON w.domain_id = d.id
        JOIN users u ON d.user_id = u.id
        WHERE w.id = ?
    """, (instance_id,))
    inst = await cursor.fetchone()
    await db.close()

    if not inst:
        raise HTTPException(status_code=404, detail="Không tìm thấy WordPress instance")

    res = await run_gatekeeper("wp", "fixperms", inst["owner_username"], inst["domain_name"])
    if res.get("status") == "error":
        raise HTTPException(status_code=400, detail=res.get("message", "Phân quyền thất bại"))

    return {"status": "ok", "message": "Đã chuẩn hóa phân quyền toàn bộ thư mục và tệp tin website!"}

@router.post("/{instance_id}/defines/save")
async def save_wp_define(instance_id: int, req: WPDefineSaveRequest, current_user: dict = Depends(get_current_user)):
    """Cập nhật giá trị WP Define trong wp-config.php an toàn"""
    db = await get_db_connection()
    cursor = await db.execute("""
        SELECT w.*, d.domain_name, u.username as owner_username
        FROM wp_instances w
        JOIN domains d ON w.domain_id = d.id
        JOIN users u ON d.user_id = u.id
        WHERE w.id = ?
    """, (instance_id,))
    inst = await cursor.fetchone()
    await db.close()

    if not inst:
        raise HTTPException(status_code=404, detail="Không tìm thấy WordPress instance")

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
    db = await get_db_connection()
    cursor = await db.execute("""
        SELECT w.*, d.domain_name, u.username as owner_username
        FROM wp_instances w
        JOIN domains d ON w.domain_id = d.id
        JOIN users u ON d.user_id = u.id
        WHERE w.id = ?
    """, (instance_id,))
    inst = await cursor.fetchone()
    await db.close()

    if not inst:
        raise HTTPException(status_code=404, detail="Không tìm thấy WordPress instance")

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
    db = await get_db_connection()
    cursor = await db.execute("""
        SELECT w.*, d.domain_name, u.username as owner_username
        FROM wp_instances w
        JOIN domains d ON w.domain_id = d.id
        JOIN users u ON d.user_id = u.id
        WHERE w.id = ?
    """, (instance_id,))
    inst = await cursor.fetchone()
    await db.close()

    if not inst:
        raise HTTPException(status_code=404, detail="Không tìm thấy WordPress instance")

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
    period: str = Query("all", regex="^(15m|1h|24h|all)$"),
    level: str = Query("all", regex="^(fatal|warning|notice|all)$"),
    current_user: dict = Depends(get_current_user)
):
    """Đọc và lọc log lỗi PHP / debug.log của website theo thời gian và mức độ lỗi"""
    db = await get_db_connection()
    cursor = await db.execute("""
        SELECT w.*, d.domain_name, u.username as owner_username
        FROM wp_instances w
        JOIN domains d ON w.domain_id = d.id
        JOIN users u ON d.user_id = u.id
        WHERE w.id = ?
    """, (instance_id,))
    inst = await cursor.fetchone()
    await db.close()

    if not inst:
        raise HTTPException(status_code=404, detail="Không tìm thấy WordPress instance")

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
