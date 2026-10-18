import os
import re
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from typing import List, Optional
from app.api.deps import get_current_user, require_role
from app.db.session import get_db_connection
from app.core.security import get_password_hash, verify_password, create_access_token
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
            SELECT u.id, u.username, u.email, COALESCE(u.full_name, '') as full_name, COALESCE(u.phone, '') as phone,
                   u.role, u.package_id, u.disk_mb, u.cpu_pct, u.mem_mb, u.status, u.created_at,
                   p.name as package_name,
                   (SELECT COUNT(*) FROM domains d WHERE d.user_id = u.id) as domain_count
            FROM users u
            LEFT JOIN packages p ON u.package_id = p.id
            WHERE u.role IN ('user', 'reseller') AND u.username != 'admin'
            ORDER BY u.created_at DESC
        """)
    elif current_user["role"] == "reseller":
        cursor = await db.execute("""
            SELECT u.id, u.username, u.email, COALESCE(u.full_name, '') as full_name, COALESCE(u.phone, '') as phone,
                   u.role, u.package_id, u.disk_mb, u.cpu_pct, u.mem_mb, u.status, u.created_at,
                   p.name as package_name,
                   (SELECT COUNT(*) FROM domains d WHERE d.user_id = u.id) as domain_count
            FROM users u
            LEFT JOIN packages p ON u.package_id = p.id
            WHERE u.role IN ('user', 'reseller') AND u.parent_id = ?
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
        await db.execute(f"UPDATE users SET status = ? WHERE id IN ({placeholders}) AND role IN ('user', 'reseller')", [req.status] + req.user_ids)
    else:
        await db.execute(f"UPDATE users SET status = ? WHERE id IN ({placeholders}) AND parent_id = ? AND role IN ('user', 'reseller')", [req.status] + req.user_ids + [current_user["id"]])
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
        cur = await db.execute(f"SELECT id, username FROM users WHERE id IN ({placeholders}) AND role IN ('user', 'reseller')", req.user_ids)
    else:
        cur = await db.execute(f"SELECT id, username FROM users WHERE id IN ({placeholders}) AND parent_id = ? AND role IN ('user', 'reseller')", req.user_ids + [current_user["id"]])
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
            VALUES (?, ?, ?, ?, 'reseller', ?, ?, ?, ?, 100, 100, 'active')
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
    plan_type: str = Field("user", pattern="^(user|website)$")
    disk_mb: int = Field(5120, ge=100, le=500000)
    mem_mb: int = Field(1024, ge=128, le=65536)
    cpu_pct: int = Field(100, ge=10, le=400)
    max_domains: int = Field(10, ge=1, le=1000)
    max_databases: int = Field(10, ge=1, le=1000)
    allow_ssh: bool = True

class PlanUpdate(BaseModel):
    name: Optional[str] = None
    plan_type: Optional[str] = None
    disk_mb: Optional[int] = None
    mem_mb: Optional[int] = None
    cpu_pct: Optional[int] = None
    max_domains: Optional[int] = None
    max_databases: Optional[int] = None
    allow_ssh: Optional[bool] = None

