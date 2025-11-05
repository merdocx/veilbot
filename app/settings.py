from __future__ import annotations

from typing import Dict, Any
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Unified application settings loaded from environment/.env.

    Validation is explicit via validate_startup(); do not raise on import.
    """

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")

    # Bot configuration
    TELEGRAM_BOT_TOKEN: str | None = Field(default=None)

    # YooKassa
    YOOKASSA_SHOP_ID: str | None = Field(default=None)
    YOOKASSA_API_KEY: str | None = Field(default=None)
    YOOKASSA_RETURN_URL: str | None = Field(default=None)

    # CryptoBot
    CRYPTOBOT_API_TOKEN: str | None = Field(default=None, description="CryptoBot API Token")
    CRYPTOBOT_API_URL: str = Field(default="https://pay.crypt.bot/api", description="CryptoBot API URL")
    CRYPTOBOT_WEBHOOK_SECRET: str | None = Field(default=None, description="CryptoBot webhook secret (optional)")
    CRYPTOBOT_DEFAULT_ASSET: str = Field(default="USDT", description="Default cryptocurrency asset")
    CRYPTOBOT_DEFAULT_NETWORK: str = Field(default="TRC20", description="Default network for USDT")

    # Database
    DATABASE_PATH: str = Field(default="vpn.db")
    DB_ENCRYPTION_KEY: str | None = Field(default=None)

    # Admin panel
    ADMIN_USERNAME: str = Field(default="admin")
    ADMIN_PASSWORD_HASH: str | None = Field(default=None)
    SECRET_KEY: str | None = Field(default=None)

    # Bot admin
    ADMIN_ID: int = Field(default=46701395)

    # Support contact
    SUPPORT_USERNAME: str | None = Field(default=None)

    # Session
    SESSION_MAX_AGE: int = Field(default=3600)
    SESSION_SECURE: bool = Field(default=True)

    # Rate limiting
    RATE_LIMIT_LOGIN: str = Field(default="5/minute")
    RATE_LIMIT_API: str = Field(default="100/minute")

    # Protocols
    PROTOCOLS: Dict[str, Dict[str, Any]] = Field(
        default_factory=lambda: {
            "outline": {
                "name": "Outline VPN",
                "description": "Современный VPN протокол с высокой скоростью",
                "icon": "🔒",
                "default_port": 443,
            },
            "v2ray": {
                "name": "V2Ray VLESS",
                "description": "Продвинутый протокол с обфускацией трафика и Reality",
                "icon": "🛡️",
                "default_port": 443,
                "default_path": "/v2ray",
            },
        }
    )

    # Admin CORS
    ADMIN_ALLOWED_ORIGINS: list[str] | str = Field(default_factory=list)

    @field_validator("ADMIN_ALLOWED_ORIGINS", mode="before")
    @classmethod
    def _parse_admin_allowed_origins(cls, value):
        """Allow env to be provided as JSON array or comma-separated string."""
        if value is None or value == "":
            return []
        if isinstance(value, (list, tuple)):
            return [str(v).strip() for v in value if str(v).strip()]
        if isinstance(value, str):
            s = value.strip()
            # Try JSON first
            try:
                import json
                parsed = json.loads(s)
                if isinstance(parsed, (list, tuple)):
                    return [str(v).strip() for v in parsed if str(v).strip()]
            except Exception:
                pass
            # Fallback to comma-separated
            return [part.strip() for part in s.split(",") if part.strip()]
        return []

    @field_validator("SESSION_MAX_AGE")
    @classmethod
    def _session_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("SESSION_MAX_AGE must be positive")
        return v

    def validate_startup(self) -> dict:
        """Perform non-fatal configuration validation for startup.

        Returns a dict with errors/warnings; caller decides how to handle.
        """
        errors: list[str] = []
        warnings: list[str] = []

        # Telegram token
        if not self.TELEGRAM_BOT_TOKEN:
            errors.append("TELEGRAM_BOT_TOKEN is required")

        # YooKassa
        if not self.YOOKASSA_SHOP_ID or not self.YOOKASSA_API_KEY:
            errors.append("YOOKASSA_SHOP_ID and YOOKASSA_API_KEY are required")
        if not self.YOOKASSA_RETURN_URL:
            errors.append("YOOKASSA_RETURN_URL is required")

        # Admin
        if not self.ADMIN_PASSWORD_HASH:
            warnings.append("ADMIN_PASSWORD_HASH not set - admin panel may not work")
        if not self.SECRET_KEY:
            warnings.append("SECRET_KEY not set - using ephemeral (insecure)")

        return {"errors": errors, "warnings": warnings, "is_valid": len(errors) == 0}


# Singleton settings instance
settings = Settings()


