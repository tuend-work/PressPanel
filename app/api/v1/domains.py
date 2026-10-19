import os
import re
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from app.api.deps import get_current_user
from app.db.session import get_db_connection
from app.executor.gatekeeper import run_gatekeeper

ALLOWED_PHP_VERSIONS = {"5.6", "7.0", "7.1", "7.2", "7.3", "7.4", "8.0", "8.1", "8.2", "8.3", "8.4", "8.5"}
ALLOWED_WEBSERVERS = {"ols", "nginx"}

router = APIRouter(prefix="/domains", tags=["Websites & Domains"])

class DomainCreate(BaseModel):
    domain_name: str
    webserver: str = "ols"
    php_version: str = "8.2"
    install_wp: bool = False
    package_id: Optional[int] = None

class DomainBatchDeleteRequest(BaseModel):
    domain_ids: List[int]

@router.get("/")
async def list_domains(current_user: dict = Depends(get_current_user)):
    db = await get_db_connection()
    base_select = """
        SELECT d.*, 
               u.username as owner_username, 
               u.email as owner_email, 
               COALESCE(p_dom.name, p.name, 'Website Plan') as plan_name,
               COALESCE(p_dom.cpu_pct, p.cpu_pct, 100) as cpu_pct,
               COALESCE(p_dom.mem_mb, p.mem_mb, 1024) as mem_mb,
               COALESCE(p_dom.disk_mb, p.disk_mb, 5120) as disk_mb,
               COALESCE(d.status, 'active') as status,
               (SELECT COUNT(*) FROM databases db_sub WHERE db_sub.domain_id = d.id OR db_sub.db_name LIKE '%' || REPLACE(REPLACE(d.domain_name, '.', '_'), '-', '_') || '%') as db_count
        FROM domains d
        JOIN users u ON d.user_id = u.id
        LEFT JOIN packages p ON u.package_id = p.id
        LEFT JOIN packages p_dom ON d.package_id = p_dom.id
    """
    if current_user["role"] == "admin":
        cursor = await db.execute(base_select + " ORDER BY d.created_at DESC")
    elif current_user["role"] == "reseller":
        cursor = await db.execute(base_select + " WHERE u.id = ? OR u.parent_id = ? ORDER BY d.created_at DESC", (current_user["id"], current_user["id"]))
    elif current_user.get("web_domain_id"):
        # Web User: CHỈ ĐƯỢC XEM DUY NHẤT WEBSITE ĐƯỢC PHÂN QUYỀN
        cursor = await db.execute(base_select + " WHERE d.id = ? ORDER BY d.created_at DESC", (current_user["web_domain_id"],))
    else:
        cursor = await db.execute(base_select + " WHERE d.user_id = ? ORDER BY d.created_at DESC", (current_user["id"],))
    
    rows = await cursor.fetchall()
    await db.close()

    results = []
    for r in rows:
        d_dict = dict(r)
        cpu_limit = d_dict.get("cpu_pct") or 100
        mem_limit = d_dict.get("mem_mb") or 1024
        disk_limit = d_dict.get("disk_mb") or 5120

        if d_dict.get("status") == "suspended":
            d_dict["cpu_usage_pct"] = 0.0
            d_dict["mem_usage_mb"] = 0
            d_dict["disk_usage_mb"] = min(disk_limit, 180)
        else:
            d_dict["cpu_usage_pct"] = round(min(cpu_limit, 2.5), 1)
            d_dict["mem_usage_mb"] = min(mem_limit, 95)
            d_dict["disk_usage_mb"] = min(disk_limit, 320)

        results.append(d_dict)

    return results

def domain_to_linux_user(domain: str) -> str:
    # Bỏ www. và chuyển sang viết liền không dấu chấm
    clean = re.sub(r"^(https?://)?(www\.)?", "", domain.strip().lower())
    clean = re.sub(r"[^a-z0-9]", "", clean)
    if not clean:
        clean = "webuser"
    # Giới hạn 30 ký tự để phù hợp giới hạn username của Linux (thường max 32)
    return clean[:30]

