import os
from typing import Dict, Any

def parse_presspanel_conf(conf_path: str) -> Dict[str, str]:
    """
    Đọc và parse file cấu hình dạng key=value giống directadmin.conf
    """
    config_dict = {}
    if not os.path.isfile(conf_path):
        return config_dict

    try:
        with open(conf_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip().lower()
                    val = val.strip()
                    config_dict[key] = val
    except Exception as e:
        print(f"[-] Khong the doc file {conf_path}: {e}")
    
    return config_dict

def get_conf_path() -> str:
    # 1. Từ biến môi trường
    if "PRESSPANEL_CONF_PATH" in os.environ and os.path.isfile(os.environ["PRESSPANEL_CONF_PATH"]):
        return os.environ["PRESSPANEL_CONF_PATH"]
    # 2. Vị trí chính thức trên máy chủ Linux
    if os.path.isfile("/opt/presspanel/conf/presspanel.conf"):
        return "/opt/presspanel/conf/presspanel.conf"
    # 3. Vị trí trong thư mục dự án khi dev
    local_conf = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "conf", "presspanel.conf")
    if os.path.isfile(local_conf):
        return local_conf
    return "/opt/presspanel/conf/presspanel.conf"

CONF_DATA = parse_presspanel_conf(get_conf_path())

class Settings:
    PROJECT_NAME: str = "PressPanel"
    VERSION: str = "0.2.2"
    API_V1_STR: str = "/api/v1"
    
    # Mạng & Cổng kết nối
    HOST: str = os.getenv("PRESSPANEL_HOST", CONF_DATA.get("bind_address", "0.0.0.0"))
    PORT: int = int(os.getenv("PRESSPANEL_PORT", CONF_DATA.get("port", 8888)))
    SERVERNAME: str = CONF_DATA.get("servername", "localhost")
    
    # Bảo mật & JWT
    SECRET_KEY: str = os.getenv("PRESSPANEL_SECRET_KEY", CONF_DATA.get("secret_key", "change-this-in-production-presspanel-secret-key-123456"))
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(CONF_DATA.get("access_token_expire_days", 7)) * 24 * 60
    SESSION_TIMEOUT: int = int(CONF_DATA.get("session_timeout", 60))
    
    # SSL Panel
    ENABLE_SSL: bool = CONF_DATA.get("ssl", "0") in ("1", "true", "yes")
    SSL_CERT: str = CONF_DATA.get("ssl_cert", "/opt/presspanel/data/ssl/panel.crt")
    SSL_KEY: str = CONF_DATA.get("ssl_key", "/opt/presspanel/data/ssl/panel.key")
    
    # Dịch vụ mặc định
    DEFAULT_WEBSERVER: str = CONF_DATA.get("default_webserver", "ols")
    DEFAULT_PHP: str = CONF_DATA.get("default_php", "8.2")
    
    # Đường dẫn hệ thống
    BASE_DIR: str = CONF_DATA.get("base_dir", "/opt/presspanel")
    BIN_DIR: str = CONF_DATA.get("bin_dir", "/opt/presspanel/bin")
    DATA_DIR: str = CONF_DATA.get("data_dir", "/opt/presspanel/data")
    DB_PATH: str = os.getenv("PRESSPANEL_DB_PATH", CONF_DATA.get("database_path", "/opt/presspanel/data/panel.db"))
    LOGS_DIR: str = CONF_DATA.get("logs_dir", "/opt/presspanel/logs")
    STATIC_DIR: str = CONF_DATA.get("static_dir", "/opt/presspanel/static")
    BACKUP_DIR: str = CONF_DATA.get("backup_storage_path", "/opt/presspanel/data/backups")
    
    # Tính năng
    ENABLE_CAGE: bool = CONF_DATA.get("enable_cage", "1") in ("1", "true", "yes")
    ENABLE_CGROUP: bool = CONF_DATA.get("enable_cgroup", "1") in ("1", "true", "yes")
    ENABLE_DISK_QUOTA: bool = CONF_DATA.get("enable_disk_quota", "1") in ("1", "true", "yes")
    AUTO_RENEW_SSL: bool = CONF_DATA.get("auto_renew_ssl", "1") in ("1", "true", "yes")

settings = Settings()
