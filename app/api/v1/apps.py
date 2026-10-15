import os
import shutil
import subprocess
import asyncio
from typing import List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel
from app.api.deps import get_current_user

router = APIRouter(prefix="/apps", tags=["App Store & Software Manager"])

class AppActionRequest(BaseModel):
    app_id: str
    action: str  # install | restart | stop | start | uninstall

# Định nghĩa các ứng dụng hỗ trợ trong App Store
SUPPORTED_APPS = [
    {
        "id": "ols",
        "name": "OpenLiteSpeed",
        "category": "webserver",
        "icon": "⚡",
        "desc": "Web Server siêu tốc với Native LSCache tối ưu hóa đặc biệt cho WordPress.",
        "service": "lshttpd",
        "binary_checks": ["/usr/local/lsws/bin/lswsctrl", "/usr/local/lsws/bin/openlitespeed"],
        "default_version": "1.8.x"
    },
    {
        "id": "nginx",
        "name": "Nginx Web Server",
        "category": "webserver",
        "icon": "🌐",
        "desc": "Máy chủ Web & Reverse Proxy tin cậy, hiệu năng cao và phổ biến hàng đầu.",
        "service": "nginx",
        "binary_checks": ["/usr/sbin/nginx", "/usr/bin/nginx"],
        "default_version": "1.24.x"
    },
    {
        "id": "mariadb",
        "name": "MariaDB Database",
        "category": "database",
        "icon": "🐬",
        "desc": "Hệ quản trị cơ sở dữ liệu quan hệ mã nguồn mở thay thế hoàn hảo cho MySQL.",
        "service": "mariadb",
        "binary_checks": ["/usr/bin/mariadb", "/usr/sbin/mariadbd", "/usr/bin/mysql"],
        "default_version": "10.11"
    },
    {
        "id": "mysql",
        "name": "MySQL Server",
        "category": "database",
        "icon": "🗄️",
        "desc": "Hệ quản trị CSDL MySQL chính thức của Oracle Corporation.",
        "service": "mysql",
        "binary_checks": ["/usr/sbin/mysqld", "/usr/bin/mysql"],
        "default_version": "8.0"
    },
    {
        "id": "redis",
        "name": "Redis Object Cache",
        "category": "database",
        "icon": "🔴",
        "desc": "Bộ nhớ đệm trong RAM tốc độ cao, hỗ trợ Redis Object Cache cho WordPress.",
        "service": "redis-server",
        "binary_checks": ["/usr/bin/redis-server", "/usr/local/bin/redis-server"],
        "default_version": "7.0"
    },
    {
        "id": "php82",
        "name": "PHP 8.2 Runtime",
        "category": "php",
        "icon": "🐘",
        "desc": "Phiên bản PHP tối ưu, khuyên dùng cho các phiên bản WordPress hiện hành.",
        "service": "php8.2-fpm",
        "binary_checks": ["/usr/bin/php8.2", "/usr/local/lsws/lsphp82/bin/php"],
        "default_version": "8.2"
    },
    {
        "id": "php83",
        "name": "PHP 8.3 Runtime",
        "category": "php",
        "icon": "🐘",
        "desc": "Phiên bản PHP hiện đại nhất, bổ sung Typed Class Constants và hiệu năng cao.",
        "service": "php8.3-fpm",
        "binary_checks": ["/usr/bin/php8.3", "/usr/local/lsws/lsphp83/bin/php"],
        "default_version": "8.3"
    },
    {
        "id": "php81",
        "name": "PHP 8.1 Runtime",
        "category": "php",
        "icon": "🐘",
        "desc": "Hỗ trợ Enums, Readonly Properties và tương thích sâu với hầu hết theme/plugin.",
        "service": "php8.1-fpm",
        "binary_checks": ["/usr/bin/php8.1", "/usr/local/lsws/lsphp81/bin/php"],
        "default_version": "8.1"
    },
    {
        "id": "php74",
        "name": "PHP 7.4 Runtime",
        "category": "php",
        "icon": "🐘",
        "desc": "Phiên bản dành riêng cho các website cũ chưa kịp nâng cấp tương thích PHP 8.x.",
        "service": "php7.4-fpm",
        "binary_checks": ["/usr/bin/php7.4", "/usr/local/lsws/lsphp74/bin/php"],
        "default_version": "7.4"
    },
    {
        "id": "phpmyadmin",
        "name": "phpMyAdmin GUI",
        "category": "tools",
        "icon": "🧭",
        "desc": "Giao diện Web trực quan quản lý CSDL MariaDB/MySQL nhanh chóng và an toàn.",
        "service": "",
        "binary_checks": ["/usr/share/phpmyadmin/index.php", "/opt/presspanel/apps/phpmyadmin/index.php"],
        "default_version": "5.2.x"
    },
    {
        "id": "wp-cli",
        "name": "WP-CLI Core",
        "category": "tools",
        "icon": "🚀",
        "desc": "Công cụ dòng lệnh chính thức để quản lý, cấu hình và bảo mật WordPress không cần trình duyệt.",
        "service": "",
        "binary_checks": ["/usr/local/bin/wp", "/usr/bin/wp"],
        "default_version": "2.10.x"
    },
    {
        "id": "certbot",
        "name": "Certbot Let's Encrypt",
        "category": "tools",
        "icon": "🔒",
        "desc": "Bộ công cụ tự động gia hạn và cấp chứng chỉ Let's Encrypt SSL miễn phí.",
        "service": "certbot.timer",
        "binary_checks": ["/usr/bin/certbot", "/snap/bin/certbot"],
        "default_version": "latest"
    },
    {
        "id": "pure-ftpd",
        "name": "Pure-FTPd Server",
        "category": "tools",
        "icon": "📁",
        "desc": "Máy chủ FTP an toàn, phân quyền nghiêm ngặt tới từng thư mục người dùng.",
        "service": "pure-ftpd",
        "binary_checks": ["/usr/sbin/pure-ftpd"],
        "default_version": "latest"
    },
    {
        "id": "fail2ban",
        "name": "Fail2ban Security",
        "category": "tools",
        "icon": "🛡️",
        "desc": "Hệ thống bảo vệ máy chủ tự động chặn IP khi phát hiện dò mật khẩu SSH/Panel.",
        "service": "fail2ban",
        "binary_checks": ["/usr/bin/fail2ban-server"],
        "default_version": "latest"
    }
]

