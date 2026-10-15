import os
import re
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from typing import List, Optional
from app.api.deps import get_current_user, require_role
from app.db.session import get_db_connection
from app.core.security import get_password_hash, create_access_token
from app.executor.gatekeeper import run_gatekeeper

router = APIRouter(prefix="/users", tags=["Customer Management"])

class CustomerCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=32)
    password: str = Field(..., min_length=6)
    email: str = Field(..., min_length=5)
    disk_mb: int = Field(5120, ge=500, le=500000)
    cpu_pct: int = Field(100, ge=10, le=400)
    mem_mb: int = Field(1024, ge=256, le=65536)
    package_id: Optional[int] = None
    # Subscription fields
    create_subscription: bool = True
    domain_type: str = "registered" # "registered" or "temporary"
    domain_name: Optional[str] = None
    webserver: str = "ols"
    php_version: str = "8.2"
    install_wp: bool = False

class CustomerStatusRequest(BaseModel):
    user_ids: List[int]
    status: str # "active" or "suspended"

class CustomerDeleteRequest(BaseModel):
    user_ids: List[int]

class CustomerChangePlan(BaseModel):
    package_id: int

@router.get("/customers")
async def list_customers(current_user: dict = Depends(get_current_user)):
    """
    Lấy danh sách các tài khoản Customer (kèm số lượng domains, tên gói plan của từng customer).
    Admin xem được tất cả customer; Reseller chỉ xem được customer của mình.
    """
    db = await get_db_connection()
    if current_user["role"] == "admin":
        cursor = await db.execute("""
            SELECT u.id, u.username, u.email, u.role, u.package_id, u.disk_mb, u.cpu_pct, u.mem_mb, u.status, u.created_at,
                   p.name as package_name,
                   (SELECT COUNT(*) FROM domains d WHERE d.user_id = u.id) as domain_count
            FROM users u
            LEFT JOIN packages p ON u.package_id = p.id
            WHERE u.role = 'user'
            ORDER BY u.created_at DESC
        """)
    elif current_user["role"] == "reseller":
        cursor = await db.execute("""
            SELECT u.id, u.username, u.email, u.role, u.package_id, u.disk_mb, u.cpu_pct, u.mem_mb, u.status, u.created_at,
                   p.name as package_name,
                   (SELECT COUNT(*) FROM domains d WHERE d.user_id = u.id) as domain_count
            FROM users u
            LEFT JOIN packages p ON u.package_id = p.id
            WHERE u.role = 'user' AND u.parent_id = ?
            ORDER BY u.created_at DESC
        """, (current_user["id"],))
    else:
        await db.close()
        raise HTTPException(status_code=403, detail="Chỉ Admin hoặc Reseller mới có quyền xem danh sách Customer.")

    rows = await cursor.fetchall()
    await db.close()
    return [dict(r) for r in rows]

@router.post("/customers/set-status")
async def set_customers_status(req: CustomerStatusRequest, current_user: dict = Depends(require_role("admin", "reseller"))):
    if not req.user_ids:
        raise HTTPException(status_code=400, detail="Vui lòng chọn ít nhất một customer")
    if req.status not in ["active", "suspended"]:
        raise HTTPException(status_code=400, detail="Trạng thái không hợp lệ (chỉ chấp nhận active hoặc suspended)")
    
    db = await get_db_connection()
    placeholders = ",".join(["?"] * len(req.user_ids))
    if current_user["role"] == "admin":
        await db.execute(f"UPDATE users SET status = ? WHERE id IN ({placeholders}) AND role = 'user'", [req.status] + req.user_ids)
    else:
        await db.execute(f"UPDATE users SET status = ? WHERE id IN ({placeholders}) AND parent_id = ? AND role = 'user'", [req.status] + req.user_ids + [current_user["id"]])
    await db.commit()
    await db.close()
    return {"status": "ok", "message": f"Đã cập nhật trạng thái {req.status.upper()} cho {len(req.user_ids)} khách hàng."}