@router.get("/plans")
async def list_service_plans(
    plan_type: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    """
    Lấy danh sách các gói dịch vụ (Service Plans)
    - Loại 1: User Plan (plan_type='user') - Quản trị viên cấp tổng tài nguyên cho Hosting User
    - Loại 2: Website Plan (plan_type='website') - Khách hàng tạo gói cho từng website con
    """
    db = await get_db_connection()
    query = """
        SELECT p.*, 
               (CASE WHEN p.plan_type = 'website' 
                     THEN (SELECT COUNT(*) FROM domains d WHERE d.package_id = p.id)
                     ELSE (SELECT COUNT(*) FROM users u WHERE u.package_id = p.id)
                END) as subscriber_count
        FROM packages p
    """
    params = []
    if current_user["role"] in ("reseller", "user") and current_user["username"] != "admin":
        # Chỉ Admin mới thấy User Plan. Còn Hosting user CHỈ nhìn thấy Website Plan
        query += " WHERE p.plan_type = 'website' AND (p.owner_id = ? OR p.owner_id = 0)"
        params.append(current_user["id"])
    elif plan_type:
        query += " WHERE p.plan_type = ?"
        params.append(plan_type)
        
    query += " ORDER BY p.id ASC"
    cursor = await db.execute(query, params)
    rows = await cursor.fetchall()
    await db.close()
    return [dict(r) for r in rows]

@router.post("/plans")
async def create_service_plan(req: PlanCreate, current_user: dict = Depends(require_role("admin", "reseller", "user"))):
    """
    Tạo gói dịch vụ (Service Plan) mới
    - Admin: Tạo User Plan hoặc Website Plan
    - Reseller / Hosting User: Tạo Website Plan trong giới hạn tài nguyên của mình
    """
    db = await get_db_connection()
    try:
        # Nếu là Hosting User (reseller hoặc user), chỉ được phép tạo Website Plan
        is_hosting = current_user["role"] in ("reseller", "user") and current_user["username"] != "admin"
        actual_plan_type = "website" if is_hosting else req.plan_type
        
        # Với gói Website Plan: Không có số lượng web, mỗi gói là 1 web duy nhất
        actual_max_domains = 1 if actual_plan_type == "website" else req.max_domains

        # Nếu là Hosting User, kiểm tra hạn mức không vượt quá gói User Plan của mình
        if is_hosting:
            if req.disk_mb > current_user.get("disk_mb", 5120):
                raise HTTPException(status_code=400, detail=f"Dung lượng website plan ({req.disk_mb}MB) vượt quá hạn mức User Plan của bạn ({current_user.get('disk_mb')}MB)")
            if req.mem_mb > current_user.get("mem_mb", 1024):
                raise HTTPException(status_code=400, detail=f"RAM website plan ({req.mem_mb}MB) vượt quá hạn mức User Plan của bạn ({current_user.get('mem_mb')}MB)")

        await db.execute("""
            INSERT INTO packages (owner_id, name, package_type, plan_type, cpu_pct, mem_mb, disk_mb, io_weight, pids_max, max_domains, max_databases, allow_ssh)
            VALUES (?, ?, ?, ?, ?, ?, ?, 100, 100, ?, ?, ?)
        """, (current_user["id"], req.name, req.package_type, actual_plan_type, req.cpu_pct, req.mem_mb, req.disk_mb, actual_max_domains, req.max_databases, 1 if req.allow_ssh else 0))
        await db.commit()
    except HTTPException:
        await db.close()
        raise
    except Exception as e:
        await db.close()
        raise HTTPException(status_code=400, detail=f"Không thể tạo gói: {str(e)}")
    
    await db.close()
    return {"status": "ok", "message": f"Tạo gói dịch vụ '{req.name}' thành công!"}

@router.put("/plans/{plan_id}")
async def update_service_plan(plan_id: int, req: PlanUpdate, current_user: dict = Depends(require_role("admin", "reseller", "user"))):
    """Cập nhật gói dịch vụ (bật/tắt SSH, chỉnh quota)"""
    db = await get_db_connection()
    cursor = await db.execute("SELECT * FROM packages WHERE id = ?", (plan_id,))
    plan = await cursor.fetchone()
    if not plan:
        await db.close()
        raise HTTPException(status_code=404, detail="Không tìm thấy gói dịch vụ")

    if current_user["role"] != "admin" and plan["owner_id"] != current_user["id"]:
        await db.close()
        raise HTTPException(status_code=403, detail="Bạn chỉ có thể chỉnh sửa gói do bạn tạo ra")

    updates = []
    params = []
    if req.name is not None:
        updates.append("name = ?")
        params.append(req.name)
    if req.plan_type is not None and current_user["role"] == "admin":
        updates.append("plan_type = ?")
        params.append(req.plan_type)
        if req.plan_type == "website":
            updates.append("max_domains = 1")
    if req.disk_mb is not None:
        updates.append("disk_mb = ?")
        params.append(req.disk_mb)
    if req.mem_mb is not None:
        updates.append("mem_mb = ?")
        params.append(req.mem_mb)
    if req.cpu_pct is not None:
        updates.append("cpu_pct = ?")
        params.append(req.cpu_pct)
    if req.max_domains is not None:
        # Nếu là website plan thì cố định max_domains = 1
        if plan["plan_type"] != "website":
            updates.append("max_domains = ?")
            params.append(req.max_domains)
    if req.max_databases is not None:
        updates.append("max_databases = ?")
        params.append(req.max_databases)
    if req.allow_ssh is not None:
        updates.append("allow_ssh = ?")
        params.append(1 if req.allow_ssh else 0)

    if updates:
        params.append(plan_id)
        await db.execute(f"UPDATE packages SET {', '.join(updates)} WHERE id = ?", params)
        await db.commit()

    await db.close()
    return {"status": "ok", "message": "Đã cập nhật gói dịch vụ thành công!"}

# ==========================================
# USER PROFILE & SECURITY MANAGEMENT
# ==========================================

class ProfileUpdateRequest(BaseModel):
    full_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None

class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str
    confirm_password: str

class CustomerAdminEditRequest(BaseModel):
    full_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    password: Optional[str] = None

@router.get("/profile")
async def get_user_profile(current_user: dict = Depends(get_current_user)):
    """Lấy thông tin hồ sơ cá nhân và hạn mức tài nguyên của tài khoản hiện tại"""
    db = await get_db_connection()
    try:
        # Trường hợp 1: Đang đăng nhập dưới quyền Website User (Isolated Linux User của domain)
        if current_user.get("web_domain_id"):
            domain_id = current_user["web_domain_id"]
            d_cur = await db.execute("""
                SELECT d.id, d.domain_name, d.document_root, d.webserver, d.php_version,
                       d.ssl_enabled, d.allow_ssh, d.created_at,
                       u.username as owner_username, u.email as owner_email, u.full_name as owner_full_name,
                       p.name as package_name, p.cpu_pct, p.mem_mb, p.disk_mb
                FROM domains d
                JOIN users u ON d.user_id = u.id
                LEFT JOIN packages p ON u.package_id = p.id
                WHERE d.id = ?
            """, (domain_id,))
            d_row = await d_cur.fetchone()
            if not d_row:
                raise HTTPException(status_code=404, detail="Không tìm thấy thông tin website này")

            wp_cur = await db.execute("SELECT site_title, admin_user, admin_email FROM wp_instances WHERE domain_id = ?", (domain_id,))
            wp_row = await wp_cur.fetchone()

            clean_d = re.sub(r"^(https?://)?(www\.)?", "", (d_row["domain_name"] or "").strip().lower())
            clean_u = re.sub(r"[^a-z0-9]", "", clean_d)[:30] or "webuser"
            web_username = current_user.get("username") or clean_u

            # Tính dung lượng thực tế
            disk_used_mb = 0
            target_dir = f"/home/{web_username}"
            if os.path.isdir(target_dir):
                try:
                    import subprocess
                    p = subprocess.run(["du", "-sm", target_dir], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=2)
                    if p.returncode == 0 and p.stdout:
                        disk_used_mb = int(p.stdout.strip().split()[0])
                except Exception:
                    pass
            elif d_row["document_root"] and os.path.isdir(d_row["document_root"]):
                try:
                    import subprocess
                    p = subprocess.run(["du", "-sm", d_row["document_root"]], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=2)
                    if p.returncode == 0 and p.stdout:
                        disk_used_mb = int(p.stdout.strip().split()[0])
                except Exception:
                    pass

            db_cur = await db.execute("SELECT COUNT(*) as cnt FROM wp_instances WHERE domain_id = ?", (domain_id,))
            db_row = await db_cur.fetchone()

            return {
                "id": d_row["id"],
                "username": web_username,
                "domain_name": d_row["domain_name"],
                "document_root": d_row["document_root"],
                "webserver": d_row["webserver"],
                "php_version": d_row["php_version"],
                "full_name": (wp_row["site_title"] if wp_row and wp_row["site_title"] else d_row["domain_name"]),
                "email": (wp_row["admin_email"] if wp_row and wp_row["admin_email"] else d_row["owner_email"]),
                "phone": "",
                "role": "user",
                "is_web_user": True,
                "owner_username": d_row["owner_username"],
                "status": "active",
                "package_id": None,
                "package_name": f"Website Plan ({d_row['domain_name']})",
                "disk_mb": d_row["disk_mb"] or 5120,
                "disk_used_mb": disk_used_mb,
                "cpu_pct": d_row["cpu_pct"] or 100,
                "mem_mb": d_row["mem_mb"] or 1024,
                "domains_count": 1,
                "max_domains": 1,
                "databases_count": db_row["cnt"] if db_row else 1,
                "max_databases": 1,
                "allow_ssh": bool(d_row["allow_ssh"]),
                "created_at": d_row["created_at"]
            }

        # Trường hợp 2: Hosting User hoặc Super Admin
        user_id = current_user["id"]
        cur = await db.execute("""
            SELECT u.id, u.username, u.email, COALESCE(u.full_name, '') as full_name, COALESCE(u.phone, '') as phone,
                   u.role, u.status, u.package_id, u.disk_mb, u.cpu_pct, u.mem_mb, u.created_at,
                   p.name as package_name, p.max_domains, p.max_databases, COALESCE(p.allow_ssh, 1) as allow_ssh
            FROM users u
            LEFT JOIN packages p ON u.package_id = p.id
            WHERE u.id = ?
        """, (user_id,))
        user_row = await cur.fetchone()
        if not user_row:
            raise HTTPException(status_code=404, detail="Không tìm thấy thông tin tài khoản")

        profile = dict(user_row)
        profile["is_web_user"] = False

        # Tính toán số lượng domain sở hữu
        d_cur = await db.execute("SELECT COUNT(*) as cnt FROM domains WHERE user_id = ?", (user_id,))
        d_row = await d_cur.fetchone()
        profile["domains_count"] = d_row["cnt"] if d_row else 0

        # Tính toán số lượng database
        db_cur = await db.execute("SELECT COUNT(*) as cnt FROM wp_instances WHERE domain_id IN (SELECT id FROM domains WHERE user_id = ?)", (user_id,))
        db_row = await db_cur.fetchone()
        profile["databases_count"] = db_row["cnt"] if db_row else 0

        # Tính dung lượng ổ cứng thực tế đã sử dụng (/home/<username>)
        disk_used_mb = 0
        target_dir = f"/home/{profile['username']}"
        if os.path.isdir(target_dir):
            try:
                import subprocess
                p = subprocess.run(["du", "-sm", target_dir], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=2)
                if p.returncode == 0 and p.stdout:
                    disk_used_mb = int(p.stdout.strip().split()[0])
            except Exception:
                pass
        profile["disk_used_mb"] = disk_used_mb

        return profile
    finally:
        await db.close()

@router.put("/profile")
async def update_user_profile(req: ProfileUpdateRequest, current_user: dict = Depends(get_current_user)):
    """Cập nhật thông tin cá nhân (Họ tên, Email, Số điện thoại)"""
    db = await get_db_connection()
    try:
        if current_user.get("web_domain_id"):
            domain_id = current_user["web_domain_id"]
            if req.email or req.full_name:
                updates = []
                params = []
                if req.full_name is not None:
                    updates.append("site_title = ?")
                    params.append(req.full_name.strip())
                if req.email:
                    clean_email = req.email.strip().lower()
                    if not re.match(r"^[^@]+@[^@]+\.[^@]+$", clean_email):
                        raise HTTPException(status_code=400, detail="Định dạng email không hợp lệ")
                    updates.append("admin_email = ?")
                    params.append(clean_email)
                if updates:
                    params.append(domain_id)
                    await db.execute(f"UPDATE wp_instances SET {', '.join(updates)} WHERE domain_id = ?", params)
                    await db.commit()
            return {"status": "ok", "message": "Cập nhật hồ sơ Website User thành công!"}

        user_id = current_user["id"]
        # Nếu đổi email, kiểm tra xem email đã tồn tại ở tài khoản khác chưa
        if req.email:
            clean_email = req.email.strip().lower()
            if not re.match(r"^[^@]+@[^@]+\.[^@]+$", clean_email):
                raise HTTPException(status_code=400, detail="Định dạng email không hợp lệ")
            c_cur = await db.execute("SELECT id FROM users WHERE email = ? AND id != ?", (clean_email, user_id))
            if await c_cur.fetchone():
                raise HTTPException(status_code=400, detail="Địa chỉ email này đã được sử dụng bởi một tài khoản khác")

        updates = []
        params = []
        if req.full_name is not None:
            updates.append("full_name = ?")
            params.append(req.full_name.strip())
        if req.email is not None:
            updates.append("email = ?")
            params.append(req.email.strip().lower())
        if req.phone is not None:
            updates.append("phone = ?")
            params.append(req.phone.strip())

        if updates:
            params.append(user_id)
            await db.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", params)
            await db.commit()

        return {"status": "ok", "message": "Cập nhật hồ sơ cá nhân thành công!"}
    finally:
        await db.close()

@router.put("/change-password")
async def change_password(req: PasswordChangeRequest, current_user: dict = Depends(get_current_user)):
    """Đổi mật khẩu tài khoản hiện tại"""
    if current_user.get("web_domain_id"):
        raise HTTPException(
            status_code=400,
            detail="Tài khoản Website User hoạt động ở chế độ lồng cách ly bảo mật. Mật khẩu được quản trị thông qua Hosting Reseller hoặc Super Admin."
        )

    if req.new_password != req.confirm_password:
        raise HTTPException(status_code=400, detail="Mật khẩu mới và xác nhận mật khẩu không khớp")
    if len(req.new_password) < 6:
        raise HTTPException(status_code=400, detail="Mật khẩu mới phải có độ dài ít nhất 6 ký tự")

    user_id = current_user["id"]
    db = await get_db_connection()
    try:
        cur = await db.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,))
        row = await cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Không tìm thấy tài khoản")

        if not verify_password(req.current_password, row["password_hash"]):
            raise HTTPException(status_code=400, detail="Mật khẩu hiện tại không chính xác")

        new_hash = get_password_hash(req.new_password)
        await db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (new_hash, user_id))
        await db.commit()

        return {"status": "ok", "message": "Đã đổi mật khẩu thành công!"}
    finally:
        await db.close()

