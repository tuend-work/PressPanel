import os
import re
import json
import subprocess
import shutil
import time
import asyncio
import platform
from typing import List, Optional, Dict
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from app.api.deps import get_current_user
from app.db.session import get_db_connection
from app.executor.gatekeeper import run_gatekeeper

ALLOWED_PHP_VERSIONS = {"5.6", "7.0", "7.1", "7.2", "7.3", "7.4", "8.0", "8.1", "8.2", "8.3", "8.4", "8.5"}
ALLOWED_WEBSERVERS = {"ols", "nginx"}

router = APIRouter(prefix="/domains", tags=["Websites & Domains"])

_disk_usage_cache = {}  # {path: (size_mb, timestamp)}

def domain_to_linux_user(domain: str) -> str:
    # Bỏ www. và chuyển sang viết liền không dấu chấm
    clean = re.sub(r"^(https?://)?(www\.)?", "", domain.strip().lower())
    clean = re.sub(r"[^a-z0-9]", "", clean)
    if not clean:
        clean = "webuser"
    # Giới hạn 30 ký tự để phù hợp giới hạn username của Linux (thường max 32)
    return clean[:30]

async def get_path_disk_usage_mb(path: str) -> float:
    if not path or not os.path.exists(path):
        return 0.0
    now = time.time()
    if path in _disk_usage_cache:
        cached_size, ts = _disk_usage_cache[path]
        if now - ts < 60:  # Cache 60 giây tránh scan I/O liên tục
            return cached_size

    size_mb = 0.0
    if platform.system() == "Linux":
        try:
            proc = await asyncio.create_subprocess_exec(
                "du", "-sm", path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=0.8)
            if proc.returncode == 0:
                first_part = out.decode("utf-8", errors="ignore").split()[0]
                size_mb = float(first_part)
        except Exception:
            pass

    if size_mb == 0.0:
        try:
            total = 0
            for root, _, files in os.walk(path):
                for f in files:
                    fp = os.path.join(root, f)
                    if not os.path.islink(fp) and os.path.exists(fp):
                        total += os.path.getsize(fp)
            size_mb = round(total / (1024 * 1024), 1)
        except Exception:
            pass

    if size_mb > 0:
        _disk_usage_cache[path] = (size_mb, now)
    elif path in _disk_usage_cache:
        return _disk_usage_cache[path][0]

    return size_mb

class DomainCreate(BaseModel):
    domain_name: str
    webserver: str = "ols"
    php_version: str = "8.2"
    install_wp: bool = False
    package_id: Optional[int] = None

class DomainBatchDeleteRequest(BaseModel):
    domain_ids: List[int]

class DomainChangePlanRequest(BaseModel):
    package_id: int

