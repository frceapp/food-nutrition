from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "NUTRIA"
    app_env: str = "development"
    app_timezone: str = "Asia/Bangkok"
    frontend_url: str = "http://localhost:5173"
    cors_origins: str = ""
    admin_phone_numbers: str = ""
    admin_login_code_minutes: int = Field(default=15, ge=1, le=30)
    firebase_project_id: str = ""
    firebase_credentials_path: Path | None = None
    # Dipakai hanya oleh scripts/migrate_mysql_to_firestore.py.
    database_name: str = "nutria"
    mysql_host: str = "localhost"
    mysql_port: int = Field(default=3306, ge=1, le=65535)
    mysql_user: str = "root"
    mysql_password: str = ""
    openrouter_api_key: str = ""
    openrouter_model: str = "google/gemini-2.5-flash-lite"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_timeout_seconds: float = Field(default=60.0, gt=0)
    openrouter_max_output_tokens: int = Field(default=1200, ge=512, le=4096)
    openrouter_app_name: str = "NUTRIA"
    openrouter_site_url: str | None = None
    max_image_size_mb: int = Field(default=5, gt=0, le=20)
    optimize_image: bool = True
    image_max_dimension: int = Field(default=1024, ge=512, le=2048)
    image_jpeg_quality: int = Field(default=75, ge=50, le=95)
    image_max_pixels: int = Field(default=25_000_000, ge=1_000_000, le=50_000_000)
    upload_dir: Path = Path("uploads")
    log_level: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @property
    def max_image_size_bytes(self) -> int:
        return self.max_image_size_mb * 1024 * 1024

    @property
    def bootstrap_admin_phones(self) -> list[str]:
        return [
            phone.strip().lstrip("+")
            for phone in self.admin_phone_numbers.split(",")
            if phone.strip()
        ]

    @property
    def allowed_cors_origins(self) -> list[str]:
        origins = [
            origin.strip().rstrip("/")
            for origin in self.cors_origins.split(",")
            if origin.strip()
        ]
        frontend_origin = self.frontend_url.strip().rstrip("/")
        if frontend_origin and frontend_origin not in origins:
            origins.append(frontend_origin)
        return origins

    @property
    def cors_origin_regex(self) -> str | None:
        if self.app_env.lower() != "development":
            return None
        return (
            r"^https?://(localhost|127\.0\.0\.1|"
            r"10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|"
            r"172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})"
            r"(?::\d+)?$"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