def check_app_status(app: Dict[str, Any]) -> Dict[str, Any]:
    """Kiểm tra thực tế nhị phân và service trên Linux"""
    installed = False
    running = False

    # 1. Kiểm tra binary
    for b in app["binary_checks"]:
        if os.path.exists(b) or shutil.which(b):
            installed = True
            break

    # 2. Kiểm tra service nếu app có service đi kèm
    if app["service"] and os.name != "nt":
        # Check systemctl is-active
        try:
            res = subprocess.run(
                ["systemctl", "is-active", "--quiet", app["service"]],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            running = (res.returncode == 0)
            if running:
                installed = True
        except Exception:
            pass
    elif installed:
        running = True

    return {
        **app,
        "installed": installed,
        "running": running
    }

@router.get("/list")
async def get_apps_list(current_user: dict = Depends(get_current_user)):
    """Trả về danh sách ứng dụng kèm trạng thái cài đặt thực tế"""
    apps_status = [check_app_status(app) for app in SUPPORTED_APPS]
    return {
        "apps": apps_status,
        "counts": {
            "all": len(apps_status),
            "installed": sum(1 for a in apps_status if a["installed"]),
            "running": sum(1 for a in apps_status if a["running"])
        }
    }

async def run_install_task(app_id: str):
    """Tiến trình cài đặt app chạy ngầm"""
    script_path = f"/opt/presspanel/scripts/apps/{app_id}.sh"
    if os.path.exists(script_path):
        proc = await asyncio.create_subprocess_exec(
            "bash", script_path, "install",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await proc.communicate()
    else:
        # Fallback cài đặt qua apt trực tiếp
        pkg_map = {
            "nginx": "nginx",
            "mariadb": "mariadb-server mariadb-client",
            "redis": "redis-server",
            "certbot": "certbot python3-certbot-nginx",
            "fail2ban": "fail2ban",
            "pure-ftpd": "pure-ftpd"
        }
        if app_id in pkg_map:
            pkg = pkg_map[app_id]
            cmd = f"DEBIAN_FRONTEND=noninteractive apt-get update && apt-get install -y {pkg}"
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await proc.communicate()

from app.api.v1.tasks import add_system_task

@router.post("/action")
async def app_action(
    req: AppActionRequest,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    """Thực thi Cài đặt / Restart / Stop / Start ứng dụng"""
    app = next((a for a in SUPPORTED_APPS if a["id"] == req.app_id), None)
    if not app:
        raise HTTPException(status_code=404, detail="Ứng dụng không tồn tại trong danh mục")

    if req.action == "install":
        script_path = f"/opt/presspanel/scripts/apps/{req.app_id}.sh"
        if os.path.exists(script_path):
            cmd = f"sudo bash {script_path} install"
        else:
            pkg_map = {
                "nginx": "nginx",
                "mariadb": "mariadb-server mariadb-client",
                "redis": "redis-server",
                "certbot": "certbot python3-certbot-nginx",
                "fail2ban": "fail2ban",
                "pure-ftpd": "pure-ftpd"
            }
            pkg = pkg_map.get(req.app_id, req.app_id)
            cmd = f"DEBIAN_FRONTEND=noninteractive apt-get update && apt-get install -y {pkg}"

        task_id = await add_system_task(
            user_id=current_user["id"],
            action_name=f"Install [{app['name']}]",
            cmd=cmd,
            background_tasks=background_tasks
        )
        return {
            "status": "ok",
            "task_id": task_id,
            "message": f"Đã thêm tác vụ cài đặt {app['name']} vào danh sách tiến trình chạy ngầm (Task list)."
        }
    
    if req.action in ["start", "stop", "restart"]:
        if not app["service"]:
            raise HTTPException(status_code=400, detail="Ứng dụng này không chạy dạng dịch vụ daemon.")
        try:
            res = subprocess.run(
                ["systemctl", req.action, app["service"]],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            if res.returncode == 0:
                return {"status": "ok", "message": f"Đã thực hiện {req.action} dịch vụ {app['name']} thành công!"}
            else:
                raise HTTPException(status_code=500, detail=f"Lỗi: {res.stderr.decode('utf-8')}")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Lỗi thao tác service: {str(e)}")

    raise HTTPException(status_code=400, detail="Hành động không hợp lệ.")