@router.post("/customers/batch-delete")
async def batch_delete_customers(req: CustomerDeleteRequest, current_user: dict = Depends(require_role("admin", "reseller"))):
    if not req.user_ids:
        raise HTTPException(status_code=400, detail="Vui lòng chọn ít nhất một customer")
    
    db = await get_db_connection()
    placeholders = ",".join(["?"] * len(req.user_ids))
    if current_user["role"] == "admin":
        cur = await db.execute(f"SELECT id, username FROM users WHERE id IN ({placeholders}) AND role = 'user'", req.user_ids)
    else:
        cur = await db.execute(f"SELECT id, username FROM users WHERE id IN ({placeholders}) AND parent_id = ? AND role = 'user'", req.user_ids + [current_user["id"]])
    users_to_del = await cur.fetchall()

    for u in users_to_del:
        u_name = u["username"]
        # Lấy domains của user để dọn vhost
        d_cur = await db.execute("SELECT domain_name, webserver FROM domains WHERE user_id = ?", (u["id"],))
        u_domains = await d_cur.fetchall()
        for d in u_domains:
            await run_gatekeeper("vhost", "remove", u_name, d["domain_name"], d["webserver"])
        # Xóa Linux user
        await run_gatekeeper("user", "delete", u_name)
        # Xóa DB
        await db.execute("DELETE FROM domains WHERE user_id = ?", (u["id"],))
        await db.execute("DELETE FROM users WHERE id = ?", (u["id"],))

    await db.commit()
    await db.close()
    return {"status": "ok", "message": f"Đã xóa thành công {len(users_to_del)} tài khoản khách hàng và dọn dẹp hệ thống."}

@router.post("/customers")
async def create_customer(req: CustomerCreate, current_user: dict = Depends(require_role("admin", "reseller"))):
    """
    Tạo tài khoản Customer mới:
    1. Kiểm tra username chỉ gồm a-z, 0-9, _
    2. Gọi gatekeeper/user create <username> <disk_mb> để tạo Linux user & thư mục
    3. Thêm bản ghi vào bảng users của SQLite
    """
    username = req.username.strip().lower()
    if not re.match(r"^[a-z0-9_-]{3,32}$", username):
        raise HTTPException(status_code=400, detail="Tên tài khoản không hợp lệ (3-32 ký tự, chỉ gồm a-z, 0-9, _, -)")

    db = await get_db_connection()
    cursor = await db.execute("SELECT id FROM users WHERE username = ? OR email = ?", (username, req.email.strip()))
    existing = await cursor.fetchone()
    if existing:
        await db.close()
        raise HTTPException(status_code=400, detail="Tên người dùng hoặc email đã tồn tại trong hệ thống")

    # Gọi C gatekeeper: user create <username> <disk_mb>
    res = await run_gatekeeper("user", "create", username, str(req.disk_mb))
    if res.get("status") == "error":
        await db.close()
        raise HTTPException(status_code=400, detail=res.get("message", "Không thể tạo tài khoản Linux hệ thống"))

    # Thiết lập mật khẩu Linux user nếu cần
    try:
        # Lưu vào SQLite
        pwd_hash = get_password_hash(req.password)
        # Xác định Package ID
        package_id = req.package_id
        if not package_id:
            pkg_cur = await db.execute("SELECT id FROM packages WHERE package_type = 'user' ORDER BY id ASC LIMIT 1")
            pkg = await pkg_cur.fetchone()
            if not pkg:
                await db.execute("""
                    INSERT INTO packages (id, owner_id, name, package_type, cpu_pct, mem_mb, disk_mb, io_weight, pids_max, max_domains, max_databases)
                    VALUES (2, 0, 'Standard Web Hosting', 'user', 100, 1024, 5120, 100, 100, 5, 5)
                """)
                await db.commit()
                package_id = 2
            else:
                package_id = pkg["id"]

        await db.execute("""
            INSERT INTO users (parent_id, username, password_hash, email, role, package_id, cpu_pct, mem_mb, disk_mb, io_weight, pids_max, status)
            VALUES (?, ?, ?, ?, 'user', ?, ?, ?, ?, 100, 100, 'active')
        """, (current_user["id"], username, pwd_hash, req.email.strip(), package_id, req.cpu_pct, req.mem_mb, req.disk_mb))
        await db.commit()

        # Lấy id vừa tạo
        id_cur = await db.execute("SELECT id FROM users WHERE username = ?", (username,))
        created_user = await id_cur.fetchone()
        user_id = created_user["id"]

        # Thiết lập hạn mức Cgroups v2 & Systemd User Slice (Khóa trần CPU, RAM, Tasks)
        parent_reseller_id = str(current_user.get("id", 1))
        await run_gatekeeper("cgroup", "set-user", parent_reseller_id, username, str(req.cpu_pct), str(req.mem_mb), "100", "100")

        # Xử lý Subscription nếu được chọn
        subscription_info = None
        if req.create_subscription:
            d_name = (req.domain_name or "").strip().lower()
            if req.domain_type == "temporary" and not d_name:
                # Tạo temporary domain dạng <username>.wpc.local hoặc <username>.temp
                d_name = f"{username}.wpc.local"

            if d_name:
                # Loại bỏ tiền tố www. hoặc http:// nếu người dùng vô tình dán vào
                # Mỗi website là một Linux user độc lập: domain viết liền không dấu chấm
                clean_d = re.sub(r"[^a-z0-9]", "", d_name)[:30] or "webuser"
                doc_root = f"/home/{clean_d}/domains/{d_name}/public_html"
                ws = req.webserver or "ols"
                php_v = req.php_version or "8.2"

                # 1. Gọi Gatekeeper tạo Vhost & tạo isolated user (gán vào Slice của hosting user này)
                vhost_res = await run_gatekeeper("vhost", "add", clean_d, d_name, ws, php_v, username)
                vhost_status = "success" if vhost_res.get("status") != "error" else "warning"

                # Áp dụng giới hạn Cgroups v2 cho Linux User của website này
                await run_gatekeeper("cgroup", "set-user", parent_reseller_id, clean_d, str(req.cpu_pct), str(req.mem_mb), "100", "100")

                # 2. Lưu vào bảng domains
                try:
                    await db.execute("""
                        INSERT INTO domains (user_id, domain_name, document_root, webserver, php_version)
                        VALUES (?, ?, ?, ?, ?)
                    """, (user_id, d_name, doc_root, ws, php_v))
                    await db.commit()
                except Exception as de:
                    pass

                # 3. Cài đặt WordPress nếu được yêu cầu
                wp_installed = False
                if req.install_wp:
                    wp_res = await run_gatekeeper("wp", "install", clean_d, d_name)
                    wp_installed = (wp_res.get("status") != "error")

                subscription_info = {
                    "domain_name": d_name,
                    "isolated_user": clean_d,
                    "webserver": ws,
                    "php_version": php_v,
                    "vhost_status": vhost_status,
                    "wordpress_installed": wp_installed
                }

    except Exception as e:
        await db.close()
        raise HTTPException(status_code=500, detail=f"Lỗi lưu trữ cơ sở dữ liệu: {str(e)}")

    await db.close()
    return {
        "status": "success",
        "message": f"Tạo Customer '{username}'" + (f" kèm Subscription ({subscription_info['domain_name']})" if subscription_info else "") + " thành công!",
        "customer": {
            "id": user_id,
            "username": username,
            "email": req.email.strip(),
            "role": "user",
            "disk_mb": req.disk_mb,
            "cpu_pct": req.cpu_pct,
            "mem_mb": req.mem_mb,
            "subscription": subscription_info
        }
    }