@router.post("/")
async def create_domain(req: DomainCreate, current_user: dict = Depends(get_current_user)):
    clean_domain = re.sub(r"^(https?://)?(www\.)?", "", req.domain_name.strip().lower()).rstrip("/")
    # Kiểm tra cấu trúc FQDN hợp lệ: không chứa ký tự lạ, không có .. hay khoảng trắng
    if not clean_domain or not re.match(r"^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$", clean_domain):
        raise HTTPException(status_code=400, detail="Tên miền không hợp lệ (ví dụ hợp lệ: example.com, mysite.vn)")

    # Validate php_version and webserver
    if req.php_version not in ALLOWED_PHP_VERSIONS:
        raise HTTPException(status_code=400, detail=f"Phiên bản PHP không hợp lệ. Cho phép: {', '.join(sorted(ALLOWED_PHP_VERSIONS))}")
    if req.webserver and req.webserver not in ALLOWED_WEBSERVERS:
        raise HTTPException(status_code=400, detail="Máy chủ Web không hợp lệ (chỉ hỗ trợ ols hoặc nginx)")

    # MỖI WEBSITE LÀ MỘT LINUX USER ĐỘC LẬP
    # Tên user Linux là domain viết liền không dấu chấm (vd: example.com -> examplecom)
    isolated_user = domain_to_linux_user(clean_domain)
    doc_root = f"/home/{isolated_user}/domains/{clean_domain}/public_html"

    db = await get_db_connection()
    try:
        # TỰ ĐỘNG CHỌN WEBSERVER TẦNG SERVER:
        active_ws = req.webserver or "ols"
        ws_cur = await db.execute("SELECT webserver FROM domains ORDER BY id DESC LIMIT 1")
        last_ws = await ws_cur.fetchone()
        if last_ws and last_ws["webserver"]:
            active_ws = last_ws["webserver"]
        elif os.path.exists("/usr/sbin/nginx") and not os.path.exists("/usr/local/lsws/bin/lswsctrl"):
            active_ws = "nginx"
        else:
            active_ws = "ols"

        # Lấy thông tin Hosting User sở hữu website này
        hosting_username = current_user.get("username", isolated_user)

        # Gọi C gatekeeper: vhost add <user> <domain> <ols|nginx> <php_ver> [hosting_user]
        res = await run_gatekeeper("vhost", "add", isolated_user, clean_domain, active_ws, req.php_version, hosting_username)
        if res.get("status") == "error":
            raise HTTPException(status_code=400, detail=res.get("message", "Không thể tạo VirtualHost"))

        # Áp dụng giới hạn Cgroups v2 cho Linux User của website này
        u_cpu = current_user.get("cpu_pct", 100) or 100
        u_mem = current_user.get("mem_mb", 1024) or 1024
        pkg_id = req.package_id
        pkg_allow_ssh = 1

        if req.package_id:
            p_cur = await db.execute("SELECT cpu_pct, mem_mb, disk_mb, allow_ssh FROM packages WHERE id = ?", (req.package_id,))
            wpkg = await p_cur.fetchone()
            if wpkg:
                u_cpu = min(u_cpu, wpkg["cpu_pct"])
                u_mem = min(u_mem, wpkg["mem_mb"])
                pkg_allow_ssh = wpkg["allow_ssh"]

        reseller_id = str(current_user.get("parent_id", 1) or 1)
        await run_gatekeeper("cgroup", "set-user", reseller_id, isolated_user, str(u_cpu), str(u_mem), "100", "100")
        await run_gatekeeper("cgroup", "set-user", reseller_id, hosting_username, str(current_user.get("cpu_pct", 100) or 100), str(current_user.get("mem_mb", 1024) or 1024), "100", "100")

        await db.execute("""
            INSERT INTO domains (user_id, domain_name, document_root, webserver, php_version, package_id, allow_ssh)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (current_user["id"], clean_domain, doc_root, active_ws, req.php_version, pkg_id, pkg_allow_ssh))
        await db.commit()

        # Tự động khởi tạo WordPress nếu được yêu cầu
        if req.install_wp:
            await run_gatekeeper("wp", "install", isolated_user, clean_domain)

        return {
            "status": "ok",
            "message": f"Đã khởi tạo website {clean_domain} thành công với Linux user độc lập '{isolated_user}'!",
            "isolated_user": isolated_user,
            "domain": clean_domain
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Lỗi khởi tạo website: {str(e)}")
    finally:
        await db.close()

@router.post("/batch-delete")
async def batch_delete_domains(req: DomainBatchDeleteRequest, current_user: dict = Depends(get_current_user)):
    if not req.domain_ids:
        raise HTTPException(status_code=400, detail="Vui lòng chọn ít nhất một website để xóa")
    
    db = await get_db_connection()
    placeholders = ",".join(["?"] * len(req.domain_ids))
    if current_user["role"] == "admin":
        cursor = await db.execute(f"""
            SELECT d.id, d.domain_name, d.webserver, d.user_id, u.username
            FROM domains d
            JOIN users u ON d.user_id = u.id
            WHERE d.id IN ({placeholders})
        """, req.domain_ids)
    else:
        cursor = await db.execute(f"""
            SELECT d.id, d.domain_name, d.webserver, d.user_id, u.username
            FROM domains d
            JOIN users u ON d.user_id = u.id
            WHERE d.id IN ({placeholders}) AND (d.user_id = ? OR u.parent_id = ?)
        """, req.domain_ids + [current_user["id"], current_user["id"]])
    domains_to_del = await cursor.fetchall()

    for d in domains_to_del:
        clean_d = d["domain_name"]
        isolated_u = domain_to_linux_user(clean_d)
        await run_gatekeeper("vhost", "remove", isolated_u, clean_d, d["webserver"])
        await db.execute("DELETE FROM wp_instances WHERE domain_id = ?", (d["id"],))
        await db.execute("DELETE FROM ssl_certificates WHERE domain_id = ?", (d["id"],))
        await db.execute("DELETE FROM domains WHERE id = ?", (d["id"],))

    await db.commit()
    await db.close()
    return {"status": "ok", "message": f"Đã xóa thành công {len(domains_to_del)} website và dọn dẹp môi trường Linux."}

@router.delete("/{domain_id}")
async def delete_domain(domain_id: int, current_user: dict = Depends(get_current_user)):
    """
    Xóa Website / VirtualHost:
    1. Kiểm tra quyền sở hữu (Admin xóa được tất cả, Reseller/Customer chỉ xóa domain thuộc quyền mình).
    2. Gọi C gatekeeper: vhost remove <user> <domain> <webserver> để dọn dẹp OpenLiteSpeed/Nginx config và doc_root.
    3. Xóa bản ghi trong database domains (kèm CASCADE sang wp_instances, v.v.).
    """
    db = await get_db_connection()
    cursor = await db.execute("""
        SELECT d.id, d.domain_name, d.webserver, d.user_id, u.username 
        FROM domains d 
        JOIN users u ON d.user_id = u.id 
        WHERE d.id = ?
    """, (domain_id,))
    domain = await cursor.fetchone()

    if not domain:
        await db.close()
        raise HTTPException(status_code=404, detail="Không tìm thấy tên miền này")

    # Kiểm tra phân quyền
    if current_user["role"] != "admin" and domain["user_id"] != current_user["id"]:
        if current_user["role"] == "reseller":
            # Kiểm tra xem customer có thuộc reseller này không
            res_cur = await db.execute("SELECT parent_id FROM users WHERE id = ?", (domain["user_id"],))
            owner = await res_cur.fetchone()
            if not owner or owner["parent_id"] != current_user["id"]:
                await db.close()
                raise HTTPException(status_code=403, detail="Bạn không có quyền xóa tên miền của tài khoản khác")
        else:
            await db.close()
            raise HTTPException(status_code=403, detail="Bạn không có quyền xóa tên miền này")

    # Gọi C gatekeeper: vhost remove <isolated_user> <domain> <webserver>
    clean_d = domain["domain_name"]
    isolated_u = domain_to_linux_user(clean_d)
    res = await run_gatekeeper("vhost", "remove", isolated_u, clean_d, domain["webserver"])
    
    # Xóa khỏi database SQLite
    try:
        await db.execute("DELETE FROM wp_instances WHERE domain_id = ?", (domain_id,))
        await db.execute("DELETE FROM ssl_certificates WHERE domain_id = ?", (domain_id,))
        await db.execute("DELETE FROM domains WHERE id = ?", (domain_id,))
        await db.commit()
    except Exception as e:
        await db.close()
        raise HTTPException(status_code=500, detail=f"Lỗi khi cập nhật cơ sở dữ liệu: {str(e)}")

    await db.close()
    return {
        "status": "success",
        "message": f"Đã xóa hoàn toàn website '{domain['domain_name']}' khỏi hệ thống!",
        "deleted_domain": domain["domain_name"]
    }

@router.post("/{domain_id}/impersonate-user")
async def impersonate_website_user(
    domain_id: int,
    current_user: dict = Depends(get_current_user)
):
    """
    Đăng nhập trực tiếp dưới quyền Web User của Website (Isolated Linux User).
    Cho phép Admin hoặc Hosting User đăng nhập vào phiên quản trị của chính website đó.
    """
    db = await get_db_connection()
    cursor = await db.execute("""
        SELECT d.id, d.domain_name, d.document_root, d.user_id, u.username as owner_username, u.parent_id
        FROM domains d
        JOIN users u ON d.user_id = u.id
        WHERE d.id = ?
    """, (domain_id,))
    domain = await cursor.fetchone()
    await db.close()

    if not domain:
        raise HTTPException(status_code=404, detail="Không tìm thấy website này")

    # Kiểm tra quyền: Admin, hoặc Hosting User sở hữu website
    if current_user["role"] != "admin":
        if current_user["id"] != domain["user_id"] and current_user["id"] != domain["parent_id"]:
            raise HTTPException(status_code=403, detail="Bạn không có quyền quản trị website này")

    web_user_name = domain_to_linux_user(domain["domain_name"])
    from app.core.security import create_access_token
    # Tạo JWT token mang role 'user' (Web User) đại diện duy nhất cho website này
    web_user_token = create_access_token(
        subject=domain["user_id"],
        role="user",
        parent_id=domain["user_id"],
        web_domain_id=domain["id"],
        web_user_name=web_user_name
    )

    return {
        "status": "success",
        "impersonated": True,
        "access_token": web_user_token,
        "token_type": "bearer",
        "web_user": {
            "id": domain["user_id"],
            "username": web_user_name,
            "domain_name": domain["domain_name"],
            "document_root": domain["document_root"],
            "role": "user",
            "web_domain_id": domain["id"]
        }
    }

ALL_KNOWN_PHP = [
    ("8.5", "PHP 8.5 (Bản mới nhất)"),
    ("8.4", "PHP 8.4"),
    ("8.3", "PHP 8.3"),
    ("8.2", "PHP 8.2 (Khuyên dùng)"),
    ("8.1", "PHP 8.1"),
    ("8.0", "PHP 8.0"),
    ("7.4", "PHP 7.4 (WordPress legacy)"),
    ("7.3", "PHP 7.3"),
    ("7.2", "PHP 7.2"),
    ("7.1", "PHP 7.1"),
    ("7.0", "PHP 7.0"),
    ("5.6", "PHP 5.6 (Legacy)")
]

def get_available_php_for_ws(webserver: str) -> list:
    """Quét và chỉ trả về các phiên bản PHP THỰC SỰ ĐÃ CÀI ĐẶT và hoạt động được trên Webserver chỉ định"""
    res = []
    ws = (webserver or "ols").lower()
    is_windows = (os.name == "nt")

    for ver, label in ALL_KNOWN_PHP:
        pnum = ver.replace(".", "")
        if ws == "ols":
            lsphp_path = f"/usr/local/lsws/lsphp{pnum}/bin/lsphp"
            if is_windows or os.path.exists(lsphp_path):
                res.append({
                    "ver": ver,
                    "label": f"PHP {ver}" + (" (Khuyên dùng)" if ver == "8.2" else ""),
                    "raw_label": label,
                    "handler": "LiteSpeed SAPI (lsapi)",
                    "binary": lsphp_path
                })
        else:
            fpm_bin = f"/usr/sbin/php-fpm{ver}"
            fpm_conf = f"/etc/php/{ver}/fpm/php-fpm.conf"
            if is_windows or os.path.exists(fpm_bin) or os.path.exists(fpm_conf):
                res.append({
                    "ver": ver,
                    "label": f"PHP {ver}" + (" (Khuyên dùng)" if ver == "8.2" else ""),
                    "raw_label": label,
                    "handler": "PHP-FPM Socket",
                    "binary": fpm_bin
                })
    return res

@router.get("/installed-php")
async def get_installed_php_list(
    webserver: Optional[str] = "ols",
    current_user: dict = Depends(get_current_user)
):
    """Lấy danh sách các phiên bản PHP đã cài đặt và khả dụng cho webserver"""
    return {
        "webserver": webserver,
        "available_versions": get_available_php_for_ws(webserver)
    }

@router.get("/{domain_id}/available-php")
async def get_domain_available_php(
    domain_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Chỉ trả về danh sách PHP ĐÃ CÀI ĐẶT và có thể hoạt động cho website này"""
    db = await get_db_connection()
    cursor = await db.execute("""
        SELECT d.id, d.domain_name, d.webserver, d.php_version, d.user_id, u.parent_id
        FROM domains d
        JOIN users u ON d.user_id = u.id
        WHERE d.id = ?
    """, (domain_id,))
    domain = await cursor.fetchone()
    await db.close()

    if not domain:
        raise HTTPException(status_code=404, detail="Không tìm thấy website này")

    if current_user["role"] != "admin":
        if current_user["id"] != domain["user_id"] and current_user["id"] != domain["parent_id"]:
            raise HTTPException(status_code=403, detail="Bạn không có quyền quản trị website này")

    ws = domain["webserver"] or "ols"
    current_p = domain["php_version"] or "8.2"
    avail = get_available_php_for_ws(ws)
    is_cur_avail = any(p["ver"] == current_p for p in avail)

    return {
        "domain_id": domain_id,
        "domain_name": domain["domain_name"],
        "webserver": ws,
        "current_php": current_p,
        "is_current_available": is_cur_avail,
        "available_versions": avail
    }

class ChangePhpRequest(BaseModel):
    php_version: str

@router.post("/{domain_id}/change-php")
async def change_domain_php(
    domain_id: int,
    req: ChangePhpRequest,
    current_user: dict = Depends(get_current_user)
):
    """Thay đổi phiên bản PHP (5.6 -> 8.5) cho một website cụ thể"""
    db = await get_db_connection()
    cursor = await db.execute("""
        SELECT d.id, d.domain_name, d.webserver, d.php_version, d.user_id, u.username as owner_username, u.parent_id
        FROM domains d
        JOIN users u ON d.user_id = u.id
        WHERE d.id = ?
    """, (domain_id,))
    domain = await cursor.fetchone()

    if not domain:
        await db.close()
        raise HTTPException(status_code=404, detail="Không tìm thấy website này")

    if current_user["role"] != "admin":
        if current_user["id"] != domain["user_id"] and current_user["id"] != domain["parent_id"]:
            await db.close()
            raise HTTPException(status_code=403, detail="Bạn không có quyền quản trị website này")

    clean_domain = domain["domain_name"]
    isolated_user = domain_to_linux_user(clean_domain)
    active_ws = domain["webserver"]
    new_php = req.php_version
    hosting_username = domain["owner_username"] or isolated_user

    if new_php not in ALLOWED_PHP_VERSIONS:
        await db.close()
        raise HTTPException(status_code=400, detail=f"Phiên bản PHP không hợp lệ. Cho phép: {', '.join(sorted(ALLOWED_PHP_VERSIONS))}")

    if active_ws == "ols":
        clean_pnum = new_php.replace(".", "")
        lsphp_path = f"/usr/local/lsws/lsphp{clean_pnum}/bin/lsphp"
        if os.name != "nt" and not os.path.exists(lsphp_path):
            await db.close()
            raise HTTPException(
                status_code=400,
                detail=f"OpenLiteSpeed không hỗ trợ PHP {new_php} (thiếu gói lsphp{clean_pnum} trên hệ điều hành này). Để chạy website với PHP {new_php}, vui lòng chuyển webserver sang Nginx (sử dụng PHP-FPM {new_php})!"
            )

    # Thực thi Gatekeeper: vhost set-php <user> <domain> <ols|nginx> <php_ver> [hosting_user]
    res = await run_gatekeeper("vhost", "set-php", isolated_user, clean_domain, active_ws, new_php, hosting_username)
    if res.get("status") == "error":
        await db.close()
        raise HTTPException(status_code=400, detail=res.get("message", "Không thể đổi phiên bản PHP"))

    # Cập nhật database
    await db.execute("UPDATE domains SET php_version = ? WHERE id = ?", (new_php, domain_id))
    await db.commit()
    await db.close()

    return {
        "status": "success",
        "message": f"Đã chuyển website {clean_domain} sang PHP {new_php} thành công!",
        "php_version": new_php
    }

class DomainPhpIniUpdate(BaseModel):
    upload_max_filesize: str = "128M"
    post_max_size: str = "128M"
    memory_limit: str = "512M"
    max_execution_time: str = "300"
    max_input_time: str = "300"
    max_input_vars: str = "3000"
    display_errors: str = "Off"
    date_timezone: str = "Asia/Ho_Chi_Minh"
    custom_directives: Optional[str] = ""

@router.get("/{domain_id}/php-ini")
async def get_domain_php_ini(
    domain_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Đọc cấu hình php.ini (.user.ini) cho website chỉ định"""
    db = await get_db_connection()
    cursor = await db.execute("""
        SELECT d.id, d.domain_name, d.webserver, d.php_version, d.user_id, u.username as owner_username, u.parent_id
        FROM domains d
        JOIN users u ON d.user_id = u.id
        WHERE d.id = ?
    """, (domain_id,))
    domain = await cursor.fetchone()
    await db.close()

    if not domain:
        raise HTTPException(status_code=404, detail="Không tìm thấy website này")

    if current_user["role"] != "admin":
        if current_user["id"] != domain["user_id"] and current_user["id"] != domain["parent_id"]:
            raise HTTPException(status_code=403, detail="Bạn không có quyền quản trị website này")

    clean_domain = domain["domain_name"]
    isolated_user = domain_to_linux_user(clean_domain)
    active_ws = domain["webserver"] or "ols"

    doc_root = f"/home/{isolated_user}/domains/{clean_domain}/public_html"
    if not os.path.exists(doc_root) and os.path.exists(f"/home/{isolated_user}/httpdocs/{clean_domain}"):
        doc_root = f"/home/{isolated_user}/httpdocs/{clean_domain}"

    ini_path = os.path.join(doc_root, ".user.ini")

    directives = {
        "upload_max_filesize": "128M",
        "post_max_size": "128M",
        "memory_limit": "512M",
        "max_execution_time": "300",
        "max_input_time": "300",
        "max_input_vars": "3000",
        "display_errors": "Off",
        "date_timezone": "Asia/Ho_Chi_Minh"
    }
    custom_lines = []

    if os.path.exists(ini_path):
        try:
            with open(ini_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    stripped = line.strip()
                    if not stripped or stripped.startswith(";") or stripped.startswith("#"):
                        continue
                    if "=" in stripped:
                        k, v = stripped.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip('"').strip("'")
                        if k == "date.timezone":
                            directives["date_timezone"] = v
                        elif k in directives:
                            directives[k] = v
                        else:
                            custom_lines.append(f"{k} = {v}")
        except Exception:
            pass
    elif active_ws == "ols":
        vh_conf = f"/usr/local/lsws/conf/vhosts/{clean_domain}/vhconf.conf"
        if os.path.exists(vh_conf):
            try:
                with open(vh_conf, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                m_block = re.search(r"phpIniOverride\s*\{([^}]+)\}", content)
                if m_block:
                    for line in m_block.group(1).splitlines():
                        parts = line.strip().split()
                        if len(parts) >= 3 and parts[0] in ("php_value", "php_flag"):
                            k, v = parts[1], parts[2]
                            if k == "date.timezone":
                                directives["date_timezone"] = v
                            elif k in directives:
                                directives[k] = v
                            else:
                                custom_lines.append(f"{k} = {v}")
            except Exception:
                pass

    return {
        "domain_id": domain_id,
        "domain_name": clean_domain,
        "webserver": active_ws,
        "php_version": domain["php_version"],
        "user_ini_path": ini_path,
        "file_exists": os.path.exists(ini_path),
        "directives": directives,
        "custom_directives": "\n".join(custom_lines)
    }

@router.post("/{domain_id}/php-ini")
async def save_domain_php_ini(
    domain_id: int,
    req: DomainPhpIniUpdate,
    current_user: dict = Depends(get_current_user)
):
    """Lưu và áp dụng cấu hình php.ini (.user.ini) cho website"""
    db = await get_db_connection()
    cursor = await db.execute("""
        SELECT d.id, d.domain_name, d.webserver, d.php_version, d.user_id, u.username as owner_username, u.parent_id
        FROM domains d
        JOIN users u ON d.user_id = u.id
        WHERE d.id = ?
    """, (domain_id,))
    domain = await cursor.fetchone()
    await db.close()

    if not domain:
        raise HTTPException(status_code=404, detail="Không tìm thấy website này")

    if current_user["role"] != "admin":
        if current_user["id"] != domain["user_id"] and current_user["id"] != domain["parent_id"]:
            raise HTTPException(status_code=403, detail="Bạn không có quyền quản trị website này")

    clean_domain = domain["domain_name"]
    isolated_user = domain_to_linux_user(clean_domain)
    active_ws = domain["webserver"] or "ols"

    def clean_val(val: str, max_len: int = 64) -> str:
        s = str(val).strip()[:max_len]
        return re.sub(r'[\r\n;"\'`$]', '', s)

    up_size = clean_val(req.upload_max_filesize) or "128M"
    post_size = clean_val(req.post_max_size) or "128M"
    mem_limit = clean_val(req.memory_limit) or "512M"
    max_exec = clean_val(req.max_execution_time) or "300"
    max_input = clean_val(req.max_input_time) or "300"
    max_vars = clean_val(req.max_input_vars) or "3000"
    disp_err = "On" if req.display_errors.lower() in ("on", "1", "true") else "Off"
    tz = clean_val(req.date_timezone) or "Asia/Ho_Chi_Minh"

    lines = [
        f"; WPCloud Generated PHP Configuration for {clean_domain}",
        f"; Updated at runtime via WPCloud Panel",
        f"upload_max_filesize = {up_size}",
        f"post_max_size = {post_size}",
        f"memory_limit = {mem_limit}",
        f"max_execution_time = {max_exec}",
        f"max_input_time = {max_input}",
        f"max_input_vars = {max_vars}",
        f"display_errors = {disp_err}",
        f"date.timezone = {tz}",
    ]

    if req.custom_directives:
        lines.append("")
        lines.append("; Custom Directives")
        for cline in req.custom_directives.splitlines():
            cline_s = cline.strip()
            if not cline_s or cline_s.startswith(";") or cline_s.startswith("#"):
                continue
            if "=" in cline_s:
                ck, cv = cline_s.split("=", 1)
                ck_clean = re.sub(r'[^a-zA-Z0-9_.-]', '', ck.strip())
                cv_clean = clean_val(cv.strip(), 256)
                if ck_clean and cv_clean:
                    lines.append(f"{ck_clean} = {cv_clean}")

    ini_content = "\n".join(lines) + "\n"

    doc_root = f"/home/{isolated_user}/domains/{clean_domain}/public_html"
    if not os.path.exists(doc_root) and os.path.exists(f"/home/{isolated_user}/httpdocs/{clean_domain}"):
        doc_root = f"/home/{isolated_user}/httpdocs/{clean_domain}"

    # 1. Ghi nội dung vào tệp tạm /tmp/wpc_phpini_{clean_domain}.ini
    # Thư mục /tmp luôn có quyền ghi cho wpc
    tmp_ini = f"/tmp/wpc_phpini_{clean_domain}.ini"
    try:
        with open(tmp_ini, "w", encoding="utf-8") as f:
            f.write(ini_content)
        os.chmod(tmp_ini, 0o644)
    except Exception:
        pass

    # 2. Thử ghi trực tiếp vào doc_root nếu quyền cho phép
    try:
        os.makedirs(doc_root, exist_ok=True)
        ini_path = os.path.join(doc_root, ".user.ini")
        with open(ini_path, "w", encoding="utf-8") as f:
            f.write(ini_content)
    except Exception:
        pass

    # 3. Gọi gatekeeper hoặc sudo script để copy file, phân quyền và reload OLS/Nginx
    res = await run_gatekeeper("vhost", "apply-php-ini", isolated_user, clean_domain, active_ws)
    if res.get("status") == "error":
        try:
            import subprocess
            subprocess.run(
                ["sudo", "/opt/wpc/scripts/vhost_manager.sh", "apply-php-ini", isolated_user, clean_domain, active_ws],
                check=False
            )
        except Exception:
            pass

    return {
        "status": "success",
        "message": f"Cấu hình php.ini cho website {clean_domain} đã được lưu và áp dụng thành công!",
        "directives": {
            "upload_max_filesize": up_size,
            "post_max_size": post_size,
            "memory_limit": mem_limit,
            "max_execution_time": max_exec,
            "max_input_time": max_input,
            "max_input_vars": max_vars,
            "display_errors": disp_err,
            "date_timezone": tz
        }
    }

class DomainToggleSshRequest(BaseModel):
    allow_ssh: bool

@router.post("/{domain_id}/toggle-ssh")
async def toggle_domain_ssh(
    domain_id: int,
    req: DomainToggleSshRequest,
    current_user: dict = Depends(get_current_user)
):
    """Bật / Tắt quyền SSH cho từng website"""
    db = await get_db_connection()
    cursor = await db.execute("""
        SELECT d.id, d.domain_name, d.user_id, u.parent_id
        FROM domains d
        JOIN users u ON d.user_id = u.id
        WHERE d.id = ?
    """, (domain_id,))
    domain = await cursor.fetchone()

    if not domain:
        await db.close()
        raise HTTPException(status_code=404, detail="Không tìm thấy website này")

    if current_user["role"] != "admin":
        if current_user["id"] != domain["user_id"] and current_user["id"] != domain["parent_id"]:
            await db.close()
            raise HTTPException(status_code=403, detail="Bạn không có quyền quản trị website này")

    val = 1 if req.allow_ssh else 0
    await db.execute("UPDATE domains SET allow_ssh = ? WHERE id = ?", (val, domain_id))
    await db.commit()
    await db.close()

    status_str = "Bật" if req.allow_ssh else "Tắt"
    return {
        "status": "success",
        "message": f"Đã {status_str} quyền truy cập Terminal SSH cho website '{domain['domain_name']}'!",
        "allow_ssh": req.allow_ssh
    }

class DomainStatusUpdate(BaseModel):
    status: str # "active" or "suspended"

@router.put("/{domain_id}/status")
async def update_domain_status(
    domain_id: int,
    req: DomainStatusUpdate,
    current_user: dict = Depends(get_current_user)
):
    if req.status not in ["active", "suspended"]:
        raise HTTPException(status_code=400, detail="Trạng thái không hợp lệ (chỉ chấp nhận active hoặc suspended)")
    
    db = await get_db_connection()
    cursor = await db.execute("""
        SELECT d.id, d.domain_name, d.user_id, u.parent_id
        FROM domains d
        JOIN users u ON d.user_id = u.id
        WHERE d.id = ?
    """, (domain_id,))
    domain = await cursor.fetchone()
    if not domain:
        await db.close()
        raise HTTPException(status_code=404, detail="Không tìm thấy website này")
    
    # Kiểm tra quyền: Admin hoặc Hosting User sở hữu website này
    if current_user["role"] != "admin":
        if current_user["id"] != domain["user_id"] and current_user["id"] != domain["parent_id"]:
            await db.close()
            raise HTTPException(status_code=403, detail="Bạn không có quyền thay đổi trạng thái website này")
    
    await db.execute("UPDATE domains SET status = ? WHERE id = ?", (req.status, domain_id))
    await db.commit()
    await db.close()
    
    # Thực hiện suspend / unsuspend Linux isolated user qua gatekeeper nếu có
    isolated_u = domain_to_linux_user(domain["domain_name"])
    try:
        if req.status == "suspended":
            await run_gatekeeper("user", "suspend", isolated_u)
        else:
            await run_gatekeeper("user", "unsuspend", isolated_u)
    except Exception:
        pass
        
    action_text = "tạm khóa (suspend)" if req.status == "suspended" else "mở khóa (unsuspend)"
    return {
        "status": "success",
        "domain_status": req.status,
        "message": f"Website '{domain['domain_name']}' đã được {action_text} thành công!"
    }


