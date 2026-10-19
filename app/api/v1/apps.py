import os
import shutil
import subprocess
import asyncio
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel
from app.api.deps import get_current_user

router = APIRouter(prefix="/apps", tags=["App Store & Software Manager"])

WPC_BASE = "/opt/wpc"

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
        "binary_checks": ["/usr/sbin/nginx", "/usr/bin/nginx", "/usr/local/openresty/nginx/sbin/nginx"],
        "default_version": "1.26.x",
        "versions": [
            {"value": "openresty", "label": "OpenResty (Nginx + LuaJIT, siêu hiệu năng)"},
            {"value": "1.26.x", "label": "Nginx 1.26.x (Ổn định mới nhất)"},
            {"value": "latest", "label": "Nginx Mainline (Mới nhất)"},
            {"value": "1.24.x", "label": "Nginx 1.24.x (LTS ổn định)"}
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
            {"value": "mariadb-11.4", "label": "MariaDB 11.4 LTS (Mới nhất, tối ưu cực cao)"},
            {"value": "mariadb-10.11", "label": "MariaDB 10.11 LTS (Khuyên dùng, nhanh & nhẹ)"},
            {"value": "mariadb-10.6", "label": "MariaDB 10.6 LTS (Cũ, tương thích cao)"},
            {"value": "mariadb-10.5", "label": "MariaDB 10.5 LTS (Kế thừa)"},
            {"value": "mysql-8.4", "label": "Oracle MySQL 8.4 LTS (Mới nhất)"},
            {"value": "mysql-8.0", "label": "Oracle MySQL 8.0 (Chính thức)"},
            {"value": "percona-8.0", "label": "Percona Server for MySQL 8.0 (Doanh nghiệp)"}
        ]
    },
    {
        "id": "redis",
        "name": "Redis Object Cache",
        "category": "database",
        "icon": "🔴",
        "desc": "Bộ nhớ đệm trong RAM tốc độ cao, hỗ trợ Redis Object Cache cho WordPress (Port 6379).",
        "service": "redis-server",
        "binary_checks": ["/usr/bin/redis-server", "/usr/local/bin/redis-server"],
        "default_version": "7.0",
        "versions": [
            {"value": "7.0", "label": "Redis 7.0 (Mới nhất)"},
            {"value": "6.2", "label": "Redis 6.2 LTS"}
        ]
    },
    {
        "id": "pgsql",
        "name": "PostgreSQL Database",
        "category": "database",
        "icon": "🐘",
        "desc": "Hệ quản trị CSDL quan hệ đối tượng mạnh mẽ hàng đầu thế giới, chuẩn SQL cao cấp (Port 5432).",
        "service": "postgresql",
        "binary_checks": ["/usr/bin/postgres", "/usr/bin/psql", "/usr/lib/postgresql/16/bin/postgres"],
        "default_version": "16",
        "versions": [
            {"value": "16", "label": "PostgreSQL 16 (Khuyên dùng)"},
            {"value": "15", "label": "PostgreSQL 15 LTS"}
        ]
    },
    {
        "id": "mongodb",
        "name": "MongoDB NoSQL Database",
        "category": "database",
        "icon": "🍃",
        "desc": "Cơ sở dữ liệu NoSQL tài liệu JSON/BSON hiệu năng cao với WiredTiger Engine (Port 27017).",
        "service": "mongod",
        "binary_checks": ["/usr/bin/mongod", "/usr/bin/mongosh"],
        "default_version": "7.0",
        "versions": [
            {"value": "7.0", "label": "MongoDB 7.0 Community"},
            {"value": "6.0", "label": "MongoDB 6.0 LTS"}
        ]
    },
    {
        "id": "sqlserver",
        "name": "Microsoft SQL Server",
        "category": "database",
        "icon": "🗄️",
        "desc": "Hệ quản trị CSDL quan hệ doanh nghiệp Microsoft SQL Server 2022 on Linux (Port 1433).",
        "service": "mssql-server",
        "binary_checks": ["/opt/mssql/bin/sqlservr", "/usr/bin/sqlcmd"],
        "default_version": "2022",
        "versions": [
            {"value": "2022", "label": "MS SQL Server 2022 on Linux"},
            {"value": "2019", "label": "MS SQL Server 2019"}
        ]
    },

    {
        "id": "php85",
        "name": "PHP 8.5 Runtime",
        "category": "php",
        "icon": "🐘",
        "desc": "Phiên bản PHP 8.5 thử nghiệm mới nhất của PHP Core Team.",
        "service": "php8.5-fpm",
        "binary_checks": ["/usr/sbin/php-fpm8.5", "/usr/local/lsws/lsphp85/bin/lsphp"],
        "default_version": "8.5"
    },
    {
        "id": "php84",
        "name": "PHP 8.4 Runtime",
        "category": "php",
        "icon": "🐘",
        "desc": "Phiên bản PHP 8.4 hiện đại nhất, hỗ trợ Property Hooks, Asymmetric Visibility.",
        "service": "php8.4-fpm",
        "binary_checks": ["/usr/sbin/php-fpm8.4", "/usr/local/lsws/lsphp84/bin/lsphp"],
        "default_version": "8.4"
    },
    {
        "id": "php83",
        "name": "PHP 8.3 Runtime",
        "category": "php",
        "icon": "🐘",
        "desc": "Phiên bản PHP 8.3 hiệu năng cao, bổ sung Typed Class Constants và json_validate.",
        "service": "php8.3-fpm",
        "binary_checks": ["/usr/sbin/php-fpm8.3", "/usr/local/lsws/lsphp83/bin/lsphp"],
        "default_version": "8.3"
    },
    {
        "id": "php82",
        "name": "PHP 8.2 Runtime",
        "category": "php",
        "icon": "🐘",
        "desc": "Phiên bản PHP 8.2 ổn định cao, khuyên dùng cho hầu hết website WordPress.",
        "service": "php8.2-fpm",
        "binary_checks": ["/usr/sbin/php-fpm8.2", "/usr/local/lsws/lsphp82/bin/lsphp"],
        "default_version": "8.2"
    },
    {
        "id": "php81",
        "name": "PHP 8.1 Runtime",
        "category": "php",
        "icon": "🐘",
        "desc": "PHP 8.1 hỗ trợ Enums, Readonly Properties, First-class Callables.",
        "service": "php8.1-fpm",
        "binary_checks": ["/usr/sbin/php-fpm8.1", "/usr/local/lsws/lsphp81/bin/lsphp"],
        "default_version": "8.1"
    },
    {
        "id": "php80",
        "name": "PHP 8.0 Runtime",
        "category": "php",
        "icon": "🐘",
        "desc": "PHP 8.0 giới thiệu JIT Compiler, Named Arguments và Union Types.",
        "service": "php8.0-fpm",
        "binary_checks": ["/usr/sbin/php-fpm8.0", "/usr/local/lsws/lsphp80/bin/lsphp"],
        "default_version": "8.0"
    },
    {
        "id": "php74",
        "name": "PHP 7.4 Runtime",
        "category": "php",
        "icon": "🐘",
        "desc": "Phiên bản PHP 7.4 Legacy ổn định nhất thế hệ PHP 7.x.",
        "service": "php7.4-fpm",
        "binary_checks": ["/usr/sbin/php-fpm7.4", "/usr/local/lsws/lsphp74/bin/lsphp"],
        "default_version": "7.4"
    },
    {
        "id": "php73",
        "name": "PHP 7.3 Runtime",
        "category": "php",
        "icon": "🐘",
        "desc": "PHP 7.3 dành cho các dự án và theme WordPress cũ.",
        "service": "php7.3-fpm",
        "binary_checks": ["/usr/sbin/php-fpm7.3", "/usr/local/lsws/lsphp73/bin/lsphp"],
        "default_version": "7.3"
    },
    {
        "id": "php72",
        "name": "PHP 7.2 Runtime",
        "category": "php",
        "icon": "🐘",
        "desc": "PHP 7.2 hỗ trợ mã nguồn WordPress 4.x - 5.x.",
        "service": "php7.2-fpm",
        "binary_checks": ["/usr/sbin/php-fpm7.2", "/usr/local/lsws/lsphp72/bin/lsphp"],
        "default_version": "7.2"
    },
    {
        "id": "php71",
        "name": "PHP 7.1 Runtime",
        "category": "php",
        "icon": "🐘",
        "desc": "PHP 7.1 hỗ trợ tương thích mã nguồn cũ.",
        "service": "php7.1-fpm",
        "binary_checks": ["/usr/sbin/php-fpm7.1", "/usr/local/lsws/lsphp71/bin/lsphp"],
        "default_version": "7.1"
    },
    {
        "id": "php70",
        "name": "PHP 7.0 Runtime",
        "category": "php",
        "icon": "🐘",
        "desc": "PHP 7.0 phiên bản nâng cấp tốc độ đột phá so với PHP 5.6.",
        "service": "php7.0-fpm",
        "binary_checks": ["/usr/sbin/php-fpm7.0", "/usr/local/lsws/lsphp70/bin/lsphp"],
        "default_version": "7.0"
    },
    {
        "id": "php56",
        "name": "PHP 5.6 Runtime",
        "category": "php",
        "icon": "🐘",
        "desc": "PHP 5.6 Legacy dành cho các website cổ, tương thích cao với script cũ.",
        "service": "php5.6-fpm",
        "binary_checks": ["/usr/sbin/php-fpm5.6", "/usr/local/lsws/lsphp56/bin/lsphp"],
        "default_version": "5.6"
    },
    {
        "id": "phpmyadmin",
        "name": "phpMyAdmin GUI",
        "category": "tools",
        "icon": "🧭",
        "desc": "Giao diện Web trực quan quản lý CSDL MariaDB/MySQL nhanh chóng và an toàn (Port 888).",
        "service": "",
        "binary_checks": ["/opt/wpc/apps/phpmyadmin/index.php", "/usr/share/phpmyadmin/index.php"],
        "default_version": "5.2.x"
    },
    {
        "id": "pgadmin",
        "name": "pgAdmin 4 GUI",
        "category": "tools",
        "icon": "🐘",
        "desc": "Giao diện Web trực quan quản trị CSDL PostgreSQL mạnh mẽ và toàn diện (Port 5050).",
        "service": "",
        "binary_checks": ["/usr/pgadmin4/bin/pgadmin4", "/usr/bin/pgadmin4"],
        "default_version": "4.x"
    },
    {
        "id": "mongo-express",
        "name": "Mongo Express GUI",
        "category": "tools",
        "icon": "🍃",
        "desc": "Giao diện Web quản trị MongoDB hiện đại, trực quan hóa Documents và Collections (Port 8081).",
        "service": "mongo-express",
        "binary_checks": ["/usr/local/bin/mongo-express", "/usr/bin/mongo-express"],
        "default_version": "1.0.x"
    },
    {
        "id": "redis-commander",
        "name": "Redis Commander GUI",
        "category": "tools",
        "icon": "🔴",
        "desc": "Giao diện Web trực quan xem, chỉnh sửa và quản lý Key-Value cache Redis (Port 8082).",
        "service": "redis-commander",
        "binary_checks": ["/usr/local/bin/redis-commander", "/usr/bin/redis-commander"],
        "default_version": "0.8.x"
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

APP_SERVICE_CANDIDATES: Dict[str, List[str]] = {
    "nginx": ["openresty", "nginx"],
    "ols": ["lshttpd", "lsws"],
    "mysql": ["mariadb", "mysqld", "mysql"],
    "redis": ["redis", "redis-server"],
    "pgsql": ["postgresql", "postgresql-16", "postgresql-15", "postgresql-14"],
    "mongodb": ["mongod", "mongodb"],
    "sqlserver": ["mssql-server"],
    "certbot": ["certbot-renew.timer", "certbot.timer", "certbot-renew.service", "certbot.service"],
    "pure-ftpd": ["pure-ftpd", "pure-ftpd-mysql"],
    "fail2ban": ["fail2ban"],
    "mongo-express": ["mongo-express"],
    "redis-commander": ["redis-commander"]
}

def get_app_service_candidates(app_id: str) -> List[str]:
    """Trả về danh sách service candidates cho một app_id (bao gồm cả dynamic PHP)"""
    if app_id in APP_SERVICE_CANDIDATES:
        return APP_SERVICE_CANDIDATES[app_id]
    if app_id.startswith("php") and len(app_id) >= 5:
        ver = f"{app_id[3]}.{app_id[4]}"
        clean_ver = app_id[3:5]
        return [f"php{ver}-fpm", f"php{clean_ver}-php-fpm", "php-fpm"]
    return []

def resolve_app_service(app_id: str) -> str:
    """Tự động phát hiện chính xác tên daemon service/timer thực tế trên OS"""
    candidates = get_app_service_candidates(app_id)
    if not candidates:
        app = next((a for a in SUPPORTED_APPS if a["id"] == app_id), None)
        return app.get("service", "") if app else ""
    if os.name == "nt":
        return candidates[0]
    
    # 1. Tìm candidate đang active
    for svc in candidates:
        try:
            r = subprocess.run(["systemctl", "is-active", "--quiet", svc], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if r.returncode == 0:
                return svc
        except Exception:
            pass
            
    # 2. Tìm candidate đã được cài đặt (enabled hoặc unit file tồn tại)
    for svc in candidates:
        try:
            r = subprocess.run(["systemctl", "status", svc], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if r.returncode in [0, 3]:
                return svc
        except Exception:
            pass

    return candidates[0]

def resolve_app_config_path(app_id: str, file_type: str = "main") -> str:
    """Tự động phát hiện chính xác đường dẫn file cấu hình thực tế tồn tại trên hệ thống (AlmaLinux, RHEL, Ubuntu, Debian)"""
    # Xử lý PHP động
    if app_id.startswith("php") and len(app_id) >= 5:
        ver = f"{app_id[3]}.{app_id[4]}"
        clean_ver = app_id[3:5]
        if file_type == "pool":
            php_pool_paths = [
                f"/etc/php/{ver}/fpm/pool.d/www.conf",
                f"/etc/opt/remi/php{clean_ver}/php-fpm.d/www.conf",
                f"/etc/php-fpm.d/www.conf"
            ]
            for p in php_pool_paths:
                if os.path.exists(p):
                    return p
            return php_pool_paths[0]
        else:
            php_ini_paths = [
                f"/etc/php/{ver}/fpm/php.ini",
                f"/etc/opt/remi/php{clean_ver}/php.ini",
                f"/etc/php.ini",
                f"/usr/local/lsws/lsphp{clean_ver}/etc/php/{ver}/litespeed/php.ini"
            ]
            for p in php_ini_paths:
                if os.path.exists(p):
                    return p
            return php_ini_paths[0]

    config_map = {
        "nginx": [
            "/usr/local/openresty/nginx/conf/nginx.conf",
            "/etc/openresty/nginx.conf",
            "/etc/nginx/nginx.conf"
        ],
        "ols": [
            "/usr/local/lsws/conf/httpd_config.conf",
            "/usr/local/lsws/conf/httpd_config.xml"
        ],
        "mysql": [
            "/etc/my.cnf.d/mariadb-server.cnf",
            "/etc/my.cnf.d/server.cnf",
            "/etc/my.cnf",
            "/etc/mysql/mariadb.conf.d/50-server.cnf",
            "/etc/mysql/my.cnf",
            "/etc/mysql/mysql.conf.d/mysqld.cnf"
        ],
        "mariadb": [
            "/etc/my.cnf.d/mariadb-server.cnf",
            "/etc/my.cnf.d/server.cnf",
            "/etc/my.cnf",
            "/etc/mysql/mariadb.conf.d/50-server.cnf",
            "/etc/mysql/my.cnf"
        ],
        "redis": [
            "/etc/redis.conf",
            "/etc/redis/redis.conf"
        ],
        "pure-ftpd": [
            "/etc/pure-ftpd/pure-ftpd.conf",
            "/etc/pure-ftpd.conf"
        ],
        "fail2ban": [
            "/etc/fail2ban/jail.local",
            "/etc/fail2ban/jail.conf"
        ],
        "certbot": [
            "/etc/letsencrypt/cli.ini"
        ]
    }

    paths = config_map.get(app_id, [])
    for p in paths:
        if os.path.exists(p):
            return p
    return paths[0] if paths else ""

def get_nginx_service() -> str:
    """Helper tương thích ngược lấy tên daemon service nginx/openresty thực tế"""
    return resolve_app_service("nginx")

def get_nginx_config_path() -> str:
    """Helper tương thích ngược lấy file config nginx/openresty thực tế"""
    return resolve_app_config_path("nginx")

def check_app_status(app: Dict[str, Any]) -> Dict[str, Any]:
    """Kiểm tra thực tế nhị phân và service trên Linux với cơ chế nhận diện thông minh đa nền tảng"""
    installed = False
    running = False

    # 1. Kiểm tra binary thực tế
    for b in app["binary_checks"]:
        if os.path.exists(b):
            installed = True
            break
        # Chỉ dùng which nếu b là command name đơn giản không có path
        if "/" not in b and shutil.which(b):
            installed = True
            break

    # Bổ sung kiểm tra binary PHP Remi trên AlmaLinux / RHEL
    if not installed and app.get("category") == "php":
        clean_num = app["id"].replace("php", "")
        remi_paths = [
            f"/opt/remi/php{clean_num}/root/usr/sbin/php-fpm",
            f"/usr/bin/php{clean_num}",
            f"/etc/opt/remi/php{clean_num}"
        ]
        for rp in remi_paths:
            if os.path.exists(rp):
                installed = True
                break

    # 2. Tự động nhận diện daemon service/timer phù hợp với hệ điều hành (RHEL/AlmaLinux vs Debian/Ubuntu)
    app_id = app["id"]
    candidates = get_app_service_candidates(app_id)
    active_service = app.get("service", "")

    if candidates and os.name != "nt":
        for svc_name in candidates:
            try:
                res = subprocess.run(
                    ["systemctl", "is-active", "--quiet", svc_name],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                if res.returncode == 0:
                    running = True
                    installed = True
                    active_service = svc_name
                    break
            except Exception:
                pass
        
        # Nếu chưa running, kiểm tra xem unit file có tồn tại trên OS không để gán đúng tên service
        if not running:
            active_service = resolve_app_service(app_id)

        app = {**app, "service": active_service}
    elif app["service"] and os.name != "nt":
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

    # 3. Đặc thù PHP: Trên OpenLiteSpeed, LSPHP chạy on-demand qua suEXEC chứ không chạy PHP-FPM service
    if app.get("category") == "php" and installed:
        if not running and os.name != "nt":
            ver_clean = app.get("default_version", "").replace(".", "")
            lsphp_bin = f"/usr/local/lsws/lsphp{ver_clean}/bin/lsphp"
            if os.path.exists(lsphp_bin):
                try:
                    res_ols = subprocess.run(
                        ["systemctl", "is-active", "--quiet", "lsws"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    )
                    if res_ols.returncode == 0:
                        running = True
                    else:
                        res_ols2 = subprocess.run(
                            ["systemctl", "is-active", "--quiet", "lshttpd"],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL
                        )
                        if res_ols2.returncode == 0:
                            running = True
                except Exception:
                    pass
    elif installed and not app["service"]:
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
    script_path = f"{WPC_BASE}/scripts/apps/{app_id}.sh"
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
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Chỉ quản trị viên cấp cao mới có quyền thao tác dịch vụ hệ thống.")

    app = next((a for a in SUPPORTED_APPS if a["id"] == req.app_id), None)
    if not app:
        raise HTTPException(status_code=404, detail="Ứng dụng không tồn tại trong danh mục")

    gatekeeper_bin = f"{WPC_BASE}/bin/app"

    # 1. HÀNH ĐỘNG CÀI ĐẶT (INSTALL)
    if req.action == "install":
        script_path = f"{WPC_BASE}/scripts/apps/{req.app_id}.sh"
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
                "pgsql": "postgresql postgresql-contrib",
                "mongodb": "mongodb",
                "sqlserver": "mssql-server",
                "certbot": "certbot python3-certbot-nginx",
                "fail2ban": "fail2ban",
                "pure-ftpd": "pure-ftpd",
                "pgadmin": "pgadmin4-web"
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
        script_path = f"{WPC_BASE}/scripts/apps/{req.app_id}.sh"
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
                "pgsql": "postgresql postgresql-contrib",
                "mongodb": "mongodb",
                "sqlserver": "mssql-server",
                "pure-ftpd": "pure-ftpd",
                "fail2ban": "fail2ban",
                "pgadmin": "pgadmin4-web"
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
        srv = resolve_app_service(req.app_id)
        candidates = get_app_service_candidates(req.app_id)
        if not srv and not candidates and not app.get("service"):
            raise HTTPException(status_code=400, detail="Ứng dụng này không chạy dạng dịch vụ daemon.")
        
        services_to_try = [srv] if srv else []
        for c in candidates:
            if c not in services_to_try:
                services_to_try.append(c)
        if app.get("service") and app["service"] not in services_to_try:
            services_to_try.append(app["service"])

        last_error = ""
        # Ưu tiên thực thi qua C Gatekeeper SUID (tránh lỗi Polkit / Interactive authentication required)
        if os.path.isfile(gatekeeper_bin):
            for target_svc in services_to_try:
                proc = subprocess.run(
                    [gatekeeper_bin, "service", req.action, target_svc],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True
                )
                if proc.returncode == 0:
                    return {"status": "ok", "message": f"Đã {req.action} dịch vụ {app['name']} ({target_svc}) thành công!"}
                last_error = proc.stderr or proc.stdout

        # Fallback sudo nếu chưa compile C Gatekeeper
        for target_svc in services_to_try:
            try:
                res = subprocess.run(
                    ["sudo", "systemctl", req.action, target_svc],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True
                )
                if res.returncode == 0:
                    return {"status": "ok", "message": f"Đã {req.action} dịch vụ {app['name']} ({target_svc}) thành công!"}
                last_error = res.stderr or res.stdout
            except Exception as e:
                last_error = str(e)

        raise HTTPException(status_code=500, detail=f"Lỗi thao tác service ({', '.join(services_to_try)}): {last_error}")

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
    """Đọc file cấu hình thực tế của ứng dụng để hiển thị trong Popup Settings (hỗ trợ cả OpenResty, MariaDB, Redis, php.ini và pool www.conf)"""
    found_path = resolve_app_config_path(app_id, file_type)

    content = ""
    gatekeeper_bin = f"{WPC_BASE}/bin/app"

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
    srv = resolve_app_service(app_id)
    autorestart = False
    if srv and os.name != "nt":
        try:
            check_enabled = subprocess.run(
                ["systemctl", "is-enabled", "--quiet", srv],
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
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Chỉ quản trị viên mới có quyền chỉnh sửa cấu hình hệ thống.")

    conf_path = resolve_app_config_path(req.app_id, req.file_type)
    if not conf_path:
        raise HTTPException(status_code=400, detail="Không tìm thấy cấu hình của ứng dụng này.")

    # Ghi nội dung an toàn qua file tạm và move
    tmp_path = f"/tmp/wpc_{req.app_id}_{req.file_type}_conf.tmp"
    gatekeeper_bin = f"{WPC_BASE}/bin/app"

    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(req.content)
        
        # Di chuyển đè vào file cấu hình chính bằng Gatekeeper SUID
        if os.path.isfile(gatekeeper_bin):
            res = subprocess.run([gatekeeper_bin, "config-write", tmp_path, conf_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if res.returncode == 0:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
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
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Chỉ quản trị viên mới có quyền đổi thiết lập dịch vụ.")
    
    app = next((a for a in SUPPORTED_APPS if a["id"] == req.app_id), None)
    srv = resolve_app_service(req.app_id)
    candidates = get_app_service_candidates(req.app_id)
    if not srv and not candidates and (not app or not app.get("service")):
        raise HTTPException(status_code=400, detail="Ứng dụng này không có dịch vụ daemon để cấu hình tự khởi động.")

    services_to_try = [srv] if srv else []
    for c in candidates:
        if c not in services_to_try:
            services_to_try.append(c)
    if app and app.get("service") and app["service"] not in services_to_try:
        services_to_try.append(app["service"])

    op = "enable" if req.enabled else "disable"
    gatekeeper_bin = f"{WPC_BASE}/bin/app"

    if os.path.isfile(gatekeeper_bin):
        for target_svc in services_to_try:
            proc = subprocess.run([gatekeeper_bin, "service", op, target_svc], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if proc.returncode == 0:
                app_name = app['name'] if app else req.app_id
                return {"status": "ok", "message": f"Đã {'bật' if req.enabled else 'tắt'} tự khởi động cho {app_name} ({target_svc}) thành công!"}

    last_error = ""
    for target_svc in services_to_try:
        try:
            res = subprocess.run(["sudo", "systemctl", op, target_svc], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if res.returncode == 0:
                app_name = app['name'] if app else req.app_id
                return {"status": "ok", "message": f"Đã {'bật' if req.enabled else 'tắt'} tự khởi động cho {app_name} ({target_svc}) thành công!"}
            last_error = res.stderr or res.stdout
        except Exception as e:
            last_error = str(e)

    raise HTTPException(status_code=500, detail=f"Lỗi: {last_error}")

# ==========================================
# CẤU HÌNH NÂNG CAO PHP (WPC Style: Extensions, Directives, Logs, phpinfo)
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
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Chỉ quản trị viên mới có quyền cài đặt hoặc gỡ bỏ extension PHP.")

    ver_clean = req.version.replace("php", "")
    if len(ver_clean) == 2:
        ver_clean = f"{ver_clean[0]}.{ver_clean[1]}"

    action_type = "install-ext" if req.action == "install" else "remove-ext"
    gatekeeper_bin = f"{WPC_BASE}/bin/app"
    script_path = f"{WPC_BASE}/scripts/apps/php.sh"

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
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Chỉ quản trị viên mới có quyền cập nhật cấu hình PHP hệ thống.")

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
        tmp_path = f"/tmp/wpc_php{ver_clean}_ini.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(content)

        gatekeeper_bin = f"{WPC_BASE}/bin/app"
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
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Chỉ quản trị viên mới có quyền tái tạo cấu hình Web Server.")
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
                SELECT d.id, d.domain_name, d.webserver, d.php_version, d.document_root,
                       COALESCE(u.username, 'admin') as username
                FROM domains d
                LEFT JOIN users u ON d.user_id = u.id
            """)
            domains = await cursor.fetchall()
            
            # Cập nhật webserver của toàn bộ websites trong DB sang webserver hệ thống được chọn
            await db.execute("UPDATE domains SET webserver = ?", (ws,))
            await db.commit()
        except Exception as dbe:
            backup_notes.append(f"Lưu ý DB: {str(dbe)}")
        finally:
            await db.close()

        gatekeeper_vhost = f"{WPC_BASE}/bin/vhost"
        script_vhost = f"{WPC_BASE}/scripts/vhost_manager.sh"
        success_count = 0
        fail_count = 0

        # 2.4. Dọn dẹp tiến trình khi chuyển đổi Web Server
        if ws == "ols":
            try:
                subprocess.run(["sudo", "systemctl", "stop", "wpc-php*-fpm@*"], check=False)
                subprocess.run(["sudo", "systemctl", "disable", "wpc-php*-fpm@*"], check=False)
                for pver in ["5.6", "7.0", "7.1", "7.2", "7.3", "7.4", "8.0", "8.1", "8.2", "8.3", "8.4", "8.5"]:
                    subprocess.run(["sudo", "systemctl", "stop", f"php{pver}-fpm"], check=False)
                    subprocess.run(["sudo", "systemctl", "disable", f"php{pver}-fpm"], check=False)
                subprocess.run(["sudo", "pkill", "-9", "-f", "php-fpm"], check=False)
            except Exception:
                pass
        elif ws == "nginx":
            try:
                subprocess.run(["sudo", "systemctl", "stop", "lshttpd"], check=False)
                subprocess.run(["sudo", "systemctl", "stop", "lsws"], check=False)
                subprocess.run(["sudo", "systemctl", "disable", "lshttpd"], check=False)
                subprocess.run(["sudo", "systemctl", "disable", "lsws"], check=False)
                subprocess.run(["sudo", "pkill", "-9", "-f", "openlitespeed"], check=False)
                subprocess.run(["sudo", "pkill", "-9", "-f", "lshttpd"], check=False)
                subprocess.run(["sudo", "pkill", "-9", "-f", "lsphp"], check=False)
                subprocess.run(["sudo", "pkill", "-9", "-f", "lscgid"], check=False)
                subprocess.run(["sudo", "rm", "-rf", "/tmp/lshttpd/*.sock"], check=False)
            except Exception:
                pass

        # 2.5. Chạy rewrite-all trên gatekeeper vhost để xử lý cấu hình gốc (listeners, đổi 8088 -> 80)
        try:
            if os.path.isfile(gatekeeper_vhost):
                proc_rw = subprocess.run([gatekeeper_vhost, "rewrite-all", ws], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                if proc_rw.returncode != 0 and os.path.isfile(script_vhost):
                    subprocess.run(["sudo", script_vhost, "rewrite-all", "all", "all", ws], check=False)
            elif os.path.isfile(script_vhost):
                subprocess.run(["sudo", script_vhost, "rewrite-all", "all", "all", ws], check=False)
        except Exception as rwe:
            backup_notes.append(f"Lưu ý rewrite gốc: {str(rwe)}")

        # 3. Tạo lại từng Vhost theo template chuẩn
        for row in domains:
            try:
                d_name = row["domain_name"]
                hosting_u = row["username"] or "admin"
                import re
                u_name = re.sub(r"[^a-z0-9]", "", d_name)[:30] or hosting_u
                php_ver = row["php_version"] or "8.2"

                success = False
                err_msg = ""
                # Ưu tiên gatekeeper
                if os.path.isfile(gatekeeper_vhost):
                    proc = subprocess.run([gatekeeper_vhost, "add", u_name, d_name, ws, php_ver, hosting_u],
                                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    if proc.returncode == 0:
                        success = True
                    else:
                        err_msg = proc.stderr or proc.stdout

                # Fallback trực tiếp sudo script
                if not success and os.path.isfile(script_vhost):
                    proc = subprocess.run(["sudo", script_vhost, "add", u_name, d_name, ws, php_ver, hosting_u],
                                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    if proc.returncode == 0:
                        success = True
                    else:
                        err_msg = proc.stderr or proc.stdout

                if success:
                    success_count += 1
                else:
                    fail_count += 1
                    if err_msg:
                        backup_notes.append(f"Lỗi tạo vhost {d_name}: {err_msg.strip()}")
            except Exception as e_vhost:
                fail_count += 1
                backup_notes.append(f"Lỗi tạo vhost {row.get('domain_name', 'unknown')}: {str(e_vhost)}")

        # 4. Khởi động lại Web Server tương ứng và đảm bảo trạng thái hoạt động
        gatekeeper_bin = f"{WPC_BASE}/bin/app"
        if ws == "nginx":
            ngx_svc = resolve_app_service("nginx")
            try:
                subprocess.run(["sudo", "systemctl", "enable", ngx_svc], check=False)
                test_cmd = ["sudo", "nginx", "-t"]
                if os.path.exists("/usr/local/openresty/nginx/sbin/nginx"):
                    test_cmd = ["sudo", "/usr/local/openresty/nginx/sbin/nginx", "-t"]
                elif os.path.exists("/usr/sbin/nginx"):
                    test_cmd = ["sudo", "/usr/sbin/nginx", "-t"]
                test_proc = subprocess.run(test_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                if test_proc.returncode != 0:
                    backup_notes.append(f"Cảnh báo Nginx/OpenResty config test: {test_proc.stderr or test_proc.stdout}")
                
                # Khởi động lại qua Gatekeeper hoặc sudo
                restarted = False
                if os.path.isfile(gatekeeper_bin):
                    p_restart = subprocess.run([gatekeeper_bin, "service", "restart", ngx_svc], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    restarted = (p_restart.returncode == 0)
                if not restarted:
                    subprocess.run(["sudo", "systemctl", "restart", ngx_svc], check=False)
            except Exception as ngx_e:
                backup_notes.append(f"Lỗi khởi động {ngx_svc}: {str(ngx_e)}")
        elif ws == "ols":
            try:
                subprocess.run(["sudo", "systemctl", "enable", "lshttpd"], check=False)
                restarted = False
                if os.path.isfile(gatekeeper_bin):
                    p1 = subprocess.run([gatekeeper_bin, "service", "restart", "lshttpd"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    p2 = subprocess.run([gatekeeper_bin, "service", "restart", "lsws"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    restarted = (p1.returncode == 0 or p2.returncode == 0)
                if not restarted:
                    subprocess.run(["sudo", "systemctl", "restart", "lshttpd"], check=False)
                    subprocess.run(["sudo", "/usr/local/lsws/bin/lswsctrl", "restart"], check=False)
            except Exception as ols_e:
                backup_notes.append(f"Lỗi khởi động OLS: {str(ols_e)}")

        if len(domains) == 0:
            msg = f"Đã chuẩn bị xong cấu hình cho {ws.upper()}, nhưng hiện tại chưa có website nào trong cơ sở dữ liệu."
        elif fail_count > 0:
            msg = f"Tái tạo cấu hình {ws.upper()} hoàn tất: {success_count}/{len(domains)} website thành công, {fail_count} thất bại."
        else:
            msg = f"Tái tạo cấu hình {ws.upper()} thành công! Đã cập nhật {success_count} website từ cơ sở dữ liệu."

        return {
            "status": "ok" if fail_count == 0 else "warning",
            "webserver": ws,
            "message": msg,
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


class WebserverSettingsUpdateRequest(BaseModel):
    webserver: str = "ols"
    serverName: Optional[str] = None
    adminEmail: Optional[str] = None
    workerProcesses: Optional[str] = None
    runUser: Optional[str] = None
    runGroup: Optional[str] = None
    chrootPath: Optional[str] = None
    enableGzip: Optional[bool] = None
    enableBrotli: Optional[bool] = None
    gracefulTimeout: Optional[int] = None
    logLevel: Optional[str] = None
    rollingSize: Optional[str] = None
    keepDays: Optional[int] = None
    maxConnections: Optional[int] = None
    maxSslConnections: Optional[int] = None
    connTimeout: Optional[int] = None
    keepAliveRequests: Optional[int] = None
    maxBodySize: Optional[str] = None
    gzipLevel: Optional[int] = None
    allowSymbolLink: Optional[str] = None
    setUIDMode: Optional[str] = None
    rateLimitEnabled: Optional[bool] = None
    rateLimitReqSec: Optional[int] = None
    rateLimitBurst: Optional[int] = None


@router.get("/webserver-settings")
async def get_webserver_settings(webserver: str = "ols", current_user: dict = Depends(get_current_user)):
    """Lấy cấu hình chi tiết của Web Server (OpenLiteSpeed hoặc Nginx) để hiển thị trong các Tab cấu hình"""
    ws = webserver.lower()
    conf_path = "/usr/local/lsws/conf/httpd_config.conf" if ws == "ols" else "/etc/nginx/nginx.conf"
    if ws != "ols" and not os.path.exists(conf_path) and os.path.exists("/usr/local/openresty/nginx/conf/nginx.conf"):
        conf_path = "/usr/local/openresty/nginx/conf/nginx.conf"

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

    import re
    settings = {
        "serverName": "localhost",
        "adminEmail": "root@localhost",
        "workerProcesses": "auto",
        "runUser": "nobody",
        "runGroup": "nobody",
        "chrootPath": "/",
        "enableGzip": True,
        "enableBrotli": True,
        "defaultCharset": "UTF-8",
        "gracefulTimeout": 300,
        "logLevel": "DEBUG",
        "logFormat": "combined",
        "rollingSize": "10M",
        "keepDays": 30,
        "maxConnections": 10000,
        "maxSslConnections": 5000,
        "connTimeout": 60,
        "keepAliveRequests": 1000,
        "maxBodySize": "128M",
        "gzipLevel": 6,
        "clientBodyBufferSize": "128k",
        "allowSymbolLink": "2",
        "setUIDMode": "2",
        "serverTokens": False,
        "blockDotFiles": True,
        "blockWpConfig": True,
        "blockUploadsPhp": True,
        "blockXmlrpc": True,
        "rateLimitEnabled": True,
        "rateLimitReqSec": 20,
        "rateLimitBurst": 40,
        "lscacheEnabled": True,
        "modSecurityEnabled": True,
        "http2Enabled": True,
        "http3Enabled": True
    }

    if content:
        if ws == "ols":
            m = re.search(r"serverName\s+([^\r\n]+)", content)
            if m: settings["serverName"] = m.group(1).strip()
            m = re.search(r"adminEmails\s+([^\r\n]+)", content)
            if m: settings["adminEmail"] = m.group(1).strip()
            m = re.search(r"user\s+([^\r\n]+)", content)
            if m: settings["runUser"] = m.group(1).strip()
            m = re.search(r"group\s+([^\r\n]+)", content)
            if m: settings["runGroup"] = m.group(1).strip()
            m = re.search(r"logLevel\s+([A-Za-z]+)", content)
            if m: settings["logLevel"] = m.group(1).upper()
            m = re.search(r"maxConnections\s+(\d+)", content)
            if m: settings["maxConnections"] = int(m.group(1))
            m = re.search(r"maxSSLConnections\s+(\d+)", content)
            if m: settings["maxSslConnections"] = int(m.group(1))
            m = re.search(r"connTimeout\s+(\d+)", content)
            if m: settings["connTimeout"] = int(m.group(1))
            m = re.search(r"maxKeepAliveReq\s+(\d+)", content)
            if m: settings["keepAliveRequests"] = int(m.group(1))
            m = re.search(r"gracefulRestartTimeout\s+(\d+)", content)
            if m: settings["gracefulTimeout"] = int(m.group(1))
            m = re.search(r"allowSymbolLink\s+(\d+)", content)
            if m: settings["allowSymbolLink"] = m.group(1)
        else:
            m = re.search(r"worker_processes\s+([^;]+);", content)
            if m: settings["workerProcesses"] = m.group(1).strip()
            m = re.search(r"worker_connections\s+(\d+);", content)
            if m: settings["maxConnections"] = int(m.group(1))
            m = re.search(r"keepalive_timeout\s+(\d+);", content)
            if m: settings["connTimeout"] = int(m.group(1))
            m = re.search(r"client_max_body_size\s+([^;]+);", content)
            if m: settings["maxBodySize"] = m.group(1).strip()

    return {
        "status": "ok",
        "webserver": ws,
        "config_path": conf_path,
        "settings": settings,
        "content": content
    }


@router.post("/webserver-settings")
async def save_webserver_settings(req: WebserverSettingsUpdateRequest, current_user: dict = Depends(get_current_user)):
    """Lưu cấu hình máy chủ Web (OLS hoặc Nginx) và khởi động lại dịch vụ tương ứng"""
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Chỉ quản trị viên mới có quyền cập nhật cấu hình Webserver.")

    ws = req.webserver.lower()
    conf_path = "/usr/local/lsws/conf/httpd_config.conf" if ws == "ols" else "/etc/nginx/nginx.conf"
    if ws != "ols" and not os.path.exists(conf_path) and os.path.exists("/usr/local/openresty/nginx/conf/nginx.conf"):
        conf_path = "/usr/local/openresty/nginx/conf/nginx.conf"

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

    if not content:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy file cấu hình webserver tại {conf_path}")

    import re
    if ws == "ols":
        if req.serverName is not None and re.search(r"^serverName\s+.*$", content, flags=re.MULTILINE):
            content = re.sub(r"^serverName\s+.*$", f"serverName              {req.serverName}", content, flags=re.MULTILINE)
        if req.adminEmail is not None and re.search(r"^adminEmails\s+.*$", content, flags=re.MULTILINE):
            content = re.sub(r"^adminEmails\s+.*$", f"adminEmails            {req.adminEmail}", content, flags=re.MULTILINE)
        if req.logLevel is not None:
            content = re.sub(r"(logLevel\s+)[A-Za-z]+", rf"\g<1>{req.logLevel}", content)
        if req.rollingSize is not None:
            content = re.sub(r"(rollingSize\s+)[0-9]+[A-Za-z]*", rf"\g<1>{req.rollingSize}", content)
        if req.maxConnections is not None:
            content = re.sub(r"(maxConnections\s+)[0-9]+", rf"\g<1>{req.maxConnections}", content)
        if req.maxSslConnections is not None:
            content = re.sub(r"(maxSSLConnections\s+)[0-9]+", rf"\g<1>{req.maxSslConnections}", content)
        if req.connTimeout is not None:
            content = re.sub(r"(connTimeout\s+)[0-9]+", rf"\g<1>{req.connTimeout}", content)
        if req.keepAliveRequests is not None:
            content = re.sub(r"(maxKeepAliveReq\s+)[0-9]+", rf"\g<1>{req.keepAliveRequests}", content)
        if req.gracefulTimeout is not None:
            content = re.sub(r"(gracefulRestartTimeout\s+)[0-9]+", rf"\g<1>{req.gracefulTimeout}", content)
        if req.allowSymbolLink is not None:
            content = re.sub(r"(allowSymbolLink\s+)[0-9]+", rf"\g<1>{req.allowSymbolLink}", content)
    else:
        if req.workerProcesses is not None:
            content = re.sub(r"worker_processes\s+[^;]+;", f"worker_processes {req.workerProcesses};", content)
        if req.maxConnections is not None:
            content = re.sub(r"worker_connections\s+[0-9]+;", f"worker_connections {req.maxConnections};", content)
        if req.connTimeout is not None:
            content = re.sub(r"keepalive_timeout\s+[0-9]+;", f"keepalive_timeout {req.connTimeout};", content)
        if req.maxBodySize is not None:
            if "client_max_body_size" in content:
                content = re.sub(r"client_max_body_size\s+[^;]+;", f"client_max_body_size {req.maxBodySize};", content)
            else:
                content = re.sub(r"(http\s*\{)", rf"\1\n    client_max_body_size {req.maxBodySize};", content)

    # Ghi file cấu hình an toàn
    tmp_path = f"/tmp/wpc_{ws}_main_conf.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(content)

        try:
            shutil.move(tmp_path, conf_path)
        except PermissionError:
            subprocess.run(["sudo", "mv", tmp_path, conf_path], check=True)

        try:
            os.chmod(conf_path, 0o644)
        except Exception:
            subprocess.run(["sudo", "chmod", "644", conf_path], check=False)

        # Reload webserver
        if ws == "ols":
            subprocess.run(["/usr/local/lsws/bin/lswsctrl", "restart"], check=False)
            subprocess.run(["sudo", "systemctl", "reload", "lshttpd"], check=False)
        else:
            if os.path.exists("/usr/local/openresty/nginx/sbin/nginx"):
                subprocess.run(["sudo", "/usr/local/openresty/nginx/sbin/nginx", "-s", "reload"], check=False)
            else:
                subprocess.run(["sudo", "nginx", "-s", "reload"], check=False)
            subprocess.run(["sudo", "systemctl", "reload", "nginx"], check=False)

        return {
            "status": "ok",
            "message": f"Đã lưu và cập nhật cấu hình máy chủ {ws.upper()} thành công!"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi lưu file cấu hình máy chủ: {str(e)}")