@router.post("/{user_id}/impersonate")
async def impersonate_customer(user_id: int, current_user: dict = Depends(require_role("admin", "reseller"))):
    """
    Chức năng Login As User (Impersonation):
    Admin hoặc Reseller có thể đăng nhập vào tài khoản của Customer để cấu hình website, WordPress, v.v.
    Trả về JWT access_token của Customer đó.
    """
    db = await get_db_connection()
    cursor = await db.execute("SELECT id, username, email, role, parent_id, status FROM users WHERE id = ?", (user_id,))
    target_user = await cursor.fetchone()
    await db.close()

    if not target_user:
        raise HTTPException(status_code=404, detail="Không tìm thấy Customer này")

    if target_user["role"] == "admin":
        raise HTTPException(status_code=400, detail="Không thể Login As vào một tài khoản Admin khác")

    if current_user["role"] == "reseller" and target_user["parent_id"] != current_user["id"]:
        raise HTTPException(status_code=403, detail="Bạn chỉ có thể Login As vào Customer do bạn trực tiếp quản lý")

    # Tạo JWT access_token đại diện cho target_user
    impersonate_token = create_access_token(
        subject=target_user["id"],
        role=target_user["role"],
        parent_id=target_user["parent_id"]
    )

    return {
        "status": "success",
        "impersonated": True,
        "access_token": impersonate_token,
        "token_type": "bearer",
        "customer": {
            "id": target_user["id"],
            "username": target_user["username"],
            "email": target_user["email"],
            "role": target_user["role"]
        }
    }