@router.put("/customers/{target_id}/edit")
async def admin_edit_customer(target_id: int, req: CustomerAdminEditRequest, current_user: dict = Depends(require_role("admin", "reseller"))):
    """Admin hoặc Reseller chỉnh sửa thông tin hoặc reset mật khẩu của Customer"""
    db = await get_db_connection()
    try:
        cur = await db.execute("SELECT id, username, parent_id, role FROM users WHERE id = ?", (target_id,))
        target = await cur.fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="Không tìm thấy người dùng này")
        if target["role"] == "admin" and current_user["id"] != target["id"]:
            raise HTTPException(status_code=403, detail="Không thể chỉnh sửa tài khoản Admin khác")
        if current_user["role"] == "reseller" and target["parent_id"] != current_user["id"]:
            raise HTTPException(status_code=403, detail="Bạn chỉ có thể chỉnh sửa khách hàng do bạn quản lý")

        updates = []
        params = []
        if req.full_name is not None:
            updates.append("full_name = ?")
            params.append(req.full_name.strip())
        if req.email:
            clean_email = req.email.strip().lower()
            if not re.match(r"^[^@]+@[^@]+\.[^@]+$", clean_email):
                raise HTTPException(status_code=400, detail="Định dạng email không hợp lệ")
            c_cur = await db.execute("SELECT id FROM users WHERE email = ? AND id != ?", (clean_email, target_id))
            if await c_cur.fetchone():
                raise HTTPException(status_code=400, detail="Email này đã được sử dụng bởi tài khoản khác")
            updates.append("email = ?")
            params.append(clean_email)
        if req.phone is not None:
            updates.append("phone = ?")
            params.append(req.phone.strip())
        if req.password:
            if len(req.password) < 6:
                raise HTTPException(status_code=400, detail="Mật khẩu phải có ít nhất 6 ký tự")
            updates.append("password_hash = ?")
            params.append(get_password_hash(req.password))

        if updates:
            params.append(target_id)
            await db.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", params)
            await db.commit()

        return {"status": "ok", "message": f"Đã cập nhật thông tin người dùng '{target['username']}' thành công!"}
    finally:
        await db.close()

