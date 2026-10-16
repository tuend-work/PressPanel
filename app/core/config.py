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
    for env_var in ("WPC_CONF_PATH", "PRESSPANEL_CONF_PATH"):
        if env_var in os.environ and os.path.isfile(os.environ[env_var]):
            return os.environ[env_var]
    # 2. Vị trí chính thức trên máy chủ Linux (/opt/wpc hoặc /opt/presspanel)
    for p in ("/opt/wpc/conf/wpc.conf", "/opt/wpc/conf/presspanel.conf", "/opt/presspanel/conf/presspanel.conf"):
        if os.path.isfile(p):
            return p
    # 3. Vị trí trong thư mục dự án khi dev
    base_proj = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for conf_name in ("wpc.conf", "presspanel.conf"):
        local_conf = os.path.join(base_proj, "conf", conf_name)
        if os.path.isfile(local_conf):
            return local_conf
    return "/opt/wpc/conf/wpc.conf"

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
    SSL_CERT: str = CONF_DATA.get("ssl_cert", f"{CONF_DATA.get('base_dir', '/opt/wpc')}/data/ssl/panel.crt")
    SSL_KEY: str = CONF_DATA.get("ssl_key", f"{CONF_DATA.get('base_dir', '/opt/wpc')}/data/ssl/panel.key")
    
    # Dịch vụ mặc định
    DEFAULT_WEBSERVER: str = CONF_DATA.get("default_webserver", "ols")
    DEFAULT_PHP: str = CONF_DATA.get("default_php", "8.2")
    
    # Đường dẫn hệ thống (Ưu tiên tuyệt đối /opt/wpc)
    DEFAULT_BASE = "/opt/wpc" if os.path.exists("/opt/wpc") or not os.path.exists("/opt/presspanel") else "/opt/presspanel"
    BASE_DIR: str = CONF_DATA.get("base_dir", DEFAULT_BASE)
    BIN_DIR: str = CONF_DATA.get("bin_dir", f"{BASE_DIR}/bin")
    DATA_DIR: str = CONF_DATA.get("data_dir", f"{BASE_DIR}/data")
    
    # DB_PATH: Luôn chuẩn hoá nếu CONF_DATA chứa đường dẫn cũ /opt/presspanel
    _raw_db_path = os.getenv("WPC_DB_PATH", os.getenv("PRESSPANEL_DB_PATH", CONF_DATA.get("database_path", f"{BASE_DIR}/data/panel.db")))
    if _raw_db_path.startswith("/opt/presspanel/") and os.path.exists("/opt/wpc"):
        _raw_db_path = _raw_db_path.replace("/opt/presspanel/", "/opt/wpc/")
    DB_PATH: str = _raw_db_path
    LOGS_DIR: str = CONF_DATA.get("logs_dir", f"{BASE_DIR}/logs")
    STATIC_DIR: str = CONF_DATA.get("static_dir", f"{BASE_DIR}/static")
    BACKUP_DIR: str = CONF_DATA.get("backup_storage_path", f"{BASE_DIR}/data/backups")
    
    # Tính năng
    ENABLE_CAGE: bool = CONF_DATA.get("enable_cage", "1") in ("1", "true", "yes")
    ENABLE_CGROUP: bool = CONF_DATA.get("enable_cgroup", "1") in ("1", "true", "yes")
    ENABLE_DISK_QUOTA: bool = CONF_DATA.get("enable_disk_quota", "1") in ("1", "true", "yes")
    AUTO_RENEW_SSL: bool = CONF_DATA.get("auto_renew_ssl", "1") in ("1", "true", "yes")

settings = Settings()