@router.put("/{user_id}/plan")
async def change_customer_plan(user_id: int, req: CustomerChangePlan, current_user: dict = Depends(require_role("admin", "reseller"))):
    """
    Đổi Service Plan (gói hosting) cho Hosting User:
    - Cập nhật database (package_id, disk_mb, cpu_pct, mem_mb).
    - Áp dụng ngay hạn mức Cgroup v2 (CPUQuota, MemoryMax) cho user và các isolated web users con.
    - Áp dụng ngay Disk Quota cho user Linux.
    """
    db = await get_db_connection()
    cursor = await db.execute("SELECT id, username, parent_id, role, package_id FROM users WHERE id = ?", (user_id,))
    target_user = await cursor.fetchone()

    if not target_user:
        await db.close()
        raise HTTPException(status_code=404, detail="Không tìm thấy Hosting User")

    if current_user["role"] == "reseller" and target_user["parent_id"] != current_user["id"]:
        await db.close()
        raise HTTPException(status_code=403, detail="Bạn chỉ có thể đổi plan cho user do bạn quản lý")

    # Lấy thông tin package mới
    pkg_cur = await db.execute("SELECT id, name, cpu_pct, mem_mb, disk_mb, max_domains, max_databases FROM packages WHERE id = ?", (req.package_id,))
    pkg = await pkg_cur.fetchone()
    if not pkg:
        await db.close()
        raise HTTPException(status_code=404, detail="Không tìm thấy Service Plan đã chọn")

    # Cập nhật thông số vào users table
    await db.execute("""
        UPDATE users 
        SET package_id = ?, disk_mb = ?, cpu_pct = ?, mem_mb = ?
        WHERE id = ?
    """, (pkg["id"], pkg["disk_mb"], pkg["cpu_pct"], pkg["mem_mb"], user_id))
    await db.commit()

    username = target_user["username"]
    parent_reseller_id = str(target_user["parent_id"] or 1)

    # 1. Cgroup v2 + Systemd User Slice (áp dụng ngay hạn mức mới)
    try:
        await run_gatekeeper("cgroup", "set-user", parent_reseller_id, username, str(pkg["cpu_pct"]), str(pkg["mem_mb"]), "100", "100")
    except Exception as e:
        print(f"Warning: Failed to update cgroup for {username}: {e}")

    # 2. Disk Quota
    try:
        await run_gatekeeper("user", "set-quota", username, str(pkg["disk_mb"]))
    except Exception as e:
        print(f"Warning: Failed to update quota for {username}: {e}")

    # 3. Đồng bộ Cgroup cho các website isolated users thuộc hosting user này
    dom_cur = await db.execute("SELECT domain_name FROM domains WHERE user_id = ?", (user_id,))
    domains = await dom_cur.fetchall()
    for d in domains:
        clean_d = re.sub(r"[^a-z0-9]", "", d["domain_name"])[:30]
        if clean_d:
            try:
                await run_gatekeeper("cgroup", "set-user", parent_reseller_id, clean_d, str(pkg["cpu_pct"]), str(pkg["mem_mb"]), "100", "100")
            except Exception:
                pass

    await db.close()
    return {
        "status": "success",
        "message": f"Đã chuyển Hosting User '{username}' sang gói '{pkg['name']}' thành công!",
        "package": {
            "id": pkg["id"],
            "name": pkg["name"],
            "disk_mb": pkg["disk_mb"],
            "cpu_pct": pkg["cpu_pct"],
            "mem_mb": pkg["mem_mb"]
        }
    }

# ==========================================
# SERVICE PLANS (PACKAGES MANAGEMENT)
# ==========================================
class PlanCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=64)
    package_type: str = Field("user", pattern="^(user|reseller)$")
    disk_mb: int = Field(5120, ge=500, le=500000)
    mem_mb: int = Field(1024, ge=256, le=65536)
    cpu_pct: int = Field(100, ge=10, le=400)
    max_domains: int = Field(10, ge=1, le=1000)
    max_databases: int = Field(10, ge=1, le=1000)

@router.get("/plans")
async def list_service_plans(current_user: dict = Depends(get_current_user)):
    """Lấy danh sách các gói dịch vụ (Service Plans / Hosting Packages)"""
    db = await get_db_connection()
    cursor = await db.execute("""
        SELECT p.*, (SELECT COUNT(*) FROM users u WHERE u.package_id = p.id) as subscriber_count
        FROM packages p
        ORDER BY p.id ASC
    """)
    rows = await cursor.fetchall()
    await db.close()
    return [dict(r) for r in rows]

@router.post("/plans")
async def create_service_plan(req: PlanCreate, current_user: dict = Depends(require_role("admin", "reseller"))):
    """Tạo gói dịch vụ (Service Plan) mới"""
    db = await get_db_connection()
    try:
        await db.execute("""
            INSERT INTO packages (owner_id, name, package_type, cpu_pct, mem_mb, disk_mb, io_weight, pids_max, max_domains, max_databases)
            VALUES (?, ?, ?, ?, ?, ?, 100, 100, ?, ?)
        """, (current_user["id"], req.name, req.package_type, req.cpu_pct, req.mem_mb, req.disk_mb, req.max_domains, req.max_databases))
        await db.commit()
    except Exception as e:
        await db.close()
        raise HTTPException(status_code=400, detail=f"Không thể tạo gói: {str(e)}")
    
    await db.close()
    return {"status": "ok", "message": f"Tạo gói dịch vụ '{req.name}' thành công!"}