class DomainUpdateRequest(BaseModel):
    php_version: Optional[str] = None
    package_id: Optional[int] = None
    allow_ssh: Optional[bool] = None

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

    # Thu thập thống kê tiến trình thực tế trên Linux cho các domain
    user_proc_stats = {}  # { username: {"cpu": 0.0, "rss_bytes": 0} }

    if platform.system() == "Linux" and rows:
        try:
            ps_proc = await asyncio.create_subprocess_exec(
                "ps", "-eo", "user:32,%cpu,rss,args", "--no-headers",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout_data, _ = await asyncio.wait_for(ps_proc.communicate(), timeout=1.5)
            for line in stdout_data.decode("utf-8", errors="ignore").splitlines():
                parts = line.strip().split(None, 3)
                if len(parts) >= 3:
                    u_name = parts[0].strip()
                    try:
                        c_val = float(parts[1])
                        m_kb = int(parts[2])
                    except (ValueError, IndexError):
                        continue
                    
                    if u_name not in user_proc_stats:
                        user_proc_stats[u_name] = {"cpu": 0.0, "rss_bytes": 0}
                    user_proc_stats[u_name]["cpu"] += c_val
                    user_proc_stats[u_name]["rss_bytes"] += m_kb * 1024

                    cmd_args = parts[3] if len(parts) > 3 else ""
                    if cmd_args and ("lsphp" in cmd_args or "pool" in cmd_args):
                        for r_item in rows:
                            iso_u = domain_to_linux_user(r_item["domain_name"])
                            if iso_u != u_name and iso_u in cmd_args:
                                if iso_u not in user_proc_stats:
                                    user_proc_stats[iso_u] = {"cpu": 0.0, "rss_bytes": 0}
                                user_proc_stats[iso_u]["cpu"] += c_val
                                user_proc_stats[iso_u]["rss_bytes"] += m_kb * 1024
        except Exception:
            pass

    results = []
    for r in rows:
        d_dict = dict(r)
        cpu_limit = d_dict.get("cpu_pct") or 100
        mem_limit = d_dict.get("mem_mb") or 1024
        disk_limit = d_dict.get("disk_mb") or 5120
        d_status = d_dict.get("status", "active")
        d_name = d_dict.get("domain_name", "")
        isolated_u = domain_to_linux_user(d_name)
        reseller_id = d_dict.get("user_id") or 1

        # Đo dung lượng Disk thực tế
        doc_root = d_dict.get("document_root")
        home_dir = f"/home/{isolated_u}"
        target_disk_path = home_dir if os.path.isdir(home_dir) else doc_root
        real_disk_mb = 0.0
        if target_disk_path:
            real_disk_mb = await get_path_disk_usage_mb(target_disk_path)

        if d_status == "suspended":
            d_dict["cpu_usage_pct"] = 0.0
            d_dict["mem_usage_mb"] = 0
            d_dict["disk_usage_mb"] = real_disk_mb
            results.append(d_dict)
            continue

        # 1. CPU thực tế từ bảng tiến trình Linux
        proc_stat = user_proc_stats.get(isolated_u, {"cpu": 0.0, "rss_bytes": 0})
        real_cpu = round(proc_stat["cpu"], 1)

        # 2. RAM thực tế từ Cgroups v2 kết hợp Process RSS
        cg_mem_bytes = 0
        cgroup_paths = [
            f"/sys/fs/cgroup/wpc/reseller_{reseller_id}/user_{isolated_u}/memory.current",
            f"/sys/fs/cgroup/wpc/reseller_1/user_{isolated_u}/memory.current",
            f"/sys/fs/cgroup/wpc/user_{isolated_u}/memory.current",
        ]
        for mp in cgroup_paths:
            if os.path.exists(mp):
                try:
                    with open(mp, "r") as f:
                        v = int(f.read().strip())
                        if v > cg_mem_bytes:
                            cg_mem_bytes = v
                except Exception:
                    pass

        real_mem_bytes = max(cg_mem_bytes, proc_stat["rss_bytes"])
        real_mem_mb = round(real_mem_bytes / (1024 * 1024), 1)

        d_dict["cpu_usage_pct"] = real_cpu
        d_dict["mem_usage_mb"] = real_mem_mb
        d_dict["disk_usage_mb"] = real_disk_mb

        results.append(d_dict)

    return results

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

        # Áp dụng khóa trần Cgroups v2 cho Hosting User và gom website con vào chung Cgroup
        reseller_id = str(current_user.get("parent_id", 1) or 1)
        hosting_cpu = current_user.get("cpu_pct", 100) or 100
        hosting_mem = current_user.get("mem_mb", 1024) or 1024

        # Thiết lập Cgroup trần duy nhất cho Hosting User
        await run_gatekeeper("cgroup", "set-user", reseller_id, hosting_username, str(hosting_cpu), str(hosting_mem), "100", "100")
        # Gom ngay lập tức tiến trình của website con vào Cgroup của Hosting User cha
        if hosting_username != isolated_user:
            await run_gatekeeper("cgroup", "attach-child", hosting_username, isolated_user)
            await run_gatekeeper("cgroup", "sync", hosting_username)

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

@router.put("/{domain_id}/plan")
async def change_domain_plan(domain_id: int, req: DomainChangePlanRequest, current_user: dict = Depends(get_current_user)):
    """
    Đổi Package/Plan cho Website riêng lẻ:
    - Cập nhật package_id trong bảng domains.
    - Tự động áp dụng giới hạn Cgroups v2 (CPUQuota, MemoryMax) và Disk Quota cho isolated Linux user của domain.
    """
    db = await get_db_connection()
    cur = await db.execute("""
        SELECT d.id, d.domain_name, d.user_id, d.package_id, u.username as owner_username, u.parent_id, u.role as owner_role,
               u.cpu_pct as parent_cpu, u.mem_mb as parent_mem, u.disk_mb as parent_disk
        FROM domains d
        JOIN users u ON d.user_id = u.id
        WHERE d.id = ?
    """, (domain_id,))
    dom = await cur.fetchone()
    if not dom:
        await db.close()
        raise HTTPException(status_code=404, detail="Không tìm thấy website")

    # Kiểm tra quyền: Admin, Reseller (cha), hoặc chính chủ sở hữu
    if current_user["role"] == "reseller" and dom["user_id"] != current_user["id"] and dom["parent_id"] != current_user["id"]:
        await db.close()
        raise HTTPException(status_code=403, detail="Bạn không có quyền quản lý website này")
    elif current_user["role"] == "user" and dom["user_id"] != current_user["id"]:
        await db.close()
        raise HTTPException(status_code=403, detail="Bạn không có quyền quản lý website này")

    pkg_cur = await db.execute("SELECT id, name, cpu_pct, mem_mb, disk_mb, allow_ssh FROM packages WHERE id = ?", (req.package_id,))
    pkg = await pkg_cur.fetchone()
    if not pkg:
        await db.close()
        raise HTTPException(status_code=404, detail="Không tìm thấy Website Plan đã chọn")

    u_cpu = pkg["cpu_pct"]
    u_mem = pkg["mem_mb"]
    if dom["owner_role"] == "user" and dom["parent_cpu"]:
        u_cpu = min(u_cpu, dom["parent_cpu"])
        u_mem = min(u_mem, dom["parent_mem"])

    await db.execute("UPDATE domains SET package_id = ?, allow_ssh = ? WHERE id = ?", (pkg["id"], pkg["allow_ssh"], domain_id))
    await db.commit()
    await db.close()

    isolated_user = domain_to_linux_user(dom["domain_name"])
    reseller_id = str(dom["parent_id"] or 1)

    hosting_username = dom.get("owner_username", isolated_user)
    try:
        if hosting_username != isolated_user:
            await run_gatekeeper("cgroup", "attach-child", hosting_username, isolated_user)
            await run_gatekeeper("cgroup", "sync", hosting_username)
        else:
            await run_gatekeeper("cgroup", "set-user", reseller_id, isolated_user, str(u_cpu), str(u_mem), "100", "100")
    except Exception as e:
        print(f"Warning: Failed to update cgroup for {isolated_user}: {e}")

    try:
        await run_gatekeeper("user", "set-quota", isolated_user, str(pkg["disk_mb"]))
    except Exception as e:
        print(f"Warning: Failed to update quota for {isolated_user}: {e}")

    return {
        "status": "ok",
        "message": f"Đã đổi gói dịch vụ cho website '{dom['domain_name']}' sang '{pkg['name']}' thành công!",
        "plan_name": pkg["name"]
    }

@router.put("/{domain_id}")
async def update_domain(domain_id: int, req: DomainUpdateRequest, current_user: dict = Depends(get_current_user)):
    """
    Sửa cấu hình Website:
    - Đổi phiên bản PHP (chạy vhost change-php)
    - Đổi Website Plan / Hạn mức
    - Bật / tắt quyền SSH
    """
    db = await get_db_connection()
    cur = await db.execute("""
        SELECT d.id, d.domain_name, d.user_id, d.php_version, d.package_id, d.allow_ssh, d.webserver,
               u.username as owner_username, u.parent_id, u.role as owner_role,
               u.cpu_pct as parent_cpu, u.mem_mb as parent_mem
        FROM domains d
        JOIN users u ON d.user_id = u.id
        WHERE d.id = ?
    """, (domain_id,))
    dom = await cur.fetchone()
    if not dom:
        await db.close()
        raise HTTPException(status_code=404, detail="Không tìm thấy website")

    if current_user["role"] == "reseller" and dom["user_id"] != current_user["id"] and dom["parent_id"] != current_user["id"]:
        await db.close()
        raise HTTPException(status_code=403, detail="Bạn không có quyền quản lý website này")
    elif current_user["role"] == "user" and dom["user_id"] != current_user["id"]:
        await db.close()
        raise HTTPException(status_code=403, detail="Bạn không có quyền quản lý website này")

    isolated_user = domain_to_linux_user(dom["domain_name"])
    reseller_id = str(dom["parent_id"] or 1)

    new_php = dom["php_version"]
    if req.php_version and req.php_version != dom["php_version"]:
        if req.php_version not in ALLOWED_PHP_VERSIONS:
            await db.close()
            raise HTTPException(status_code=400, detail="Phiên bản PHP không hợp lệ")
        new_php = req.php_version
        try:
            await run_gatekeeper("vhost", "change-php", isolated_user, dom["domain_name"], dom["webserver"] or "ols", new_php)
        except Exception as e:
            await db.close()
            raise HTTPException(status_code=500, detail=f"Lỗi khi chuyển đổi PHP: {str(e)}")

    new_pkg_id = dom["package_id"]
    new_ssh = dom["allow_ssh"]
    if req.allow_ssh is not None:
        new_ssh = 1 if req.allow_ssh else 0
        try:
            await run_gatekeeper("vhost", "toggle-ssh", isolated_user, "enable" if new_ssh else "disable")
        except Exception:
            pass

    if req.package_id and req.package_id != dom["package_id"]:
        pkg_cur = await db.execute("SELECT id, name, cpu_pct, mem_mb, disk_mb, allow_ssh FROM packages WHERE id = ?", (req.package_id,))
        pkg = await pkg_cur.fetchone()
        if pkg:
            new_pkg_id = pkg["id"]
            u_cpu = pkg["cpu_pct"]
            u_mem = pkg["mem_mb"]
            if dom["owner_role"] == "user" and dom["parent_cpu"]:
                u_cpu = min(u_cpu, dom["parent_cpu"])
                u_mem = min(u_mem, dom["parent_mem"])
            try:
                hosting_username = dom.get("owner_username", isolated_user)
                if hosting_username != isolated_user:
                    await run_gatekeeper("cgroup", "attach-child", hosting_username, isolated_user)
                    await run_gatekeeper("cgroup", "sync", hosting_username)
                else:
                    await run_gatekeeper("cgroup", "set-user", reseller_id, isolated_user, str(u_cpu), str(u_mem), "100", "100")
                await run_gatekeeper("user", "set-quota", isolated_user, str(pkg["disk_mb"]))
            except Exception:
                pass

    await db.execute("""
        UPDATE domains 
        SET php_version = ?, package_id = ?, allow_ssh = ?
        WHERE id = ?
    """, (new_php, new_pkg_id, new_ssh, domain_id))
    await db.commit()
    await db.close()

    return {
        "status": "ok",
        "message": f"Đã cập nhật website '{dom['domain_name']}' thành công!",
        "php_version": new_php,
        "package_id": new_pkg_id,
        "allow_ssh": new_ssh
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

AVAILABLE_PHP_EXTENSIONS = [
    {
        "id": "opcache",
        "name": "Zend OPcache",
        "desc": "Tăng tốc thực thi PHP bằng bộ nhớ cache bytecode, giảm tải CPU và tối ưu tốc độ tải trang.",
        "category": "Performance",
        "default": True,
        "core": False,
        "doc_url": "https://www.php.net/manual/en/book.opcache.php"
    },
    {
        "id": "redis",
        "name": "Redis Object Cache",
        "desc": "Hỗ trợ kết nối Redis / Valkey server làm Object Cache, giảm tải truy vấn MySQL WordPress.",
        "category": "Database & Cache",
        "default": True,
        "core": False,
        "doc_url": "https://github.com/phpredis/phpredis"
    },
    {
        "id": "imagick",
        "name": "ImageMagick (Imagick)",
        "desc": "Xử lý đồ họa ảnh chất lượng cao, tối ưu nén ảnh WebP/AVIF cho WordPress Media Library.",
        "category": "Graphics",
        "default": True,
        "core": False,
        "doc_url": "https://www.php.net/manual/en/book.imagick.php"
    },
    {
        "id": "gd",
        "name": "GD Library",
        "desc": "Thư viện đồ họa ảnh cơ bản, hỗ trợ cắt ảnh thumbnails, watermark và mã captcha.",
        "category": "Graphics",
        "default": True,
        "core": True,
        "doc_url": "https://www.php.net/manual/en/book.image.php"
    },
    {
        "id": "intl",
        "name": "Intl (Internationalization)",
        "desc": "Hỗ trợ bản địa hóa, đa ngôn ngữ, định dạng tiền tệ và số học cho WooCommerce / WPML.",
        "category": "Localization",
        "default": True,
        "core": False,
        "doc_url": "https://www.php.net/manual/en/book.intl.php"
    },
    {
        "id": "zip",
        "name": "Zip Archive",
        "desc": "Nén và giải nén tệp zip khi cài đặt, cập nhật Plugin / Theme WordPress và xuất backup.",
        "category": "System",
        "default": True,
        "core": False,
        "doc_url": "https://www.php.net/manual/en/book.zip.php"
    },
    {
        "id": "curl",
        "name": "cURL Client",
        "desc": "Truyền nhận dữ liệu HTTP/HTTPS, WordPress REST API, Webhooks thanh toán và WP-Cron.",
        "category": "Network",
        "default": True,
        "core": True,
        "doc_url": "https://www.php.net/manual/en/book.curl.php"
    },
    {
        "id": "mbstring",
        "name": "Mbstring",
        "desc": "Xử lý chuỗi ký tự UTF-8 đa byte, tiếng Việt có dấu và các bộ mã quốc tế không lỗi font.",
        "category": "Core",
        "default": True,
        "core": True,
        "doc_url": "https://www.php.net/manual/en/book.mbstring.php"
    },
    {
        "id": "exif",
        "name": "EXIF Data",
        "desc": "Đọc siêu dữ liệu ảnh chụp từ máy ảnh / smartphone (thời gian chụp, camera, vị trí GPS).",
        "category": "Graphics",
        "default": True,
        "core": False,
        "doc_url": "https://www.php.net/manual/en/book.exif.php"
    },
    {
        "id": "bcmath",
        "name": "BCMath",
        "desc": "Tính toán số học với độ chính xác cao, bắt buộc cho các cổng thanh toán và WooCommerce.",
        "category": "Math",
        "default": True,
        "core": False,
        "doc_url": "https://www.php.net/manual/en/book.bc.php"
    },
    {
        "id": "fileinfo",
        "name": "Fileinfo",
        "desc": "Nhận diện định dạng tệp tải lên qua Magic Byte an toàn, bảo vệ chống upload shell độc hại.",
        "category": "Security",
        "default": True,
        "core": True,
        "doc_url": "https://www.php.net/manual/en/book.fileinfo.php"
    },
    {
        "id": "sodium",
        "name": "Sodium (Crypto)",
        "desc": "Thư viện mật mã học hiện đại trong PHP core (mã hóa mật khẩu, token bảo mật, chữ ký số).",
        "category": "Security",
        "default": True,
        "core": False,
        "doc_url": "https://www.php.net/manual/en/book.sodium.php"
    },
    {
        "id": "soap",
        "name": "SOAP Web Services",
        "desc": "Giao thức XML kết nối các dịch vụ web ngân hàng, phần mềm kế toán ERP, hóa đơn điện tử.",
        "category": "Web Services",
        "default": False,
        "core": False,
        "doc_url": "https://www.php.net/manual/en/book.soap.php"
    },
    {
        "id": "memcached",
        "name": "Memcached",
        "desc": "Bộ nhớ đệm đối tượng Memcached phân tán hiệu năng cao.",
        "category": "Database & Cache",
        "default": False,
        "core": False,
        "doc_url": "https://www.php.net/manual/en/book.memcached.php"
    },
    {
        "id": "xdebug",
        "name": "Xdebug (Dev Only)",
        "desc": "Bộ công cụ gỡ lỗi mã nguồn PHP và profiling hiệu năng (khuyên tắt trên Production).",
        "category": "Development",
        "default": False,
        "core": False,
        "doc_url": "https://xdebug.org/"
    }
]

def get_installed_php_modules(php_version: str) -> set:
    ver_clean = (php_version or "8.2").replace("php", "")
    if len(ver_clean) == 2 and "." not in ver_clean:
        ver_clean = f"{ver_clean[0]}.{ver_clean[1]}"
    php_num = ver_clean.replace(".", "")

    modules = set()
    # 1. Quét nhị phân LSPHP (OpenLiteSpeed) và PHP CLI
    lsphp_bin = f"/usr/local/lsws/lsphp{php_num}/bin/lsphp"
    php_cli = f"/usr/bin/php{ver_clean}"

    for bin_path in [lsphp_bin, php_cli]:
        if os.path.exists(bin_path):
            try:
                res = subprocess.run([bin_path, "-m"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=3)
                if res.returncode == 0:
                    for line in res.stdout.splitlines():
                        m = line.strip().lower()
                        if m and not m.startswith("["):
                            modules.add(m)
            except Exception:
                pass

    # 2. Quét conf.d / mods-available
    for check_dir in [
        f"/etc/php/{ver_clean}/mods-available",
        f"/etc/php/{ver_clean}/fpm/conf.d",
        f"/usr/local/lsws/lsphp{php_num}/etc/php/{ver_clean}/mods-available",
        f"/usr/local/lsws/lsphp{php_num}/etc/php.d"
    ]:
        if os.path.isdir(check_dir):
            try:
                for f_name in os.listdir(check_dir):
                    f_clean = f_name.lower().replace(".ini", "")
                    if "-" in f_clean:
                        f_clean = f_clean.split("-", 1)[-1]
                    if f_clean:
                        modules.add(f_clean)
            except Exception:
                pass

    return modules

def is_ext_module_installed(ext_id: str, installed_modules: set) -> bool:
    if not installed_modules:
        return True
    
    if ext_id == "opcache":
        return ("zend opcache" in installed_modules or "opcache" in installed_modules)
    elif ext_id == "redis":
        return ("redis" in installed_modules)
    elif ext_id == "imagick":
        return ("imagick" in installed_modules or "imagemagick" in installed_modules)
    elif ext_id in ["gd", "intl", "zip", "curl", "mbstring", "exif", "bcmath", "fileinfo", "sodium", "soap", "memcached", "xdebug"]:
        return (ext_id in installed_modules)
    return True

@router.get("/{domain_id}/php-extensions")
async def get_domain_php_extensions(
    domain_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Lấy danh sách các PHP extension và trạng thái bật/tắt của từng website"""
    db = await get_db_connection()
    cursor = await db.execute("""
        SELECT d.id, d.domain_name, d.webserver, d.php_version, d.user_id, d.php_extensions, u.parent_id
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
    doc_root = f"/home/{isolated_user}/domains/{clean_domain}/public_html"
    if not os.path.exists(doc_root) and os.path.exists(f"/home/{isolated_user}/httpdocs/{clean_domain}"):
        doc_root = f"/home/{isolated_user}/httpdocs/{clean_domain}"
    ini_path = os.path.join(doc_root, ".user.ini")

    # Đọc cấu hình đã lưu trong DB nếu có
    saved_exts = {}
    if domain["php_extensions"]:
        try:
            saved_exts = json.loads(domain["php_extensions"])
        except Exception:
            saved_exts = {}

    # Nếu chưa có trong DB, đọc từ .user.ini
    if not saved_exts and os.path.exists(ini_path):
        try:
            with open(ini_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line_s = line.strip()
                    if line_s.startswith("opcache.enable"):
                        saved_exts["opcache"] = ("1" in line_s or "On" in line_s)
                    elif line_s.startswith("xdebug.mode"):
                        saved_exts["xdebug"] = ("off" not in line_s.lower())
        except Exception:
            pass

    installed_mods = get_installed_php_modules(domain["php_version"])

    # Build response list
    result_list = []
    for item in AVAILABLE_PHP_EXTENSIONS:
        ext_id = item["id"]
        is_enabled = saved_exts.get(ext_id, item["default"])
        is_inst = is_ext_module_installed(ext_id, installed_mods)
        result_list.append({
            **item,
            "enabled": bool(is_enabled),
            "installed": is_inst
        })

    return {
        "domain_id": domain_id,
        "domain_name": clean_domain,
        "php_version": domain["php_version"],
        "webserver": domain["webserver"],
        "extensions": result_list
    }

class DomainPhpExtensionsUpdate(BaseModel):
    extensions: Dict[str, bool]

@router.post("/{domain_id}/php-extensions")
async def save_domain_php_extensions(
    domain_id: int,
    req: DomainPhpExtensionsUpdate,
    current_user: dict = Depends(get_current_user)
):
    """Lưu và áp dụng bật/tắt PHP extensions cho từng website, tự động cài extension nếu còn thiếu"""
    db = await get_db_connection()
    cursor = await db.execute("""
        SELECT d.id, d.domain_name, d.webserver, d.php_version, d.user_id, u.parent_id
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

    # 1. Lưu vào Database
    ext_json = json.dumps(req.extensions, ensure_ascii=False)
    await db.execute("UPDATE domains SET php_extensions = ? WHERE id = ?", (ext_json, domain_id))
    await db.commit()
    await db.close()

    clean_domain = domain["domain_name"]
    isolated_user = domain_to_linux_user(clean_domain)
    active_ws = domain["webserver"] or "ols"

    php_ver = (domain["php_version"] or "8.2").replace("php", "")
    if len(php_ver) == 2 and "." not in php_ver:
        php_ver = f"{php_ver[0]}.{php_ver[1]}"
    php_num = php_ver.replace(".", "")

    # 2. Tự động phát hiện và cài đặt gói extension còn thiếu nếu người dùng bật ON
    PKG_EXT_LIST = ["redis", "imagick", "intl", "soap", "memcached", "bcmath"]
    installed_mods = get_installed_php_modules(domain["php_version"])
    
    installed_any = False
    for pkg_ext in PKG_EXT_LIST:
        if req.extensions.get(pkg_ext) is True:
            # Nếu đã xác định được danh sách modules mà extension chưa có, tự động kích hoạt cài đặt
            if installed_mods and not is_ext_module_installed(pkg_ext, installed_mods):
                try:
                    await run_gatekeeper("app", "php-ext", "install", php_ver, pkg_ext)
                    installed_any = True
                except Exception:
                    try:
                        subprocess.run(
                            ["sudo", "/opt/wpc/scripts/apps/php.sh", "install-ext", php_ver, pkg_ext],
                            check=False,
                            timeout=60
                        )
                        installed_any = True
                    except Exception:
                        pass

    if installed_any:
        try:
            subprocess.run(["pkill", "-9", "-f", f"lsphp{php_num}"], check=False)
            subprocess.run(["/usr/local/lsws/bin/lswsctrl", "restart"], check=False)
        except Exception:
            pass

    doc_root = f"/home/{isolated_user}/domains/{clean_domain}/public_html"
    if not os.path.exists(doc_root) and os.path.exists(f"/home/{isolated_user}/httpdocs/{clean_domain}"):
        doc_root = f"/home/{isolated_user}/httpdocs/{clean_domain}"

    ini_path = os.path.join(doc_root, ".user.ini")

    # 3. Đọc nội dung .user.ini hiện tại và cập nhật directives liên quan đến extension
    existing_content = ""
    if os.path.exists(ini_path):
        try:
            with open(ini_path, "r", encoding="utf-8", errors="ignore") as f:
                existing_content = f.read()
        except Exception:
            existing_content = ""

    # Loại bỏ block extension cũ nếu có
    clean_lines = []
    in_ext_block = False
    for line in existing_content.splitlines():
        if "; --- PHP EXTENSIONS CONFIGURATION ---" in line:
            in_ext_block = True
            continue
        if in_ext_block:
            if line.startswith(";") and "--- END PHP EXTENSIONS ---" in line:
                in_ext_block = False
            continue
        line_s = line.strip()
        if line_s.startswith("opcache.enable") or line_s.startswith("xdebug.mode") or line_s.startswith("disable_classes"):
            continue
        clean_lines.append(line)

    ext_lines = [
        "",
        "; --- PHP EXTENSIONS CONFIGURATION ---",
        f"; Managed by PressPanel for {clean_domain}"
    ]

    opcache_on = req.extensions.get("opcache", True)
    ext_lines.append(f"opcache.enable = {1 if opcache_on else 0}")

    xdebug_on = req.extensions.get("xdebug", False)
    ext_lines.append(f"xdebug.mode = {'develop,debug' if xdebug_on else 'off'}")

    redis_on = req.extensions.get("redis", True)
    if redis_on:
        ext_lines.append("redis.session.locking_enabled = 1")

    # Cách ly theo từng website: Vô hiệu hóa classes của các extension bị tắt
    disabled_classes = []
    if not redis_on:
        disabled_classes.extend(["Redis", "RedisCluster", "RedisSentinel", "RedisException"])
    if not req.extensions.get("imagick", True):
        disabled_classes.extend(["Imagick", "ImagickDraw", "ImagickPixel"])
    if not req.extensions.get("soap", False):
        disabled_classes.extend(["SoapClient", "SoapServer", "SoapFault"])
    if not req.extensions.get("memcached", False):
        disabled_classes.append("Memcached")

    if disabled_classes:
        ext_lines.append(f'disable_classes = "{",".join(disabled_classes)}"')

    ext_lines.append("; --- END PHP EXTENSIONS ---")

    new_ini_content = "\n".join(clean_lines).rstrip() + "\n" + "\n".join(ext_lines) + "\n"

    tmp_ini = f"/tmp/wpc_phpini_{clean_domain}.ini"
    try:
        with open(tmp_ini, "w", encoding="utf-8") as f:
            f.write(new_ini_content)
        os.chmod(tmp_ini, 0o644)
    except Exception:
        pass

    try:
        os.makedirs(doc_root, exist_ok=True)
        with open(ini_path, "w", encoding="utf-8") as f:
            f.write(new_ini_content)
    except Exception:
        pass

    # 4. Kích hoạt Gatekeeper apply-php-ini để đồng bộ vhconf.conf và reload worker
    res = await run_gatekeeper("vhost", "apply-php-ini", isolated_user, clean_domain, active_ws)
    if res.get("status") == "error":
        try:
            subprocess.run(
                ["sudo", "/opt/wpc/scripts/vhost_manager.sh", "apply-php-ini", isolated_user, clean_domain, active_ws],
                check=False
            )
        except Exception:
            pass

    return {
        "status": "success",
        "message": f"Cấu hình PHP Extensions cho website {clean_domain} đã được lưu và áp dụng thành công!" + (" (Đã cài đặt các extension còn thiếu)" if installed_any else ""),
        "extensions": req.extensions
    }

def get_safe_fpm_socket_owner(isolated_user: str) -> tuple[str, str]:
    """
    Xác định an toàn user/group sở hữu socket PHP-FPM.
    Tránh lỗi 'cannot get uid for user www-data' trên RHEL/AlmaLinux/CentOS
    nơi user www-data không tồn tại trong /etc/passwd.
    """
    if platform.system() != "Linux":
        return ("www-data", "www-data")

    try:
        import pwd
        # Thứ tự ưu tiên người dùng web server: www-data (Debian/Ubuntu) -> nginx (RHEL/Alma) -> apache -> isolated_user
        for cand in ["www-data", "nginx", "apache"]:
            try:
                pwd.getpwnam(cand)
                return (cand, cand)
            except KeyError:
                continue
        if isolated_user:
            try:
                pwd.getpwnam(isolated_user)
                return (isolated_user, isolated_user)
            except KeyError:
                pass
    except Exception:
        pass

    return ("nobody", "nobody")

def sanitize_fpm_config_content(content: str, isolated_user: str) -> str:
    """Tự động thay thế listen.owner/listen.group nếu trỏ đến user/group không tồn tại trên hệ thống (như www-data trên AlmaLinux)"""
    safe_u, safe_g = get_safe_fpm_socket_owner(isolated_user)
    if platform.system() == "Linux":
        try:
            import pwd
            for m in re.finditer(r'^[ \t]*listen\.owner[ \t]*=[ \t]*([^\r\n#;]+)', content, re.MULTILINE):
                u = m.group(1).strip()
                try:
                    pwd.getpwnam(u)
                except KeyError:
                    content = content.replace(m.group(0), f"listen.owner = {safe_u}")
            for m in re.finditer(r'^[ \t]*listen\.group[ \t]*=[ \t]*([^\r\n#;]+)', content, re.MULTILINE):
                g = m.group(1).strip()
                try:
                    import grp
                    grp.getgrnam(g)
                except (KeyError, Exception):
                    content = content.replace(m.group(0), f"listen.group = {safe_g}")
        except Exception:
            if safe_u != "www-data":
                content = re.sub(r'^[ \t]*listen\.owner[ \t]*=[ \t]*www-data', f'listen.owner = {safe_u}', content, flags=re.MULTILINE)
                content = re.sub(r'^[ \t]*listen\.group[ \t]*=[ \t]*www-data', f'listen.group = {safe_g}', content, flags=re.MULTILINE)
    return content

class DomainFpmSettingsUpdate(BaseModel):
    mode: str = "form"  # "form" or "raw"
    pm: Optional[str] = "dynamic"
    pm_max_children: Optional[int] = 10
    pm_start_servers: Optional[int] = 2
    pm_min_spare_servers: Optional[int] = 1
    pm_max_spare_servers: Optional[int] = 3
    pm_process_idle_timeout: Optional[str] = "10s"
    pm_max_requests: Optional[int] = 500
    request_terminate_timeout: Optional[str] = "0s"
    request_slowlog_timeout: Optional[str] = "0s"
    catch_workers_output: Optional[str] = "yes"
    clear_env: Optional[str] = "no"
    raw_config: Optional[str] = None

class DomainFpmControlRequest(BaseModel):
    action: str  # "start", "stop", "restart", "reload"

class DomainLsapiSettingsUpdate(BaseModel):
    mode: str = "form"  # "form" or "raw"
    lsapi_mode: Optional[str] = "avoid_fork"  # "avoid_fork" or "ondemand"
    lsapi_children: Optional[int] = 10
    lsapi_avoid_fork: Optional[str] = "1"
    max_conns: Optional[int] = 10
    max_requests: Optional[int] = 1000
    init_timeout: Optional[int] = 60
    retry_timeout: Optional[int] = 0
    persist_conn: Optional[int] = 1
    instances: Optional[int] = 1
    mem_soft_limit: Optional[str] = "1024M"
    mem_hard_limit: Optional[str] = "2048M"
    proc_soft_limit: Optional[int] = 200
    proc_hard_limit: Optional[int] = 300
    raw_config: Optional[str] = None

class DomainLsapiControlRequest(BaseModel):
    action: str  # "restart", "reload"

@router.get("/{domain_id}/fpm-service")
async def get_domain_fpm_service(
    domain_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Lấy thông tin và trạng thái PHP-FPM Service độc lập của website (Nginx)"""
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
    php_ver = str(domain["php_version"] or "8.2")
    service_name = f"wpc-php{php_ver}-fpm@{isolated_user}.service"
    conf_dir = f"/etc/php/{php_ver}/fpm/users"
    conf_path = f"{conf_dir}/{isolated_user}.conf"
    sock_path = f"/run/php/php{php_ver}-fpm-{isolated_user}.sock"
    pid_path = f"/run/php/php{php_ver}-fpm-{isolated_user}.pid"

    # Trạng thái service từ systemd
    status = "inactive"
    enabled = "unknown"
    pid = ""
    memory_mb = 0.0
    active_since = ""

    if platform.system() == "Linux":
        try:
            r_act = subprocess.run(["systemctl", "is-active", service_name], capture_output=True, text=True)
            status = r_act.stdout.strip() or "inactive"

            r_en = subprocess.run(["systemctl", "is-enabled", service_name], capture_output=True, text=True)
            enabled = r_en.stdout.strip() or "disabled"

            r_show = subprocess.run(
                ["systemctl", "show", "-p", "MainPID", "-p", "ActiveEnterTimestamp", "-p", "MemoryCurrent", service_name],
                capture_output=True, text=True
            )
            for line in r_show.stdout.splitlines():
                if line.startswith("MainPID="):
                    val = line.split("=", 1)[1].strip()
                    if val and val != "0":
                        pid = val
                elif line.startswith("ActiveEnterTimestamp="):
                    active_since = line.split("=", 1)[1].strip()
                elif line.startswith("MemoryCurrent="):
                    val = line.split("=", 1)[1].strip()
                    if val.isdigit() and int(val) > 0:
                        memory_mb = round(int(val) / (1024 * 1024), 2)
        except Exception:
            pass

    # Đọc cấu hình pool nếu có
    settings = {
        "pm": "dynamic",
        "pm_max_children": 10,
        "pm_start_servers": 2,
        "pm_min_spare_servers": 1,
        "pm_max_spare_servers": 3,
        "pm_process_idle_timeout": "10s",
        "pm_max_requests": 500,
        "request_terminate_timeout": "0s",
        "request_slowlog_timeout": "0s",
        "catch_workers_output": "yes",
        "clear_env": "no"
    }
    raw_config = ""
    sock_owner, sock_group = get_safe_fpm_socket_owner(isolated_user)

    if os.path.exists(conf_path):
        try:
            with open(conf_path, "r", encoding="utf-8", errors="ignore") as f:
                raw_config = f.read()
            raw_config = sanitize_fpm_config_content(raw_config, isolated_user)
            for line in raw_config.splitlines():
                line = line.strip()
                if not line or line.startswith(";") or line.startswith("#"):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    if k == "pm":
                        settings["pm"] = v
                    elif k == "pm.max_children" and v.isdigit():
                        settings["pm_max_children"] = int(v)
                    elif k == "pm.start_servers" and v.isdigit():
                        settings["pm_start_servers"] = int(v)
                    elif k == "pm.min_spare_servers" and v.isdigit():
                        settings["pm_min_spare_servers"] = int(v)
                    elif k == "pm.max_spare_servers" and v.isdigit():
                        settings["pm_max_spare_servers"] = int(v)
                    elif k == "pm.process_idle_timeout":
                        settings["pm_process_idle_timeout"] = v
                    elif k == "pm.max_requests" and v.isdigit():
                        settings["pm_max_requests"] = int(v)
                    elif k == "request_terminate_timeout":
                        settings["request_terminate_timeout"] = v
                    elif k == "request_slowlog_timeout":
                        settings["request_slowlog_timeout"] = v
                    elif k == "catch_workers_output":
                        settings["catch_workers_output"] = v.lower()
                    elif k == "clear_env":
                        settings["clear_env"] = v.lower()
        except Exception:
            pass
    else:
        # Template mặc định an toàn
        raw_config = f"""; WPCloud PHP-FPM Pool Configuration
[global]
pid = {pid_path}
error_log = /var/log/php{php_ver}-fpm-{isolated_user}.log
log_level = notice
daemonize = no

[{isolated_user}]
user = {isolated_user}
group = {isolated_user}
listen = {sock_path}
listen.owner = {sock_owner}
listen.group = {sock_group}
listen.mode = 0666

pm = dynamic
pm.max_children = 10
pm.start_servers = 2
pm.min_spare_servers = 1
pm.max_spare_servers = 3
pm.process_idle_timeout = 10s
pm.max_requests = 500
request_terminate_timeout = 0s
request_slowlog_timeout = 0s
catch_workers_output = yes
clear_env = no

pm.status_path = /status-{isolated_user}
ping.path = /ping-{isolated_user}

php_admin_value[open_basedir] = /home/{isolated_user}/:/tmp/:/var/tmp/
php_admin_value[upload_tmp_dir] = /home/{isolated_user}/.tmp
php_admin_value[session.save_path] = /home/{isolated_user}/.sessions
php_admin_value[disable_functions] = exec,passthru,shell_exec,system,proc_open,popen,curl_multi_exec,parse_ini_file,show_source
php_admin_flag[log_errors] = on
php_admin_value[error_log] = /home/{isolated_user}/logs/php-fpm.error.log
"""

    return {
        "status": "success",
        "domain_name": clean_domain,
        "user": isolated_user,
        "php_version": php_ver,
        "service_name": service_name,
        "service_status": status,
        "service_enabled": enabled,
        "pid": pid,
        "memory_mb": memory_mb,
        "active_since": active_since,
        "conf_path": conf_path,
        "sock_path": sock_path,
        "sock_exists": os.path.exists(sock_path),
        "settings": settings,
        "raw_config": raw_config
    }

@router.post("/{domain_id}/fpm-service")
async def save_domain_fpm_service(
    domain_id: int,
    req: DomainFpmSettingsUpdate,
    current_user: dict = Depends(get_current_user)
):
    """Lưu cấu hình và tự động reload PHP-FPM Service của website"""
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
    php_ver = str(domain["php_version"] or "8.2")
    service_name = f"wpc-php{php_ver}-fpm@{isolated_user}.service"
    conf_dir = f"/etc/php/{php_ver}/fpm/users"
    conf_path = f"{conf_dir}/{isolated_user}.conf"
    sock_path = f"/run/php/php{php_ver}-fpm-{isolated_user}.sock"
    pid_path = f"/run/php/php{php_ver}-fpm-{isolated_user}.pid"

    os.makedirs(conf_dir, exist_ok=True)
    sock_owner, sock_group = get_safe_fpm_socket_owner(isolated_user)

    if req.mode == "raw" and req.raw_config:
        content = req.raw_config
    else:
        # Sinh nội dung từ form với socket owner an toàn đã nhận diện theo OS
        pm_mode = req.pm if req.pm in ["dynamic", "ondemand", "static"] else "dynamic"
        max_ch = max(1, min(100, int(req.pm_max_children or 10)))
        start_s = max(1, min(max_ch, int(req.pm_start_servers or 2)))
        min_s = max(1, min(start_s, int(req.pm_min_spare_servers or 1)))
        max_s = max(min_s, min(max_ch, int(req.pm_max_spare_servers or 3)))
        idle_timeout = str(req.pm_process_idle_timeout or "10s").strip()
        max_req = max(0, int(req.pm_max_requests or 500))
        timeout = str(req.request_terminate_timeout or "0s").strip()
        slowlog_timeout = str(req.request_slowlog_timeout or "0s").strip()
        catch_workers = "yes" if req.catch_workers_output != "no" else "no"
        clear_env = "yes" if req.clear_env == "yes" else "no"

        if pm_mode == "ondemand":
            pm_directives = f"""pm = ondemand
pm.max_children = {max_ch}
pm.process_idle_timeout = {idle_timeout}
pm.max_requests = {max_req}"""
        elif pm_mode == "static":
            pm_directives = f"""pm = static
pm.max_children = {max_ch}
pm.max_requests = {max_req}"""
        else:
            pm_directives = f"""pm = dynamic
pm.max_children = {max_ch}
pm.start_servers = {start_s}
pm.min_spare_servers = {min_s}
pm.max_spare_servers = {max_s}
pm.max_requests = {max_req}"""

        content = f"""; WPCloud PHP-FPM Pool Configuration
[global]
pid = {pid_path}
error_log = /var/log/php{php_ver}-fpm-{isolated_user}.log
log_level = notice
daemonize = no

[{isolated_user}]
user = {isolated_user}
group = {isolated_user}
listen = {sock_path}
listen.owner = {sock_owner}
listen.group = {sock_group}
listen.mode = 0666

{pm_directives}
request_terminate_timeout = {timeout}
request_slowlog_timeout = {slowlog_timeout}
catch_workers_output = {catch_workers}
clear_env = {clear_env}

pm.status_path = /status-{isolated_user}
ping.path = /ping-{isolated_user}

php_admin_value[open_basedir] = /home/{isolated_user}/:/tmp/:/var/tmp/
php_admin_value[upload_tmp_dir] = /home/{isolated_user}/.tmp
php_admin_value[session.save_path] = /home/{isolated_user}/.sessions
php_admin_value[disable_functions] = exec,passthru,shell_exec,system,proc_open,popen,curl_multi_exec,parse_ini_file,show_source
php_admin_flag[log_errors] = on
php_admin_value[error_log] = /home/{isolated_user}/logs/php-fpm.error.log
"""

    # Luôn kiểm tra khử lỗi listen.owner/listen.group không tồn tại (như www-data trên AlmaLinux)
    content = sanitize_fpm_config_content(content, isolated_user)

    # Ghi file cấu hình an toàn qua sudo/python
    try:
        tmp_conf = f"/tmp/wpc_fpm_{isolated_user}_{int(time.time())}.conf"
        with open(tmp_conf, "w", encoding="utf-8") as f:
            f.write(content)
        subprocess.run(["sudo", "mv", tmp_conf, conf_path], check=False)
        subprocess.run(["sudo", "chmod", "644", conf_path], check=False)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Không thể ghi file cấu hình FPM: {str(e)}")

    # Đảm bảo restart service để phục hồi ngay lập tức kể cả khi service đang ở trạng thái failed
    if platform.system() == "Linux":
        try:
            subprocess.run(
                ["sudo", "/opt/wpc/scripts/php_fpm_service.sh", "restart", isolated_user, php_ver],
                check=False
            )
        except Exception:
            pass

    return {
        "status": "success",
        "message": f"Đã lưu cấu hình và tự động khởi động lại dịch vụ {service_name} thành công!"
    }

@router.post("/{domain_id}/fpm-service/reset")
async def reset_domain_fpm_service(
    domain_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Khôi phục cấu hình PHP-FPM của website về mặc định của hệ thống"""
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
    php_ver = str(domain["php_version"] or "8.2")
    service_name = f"wpc-php{php_ver}-fpm@{isolated_user}.service"
    conf_dir = f"/etc/php/{php_ver}/fpm/users"
    conf_path = f"{conf_dir}/{isolated_user}.conf"

    if platform.system() == "Linux":
        try:
            # Tái tạo sạch cấu hình mặc định an toàn cho OS hiện tại qua script
            subprocess.run(
                ["sudo", "/opt/wpc/scripts/php_fpm_service.sh", "create", isolated_user, php_ver],
                check=False
            )
            subprocess.run(
                ["sudo", "/opt/wpc/scripts/php_fpm_service.sh", "restart", isolated_user, php_ver],
                check=False
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Lỗi khi khôi phục dịch vụ PHP-FPM: {str(e)}")
    else:
        # Dev Windows simulation
        sock_owner, sock_group = get_safe_fpm_socket_owner(isolated_user)
        sock_path = f"/run/php/php{php_ver}-fpm-{isolated_user}.sock"
        pid_path = f"/run/php/php{php_ver}-fpm-{isolated_user}.pid"
        os.makedirs(conf_dir, exist_ok=True)
        default_content = f"""; WPCloud PHP-FPM Pool Configuration
[global]
pid = {pid_path}
error_log = /var/log/php{php_ver}-fpm-{isolated_user}.log
log_level = notice
daemonize = no

[{isolated_user}]
user = {isolated_user}
group = {isolated_user}
listen = {sock_path}
listen.owner = {sock_owner}
listen.group = {sock_group}
listen.mode = 0666

pm = dynamic
pm.max_children = 10
pm.start_servers = 2
pm.min_spare_servers = 1
pm.max_spare_servers = 3
pm.max_requests = 500
request_terminate_timeout = 0s

pm.status_path = /status-{isolated_user}
ping.path = /ping-{isolated_user}

php_admin_value[open_basedir] = /home/{isolated_user}/:/tmp/:/var/tmp/
php_admin_value[upload_tmp_dir] = /home/{isolated_user}/.tmp
php_admin_value[session.save_path] = /home/{isolated_user}/.sessions
php_admin_value[disable_functions] = exec,passthru,shell_exec,system,proc_open,popen,curl_multi_exec,parse_ini_file,show_source
php_admin_flag[log_errors] = on
php_admin_value[error_log] = /home/{isolated_user}/logs/php-fpm.error.log
"""
        with open(conf_path, "w", encoding="utf-8") as f:
            f.write(default_content)

    return {
        "status": "success",
        "message": f"Đã khôi phục cấu hình PHP-FPM của website {clean_domain} về mặc định hệ thống thành công!"
    }

@router.post("/{domain_id}/fpm-service/control")
async def control_domain_fpm_service(
    domain_id: int,
    req: DomainFpmControlRequest,
    current_user: dict = Depends(get_current_user)
):
    """Điều khiển dịch vụ PHP-FPM của website: start, stop, restart, reload"""
    if req.action not in ["start", "stop", "restart", "reload"]:
        raise HTTPException(status_code=400, detail="Thao tác không hợp lệ. Chọn start, stop, restart hoặc reload")

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
    php_ver = str(domain["php_version"] or "8.2")
    service_name = f"wpc-php{php_ver}-fpm@{isolated_user}.service"

    if platform.system() == "Linux":
        try:
            subprocess.run(
                ["sudo", "/opt/wpc/scripts/php_fpm_service.sh", req.action, isolated_user, php_ver],
                check=False
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Lỗi thực thi lệnh {req.action}: {str(e)}")

    # Lấy trạng thái mới
    new_status = "inactive"
    if platform.system() == "Linux":
        try:
            r = subprocess.run(["systemctl", "is-active", service_name], capture_output=True, text=True)
            new_status = r.stdout.strip() or "inactive"
        except Exception:
            pass

    action_vn = {
        "start": "khởi chạy",
        "stop": "dừng",
        "restart": "khởi động lại",
        "reload": "nạp lại cấu hình"
    }.get(req.action, req.action)

    return {
        "status": "success",
        "message": f"Đã {action_vn} dịch vụ {service_name} thành công!",
        "service_status": new_status
    }

# =========================================================================
# OPENLITESPEED LSAPI PROCESS MANAGER ENDPOINTS (LSAPI_CHILDREN, AVOID_FORK)
# =========================================================================
@router.get("/{domain_id}/lsapi-service")
async def get_domain_lsapi_service(
    domain_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Lấy thông tin và cấu hình LSAPI Process Manager (OpenLiteSpeed) của website"""
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
    php_ver = str(domain["php_version"] or "8.2")
    processor_name = f"lsphp_{isolated_user}"
    conf_dir = f"/usr/local/lsws/conf/vhosts/{clean_domain}"
    conf_path = f"{conf_dir}/vhconf.conf"
    sock_path = f"/tmp/lshttpd/lsphp_{isolated_user}.sock"

    status = "inactive"
    pid = ""
    memory_mb = 0.0

    if platform.system() == "Linux":
        try:
            r = subprocess.run(["pgrep", "-u", isolated_user, "-f", "lsphp"], capture_output=True, text=True)
            pids = [p.strip() for p in r.stdout.splitlines() if p.strip()]
            if pids:
                pid = pids[0]
                status = "active"
                r_mem = subprocess.run(["ps", "-u", isolated_user, "-o", "rss,cmd"], capture_output=True, text=True)
                total_kb = 0
                for line in r_mem.stdout.splitlines():
                    if "lsphp" in line:
                        parts = line.strip().split()
                        if parts and parts[0].isdigit():
                            total_kb += int(parts[0])
                memory_mb = round(total_kb / 1024, 2)
            else:
                status = "idle" if os.path.exists(conf_path) else "inactive"
        except Exception:
            pass

    settings = {
        "lsapi_mode": "avoid_fork",
        "lsapi_children": 10,
        "lsapi_avoid_fork": "1",
        "max_conns": 10,
        "max_requests": 1000,
        "init_timeout": 60,
        "retry_timeout": 0,
        "persist_conn": 1,
        "instances": 1,
        "mem_soft_limit": "1024M",
        "mem_hard_limit": "2048M",
        "proc_soft_limit": 200,
        "proc_hard_limit": 300
    }
    raw_config = ""

    if os.path.exists(conf_path):
        try:
            with open(conf_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            m_block = re.search(rf"(extprocessor\s+lsphp_{isolated_user}\s*\{{[\s\S]*?\n\}})", content)
            if m_block:
                raw_config = m_block.group(1)
                m_mc = re.search(r"maxConns\s+(\d+)", raw_config)
                if m_mc:
                    settings["max_conns"] = int(m_mc.group(1))
                m_ch = re.search(r"(?:PHP_)?LSAPI_CHILDREN=(\d+)", raw_config)
                if m_ch:
                    settings["lsapi_children"] = int(m_ch.group(1))
                m_af = re.search(r"LSAPI_AVOID_FORK=([^\s\r\n]+)", raw_config)
                if m_af:
                    settings["lsapi_avoid_fork"] = m_af.group(1).strip()
                    settings["lsapi_mode"] = "ondemand" if m_af.group(1).strip() == "0" else "avoid_fork"
                m_req = re.search(r"(?:PHP_LSAPI_MAX_REQUESTS|LSAPI_MAX_REQS)=(\d+)", raw_config)
                if m_req:
                    settings["max_requests"] = int(m_req.group(1))
                m_it = re.search(r"initTimeout\s+(\d+)", raw_config)
                if m_it:
                    settings["init_timeout"] = int(m_it.group(1))
                m_rt = re.search(r"retryTimeout\s+(\d+)", raw_config)
                if m_rt:
                    settings["retry_timeout"] = int(m_rt.group(1))
                m_pc = re.search(r"persistConn\s+(\d+)", raw_config)
                if m_pc:
                    settings["persist_conn"] = int(m_pc.group(1))
                m_in = re.search(r"instances\s+(\d+)", raw_config)
                if m_in:
                    settings["instances"] = int(m_in.group(1))
                m_ms = re.search(r"memSoftLimit\s+([^\r\n]+)", raw_config)
                if m_ms:
                    settings["mem_soft_limit"] = m_ms.group(1).strip()
                m_mh = re.search(r"memHardLimit\s+([^\r\n]+)", raw_config)
                if m_mh:
                    settings["mem_hard_limit"] = m_mh.group(1).strip()
                m_ps = re.search(r"procSoftLimit\s+(\d+)", raw_config)
                if m_ps:
                    settings["proc_soft_limit"] = int(m_ps.group(1))
                m_ph = re.search(r"procHardLimit\s+(\d+)", raw_config)
                if m_ph:
                    settings["proc_hard_limit"] = int(m_ph.group(1))
        except Exception:
            pass

    return {
        "status": "success",
        "domain_name": clean_domain,
        "user": isolated_user,
        "php_version": php_ver,
        "processor_name": processor_name,
        "service_status": status,
        "pid": pid,
        "memory_mb": memory_mb,
        "conf_path": conf_path,
        "sock_path": sock_path,
        "sock_exists": os.path.exists(sock_path),
        "settings": settings,
        "raw_config": raw_config
    }

@router.post("/{domain_id}/lsapi-service")
async def save_domain_lsapi_service(
    domain_id: int,
    req: DomainLsapiSettingsUpdate,
    current_user: dict = Depends(get_current_user)
):
    """Lưu cấu hình và tự động reload OpenLiteSpeed LSAPI Processor của website"""
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
    php_ver = str(domain["php_version"] or "8.2")
    php_num = php_ver.replace(".", "")
    conf_dir = f"/usr/local/lsws/conf/vhosts/{clean_domain}"
    conf_path = f"{conf_dir}/vhconf.conf"
    sock_path = f"/tmp/lshttpd/lsphp_{isolated_user}.sock"

    if not os.path.exists(conf_path):
        raise HTTPException(status_code=404, detail=f"Không tìm thấy file cấu hình VHost OpenLiteSpeed: {conf_path}")

    try:
        with open(conf_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        if req.mode == "raw" and req.raw_config:
            pattern = rf"(extprocessor\s+lsphp_{isolated_user}\s*\{{[\s\S]*?\n\}})"
            if re.search(pattern, content):
                content = re.sub(pattern, req.raw_config.strip(), content)
            else:
                content += "\n\n" + req.raw_config.strip() + "\n"
        else:
            avoid_fork = req.lsapi_avoid_fork if req.lsapi_avoid_fork is not None else ("0" if req.lsapi_mode == "ondemand" else "1")
            max_ch = max(1, min(100, int(req.lsapi_children or 10)))
            max_conns = max(1, min(200, int(req.max_conns or max_ch)))
            max_req = max(0, int(req.max_requests or 1000))
            init_t = max(5, int(req.init_timeout or 60))
            retry_t = max(0, int(req.retry_timeout or 0))
            persist_c = 1 if req.persist_conn in [1, "1", True] else 0
            inst = max(1, int(req.instances or 1))
            mem_s = str(req.mem_soft_limit or "1024M").strip()
            mem_h = str(req.mem_hard_limit or "2048M").strip()
            proc_s = max(10, int(req.proc_soft_limit or 200))
            proc_h = max(20, int(req.proc_hard_limit or 300))

            m_path = re.search(r"path\s+([^\r\n]+)", content)
            path = m_path.group(1).strip() if m_path else f"/usr/local/lsws/lsphp{php_num}/bin/lsphp"

            new_block = f"""extprocessor lsphp_{isolated_user} {{
  type                    lsapi
  address                 uds://tmp/lshttpd/lsphp_{isolated_user}.sock
  maxConns                {max_conns}
  env                     LSAPI_CHILDREN={max_ch}
  env                     LSAPI_AVOID_FORK={avoid_fork}
  env                     PHP_LSAPI_MAX_REQUESTS={max_req}
  initTimeout             {init_t}
  retryTimeout            {retry_t}
  persistConn             {persist_c}
  respBuffer              0
  autoStart               1
  path                    {path}
  backlog                 100
  instances               {inst}
  runOn                   1
  extUser                 {isolated_user}
  extGroup                {isolated_user}
  priority                0
  memSoftLimit            {mem_s}
  memHardLimit            {mem_h}
  procSoftLimit           {proc_s}
  procHardLimit           {proc_h}
}}"""

            pattern = rf"(extprocessor\s+lsphp_{isolated_user}\s*\{{[\s\S]*?\n\}})"
            if re.search(pattern, content):
                content = re.sub(pattern, new_block, content)
            else:
                content += "\n\n" + new_block + "\n"

        tmp_conf = f"/tmp/wpc_lsapi_{isolated_user}_{int(time.time())}.conf"
        with open(tmp_conf, "w", encoding="utf-8") as f:
            f.write(content)
        subprocess.run(["sudo", "mv", tmp_conf, conf_path], check=False)
        subprocess.run(["sudo", "chmod", "644", conf_path], check=False)
        subprocess.run(["sudo", "chown", "lsadm:lsadm", conf_path], check=False)

        if platform.system() == "Linux":
            subprocess.run(["sudo", "pkill", "-9", "-u", isolated_user, "-f", "lsphp"], check=False)
            subprocess.run(["sudo", "rm", "-f", sock_path], check=False)
            subprocess.run(["sudo", "mkdir", "-p", "/tmp/lshttpd"], check=False)
            subprocess.run(["sudo", "chmod", "1777", "/tmp/lshttpd"], check=False)
            subprocess.run(["sudo", "/usr/local/lsws/bin/lswsctrl", "restart"], check=False)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Không thể cập nhật cấu hình LSAPI: {str(e)}")

    return {
        "status": "success",
        "message": f"Đã lưu cấu hình và khởi tạo lại LSAPI Process Pool cho website {clean_domain} thành công!"
    }

@router.post("/{domain_id}/lsapi-service/reset")
async def reset_domain_lsapi_service(
    domain_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Khôi phục cấu hình LSAPI Processor của website về giá trị chuẩn mặc định"""
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
    php_ver = str(domain["php_version"] or "8.2")
    php_num = php_ver.replace(".", "")
    conf_dir = f"/usr/local/lsws/conf/vhosts/{clean_domain}"
    conf_path = f"{conf_dir}/vhconf.conf"
    sock_path = f"/tmp/lshttpd/lsphp_{isolated_user}.sock"

    if os.path.exists(conf_path):
        try:
            with open(conf_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            default_block = f"""extprocessor lsphp_{isolated_user} {{
  type                    lsapi
  address                 uds://tmp/lshttpd/lsphp_{isolated_user}.sock
  maxConns                10
  env                     LSAPI_CHILDREN=10
  env                     LSAPI_AVOID_FORK=1
  env                     PHP_LSAPI_MAX_REQUESTS=1000
  initTimeout             60
  retryTimeout            0
  persistConn             1
  respBuffer              0
  autoStart               1
  path                    /usr/local/lsws/lsphp{php_num}/bin/lsphp
  backlog                 100
  instances               1
  runOn                   1
  extUser                 {isolated_user}
  extGroup                {isolated_user}
  priority                0
  memSoftLimit            1024M
  memHardLimit            2048M
  procSoftLimit           200
  procHardLimit           300
}}"""

            pattern = rf"(extprocessor\s+lsphp_{isolated_user}\s*\{{[\s\S]*?\n\}})"
            if re.search(pattern, content):
                content = re.sub(pattern, default_block, content)
            else:
                content += "\n\n" + default_block + "\n"

            tmp_conf = f"/tmp/wpc_lsapi_{isolated_user}_{int(time.time())}.conf"
            with open(tmp_conf, "w", encoding="utf-8") as f:
                f.write(content)
            subprocess.run(["sudo", "mv", tmp_conf, conf_path], check=False)
            subprocess.run(["sudo", "chmod", "644", conf_path], check=False)
            subprocess.run(["sudo", "chown", "lsadm:lsadm", conf_path], check=False)

            if platform.system() == "Linux":
                subprocess.run(["sudo", "pkill", "-9", "-u", isolated_user, "-f", "lsphp"], check=False)
                subprocess.run(["sudo", "rm", "-f", sock_path], check=False)
                subprocess.run(["sudo", "/usr/local/lsws/bin/lswsctrl", "restart"], check=False)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Lỗi khi khôi phục cấu hình LSAPI: {str(e)}")

    return {
        "status": "success",
        "message": f"Đã khôi phục cấu hình LSAPI Processor của website {clean_domain} về mặc định thành công!"
    }

@router.post("/{domain_id}/lsapi-service/control")
async def control_domain_lsapi_service(
    domain_id: int,
    req: DomainLsapiControlRequest,
    current_user: dict = Depends(get_current_user)
):
    """Điều khiển tiến trình LSPHP của website (Restart / Kill old workers)"""
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
    sock_path = f"/tmp/lshttpd/lsphp_{isolated_user}.sock"

    if platform.system() == "Linux":
        try:
            subprocess.run(["sudo", "pkill", "-9", "-u", isolated_user, "-f", "lsphp"], check=False)
            subprocess.run(["sudo", "rm", "-f", sock_path], check=False)
            subprocess.run(["sudo", "mkdir", "-p", "/tmp/lshttpd"], check=False)
            subprocess.run(["sudo", "chmod", "1777", "/tmp/lshttpd"], check=False)
            subprocess.run(["sudo", "/usr/local/lsws/bin/lswsctrl", "restart"], check=False)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Lỗi khi thực hiện {req.action}: {str(e)}")

    return {
        "status": "success",
        "message": f"Đã khởi động lại toàn bộ tiến trình LSPHP và nạp lại OpenLiteSpeed cho {clean_domain} thành công!"
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
        SELECT d.id, d.domain_name, d.webserver, d.user_id, u.parent_id
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
    
    # Thực hiện suspend / unsuspend Webserver VirtualHost & Linux isolated user
    isolated_u = domain_to_linux_user(domain["domain_name"])
    ws = domain["webserver"] or "ols"
    action = "suspend" if req.status == "suspended" else "unsuspend"

    # 1. Cấu hình VirtualHost chuyển sang trang tạm khóa / khôi phục vhost active
    try:
        res = await run_gatekeeper("vhost", action, isolated_u, domain["domain_name"], ws)
        if res.get("status") != "success":
            subprocess.run(["sudo", "/opt/wpc/scripts/vhost_manager.sh", action, isolated_u, domain["domain_name"], ws], check=False)
    except Exception:
        try:
            subprocess.run(["sudo", "/opt/wpc/scripts/vhost_manager.sh", action, isolated_u, domain["domain_name"], ws], check=False)
        except Exception:
            pass

    # 2. Khóa / Mở khóa Linux isolated user và dọn tiến trình chạy ngầm
    try:
        await run_gatekeeper("user", action, isolated_u)
    except Exception:
        flag = "-L" if action == "suspend" else "-U"
        subprocess.run(["sudo", "usermod", flag, isolated_u], check=False)
        
    if action == "suspend":
        subprocess.run(["sudo", "pkill", "-9", "-u", isolated_u], check=False)
        subprocess.run(["sudo", "rm", "-f", f"/tmp/lshttpd/lsphp_{isolated_u}.sock"], check=False)
        
    action_text = "tạm khóa (suspend)" if req.status == "suspended" else "mở khóa (unsuspend)"
    return {
        "status": "success",
        "domain_status": req.status,
        "message": f"Website '{domain['domain_name']}' đã được {action_text} thành công!"
    }


class VHostConfigRequest(BaseModel):
    content: str


class VHostSettingsUpdateRequest(BaseModel):
    php_version: Optional[str] = None
    aliases: Optional[str] = None
    adminEmail: Optional[str] = None
    indexFiles: Optional[str] = None
    enableGzip: Optional[bool] = None
    enableBr: Optional[bool] = None
    autoFixPerms: Optional[bool] = None
    logLevel: Optional[str] = None
    rollingSize: Optional[str] = None
    keepDays: Optional[int] = None
    secHeaders: Optional[bool] = None
    blockWpConfig: Optional[bool] = None
    blockUploadsPhp: Optional[bool] = None
    blockDotFiles: Optional[bool] = None
    blockXmlrpc: Optional[bool] = None
    maxConns: Optional[int] = None
    memSoftLimit: Optional[str] = None
    memHardLimit: Optional[str] = None
    procSoftLimit: Optional[int] = None
    procHardLimit: Optional[int] = None
    rewriteRules: Optional[str] = None
    wsUri: Optional[str] = None
    wsTarget: Optional[str] = None
    wsEnabled: Optional[bool] = None
    lscacheEnabled: Optional[bool] = None
    browserCacheEnabled: Optional[bool] = None


def parse_vhost_content(content: str, ws: str, domain_name: str) -> dict:
    settings = {
        "aliases": f"www.{domain_name}",
        "adminEmail": f"admin@{domain_name}",
        "indexFiles": "index.php, index.html, index.htm",
        "enableGzip": True,
        "enableBr": True,
        "autoFixPerms": True,
        "logLevel": "DEBUG",
        "rollingSize": "10M",
        "keepDays": 30,
        "secHeaders": True,
        "blockWpConfig": True,
        "blockUploadsPhp": True,
        "blockDotFiles": True,
        "blockXmlrpc": True,
        "maxConns": 10,
        "memSoftLimit": "1024M",
        "memHardLimit": "2048M",
        "procSoftLimit": 200,
        "procHardLimit": 300,
        "rewriteRules": """# 1. Cho phép SSL ACME challenge (Let's Encrypt / Certbot)
RewriteRule ^/\\.well-known/ - [L]

# 2. Chặn truy cập file ẩn, file cấu hình và VCS repository
RewriteRule ^/(\\.git|\\.env|\\.user\\.ini|\\.svn|composer\\.(json|lock)|package\\.json|\\.sql|\\.bak|\\.log) - [F,L]

# 3. Bảo vệ file cấu hình WordPress wp-config.php
RewriteRule ^/wp-config\\.php$ - [F,L]

# 4. Chống thực thi mã độc PHP trong thư mục uploads và cache
RewriteRule ^/(wp-content/uploads|cache)/.*\\.php$ - [F,L]

# 5. Chặn XML-RPC chống tấn công Brute-force & DDoS WordPress
RewriteRule ^/xmlrpc\\.php$ - [F,L]""",
        "wsUri": "/ws",
        "wsTarget": "127.0.0.1:3000",
        "wsEnabled": False,
        "lscacheEnabled": True,
        "browserCacheEnabled": True
    }
    if not content:
        return settings

    if ws == "ols":
        m_alias = re.search(r"vhAliases\s+([^\r\n]+)", content)
        if m_alias:
            settings["aliases"] = m_alias.group(1).strip()

        m_mail = re.search(r"adminEmails\s+([^\r\n]+)", content)
        if m_mail:
            settings["adminEmail"] = m_mail.group(1).strip()

        m_idx = re.search(r"indexFiles\s+([^\r\n]+)", content)
        if m_idx:
            settings["indexFiles"] = m_idx.group(1).strip()

        m_gzip = re.search(r"enableGzip\s+(\d+)", content)
        if m_gzip:
            settings["enableGzip"] = (m_gzip.group(1) == "1")

        m_br = re.search(r"enableBr\s+(\d+)", content)
        if m_br:
            settings["enableBr"] = (m_br.group(1) == "1")

        m_loglevel = re.search(r"logLevel\s+([A-Za-z]+)", content)
        if m_loglevel:
            settings["logLevel"] = m_loglevel.group(1).upper()

        m_roll = re.search(r"rollingSize\s+([0-9]+[A-Za-z]*)", content)
        if m_roll:
            settings["rollingSize"] = m_roll.group(1)

        m_days = re.search(r"keepDays\s+(\d+)", content)
        if m_days:
            try:
                settings["keepDays"] = int(m_days.group(1))
            except Exception:
                pass

        m_mc = re.search(r"maxConns\s+(\d+)", content)
        if m_mc:
            try:
                settings["maxConns"] = int(m_mc.group(1))
            except Exception:
                pass

        m_ms = re.search(r"memSoftLimit\s+([^\r\n]+)", content)
        if m_ms:
            settings["memSoftLimit"] = m_ms.group(1).strip()
        m_mh = re.search(r"memHardLimit\s+([^\r\n]+)", content)
        if m_mh:
            settings["memHardLimit"] = m_mh.group(1).strip()

        m_ps = re.search(r"procSoftLimit\s+(\d+)", content)
        if m_ps:
            try:
                settings["procSoftLimit"] = int(m_ps.group(1))
            except Exception:
                pass
        m_ph = re.search(r"procHardLimit\s+(\d+)", content)
        if m_ph:
            try:
                settings["procHardLimit"] = int(m_ph.group(1))
            except Exception:
                pass

        m_rw = re.search(r"rules\s+<<<END_rules([\s\S]*?)END_rules", content)
        if m_rw:
            settings["rewriteRules"] = m_rw.group(1).strip()

        settings["blockWpConfig"] = ("wp-config.php" in content)
        settings["blockUploadsPhp"] = ("uploads" in content and ".php" in content)
        settings["blockDotFiles"] = (".env" in content or ".git" in content)
        settings["blockXmlrpc"] = ("xmlrpc.php" in content)
        settings["secHeaders"] = ("X-Frame-Options" in content)

        m_ws = re.search(r"websocket\s+([^\s{]+)\s*\{[^}]*address\s+([^\r\n}]+)", content)
        if m_ws:
            settings["wsEnabled"] = True
            settings["wsUri"] = m_ws.group(1).strip()
            settings["wsTarget"] = m_ws.group(2).strip()

    else:
        m_srv = re.search(r"server_name\s+([^;]+);", content)
        if m_srv:
            names = m_srv.group(1).strip().split()
            aliases = [n for n in names if n != domain_name]
            settings["aliases"] = ", ".join(aliases) if aliases else ""

        m_idx = re.search(r"index\s+([^;]+);", content)
        if m_idx:
            settings["indexFiles"] = m_idx.group(1).strip()

        settings["blockWpConfig"] = ("wp-config.php" in content)
        settings["blockUploadsPhp"] = ("uploads" in content and ".php" in content)
        settings["blockDotFiles"] = (".env" in content or ".git" in content)
        settings["blockXmlrpc"] = ("xmlrpc.php" in content)
        settings["secHeaders"] = ("X-Frame-Options" in content)

        m_ws = re.search(r"location\s+([^\s{]+)\s*\{[^}]*proxy_pass\s+http://([^;]+);", content)
        if m_ws:
            settings["wsEnabled"] = True
            settings["wsUri"] = m_ws.group(1).strip()
            settings["wsTarget"] = m_ws.group(2).strip()

    return settings


def apply_vhost_settings(content: str, ws: str, domain_name: str, settings: VHostSettingsUpdateRequest, php_ver: str = "8.2") -> str:
    if not content:
        content = ""
    # Chuẩn hóa triệt để newline UNIX (LF) loại bỏ mọi ký tự \r gây hỏng heredoc OLS
    content = content.replace("\r\n", "\n").replace("\r", "\n")

    if ws == "ols":
        # 1. vhAliases
        if settings.aliases is not None:
            clean_al = settings.aliases.strip()
            if re.search(r"^vhAliases\s+.*$", content, flags=re.MULTILINE):
                content = re.sub(r"^vhAliases\s+.*$", f"vhAliases                 {clean_al}", content, flags=re.MULTILINE)
            elif re.search(r"^vhDomain\s+.*$", content, flags=re.MULTILINE):
                content = re.sub(r"^(vhDomain\s+.*)$", rf"\1\nvhAliases                 {clean_al}", content, flags=re.MULTILINE)

        # 2. adminEmails
        if settings.adminEmail is not None:
            clean_email = settings.adminEmail.strip()
            if re.search(r"^adminEmails\s+.*$", content, flags=re.MULTILINE):
                content = re.sub(r"^adminEmails\s+.*$", f"adminEmails               {clean_email}", content, flags=re.MULTILINE)
            elif re.search(r"^vhAliases\s+.*$", content, flags=re.MULTILINE):
                content = re.sub(r"^(vhAliases\s+.*)$", rf"\1\nadminEmails               {clean_email}", content, flags=re.MULTILINE)

        # 3. indexFiles (luôn đảm bảo có chỉ mục hợp lệ tránh triệt để lỗi 403 Directory Listing Denied)
        if settings.indexFiles is not None:
            idx = settings.indexFiles.strip() or "index.php, index.html, index.htm"
            if re.search(r"^indexFiles\s+.*$", content, flags=re.MULTILINE):
                content = re.sub(r"^indexFiles\s+.*$", f"indexFiles                {idx}", content, flags=re.MULTILINE)
            else:
                content = f"indexFiles                {idx}\n\n" + content
        elif not re.search(r"^indexFiles\s+", content, flags=re.MULTILINE):
            content = "indexFiles                index.php, index.html, index.htm\n\n" + content

        # Đảm bảo accessControl luôn có
        if not re.search(r"accessControl\s*\{", content):
            content = content + "\n\naccessControl {\n  allow                   *\n}\n"

        # 4. enableGzip
        if settings.enableGzip is not None:
            gz_val = "1" if settings.enableGzip else "0"
            if re.search(r"^enableGzip\s+.*$", content, flags=re.MULTILINE):
                content = re.sub(r"^enableGzip\s+.*$", f"enableGzip                {gz_val}", content, flags=re.MULTILINE)

        # 5. enableBr
        if settings.enableBr is not None:
            br_val = "1" if settings.enableBr else "0"
            if re.search(r"^enableBr\s+.*$", content, flags=re.MULTILINE):
                content = re.sub(r"^enableBr\s+.*$", f"enableBr                  {br_val}", content, flags=re.MULTILINE)

        # 6. Log level
        if settings.logLevel is not None:
            content = re.sub(r"(errorlog[^{]*\{[^}]*logLevel\s+)[A-Za-z]+", rf"\g<1>{settings.logLevel}", content)

        # 7. Rolling size & keepDays
        if settings.rollingSize is not None:
            content = re.sub(r"(rollingSize\s+)[0-9]+[A-Za-z]*", rf"\g<1>{settings.rollingSize}", content)
        if settings.keepDays is not None:
            content = re.sub(r"(keepDays\s+)[0-9]+", rf"\g<1>{settings.keepDays}", content)

        # 8. External App tuning
        if settings.maxConns is not None:
            content = re.sub(r"(maxConns\s+)[0-9]+", rf"\g<1>{settings.maxConns}", content)
            content = re.sub(r"((?:PHP_)?LSAPI_CHILDREN=)[0-9]+", rf"\g<1>{settings.maxConns}", content)
        if settings.memSoftLimit is not None:
            content = re.sub(r"(memSoftLimit\s+)[^\r\n]+", rf"\g<1>{settings.memSoftLimit}", content)
        if settings.memHardLimit is not None:
            content = re.sub(r"(memHardLimit\s+)[^\r\n]+", rf"\g<1>{settings.memHardLimit}", content)
        if settings.procSoftLimit is not None:
            content = re.sub(r"(procSoftLimit\s+)[0-9]+", rf"\g<1>{settings.procSoftLimit}", content)
        if settings.procHardLimit is not None:
            content = re.sub(r"(procHardLimit\s+)[0-9]+", rf"\g<1>{settings.procHardLimit}", content)

        # 9. Rewrite Rules
        if settings.rewriteRules is not None:
            clean_rw = settings.rewriteRules.replace("\r\n", "\n").replace("\r", "\n").strip()
            rw_block = f"rules                   <<<END_rules\n{clean_rw}\nEND_rules"
            if "END_rules" in content:
                content = re.sub(r"rules\s+<<<END_rules[\s\S]*?END_rules", lambda _: rw_block, content)

        # 10. WebSocket Proxy
        if settings.wsEnabled is True:
            uri = (settings.wsUri or "/ws").strip()
            tgt = (settings.wsTarget or "127.0.0.1:3000").strip()
            ws_code = f"\nwebsocket {uri} {{\n  address                 {tgt}\n}}\n"
            if re.search(r"websocket\s+[^\s{]+\s*\{[^}]*\}", content):
                content = re.sub(r"websocket\s+[^\s{]+\s*\{[^}]*\}", lambda _: ws_code.strip(), content)
            else:
                content = content + "\n" + ws_code
        elif settings.wsEnabled is False:
            content = re.sub(r"\n?websocket\s+[^\s{]+\s*\{[^}]*\}\n?", "\n", content)
            content = re.sub(r"\n?websocket\s+[^\s{]+\s*\{[^}]*\}\n?", "\n", content)

        # 11. BẢO VỆ CHỐNG SHELL METACHARACTERS & LỖI 403 DO PLACEHOLDER PHP
        # Bắt buộc thay thế mọi {{PHP_VERSION}} hoặc {{PHP_NUM}} còn sót lại bằng số phiên bản PHP cụ thể (ví dụ: 82)
        php_clean = str(php_ver or "8.2").strip()
        php_num = php_clean.replace(".", "")
        content = re.sub(r"^[ \t]*path[ \t]+/usr/local/lsws/lsphp[^\n\r]*[{][^\n\r]*/bin/lsphp", f"  path                    /usr/local/lsws/lsphp{php_num}/bin/lsphp", content, flags=re.MULTILINE)
        content = re.sub(r"/usr/local/lsws/lsphp\{\{[^}]+\}\}/bin/lsphp", f"/usr/local/lsws/lsphp{php_num}/bin/lsphp", content)
        content = content.replace("{{PHP_VERSION}}", php_num).replace("{{PHP_NUM}}", php_num)

    else:
        # Nginx
        if settings.aliases is not None:
            alias_list = [a.strip() for a in settings.aliases.replace(",", " ").split() if a.strip()]
            srv_names = [domain_name] + [a for a in alias_list if a != domain_name]
            content = re.sub(r"server_name\s+[^;]+;", f"server_name {' '.join(srv_names)};", content)

        if settings.indexFiles is not None:
            idx = " ".join(settings.indexFiles.replace(",", " ").split())
            content = re.sub(r"index\s+[^;]+;", f"index {idx};", content)

        if settings.wsEnabled is True:
            uri = (settings.wsUri or "/ws").strip()
            tgt = (settings.wsTarget or "127.0.0.1:3000").strip()
            ws_block = f"""
    location {uri} {{
        proxy_pass http://{tgt};
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
    }}"""
            if f"location {uri}" in content:
                content = re.sub(rf"location\s+{re.escape(uri)}\s*\{{[^}}]*\}}", lambda _: ws_block.strip(), content)
            else:
                last_brace = content.rfind("}")
                if last_brace != -1:
                    content = content[:last_brace] + ws_block + "\n}\n"
        elif settings.wsEnabled is False and settings.wsUri:
            content = re.sub(rf"\n?\s*location\s+{re.escape(settings.wsUri.strip())}\s*\{{[^}}]*\}}", "", content)

    return content


@router.get("/{domain_id}/vhost-config")
async def get_domain_vhost_config(domain_id: int, current_user: dict = Depends(get_current_user)):
    """Lấy nội dung file cấu hình VirtualHost (OpenLiteSpeed / Nginx) cho từng domain kèm settings đã parse"""
    db = await get_db_connection()
    cursor = await db.execute("""
        SELECT d.*, u.username as owner_username, u.parent_id
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
            raise HTTPException(status_code=403, detail="Không có quyền truy cập cấu hình website này")

    clean_domain = domain["domain_name"]
    ws = domain["webserver"] or "ols"
    isolated_user = domain_to_linux_user(clean_domain)
    doc_root = f"/home/{isolated_user}/domains/{clean_domain}/public_html"

    if ws == "ols":
        conf_path = f"/usr/local/lsws/conf/vhosts/{clean_domain}/vhconf.conf"
    else:
        conf_path = f"/etc/nginx/conf.d/{clean_domain}.conf"

    content = ""
    if os.path.exists(conf_path):
        try:
            with open(conf_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except PermissionError:
            try:
                proc = subprocess.run(["sudo", "cat", conf_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                if proc.returncode == 0:
                    content = proc.stdout
            except Exception:
                pass
        except Exception as e:
            content = f"# Lỗi đọc file cấu hình: {str(e)}"
    else:
        content = f"# Chưa tìm thấy file vhost cấu hình tại {conf_path}.\n# Bấm 'Tái Tạo VHost Từ Template' để tự động tạo lại."

    if content and ws == "ols":
        p_ver = str(domain["php_version"] or "8.2").strip()
        p_num = p_ver.replace(".", "")
        content = re.sub(r"^[ \t]*path[ \t]+/usr/local/lsws/lsphp[^\n\r]*[{][^\n\r]*/bin/lsphp", f"  path                    /usr/local/lsws/lsphp{p_num}/bin/lsphp", content, flags=re.MULTILINE)
        content = re.sub(r"/usr/local/lsws/lsphp\{\{[^}]+\}\}/bin/lsphp", f"/usr/local/lsws/lsphp{p_num}/bin/lsphp", content)
        content = content.replace("{{PHP_VERSION}}", p_num).replace("{{PHP_NUM}}", p_num)

    parsed_settings = parse_vhost_content(content, ws, clean_domain)

    return {
        "status": "success",
        "domain_id": domain_id,
        "domain_name": clean_domain,
        "webserver": ws,
        "php_version": domain["php_version"],
        "isolated_user": isolated_user,
        "doc_root": doc_root,
        "ssl_enabled": bool(domain["ssl_enabled"]),
        "config_path": conf_path,
        "content": content,
        "settings": parsed_settings
    }


@router.post("/{domain_id}/vhost-settings")
async def save_domain_vhost_settings(domain_id: int, req: VHostSettingsUpdateRequest, current_user: dict = Depends(get_current_user)):
    """Lưu các thiết lập VirtualHost theo từng Tab chức năng và nạp lại máy chủ web"""
    db = await get_db_connection()
    cursor = await db.execute("""
        SELECT d.*, u.username as owner_username, u.parent_id
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
            raise HTTPException(status_code=403, detail="Không có quyền chỉnh sửa cấu hình website này")

    clean_domain = domain["domain_name"]
    ws = domain["webserver"] or "ols"
    isolated_user = domain_to_linux_user(clean_domain)
    if ws == "ols":
        conf_path = f"/usr/local/lsws/conf/vhosts/{clean_domain}/vhconf.conf"
    else:
        conf_path = f"/etc/nginx/conf.d/{clean_domain}.conf"

    # Đọc cấu hình hiện tại
    content = ""
    if os.path.exists(conf_path):
        try:
            with open(conf_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except Exception:
            try:
                proc = subprocess.run(["sudo", "cat", conf_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                if proc.returncode == 0:
                    content = proc.stdout
            except Exception:
                pass

    php_ver = str(domain["php_version"] or "8.2").strip()
    if req.php_version and req.php_version in ALLOWED_PHP_VERSIONS and req.php_version != php_ver:
        db = await get_db_connection()
        await db.execute("UPDATE domains SET php_version = ? WHERE id = ?", (req.php_version, domain_id))
        await db.commit()
        await db.close()
        php_ver = req.php_version
    php_num = php_ver.replace(".", "")

    if not content:
        # Nếu chưa có file vhost, lấy từ template
        tpl_path = f"/opt/wpc/templates/vhost_ols/default.tpl" if ws == "ols" else f"/opt/wpc/templates/vhost_nginx/default.tpl"
        if os.path.exists(tpl_path):
            with open(tpl_path, "r", encoding="utf-8", errors="ignore") as tf:
                content = tf.read().replace("{{DOMAIN}}", clean_domain).replace("{{USERNAME}}", isolated_user).replace("{{PHP_VERSION}}", php_num).replace("{{PHP_NUM}}", php_num)
        else:
            content = f"# VirtualHost for {clean_domain}\n"

    new_content = apply_vhost_settings(content, ws, clean_domain, req, php_ver=php_ver)
    new_content = new_content.replace("\r\n", "\n").replace("\r", "\n")

    # Ghi file cấu hình an toàn
    conf_dir = os.path.dirname(conf_path)
    try:
        os.makedirs(conf_dir, exist_ok=True)
    except Exception:
        subprocess.run(["sudo", "mkdir", "-p", conf_dir], check=False)

    import tempfile
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, prefix=f"wpc_vh_{clean_domain}_", suffix=".tmp") as tf:
            tf.write(new_content)
            tmp_path = tf.name

        try:
            shutil.move(tmp_path, conf_path)
        except Exception:
            subprocess.run(["sudo", "mv", "-f", tmp_path, conf_path], check=False)

        try:
            os.chmod(conf_path, 0o644)
        except Exception:
            subprocess.run(["sudo", "chmod", "644", conf_path], check=False)

        # Gọi C SUID Gatekeeper để reload và chuẩn hóa phân quyền vhost
        try:
            res_gate = await run_gatekeeper("vhost", "reload-vhost", isolated_user, clean_domain, ws, php_ver)
            if res_gate.get("status") != "success":
                subprocess.run(["sudo", "/opt/wpc/scripts/vhost_manager.sh", "reload-vhost", isolated_user, clean_domain, ws, php_ver], check=False)
        except Exception:
            try:
                subprocess.run(["sudo", "/opt/wpc/scripts/vhost_manager.sh", "reload-vhost", isolated_user, clean_domain, ws, php_ver], check=False)
            except Exception:
                pass

        # Reload webserver fallback
        if ws == "ols":
            subprocess.run(["sudo", "chown", "-R", "lsadm:lsadm", conf_dir], check=False)
            subprocess.run(["sudo", "chmod", "755", conf_dir], check=False)
            subprocess.run(["sudo", "chmod", "644", conf_path], check=False)

            # Dừng tiến trình lsphp cũ và xóa socket để OpenLiteSpeed khởi tạo lại pool với thông số mới (LSAPI_CHILDREN)
            subprocess.run(["sudo", "pkill", "-9", "-u", isolated_user, "-f", "lsphp"], check=False)
            subprocess.run(["sudo", "rm", "-f", f"/tmp/lshttpd/lsphp_{isolated_user}.sock"], check=False)
            subprocess.run(["sudo", "mkdir", "-p", "/tmp/lshttpd"], check=False)
            subprocess.run(["sudo", "chmod", "1777", "/tmp/lshttpd"], check=False)

            # Chuẩn hóa phân quyền 0711 Shared Hosting cho toàn bộ cây thư mục website tránh triệt để lỗi 403
            subprocess.run(["sudo", "chmod", "711", "/home"], check=False)
            subprocess.run(["sudo", "chmod", "711", f"/home/{isolated_user}"], check=False)
            subprocess.run(["sudo", "chmod", "711", f"/home/{isolated_user}/domains"], check=False)
            subprocess.run(["sudo", "chmod", "711", f"/home/{isolated_user}/domains/{clean_domain}"], check=False)
            subprocess.run(["sudo", "chmod", "755", f"/home/{isolated_user}/domains/{clean_domain}/public_html"], check=False)
            subprocess.run(["sudo", "find", f"/home/{isolated_user}/domains/{clean_domain}/public_html", "-type", "d", "-exec", "chmod", "755", "{}", "+"], check=False)
            subprocess.run(["sudo", "find", f"/home/{isolated_user}/domains/{clean_domain}/public_html", "-type", "f", "-exec", "chmod", "644", "{}", "+"], check=False)

            subprocess.run(["sudo", "/usr/local/lsws/bin/lswsctrl", "restart"], check=False)
        else:
            subprocess.run(["sudo", "nginx", "-t"], check=False)
            subprocess.run(["sudo", "systemctl", "reload", "nginx"], check=False)

        updated_settings = parse_vhost_content(new_content, ws, clean_domain)
        return {
            "status": "success",
            "message": f"Đã lưu và cập nhật cấu hình VirtualHost cho {clean_domain} thành công!",
            "content": new_content,
            "settings": updated_settings
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Lỗi lưu file cấu hình VirtualHost: {str(e)}",
            "detail": str(e)
        }


@router.post("/{domain_id}/vhost-config")
async def save_domain_vhost_config(domain_id: int, req: VHostConfigRequest, current_user: dict = Depends(get_current_user)):
    """Lưu và nạp lại cấu hình VirtualHost cho domain (raw editor)"""
    db = await get_db_connection()
    cursor = await db.execute("""
        SELECT d.*, u.username as owner_username, u.parent_id
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
            raise HTTPException(status_code=403, detail="Không có quyền chỉnh sửa cấu hình website này")

    clean_domain = domain["domain_name"]
    ws = domain["webserver"] or "ols"
    isolated_user = domain_to_linux_user(clean_domain)
    if ws == "ols":
        conf_path = f"/usr/local/lsws/conf/vhosts/{clean_domain}/vhconf.conf"
    else:
        conf_path = f"/etc/nginx/conf.d/{clean_domain}.conf"

    conf_dir = os.path.dirname(conf_path)
    try:
        os.makedirs(conf_dir, exist_ok=True)
    except Exception:
        subprocess.run(["sudo", "mkdir", "-p", conf_dir], check=False)

    clean_content = req.content.replace("\r\n", "\n").replace("\r", "\n")
    php_ver = str(domain["php_version"] or "8.2").strip()

    # Tự động phát hiện nếu người dùng sửa phiên bản PHP trong raw config và lưu vào DB của panel
    if ws == "ols":
        m_php = re.search(r"path\s+/usr/local/lsws/lsphp([0-9]{2})/bin/lsphp", clean_content)
        if m_php:
            detected_num = m_php.group(1)
            detected_ver = f"{detected_num[0]}.{detected_num[1]}"
            if detected_ver in ALLOWED_PHP_VERSIONS and detected_ver != php_ver:
                db = await get_db_connection()
                await db.execute("UPDATE domains SET php_version = ? WHERE id = ?", (detected_ver, domain_id))
                await db.commit()
                await db.close()
                php_ver = detected_ver
    else:
        m_php = re.search(r"php([0-9]\.[0-9])-fpm", clean_content)
        if m_php:
            detected_ver = m_php.group(1)
            if detected_ver in ALLOWED_PHP_VERSIONS and detected_ver != php_ver:
                db = await get_db_connection()
                await db.execute("UPDATE domains SET php_version = ? WHERE id = ?", (detected_ver, domain_id))
                await db.commit()
                await db.close()
                php_ver = detected_ver

    php_num = php_ver.replace(".", "")
    if ws == "ols":
        clean_content = re.sub(r"^[ \t]*path[ \t]+/usr/local/lsws/lsphp[^\n\r]*[{][^\n\r]*/bin/lsphp", f"  path                    /usr/local/lsws/lsphp{php_num}/bin/lsphp", clean_content, flags=re.MULTILINE)
        clean_content = re.sub(r"/usr/local/lsws/lsphp\{\{[^}]+\}\}/bin/lsphp", f"/usr/local/lsws/lsphp{php_num}/bin/lsphp", clean_content)
        clean_content = clean_content.replace("{{PHP_VERSION}}", php_num).replace("{{PHP_NUM}}", php_num)

    import tempfile
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, prefix=f"wpc_vh_{clean_domain}_", suffix=".tmp") as tf:
            tf.write(clean_content)
            tmp_path = tf.name

        try:
            shutil.move(tmp_path, conf_path)
        except Exception:
            subprocess.run(["sudo", "mv", "-f", tmp_path, conf_path], check=False)

        try:
            os.chmod(conf_path, 0o644)
        except Exception:
            subprocess.run(["sudo", "chmod", "644", conf_path], check=False)

        # Gọi C SUID Gatekeeper để reload và chuẩn hóa phân quyền vhost
        try:
            res_gate = await run_gatekeeper("vhost", "reload-vhost", isolated_user, clean_domain, ws, php_ver)
            if res_gate.get("status") != "success":
                subprocess.run(["sudo", "/opt/wpc/scripts/vhost_manager.sh", "reload-vhost", isolated_user, clean_domain, ws, php_ver], check=False)
        except Exception:
            try:
                subprocess.run(["sudo", "/opt/wpc/scripts/vhost_manager.sh", "reload-vhost", isolated_user, clean_domain, ws, php_ver], check=False)
            except Exception:
                pass

        # Reload webserver fallback
        if ws == "ols":
            subprocess.run(["sudo", "chown", "-R", "lsadm:lsadm", conf_dir], check=False)
            subprocess.run(["sudo", "chmod", "755", conf_dir], check=False)
            subprocess.run(["sudo", "chmod", "644", conf_path], check=False)
            subprocess.run(["sudo", "pkill", "-9", "-u", isolated_user, "-f", "lsphp"], check=False)
            subprocess.run(["sudo", "rm", "-f", f"/tmp/lshttpd/lsphp_{isolated_user}.sock"], check=False)
            subprocess.run(["sudo", "mkdir", "-p", "/tmp/lshttpd"], check=False)
            subprocess.run(["sudo", "chmod", "1777", "/tmp/lshttpd"], check=False)

            subprocess.run(["sudo", "chmod", "711", "/home"], check=False)
            subprocess.run(["sudo", "chmod", "711", f"/home/{isolated_user}"], check=False)
            subprocess.run(["sudo", "chmod", "711", f"/home/{isolated_user}/domains"], check=False)
            subprocess.run(["sudo", "chmod", "711", f"/home/{isolated_user}/domains/{clean_domain}"], check=False)
            subprocess.run(["sudo", "chmod", "755", f"/home/{isolated_user}/domains/{clean_domain}/public_html"], check=False)
            subprocess.run(["sudo", "find", f"/home/{isolated_user}/domains/{clean_domain}/public_html", "-type", "d", "-exec", "chmod", "755", "{}", "+"], check=False)
            subprocess.run(["sudo", "find", f"/home/{isolated_user}/domains/{clean_domain}/public_html", "-type", "f", "-exec", "chmod", "644", "{}", "+"], check=False)

            subprocess.run(["sudo", "/usr/local/lsws/bin/lswsctrl", "restart"], check=False)
        else:
            subprocess.run(["sudo", "nginx", "-t"], check=False)
            subprocess.run(["sudo", "systemctl", "reload", "nginx"], check=False)

        updated_settings = parse_vhost_content(clean_content, ws, clean_domain)
        return {
            "status": "success",
            "message": f"Đã lưu và nạp lại cấu hình VirtualHost cho {clean_domain}!",
            "settings": updated_settings
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Lỗi lưu file cấu hình: {str(e)}",
            "detail": str(e)
        }


@router.post("/{domain_id}/vhost-rebuild")
async def rebuild_domain_vhost(domain_id: int, current_user: dict = Depends(get_current_user)):
    """Tái tạo VirtualHost từ template chuẩn của OpenLiteSpeed / Nginx"""
    db = await get_db_connection()
    cursor = await db.execute("""
        SELECT d.*, u.username as owner_username, u.parent_id
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
            raise HTTPException(status_code=403, detail="Không có quyền tái tạo website này")

    clean_domain = domain["domain_name"]
    ws = domain["webserver"] or "ols"
    isolated_user = domain_to_linux_user(clean_domain)
    hosting_username = domain["owner_username"]

    res = await run_gatekeeper("vhost", "add", isolated_user, clean_domain, ws, domain["php_version"], hosting_username)
    return {"status": "success", "message": f"Đã tái tạo cấu hình VirtualHost cho {clean_domain} từ template chuẩn!"}



