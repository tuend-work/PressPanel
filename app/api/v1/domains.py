from fastapi import APIRouter, Depends, HTTPException
from typing import List
from pydantic import BaseModel
from app.api.deps import get_current_user
from app.db.session import get_db_connection
from app.executor.gatekeeper import run_gatekeeper

router = APIRouter(prefix="/domains", tags=["Websites & Domains"])

class DomainCreate(BaseModel):
    domain_name: str
    webserver: str = "ols"
    php_version: str = "8.2"
    install_wp: bool = False
    package_id: Optional[int] = None

import re
from typing import Optional

class DomainBatchDeleteRequest(BaseModel):
    domain_ids: List[int]

@router.get("/")
async def list_domains(current_user: dict = Depends(get_current_user)):
    db = await get_db_connection()
    if current_user["role"] == "admin":
        cursor = await db.execute("""
            SELECT d.*, u.username as owner_username, u.email as owner_email, p.name as plan_name
            FROM domains d
            JOIN users u ON d.user_id = u.id
            LEFT JOIN packages p ON u.package_id = p.id
            ORDER BY d.created_at DESC
        """)
    elif current_user["role"] == "reseller":
        cursor = await db.execute("""
            SELECT d.*, u.username as owner_username, u.email as owner_email, p.name as plan_name
            FROM domains d
            JOIN users u ON d.user_id = u.id
            LEFT JOIN packages p ON u.package_id = p.id
            WHERE u.id = ? OR u.parent_id = ?
            ORDER BY d.created_at DESC
        """, (current_user["id"], current_user["id"]))
    elif current_user.get("web_domain_id"):
        # Web User: CHỈ ĐƯỢC XEM DUY NHẤT WEBSITE ĐƯỢC PHÂN QUYỀN
        cursor = await db.execute("""
            SELECT d.*, u.username as owner_username, u.email as owner_email, p.name as plan_name
            FROM domains d
            JOIN users u ON d.user_id = u.id
            LEFT JOIN packages p ON u.package_id = p.id
            WHERE d.id = ?
            ORDER BY d.created_at DESC
        """, (current_user["web_domain_id"],))
    else:
        cursor = await db.execute("""
            SELECT d.*, u.username as owner_username, u.email as owner_email, p.name as plan_name
            FROM domains d
            JOIN users u ON d.user_id = u.id
            LEFT JOIN packages p ON u.package_id = p.id
            WHERE d.user_id = ?
            ORDER BY d.created_at DESC
        """, (current_user["id"],))
    
    rows = await cursor.fetchall()
    await db.close()
    return [dict(r) for r in rows]

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
    if not clean_domain:
        raise HTTPException(status_code=400, detail="Tên miền không hợp lệ")

    # MỖI WEBSITE LÀ MỘT LINUX USER ĐỘC LẬP
    # Tên user Linux là domain viết liền không dấu chấm (vd: presspanel.org -> presspanelorg)
    isolated_user = domain_to_linux_user(clean_domain)
    doc_root = f"/home/{isolated_user}/domains/{clean_domain}/public_html"

    # TỰ ĐỘNG CHỌN WEBSERVER TẦNG SERVER:
    # QTV cấu hình/bật webserver nào (OLS hoặc Nginx) thì hệ thống sử dụng webserver đó cho tất cả web.
    active_ws = req.webserver or "ols"
    db = await get_db_connection()
    # Kiểm tra xem có cấu hình webserver hệ thống nào đang được dùng gần nhất không
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
    # Script vhost_manager.sh sẽ tự động gọi ensure_linux_user tạo isolated_user & phân quyền an toàn,
    # đồng thời gán service PHP-FPM con vào Slice của Hosting User cha (wpc-hosting_<hosting_user>.slice)!
    res = await run_gatekeeper("vhost", "add", isolated_user, clean_domain, active_ws, req.php_version, hosting_username)
    if res.get("status") == "error":
        await db.close()
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
    # Đảm bảo slice của Hosting user cha cũng được cập nhật hạn mức
    await run_gatekeeper("cgroup", "set-user", reseller_id, hosting_username, str(current_user.get("cpu_pct", 100) or 100), str(current_user.get("mem_mb", 1024) or 1024), "100", "100")

    try:
        await db.execute("""
            INSERT INTO domains (user_id, domain_name, document_root, webserver, php_version, package_id, allow_ssh)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (current_user["id"], clean_domain, doc_root, active_ws, req.php_version, pkg_id, pkg_allow_ssh))
        await db.commit()
    except Exception as e:
        await db.close()
        raise HTTPException(status_code=400, detail=f"Tên miền đã tồn tại: {str(e)}")
    
    await db.close()

    # Tự động khởi tạo WordPress nếu được yêu cầu
    if req.install_wp:
        await run_gatekeeper("wp", "install", isolated_user, clean_domain)

    return {
        "status": "ok",
        "message": f"Đã khởi tạo website {clean_domain} thành công với Linux user độc lập '{isolated_user}'!",
        "isolated_user": isolated_user,
        "domain": clean_domain
    }

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
            "role": "user"
        }
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


