import os
from typing import Dict, Any

def parse_wpc_conf(conf_path: str) -> Dict[str, str]:
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
    for env_k in ["PRESSPANEL_CONF_PATH", "WPC_CONF_PATH"]:
        if env_k in os.environ and os.path.isfile(os.environ[env_k]):
            return os.environ[env_k]
    # 2. Vị trí chính thức trên máy chủ Linux (/opt/presspanel hoặc /opt/wpc)
    for sys_cand in ["/opt/presspanel/conf/presspanel.conf", "/opt/wpc/conf/wpc.conf"]:
        if os.path.isfile(sys_cand):
            return sys_cand
    # 3. Vị trí trong thư mục dự án khi dev
    base_proj = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for local_cand in [
        os.path.join(base_proj, "conf", "presspanel.conf"),
        os.path.join(base_proj, "conf", "wpc.conf")
    ]:
        if os.path.isfile(local_cand):
            return local_cand
    return "/opt/presspanel/conf/presspanel.conf"

CONF_DATA = parse_wpc_conf(get_conf_path())

def get_or_create_secret_key() -> str:
    env_key = os.getenv("PRESSPANEL_SECRET_KEY") or os.getenv("WPC_SECRET_KEY")
    if env_key and len(env_key) >= 16 and "change-this" not in env_key and "change-me" not in env_key:
        return env_key
    conf_key = CONF_DATA.get("secret_key", "")
    if conf_key and len(conf_key) >= 16 and "change-this" not in conf_key and "change-me" not in conf_key:
        return conf_key

    # Tự động sinh và lưu khóa an toàn ngẫu nhiên nếu chưa có hoặc đang dùng khóa mặc định
    key_file = os.path.join(CONF_DATA.get("data_dir", "/opt/presspanel/data"), ".secret_key")
    if not os.path.isdir(os.path.dirname(key_file)):
        base_proj = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        key_file = os.path.join(base_proj, "data", ".secret_key")

    try:
        if os.path.isfile(key_file):
            with open(key_file, "r", encoding="utf-8") as f:
                k = f.read().strip()
                if len(k) >= 32:
                    return k
        import secrets
        new_k = secrets.token_hex(32)
        os.makedirs(os.path.dirname(key_file), exist_ok=True)
        with open(key_file, "w", encoding="utf-8") as f:
            f.write(new_k)
        try:
            os.chmod(key_file, 0o600)
        except Exception:
            pass
        return new_k
    except Exception:
        return conf_key or "presspanel-fallback-secret-key-32-chars-long-minimum!!"

def get_current_panel_version() -> str:
    for cand in [
        "/opt/presspanel/VERSION",
        "/opt/wpc/VERSION",
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "VERSION")
    ]:
        if os.path.isfile(cand):
            try:
                with open(cand, "r", encoding="utf-8") as f:
                    v = f.read().strip()
                    if v:
                        return v
            except Exception:
                pass
    return "0.5.205"

class Settings:
    PROJECT_NAME: str = "PressPanel"
    VERSION: str = get_current_panel_version()
    API_V1_STR: str = "/api/v1"
    
    # Mạng & Cổng kết nối
    HOST: str = os.getenv("PRESSPANEL_HOST", os.getenv("WPC_HOST", CONF_DATA.get("bind_address", "0.0.0.0")))
    PORT: int = int(os.getenv("PRESSPANEL_PORT", os.getenv("WPC_PORT", CONF_DATA.get("port", 8888))))
    SERVERNAME: str = CONF_DATA.get("servername", "localhost")
    
    # Bảo mật & JWT (Tự sinh ngẫu nhiên nếu chưa cấu hình)
    SECRET_KEY: str = get_or_create_secret_key()
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(CONF_DATA.get("access_token_expire_days", 7)) * 24 * 60
    SESSION_TIMEOUT: int = int(CONF_DATA.get("session_timeout", 60))
    
    # SSL Panel
    ENABLE_SSL: bool = CONF_DATA.get("ssl", "0") in ("1", "true", "yes")
    SSL_CERT: str = CONF_DATA.get("ssl_cert", f"{CONF_DATA.get('base_dir', '/opt/presspanel')}/data/ssl/panel.crt")
    SSL_KEY: str = CONF_DATA.get("ssl_key", f"{CONF_DATA.get('base_dir', '/opt/presspanel')}/data/ssl/panel.key")
    
    # Dịch vụ mặc định
    DEFAULT_WEBSERVER: str = CONF_DATA.get("default_webserver", "ols")
    DEFAULT_PHP: str = CONF_DATA.get("default_php", "8.2")
    
    # Đường dẫn hệ thống (/opt/presspanel)
    BASE_DIR: str = CONF_DATA.get("base_dir", "/opt/presspanel")
    BIN_DIR: str = CONF_DATA.get("bin_dir", f"{BASE_DIR}/bin")
    DATA_DIR: str = CONF_DATA.get("data_dir", f"{BASE_DIR}/data")
    
    # DB_PATH: Mặc định /opt/presspanel/data/panel.db
    DB_PATH: str = os.getenv("PRESSPANEL_DB_PATH", os.getenv("WPC_DB_PATH", CONF_DATA.get("database_path", f"{BASE_DIR}/data/panel.db")))
    LOGS_DIR: str = CONF_DATA.get("logs_dir", f"{BASE_DIR}/logs")
    STATIC_DIR: str = CONF_DATA.get("static_dir", f"{BASE_DIR}/static")
    BACKUP_DIR: str = CONF_DATA.get("backup_storage_path", f"{BASE_DIR}/data/backups")
    
    # Tính năng
    ENABLE_CAGE: bool = CONF_DATA.get("enable_cage", "1") in ("1", "true", "yes")
    ENABLE_CGROUP: bool = CONF_DATA.get("enable_cgroup", "1") in ("1", "true", "yes")
    ENABLE_DISK_QUOTA: bool = CONF_DATA.get("enable_disk_quota", "1") in ("1", "true", "yes")
    AUTO_RENEW_SSL: bool = CONF_DATA.get("auto_renew_ssl", "1") in ("1", "true", "yes")

settings = Settings()
