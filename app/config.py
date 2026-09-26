"""应用配置：全部从环境变量读取，无变量时使用安全默认值。"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


class Settings:
    """轻量设置对象（不依赖 pydantic-settings）。"""

    def __init__(self) -> None:
        self.app_name = os.environ.get("BIZPORTAL_APP_NAME", "BizPortal")
        self.secret_key = os.environ.get("BIZPORTAL_SECRET_KEY", "dev-only-insecure-secret-key")
        self.database_url = os.environ.get("BIZPORTAL_DATABASE_URL", "")
        self.data_dir = Path(os.environ.get("BIZPORTAL_DATA_DIR", str(BASE_DIR / "data")))
        self.debug = _env_bool("BIZPORTAL_DEBUG", False)
        # 默认管理员（首次初始化时写入）
        self.bootstrap_admin_email = os.environ.get("BIZPORTAL_ADMIN_EMAIL", "admin")
        self.bootstrap_admin_password = os.environ.get("BIZPORTAL_ADMIN_PASSWORD", "Admin@123")
        # 单进程内缓存目录创建标记
        self.upload_dir = self.data_dir / "uploads"
        self.upload_dir.mkdir(parents=True, exist_ok=True)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "bizportal.db"

    def resolve_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{self.db_path}"


settings = Settings()
