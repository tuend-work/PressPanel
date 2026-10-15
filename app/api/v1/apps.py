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
    version: str = "default"

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
        "default_version": "1.8.x",
        "versions": [
            {"value": "1.8.x", "label": "OpenLiteSpeed 1.8.x (Khuyên dùng)"},
            {"value": "1.7.x", "label": "OpenLiteSpeed 1.7.x (Legacy)"}
        ]
    },
    {
        "id": "nginx",
        "name": "Nginx Web Server",
        "category": "webserver",
        "icon": "🌐",
        "desc": "Máy chủ Web & Reverse Proxy tin cậy, hiệu năng cao và phổ biến hàng đầu.",
        "service": "nginx",
        "binary_checks": ["/usr/sbin/nginx", "/usr/bin/nginx"],
        "default_version": "1.24.x",
        "versions": [
            {"value": "1.24.x", "label": "Nginx 1.24.x (Ổn định)"},
            {"value": "latest", "label": "Nginx Mainline (Mới nhất)"}
        ]
    },
    {
        "id": "mysql",
        "name": "MySQL / MariaDB Database",
        "category": "database",
        "icon": "🐬",
        "desc": "Hệ quản trị CSDL quan hệ tốc độ cao, hỗ trợ tùy chọn cài MariaDB hoặc Oracle MySQL.",
        "service": "mariadb",
        "binary_checks": ["/usr/bin/mariadb", "/usr/sbin/mariadbd", "/usr/sbin/mysqld", "/usr/bin/mysql"],
        "default_version": "mariadb-10.11",
        "versions": [
            {"value": "mariadb-10.11", "label": "MariaDB 10.11 LTS (Khuyên dùng, nhanh & nhẹ)"},
            {"value": "mariadb-10.6", "label": "MariaDB 10.6 LTS (Cũ, tương thích cao)"},
            {"value": "mysql-8.0", "label": "Oracle MySQL 8.0 (Chính thức)"}
        ]
    },
    {
        "id": "redis",
        "name": "Redis Object Cache",
        "category": "database",
        "icon": "🔴",
        "desc": "Bộ nhớ đệm trong RAM tốc độ cao, hỗ trợ Redis Object Cache cho WordPress.",
        "service": "redis-server",
        "binary_checks": ["/usr/bin/redis-server", "/usr/local/bin/redis-server"],
        "default_version": "7.0",
        "versions": [
            {"value": "7.0", "label": "Redis 7.0"},
            {"value": "6.2", "label": "Redis 6.2"}
        ]
    },

    {
        "id": "php85",
        "name": "PHP 8.5 Runtime",
        "category": "php",
        "icon": "🐘",
        "desc": "Phiên bản PHP 8.5 thử nghiệm mới nhất của PHP Core Team.",
        "service": "php8.5-fpm",
        "binary_checks": ["/usr/bin/php8.5", "/usr/local/lsws/lsphp85/bin/php"],
        "default_version": "8.5"
    },
    {
        "id": "php84",
        "name": "PHP 8.4 Runtime",
        "category": "php",
        "icon": "🐘",
        "desc": "Phiên bản PHP 8.4 hiện đại nhất, hỗ trợ Property Hooks, Asymmetric Visibility.",
        "service": "php8.4-fpm",
        "binary_checks": ["/usr/bin/php8.4", "/usr/local/lsws/lsphp84/bin/php"],
        "default_version": "8.4"
    },
    {
        "id": "php83",
        "name": "PHP 8.3 Runtime",
        "category": "php",
        "icon": "🐘",
        "desc": "Phiên bản PHP 8.3 hiệu năng cao, bổ sung Typed Class Constants và json_validate.",
        "service": "php8.3-fpm",
        "binary_checks": ["/usr/bin/php8.3", "/usr/local/lsws/lsphp83/bin/php"],
        "default_version": "8.3"
    },
    {
        "id": "php82",
        "name": "PHP 8.2 Runtime",
        "category": "php",
        "icon": "🐘",
        "desc": "Phiên bản PHP 8.2 ổn định cao, khuyên dùng cho hầu hết website WordPress.",
        "service": "php8.2-fpm",
        "binary_checks": ["/usr/bin/php8.2", "/usr/local/lsws/lsphp82/bin/php"],
        "default_version": "8.2"
    },
    {
        "id": "php81",
        "name": "PHP 8.1 Runtime",
        "category": "php",
        "icon": "🐘",
        "desc": "PHP 8.1 hỗ trợ Enums, Readonly Properties, First-class Callables.",
        "service": "php8.1-fpm",
        "binary_checks": ["/usr/bin/php8.1", "/usr/local/lsws/lsphp81/bin/php"],
        "default_version": "8.1"
    },
    {
        "id": "php80",
        "name": "PHP 8.0 Runtime",
        "category": "php",
        "icon": "🐘",
        "desc": "PHP 8.0 giới thiệu JIT Compiler, Named Arguments và Union Types.",
        "service": "php8.0-fpm",
        "binary_checks": ["/usr/bin/php8.0", "/usr/local/lsws/lsphp80/bin/php"],
        "default_version": "8.0"
    },
    {
        "id": "php74",
        "name": "PHP 7.4 Runtime",
        "category": "php",
        "icon": "🐘",
        "desc": "Phiên bản PHP 7.4 Legacy ổn định nhất thế hệ PHP 7.x.",
        "service": "php7.4-fpm",
        "binary_checks": ["/usr/bin/php7.4", "/usr/local/lsws/lsphp74/bin/php"],
        "default_version": "7.4"
    },
    {
        "id": "php73",
        "name": "PHP 7.3 Runtime",
        "category": "php",
        "icon": "🐘",
        "desc": "PHP 7.3 dành cho các dự án và theme WordPress cũ.",
        "service": "php7.3-fpm",
        "binary_checks": ["/usr/bin/php7.3", "/usr/local/lsws/lsphp73/bin/php"],
        "default_version": "7.3"
    },
    {
        "id": "php72",
        "name": "PHP 7.2 Runtime",
        "category": "php",
        "icon": "🐘",
        "desc": "PHP 7.2 hỗ trợ mã nguồn WordPress 4.x - 5.x.",
        "service": "php7.2-fpm",
        "binary_checks": ["/usr/bin/php7.2", "/usr/local/lsws/lsphp72/bin/php"],
        "default_version": "7.2"
    },
    {
        "id": "php71",
        "name": "PHP 7.1 Runtime",
        "category": "php",
        "icon": "🐘",
        "desc": "PHP 7.1 hỗ trợ tương thích mã nguồn cũ.",
        "service": "php7.1-fpm",
        "binary_checks": ["/usr/bin/php7.1", "/usr/local/lsws/lsphp71/bin/php"],
        "default_version": "7.1"
    },
    {
        "id": "php70",
        "name": "PHP 7.0 Runtime",
        "category": "php",
        "icon": "🐘",
        "desc": "PHP 7.0 phiên bản nâng cấp tốc độ đột phá so với PHP 5.6.",
        "service": "php7.0-fpm",
        "binary_checks": ["/usr/bin/php7.0", "/usr/local/lsws/lsphp70/bin/php"],
        "default_version": "7.0"
    },
    {
        "id": "php56",
        "name": "PHP 5.6 Runtime",
        "category": "php",
        "icon": "🐘",
        "desc": "PHP 5.6 Legacy dành cho các website cổ, tương thích cao với script cũ.",
        "service": "php5.6-fpm",
        "binary_checks": ["/usr/bin/php5.6", "/usr/local/lsws/lsphp56/bin/php"],
        "default_version": "5.6"
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

class ConfigUpdateRequest(BaseModel):
    app_id: str
    content: str
    file_type: str = "main"

class AutorestartRequest(BaseModel):
    app_id: str
    enabled: bool

@router.post("/action")
async def app_action(
    req: AppActionRequest,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    """Thực thi Cài đặt / Restart / Stop / Start / Gỡ bỏ ứng dụng"""
    app = next((a for a in SUPPORTED_APPS if a["id"] == req.app_id), None)
    if not app:
        raise HTTPException(status_code=404, detail="Ứng dụng không tồn tại trong danh mục")

    gatekeeper_bin = "/opt/presspanel/bin/app"

    # 1. HÀNH ĐỘNG CÀI ĐẶT (INSTALL)
    if req.action == "install":
        script_path = f"/opt/presspanel/scripts/apps/{req.app_id}.sh"
        version_param = req.version if req.version and req.version != "default" else app.get("default_version", "latest")
        
        if os.path.isfile(gatekeeper_bin):
            cmd = f"{gatekeeper_bin} install {req.app_id} {version_param}"
        elif os.path.exists(script_path):
            cmd = f"sudo bash {script_path} install {version_param}"
        else:
            pkg_map = {
                "nginx": "nginx",
                "mysql": "mariadb-server mariadb-client",
                "mariadb": "mariadb-server mariadb-client",
                "redis": "redis-server",
                "certbot": "certbot python3-certbot-nginx",
                "fail2ban": "fail2ban",
                "pure-ftpd": "pure-ftpd"
            }
            pkg = pkg_map.get(req.app_id, req.app_id)
            cmd = f"sudo DEBIAN_FRONTEND=noninteractive apt-get update && sudo apt-get install -y {pkg}"

        task_id = await add_system_task(
            user_id=current_user["id"],
            action_name=f"Install [{app['name']} - {version_param}]",
            cmd=cmd,
            background_tasks=background_tasks
        )

        return {
            "status": "ok",
            "task_id": task_id,
            "message": f"Đã thêm tác vụ cài đặt {app['name']} vào danh sách tiến trình chạy ngầm (Task list)."
        }

    # 2. HÀNH ĐỘNG GỠ CÀI ĐẶT (UNINSTALL / REMOVE)
    if req.action in ["uninstall", "remove"]:
        script_path = f"/opt/presspanel/scripts/apps/{req.app_id}.sh"
        if os.path.isfile(gatekeeper_bin):
            cmd = f"{gatekeeper_bin} remove {req.app_id}"
        elif os.path.exists(script_path):
            cmd = f"sudo bash {script_path} remove"
        else:
            pkg_map = {
                "nginx": "nginx",
                "mysql": "mariadb-server mariadb-client",
                "mariadb": "mariadb-server mariadb-client",
                "redis": "redis-server",
                "pure-ftpd": "pure-ftpd",
                "fail2ban": "fail2ban"
            }
            pkg = pkg_map.get(req.app_id, req.app_id)
            cmd = f"sudo apt-get remove -y {pkg}"

        task_id = await add_system_task(
            user_id=current_user["id"],
            action_name=f"Uninstall [{app['name']}]",
            cmd=cmd,
            background_tasks=background_tasks
        )

        return {
            "status": "ok",
            "task_id": task_id,
            "message": f"Đã thêm tác vụ gỡ cài đặt {app['name']} vào danh sách tiến trình chạy ngầm (Task list)."
        }

    # 3. HÀNH ĐỘNG ĐIỀU KHIỂN DỊCH VỤ (START / STOP / RESTART / RELOAD)
    if req.action in ["start", "stop", "restart", "reload"]:
        if not app.get("service"):
            raise HTTPException(status_code=400, detail="Ứng dụng này không chạy dạng dịch vụ daemon.")
        
        srv = app["service"]
        # Ưu tiên thực thi qua C Gatekeeper SUID (tránh lỗi Polkit / Interactive authentication required)
        if os.path.isfile(gatekeeper_bin):
            proc = subprocess.run(
                [gatekeeper_bin, "service", req.action, srv],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            if proc.returncode == 0:
                return {"status": "ok", "message": f"Đã {req.action} dịch vụ {app['name']} thành công!"}
            else:
                raise HTTPException(status_code=500, detail=f"Lỗi: {proc.stderr or proc.stdout}")

        # Fallback sudo nếu chưa compile C Gatekeeper
        try:
            res = subprocess.run(
                ["sudo", "systemctl", req.action, srv],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            if res.returncode == 0:
                return {"status": "ok", "message": f"Đã {req.action} dịch vụ {app['name']} thành công!"}
            else:
                # Thử lại bằng systemctl trực tiếp
                res2 = subprocess.run(
                    ["systemctl", req.action, srv],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True
                )
                if res2.returncode == 0:
                    return {"status": "ok", "message": f"Đã {req.action} dịch vụ {app['name']} thành công!"}
                raise HTTPException(status_code=500, detail=f"Lỗi: {res.stderr or res2.stderr}")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Lỗi thao tác service: {str(e)}")

    raise HTTPException(status_code=400, detail="Hành động không hợp lệ.")

# ==========================================
# CẤU HÌNH POPUP SETTINGS & TỰ KHỞI ĐỘNG LẠI (AUTORESTART)
# ==========================================
APP_CONFIG_PATHS = {
    "nginx": "/etc/nginx/nginx.conf",
    "ols": "/usr/local/lsws/conf/httpd_config.conf",
    "mysql": "/etc/mysql/my.cnf",
    "mariadb": "/etc/mysql/mariadb.conf.d/50-server.cnf",
    "redis": "/etc/redis/redis.conf",
    "pure-ftpd": "/etc/pure-ftpd/pure-ftpd.conf",
    "fail2ban": "/etc/fail2ban/jail.local"
}

@router.get("/config")
async def get_app_config(app_id: str, file_type: str = "main", current_user: dict = Depends(get_current_user)):
    """Đọc file cấu hình thực tế của ứng dụng để hiển thị trong Popup Settings (hỗ trợ cả php.ini và pool www.conf)"""
    lookup_id = f"{app_id}_pool" if file_type == "pool" and app_id.startswith("php") else app_id
    conf_path = APP_CONFIG_PATHS.get(lookup_id)
    
    # Lấy phiên bản PHP nếu là app PHP (vd: php84 -> 8.4, php56 -> 5.6)
    php_ver = None
    if app_id.startswith("php") and len(app_id) >= 5:
        php_ver = f"{app_id[3]}.{app_id[4]}"
        if file_type == "pool":
            conf_path = f"/etc/php/{php_ver}/fpm/pool.d/www.conf"
        else:
            conf_path = f"/etc/php/{php_ver}/fpm/php.ini"

    alternatives = {
        "ols": ["/usr/local/lsws/conf/httpd_config.conf", "/usr/local/lsws/conf/httpd_config.xml"],
        "mysql": ["/etc/mysql/my.cnf", "/etc/mysql/mysql.conf.d/mysqld.cnf", "/etc/my.cnf"],
        "mariadb": ["/etc/mysql/mariadb.conf.d/50-server.cnf", "/etc/mysql/my.cnf"],
        "fail2ban": ["/etc/fail2ban/jail.local", "/etc/fail2ban/jail.conf"]
    }
    if php_ver:
        php_num = php_ver.replace(".", "")
        if file_type == "pool":
            alternatives[lookup_id] = [
                f"/etc/php/{php_ver}/fpm/pool.d/www.conf",
                f"/etc/php-fpm.d/www.conf"
            ]
        else:
            alternatives[lookup_id] = [
                f"/etc/php/{php_ver}/fpm/php.ini",
                f"/usr/local/lsws/lsphp{php_num}/etc/php/{php_ver}/litespeed/php.ini",
                f"/etc/php.ini"
            ]

    # Thử tìm file thực tế tồn tại trên hệ thống
    found_path = None
    paths_to_check = [conf_path] + alternatives.get(lookup_id, []) if conf_path else alternatives.get(lookup_id, [])
    
    # 1. Kiểm tra tồn tại bằng gatekeeper hoặc test -f (tránh permission error do thư mục cha 0700)
    for p in paths_to_check:
        if not p:
            continue
        if os.path.exists(p):
            found_path = p
            break
        # Nếu cha thư mục bị chặn quyền (như /usr/local/lsws/conf/), kiểm tra qua subprocess
        if os.name != "nt":
            try:
                check_ret = subprocess.run(["test", "-f", p], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if check_ret.returncode == 0:
                    found_path = p
                    break
            except Exception:
                pass

    if not found_path and conf_path:
        found_path = conf_path

    content = ""
    gatekeeper_bin = "/opt/presspanel/bin/app"

    # Đọc nội dung: Ưu tiên dùng gatekeeper app config-read nếu chạy trên linux
    if found_path:
        # Thử 1: Gatekeeper SUID
        if os.path.isfile(gatekeeper_bin):
            try:
                proc = subprocess.run([gatekeeper_bin, "config-read", found_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                if proc.returncode == 0 and proc.stdout:
                    content = proc.stdout
            except Exception:
                pass

        # Thử 2: Đọc trực tiếp nếu có quyền
        if not content:
            try:
                with open(found_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
            except PermissionError:
                # Thử qua sudo cat nếu permission error
                try:
                    proc = subprocess.run(["sudo", "cat", found_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    if proc.returncode == 0:
                        content = proc.stdout
                except Exception:
                    pass
            except Exception as e:
                content = f"# Lỗi đọc file cấu hình: {str(e)}"

    if not content:
        content = f"# Chưa tìm thấy file cấu hình cho ứng dụng {app_id}.\n# Đường dẫn dự kiến: {found_path or 'Mặc định hệ thống'}"

    # Kiểm tra trạng thái tự khởi động lại (systemctl is-enabled)
    app = next((a for a in SUPPORTED_APPS if a["id"] == app_id), None)
    autorestart = False
    if app and app.get("service") and os.name != "nt":
        try:
            check_enabled = subprocess.run(
                ["systemctl", "is-enabled", "--quiet", app["service"]],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            autorestart = (check_enabled.returncode == 0)
        except Exception:
            pass

    return {
        "status": "ok",
        "app_id": app_id,
        "config_path": found_path or "Chưa xác định",
        "content": content,
        "autorestart": autorestart
    }

@router.post("/config")
async def save_app_config(req: ConfigUpdateRequest, current_user: dict = Depends(get_current_user)):
    """Lưu chỉnh sửa file cấu hình từ Popup Settings"""
    lookup_id = f"{req.app_id}_pool" if req.file_type == "pool" and req.app_id.startswith("php") else req.app_id
    conf_path = APP_CONFIG_PATHS.get(lookup_id)
    
    # Xác định đường dẫn cấu hình PHP động cho mọi phiên bản (5.6 - 8.5)
    if req.app_id.startswith("php") and len(req.app_id) >= 5:
        php_ver = f"{req.app_id[3]}.{req.app_id[4]}"
        if req.file_type == "pool":
            conf_path = f"/etc/php/{php_ver}/fpm/pool.d/www.conf"
        else:
            conf_path = f"/etc/php/{php_ver}/fpm/php.ini"

    if not conf_path:
        raise HTTPException(status_code=400, detail="Không tìm thấy cấu hình của ứng dụng này.")

    # Ghi nội dung an toàn qua file tạm và move
    tmp_path = f"/tmp/presspanel_{lookup_id}_conf.tmp"
    gatekeeper_bin = "/opt/presspanel/bin/app"

    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(req.content)
        
        # Di chuyển đè vào file cấu hình chính bằng Gatekeeper SUID
        if os.path.isfile(gatekeeper_bin):
            res = subprocess.run([gatekeeper_bin, "config-write", tmp_path, conf_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if res.returncode == 0:
                return {"status": "ok", "message": f"Đã lưu file cấu hình {conf_path} thành công!"}

        # Fallback 1: Dùng sudo cp
        res_sudo = subprocess.run(["sudo", "cp", tmp_path, conf_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res_sudo.returncode == 0:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            return {"status": "ok", "message": f"Đã lưu file cấu hình {conf_path} thành công!"}

        # Fallback 2: Ghi trực tiếp nếu có quyền
        with open(conf_path, "w", encoding="utf-8") as f:
            f.write(req.content)
        
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

        return {"status": "ok", "message": f"Đã lưu file cấu hình {conf_path} thành công!"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Không thể lưu file cấu hình: {str(e)}")

@router.post("/autorestart")
async def toggle_autorestart(req: AutorestartRequest, current_user: dict = Depends(get_current_user)):
    """Bật/Tắt chế độ tự khởi động lại (systemctl enable/disable) cho ứng dụng khi bị tắt"""
    app = next((a for a in SUPPORTED_APPS if a["id"] == req.app_id), None)
    if not app or not app.get("service"):
        raise HTTPException(status_code=400, detail="Ứng dụng này không có dịch vụ daemon để cấu hình tự khởi động.")

    srv = app["service"]
    op = "enable" if req.enabled else "disable"
    gatekeeper_bin = "/opt/presspanel/bin/app"

    if os.path.isfile(gatekeeper_bin):
        proc = subprocess.run([gatekeeper_bin, "service", op, srv], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if proc.returncode == 0:
            return {"status": "ok", "message": f"Đã {'bật' if req.enabled else 'tắt'} tự khởi động cho {app['name']} thành công!"}

    try:
        res = subprocess.run(["sudo", "systemctl", op, srv], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode == 0:
            return {"status": "ok", "message": f"Đã {'bật' if req.enabled else 'tắt'} tự khởi động cho {app['name']} thành công!"}
        else:
            # Fallback không sudo
            subprocess.run(["systemctl", op, srv], check=True)
            return {"status": "ok", "message": f"Đã {'bật' if req.enabled else 'tắt'} tự khởi động cho {app['name']} thành công!"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi: {str(e)}")

# ==========================================
# CẤU HÌNH NÂNG CAO PHP (aaPanel Style: Extensions, Directives, Logs, phpinfo)
# ==========================================
PHP_EXTENSIONS_LIST = [
    {"name": "ionCube", "pkg": "ioncube-loader", "type": "Script decryption", "desc": "Used to decrypt ZendOptimizer/ionCube encryption scripts"},
    {"name": "fileinfo", "pkg": "fileinfo", "type": "Universal Extensions", "desc": "For getting file MIME, encoding, etc.!"},
    {"name": "opcache", "pkg": "opcache", "type": "cache", "desc": "for speeding up PHP scripts!"},
    {"name": "memcache", "pkg": "memcache", "type": "cache", "desc": "powerful content cache"},
    {"name": "memcached", "pkg": "memcached", "type": "Buffer", "desc": "More advanced features than memcache"},
    {"name": "redis", "pkg": "redis", "type": "cache", "desc": "Key-Value database based on memory can also be persisted"},
    {"name": "mcrypt", "pkg": "mcrypt", "type": "Universal Extensions", "desc": "mcrypt encryption library"},
    {"name": "apcu", "pkg": "apcu", "type": "Buffer", "desc": "Userland and script buffer in memory"},
    {"name": "imagemagick", "pkg": "imagick", "type": "Universal Extensions", "desc": "Imagick High Performance Graphics Library"},
    {"name": "xdebug", "pkg": "xdebug", "type": "Debugger", "desc": "Powerful interactive debugging tool for developers"}
]

@router.get("/php/extensions")
async def get_php_extensions(version: str = "8.2", current_user: dict = Depends(get_current_user)):
    """Lấy danh sách extensions và trạng thái đã cài đặt của phiên bản PHP"""
    ver_clean = version.replace("php", "")
    if len(ver_clean) == 2:
        ver_clean = f"{ver_clean[0]}.{ver_clean[1]}"

    installed_modules = []
    php_bin = f"/usr/bin/php{ver_clean}"
    if os.path.exists(php_bin):
        try:
            res = subprocess.run([php_bin, "-m"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if res.returncode == 0:
                installed_modules = [m.strip().lower() for m in res.stdout.splitlines() if m.strip()]
        except Exception:
            pass

    extensions_status = []
    for ext in PHP_EXTENSIONS_LIST:
        pkg_name = ext["pkg"]
        is_installed = False
        # Kiểm tra qua php -m
        if ext["name"].lower() in installed_modules or pkg_name.lower() in installed_modules:
            is_installed = True
        # Kiểm tra file ini module trong conf.d
        elif os.path.exists(f"/etc/php/{ver_clean}/mods-available/{pkg_name}.ini") or \
             os.path.exists(f"/etc/php/{ver_clean}/fpm/conf.d/20-{pkg_name}.ini"):
            is_installed = True

        extensions_status.append({
            **ext,
            "installed": is_installed
        })

    return {
        "version": ver_clean,
        "extensions": extensions_status
    }

class ExtensionActionRequest(BaseModel):
    version: str
    extension: str
    action: str  # install | remove

@router.post("/php/extension-action")
async def toggle_php_extension(
    req: ExtensionActionRequest,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    """Cài đặt hoặc Gỡ bỏ Extension cho phiên bản PHP"""
    ver_clean = req.version.replace("php", "")
    if len(ver_clean) == 2:
        ver_clean = f"{ver_clean[0]}.{ver_clean[1]}"

    action_type = "install-ext" if req.action == "install" else "remove-ext"
    gatekeeper_bin = "/opt/presspanel/bin/app"
    script_path = "/opt/presspanel/scripts/apps/php.sh"

    if os.path.isfile(gatekeeper_bin):
        cmd = f"{gatekeeper_bin} php-ext {'install' if req.action == 'install' else 'remove'} {ver_clean} {req.extension}"
    else:
        cmd = f"sudo bash {script_path} {action_type} {ver_clean} {req.extension}"

    task_id = await add_system_task(
        user_id=current_user["id"],
        action_name=f"{'Cài đặt' if req.action == 'install' else 'Gỡ bỏ'} extension {req.extension} cho PHP {ver_clean}",
        cmd=cmd,
        background_tasks=background_tasks
    )

    return {
        "status": "ok",
        "task_id": task_id,
        "message": f"Đã bắt đầu tiến trình {'cài đặt' if req.action == 'install' else 'gỡ bỏ'} extension {req.extension}."
    }

@router.get("/php/directives")
async def get_php_directives(version: str = "8.2", current_user: dict = Depends(get_current_user)):
    """Đọc các thông số cấu hình cụ thể từ file php.ini (upload, timeout, memory, disabled_functions)"""
    ver_clean = version.replace("php", "")
    if len(ver_clean) == 2:
        ver_clean = f"{ver_clean[0]}.{ver_clean[1]}"

    ini_path = f"/etc/php/{ver_clean}/fpm/php.ini"
    data = {
        "upload_max_filesize": "50M",
        "post_max_size": "50M",
        "max_execution_time": "60",
        "max_input_time": "60",
        "memory_limit": "256M",
        "disable_functions": "passthru,exec,system,chroot,chgrp,chown,shell_exec,proc_open,proc_get_status,popen,ini_alter,ini_restore,dl,openlog,syslog,readlink,symlink,popepassthru,stream_socket_server"
    }

    if os.path.exists(ini_path):
        try:
            with open(ini_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            import re
            for k in ["upload_max_filesize", "post_max_size", "max_execution_time", "max_input_time", "memory_limit", "disable_functions"]:
                m = re.search(rf"^\s*{k}\s*=\s*([^\r\n;]+)", content, re.MULTILINE)
                if m:
                    data[k] = m.group(1).strip()
        except Exception:
            pass

    return {
        "version": ver_clean,
        "ini_path": ini_path,
        "directives": data
    }

class DirectivesUpdateRequest(BaseModel):
    version: str
    directives: Dict[str, str]

@router.post("/php/directives")
async def save_php_directives(req: DirectivesUpdateRequest, current_user: dict = Depends(get_current_user)):
    """Cập nhật các thông số trong php.ini và reload service"""
    ver_clean = req.version.replace("php", "")
    if len(ver_clean) == 2:
        ver_clean = f"{ver_clean[0]}.{ver_clean[1]}"

    ini_path = f"/etc/php/{ver_clean}/fpm/php.ini"
    if not os.path.exists(ini_path):
        raise HTTPException(status_code=404, detail=f"Không tìm thấy file cấu hình {ini_path}")

    try:
        with open(ini_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        import re
        for k, v in req.directives.items():
            if re.search(rf"^\s*{k}\s*=", content, re.MULTILINE):
                content = re.sub(rf"^\s*{k}\s*=.*$", f"{k} = {v}", content, flags=re.MULTILINE)
            else:
                content += f"\n{k} = {v}\n"

        # Ghi lại qua file tạm và gatekeeper
        tmp_path = f"/tmp/presspanel_php{ver_clean}_ini.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(content)

        gatekeeper_bin = "/opt/presspanel/bin/app"
        if os.path.isfile(gatekeeper_bin):
            subprocess.run([gatekeeper_bin, "config-write", tmp_path, ini_path], check=False)
            subprocess.run([gatekeeper_bin, "service", "reload", f"php{ver_clean}-fpm"], check=False)
        else:
            subprocess.run(["sudo", "cp", tmp_path, ini_path], check=False)
            subprocess.run(["sudo", "systemctl", "reload", f"php{ver_clean}-fpm"], check=False)
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

        return {"status": "ok", "message": f"Cập nhật thông số PHP {ver_clean} thành công!"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi cập nhật php.ini: {str(e)}")

@router.get("/php/info")
async def get_phpinfo(version: str = "8.2", current_user: dict = Depends(get_current_user)):
    """Lấy kết quả php -i của phiên bản PHP được chọn"""
    ver_clean = version.replace("php", "")
    if len(ver_clean) == 2:
        ver_clean = f"{ver_clean[0]}.{ver_clean[1]}"

    php_bin = f"/usr/bin/php{ver_clean}"
    if not os.path.exists(php_bin):
        return {"status": "error", "message": f"Nhị phân {php_bin} chưa được cài đặt."}

    try:
        res = subprocess.run([php_bin, "-i"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return {
            "status": "ok",
            "version": ver_clean,
            "phpinfo": res.stdout
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

# ==========================================
# REWRITE WEBSERVER CONFIG (TÁI TẠO CẤU HÌNH TỪ TEMPLATE & DATABASE)
# ==========================================
class RewriteConfigRequest(BaseModel):
    webserver: str  # ols | nginx

@router.post("/rewrite-webserver")
async def rewrite_webserver_config(
    req: RewriteConfigRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Backup cấu hình vhost hiện tại và tái tạo lại toàn bộ file VirtualHost
    từ template và dữ liệu websites trong SQLite database.
    """
    import time
    from app.db.session import get_db_connection

    ws = req.webserver.lower()
    if ws not in ["ols", "nginx"]:
        raise HTTPException(status_code=400, detail="Chỉ hỗ trợ tái tạo cấu hình cho OpenLiteSpeed ('ols') hoặc Nginx ('nginx').")

    backup_ts = int(time.time())
    backup_notes = []

    try:
        # 1. Sao lưu (Backup) cấu hình hiện tại
        if ws == "nginx":
            nginx_conf_dir = "/etc/nginx/conf.d"
            if os.path.exists(nginx_conf_dir):
                backup_dir = f"/etc/nginx/conf.d_backup_{backup_ts}"
                try:
                    subprocess.run(["sudo", "cp", "-r", nginx_conf_dir, backup_dir], check=False)
                    backup_notes.append(f"Đã sao lưu Nginx sang {backup_dir}")
                except Exception as e:
                    backup_notes.append(f"Lỗi sao lưu Nginx: {str(e)}")
        elif ws == "ols":
            ols_vhosts_dir = "/usr/local/lsws/conf/vhosts"
            if os.path.exists(ols_vhosts_dir):
                backup_dir = f"/usr/local/lsws/conf/vhosts_backup_{backup_ts}"
                try:
                    subprocess.run(["sudo", "cp", "-r", ols_vhosts_dir, backup_dir], check=False)
                    backup_notes.append(f"Đã sao lưu OpenLiteSpeed sang {backup_dir}")
                except Exception as e:
                    backup_notes.append(f"Lỗi sao lưu OLS: {str(e)}")

        # 2. Lấy TOÀN BỘ websites trong DB để áp dụng Web Server cấp hệ thống
        db = await get_db_connection()
        domains = []
        try:
            cursor = await db.execute("""
                SELECT d.id, d.domain_name, d.webserver, d.php_version, d.document_root, u.username
                FROM domains d
                JOIN users u ON d.user_id = u.id
            """)
            domains = await cursor.fetchall()
            
            # Cập nhật webserver của toàn bộ websites trong DB sang webserver hệ thống được chọn
            await db.execute("UPDATE domains SET webserver = ?", (ws,))
            await db.commit()
        except Exception as dbe:
            backup_notes.append(f"Lưu ý DB: {str(dbe)}")
        finally:
            await db.close()

        gatekeeper_vhost = "/opt/presspanel/bin/vhost"
        script_vhost = "/opt/presspanel/scripts/vhost_manager.sh"
        success_count = 0
        fail_count = 0

        # 2.5. Chạy rewrite-all trên gatekeeper vhost để xử lý cấu hình gốc (listeners, đổi 8088 -> 80)
        try:
            if os.path.isfile(gatekeeper_vhost):
                subprocess.run([gatekeeper_vhost, "rewrite-all", ws], check=False)
            elif os.path.isfile(script_vhost):
                subprocess.run(["bash", script_vhost, "rewrite-all", "all", "all", ws], check=False)
        except Exception as rwe:
            backup_notes.append(f"Lưu ý rewrite gốc: {str(rwe)}")

        # 3. Tạo lại từng Vhost theo template chuẩn
        for row in domains:
            try:
                d_name = row["domain_name"]
                import re
                u_name = re.sub(r"[^a-z0-9]", "", d_name)[:30] or row["username"]
                php_ver = row["php_version"] or "8.2"

                cmd = None
                if os.path.isfile(gatekeeper_vhost):
                    cmd = [gatekeeper_vhost, "add", u_name, d_name, ws, php_ver]
                elif os.path.isfile(script_vhost):
                    cmd = ["bash", script_vhost, "add", u_name, d_name, ws, php_ver]

                if cmd:
                    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    if proc.returncode == 0:
                        success_count += 1
                    else:
                        fail_count += 1
            except Exception:
                fail_count += 1

        # 4. Reload Web Server tương ứng
        srv_name = "lshttpd" if ws == "ols" else "nginx"
        gatekeeper_app = "/opt/presspanel/bin/app"
        try:
            if os.path.isfile(gatekeeper_app):
                subprocess.run([gatekeeper_app, "service", "reload", srv_name], check=False)
            else:
                subprocess.run(["systemctl", "reload", srv_name], check=False)
        except Exception:
            pass

        return {
            "status": "ok",
            "webserver": ws,
            "message": f"Tái tạo cấu hình {ws.upper()} thành công! Đã cập nhật {success_count} website từ cơ sở dữ liệu.",
            "details": {
                "backup_info": backup_notes,
                "total_domains": len(domains),
                "recreated": success_count,
                "failed": fail_count
            }
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Lỗi tái tạo cấu hình webserver: {str(e)}")


