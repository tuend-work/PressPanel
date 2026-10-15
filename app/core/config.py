import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "PressPanel"
    VERSION: str = "0.2.0"
    API_V1_STR: str = "/api/v1"
    
    # Server & Security
    HOST: str = "0.0.0.0"
    PORT: int = 8888
    SECRET_KEY: str = os.getenv("PRESSPANEL_SECRET_KEY", "change-this-in-production-presspanel-secret-key-123456")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days
    
    # Paths
    BASE_DIR: str = "/opt/presspanel"
    DB_PATH: str = os.getenv("PRESSPANEL_DB_PATH", "/opt/presspanel/data/panel.db")
    LOGS_DIR: str = "/opt/presspanel/logs"
    STATIC_DIR: str = "/opt/presspanel/static"

    class Config:
        env_file = ".env"

settings = Settings()
