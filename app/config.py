"""Modul konfigurasi aplikasi ADMS ZKTeco."""
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Info Aplikasi & Jaringan
    APP_NAME: str = "ADMS ZKTeco Server"
    APP_VERSION: str = "1.0.0"
    HOST: str = "0.0.0.0"
    PORT: int = 80
    LOG_LEVEL: str = "INFO"

    # Database URLs
    # Default ke PostgreSQL, namun jika di dev tanpa PG bisa fallback ke sqlite async
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/adms_db"
    DATABASE_URL_SYNC: str = "postgresql://postgres:postgres@localhost:5432/adms_db"

    # Keamanan & Biometrik
    SECRET_KEY: str = "insecure-secret-key-please-change-in-production-min32bytes"
    # Kunci enkripsi 32-karakter untuk AES-256-GCM
    BIOMETRIC_ENCRYPTION_KEY: str = "12345678901234567890123456789012"

    # Protokol iClock / Mesin
    DEFAULT_TZ_OFFSET_MINUTES: int = 420  # +07:00 (WIB)
    ICLOCK_DELAY: int = 30                # Polling interval (detik)
    ICLOCK_ERROR_DELAY: int = 30          # Retry delay bila ada error
    ICLOCK_TRANS_INTERVAL: int = 1        # Interval transmisi
    ALLOW_UNKNOWN_DEVICES: bool = True    # Otomatis daftarkan serial number baru

    # Device Command Queue
    COMMAND_MAX_ATTEMPTS: int = 5
    COMMAND_TIMEOUT_SECONDS: int = 300    # 5 menit

    # Outbox Forwarder (Sistem Hilir HR / Payroll)
    OUTBOX_ENABLED: bool = False
    OUTBOX_TARGET_URL: Optional[str] = None
    OUTBOX_AUTH_HEADER: Optional[str] = None
    OUTBOX_BATCH_SIZE: int = 50
    OUTBOX_POLL_INTERVAL: int = 5
    OUTBOX_MAX_ATTEMPTS: int = 10

    # Retensi raw requests
    RAW_REQUEST_RETENTION_DAYS: int = 90


settings = Settings()
