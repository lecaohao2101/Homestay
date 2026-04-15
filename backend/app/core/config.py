from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "Homestay API"
    API_V1_STR: str = "/api/v1"
    ENVIRONMENT: str = "development"

    SECRET_KEY: str = "change-this-secret-key-in-production-123456"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    ALGORITHM: str = "HS256"
    PASSWORD_MIN_LENGTH: int = 10

    MONGO_URI: str = "mongodb://localhost:27017"
    MONGO_DB_NAME: str = "homestay"
    BACKEND_CORS_ORIGINS: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    ALLOWED_HOSTS: list[str] = Field(default_factory=lambda: ["localhost", "127.0.0.1"])

    LOGIN_MAX_ATTEMPTS: int = 5
    LOGIN_WINDOW_SECONDS: int = 300
    LOGIN_LOCKOUT_SECONDS: int = 600
    PENDING_BOOKING_TTL_MINUTES: int = 15
    PAYMENT_TIMEOUT_MINUTES: int = 15
    PAYMENT_CALLBACK_MAX_AGE_SECONDS: int = 900
    PAYMENT_WEBHOOK_EVENTS_TTL_DAYS: int = 30
    PAYMENT_AUDIT_LOGS_TTL_DAYS: int = 180
    PAYMENT_WEBHOOK_DEAD_LETTERS_TTL_DAYS: int = 90
    REFUND_WEBHOOK_DEAD_LETTERS_TTL_DAYS: int = 90
    JOB_DEAD_LETTERS_TTL_DAYS: int = 180
    AUTH_LOGIN_RATE_LIMIT: int = 20
    AUTH_LOGIN_RATE_WINDOW_SECONDS: int = 60
    AUTH_REGISTER_RATE_LIMIT: int = 10
    AUTH_REGISTER_RATE_WINDOW_SECONDS: int = 60
    AUTH_REFRESH_RATE_LIMIT: int = 30
    AUTH_REFRESH_RATE_WINDOW_SECONDS: int = 60
    AUTH_LOGOUT_RATE_LIMIT: int = 30
    AUTH_LOGOUT_RATE_WINDOW_SECONDS: int = 60
    PAYMENTS_CREATE_RATE_LIMIT: int = 20
    PAYMENTS_CREATE_RATE_WINDOW_SECONDS: int = 60
    PAYMENTS_WEBHOOK_RATE_LIMIT: int = 120
    PAYMENTS_WEBHOOK_RATE_WINDOW_SECONDS: int = 60
    REFUNDS_ADMIN_RATE_LIMIT: int = 60
    REFUNDS_ADMIN_RATE_WINDOW_SECONDS: int = 60
    REFUNDS_WEBHOOK_RATE_LIMIT: int = 120
    REFUNDS_WEBHOOK_RATE_WINDOW_SECONDS: int = 60
    PAYMENT_WEBHOOK_ALLOWED_IPS: list[str] = Field(default_factory=lambda: ["*"])
    REFUND_WEBHOOK_ALLOWED_IPS: list[str] = Field(default_factory=lambda: ["*"])
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30
    MONEY_BACKFILL_MAX_RETRIES: int = 3
    MONEY_BACKFILL_RETRY_DELAY_SECONDS: int = 30

    VNPAY_TMN_CODE: str = "demo"
    VNPAY_HASH_SECRET: str = "replace-with-vnpay-secret"
    VNPAY_PAYMENT_URL: str = "https://sandbox.vnpayment.vn/paymentv2/vpcpay.html"
    VNPAY_RETURN_URL: str = "http://localhost:3000/payment/return"
    VNPAY_IPN_URL: str = "http://localhost:8000/api/v1/payments/vnpay/ipn"
    VNPAY_ENABLED: bool = True
    VNPAY_DISPLAY_ORDER: int = 10
    VNPAY_MAINTENANCE_MESSAGE: str | None = None
    VNPAY_ICON_URL: str | None = None
    MOMO_PARTNER_CODE: str = "MOMO_DEMO"
    MOMO_ACCESS_KEY: str = "replace-with-momo-access-key"
    MOMO_SECRET_KEY: str = "replace-with-momo-secret-key"
    MOMO_PAYMENT_URL: str = "https://test-payment.momo.vn/v2/gateway/api/create"
    MOMO_RETURN_URL: str = "http://localhost:3000/payment/momo-return"
    MOMO_IPN_URL: str = "http://localhost:8000/api/v1/payments/momo/ipn"
    MOMO_REQUEST_TYPE: str = "captureWallet"
    MOMO_ENABLED: bool = True
    MOMO_DISPLAY_ORDER: int = 20
    MOMO_MAINTENANCE_MESSAGE: str | None = None
    MOMO_ICON_URL: str | None = None
    REFUND_FULL_HOURS: int = 72
    REFUND_PARTIAL_HOURS: int = 24
    REFUND_PARTIAL_PERCENT: int = 50
    REFUND_WEBHOOK_SECRET: str = "replace-with-refund-webhook-secret"
    REFUND_RECONCILE_TIMEOUT_MINUTES: int = 30
    REFUND_RECONCILE_JOB_ENABLED: bool = True
    REFUND_RECONCILE_INTERVAL_SECONDS: int = 60
    REFUND_RECONCILE_MAX_CONSECUTIVE_FAILURES: int = 5
    REFUND_RECONCILE_RETRY_BASE_DELAY_SECONDS: int = 5
    REFUND_RECONCILE_RETRY_MAX_DELAY_SECONDS: int = 300

    MEDIA_STORAGE_PROVIDER: str = "local"
    MEDIA_LOCAL_DIR: str = "./media"
    MEDIA_BASE_URL: str = "/api/v1/media/files"
    MEDIA_MAX_FILE_SIZE_MB: int = 5
    MEDIA_ALLOWED_TYPES: list[str] = Field(
        default_factory=lambda: ["image/jpeg", "image/png", "image/webp"]
    )

    ADMIN_EMAIL: str = "admin@homestay.local"
    ADMIN_PASSWORD: str = "ChangeMeAdmin@123"
    ADMIN_FULL_NAME: str = "System Admin"

    @field_validator("SECRET_KEY")
    @classmethod
    def validate_secret_key(cls, value: str) -> str:
        if len(value) < 32:
            raise ValueError("SECRET_KEY must be at least 32 characters")
        return value

    @field_validator("ENVIRONMENT")
    @classmethod
    def validate_environment(cls, value: str) -> str:
        allowed = {"development", "staging", "production"}
        normalized = value.lower()
        if normalized not in allowed:
            raise ValueError("ENVIRONMENT must be one of: development, staging, production")
        return normalized

    @model_validator(mode="after")
    def validate_production_secrets(self):
        if self.ENVIRONMENT != "production":
            return self

        disallowed = {
            "replace-with-vnpay-secret",
            "replace-with-momo-secret-key",
            "replace-with-momo-access-key",
            "replace-with-refund-webhook-secret",
            "ChangeMeAdmin@123",
        }
        secret_fields = {
            "VNPAY_HASH_SECRET": self.VNPAY_HASH_SECRET,
            "MOMO_SECRET_KEY": self.MOMO_SECRET_KEY,
            "MOMO_ACCESS_KEY": self.MOMO_ACCESS_KEY,
            "REFUND_WEBHOOK_SECRET": self.REFUND_WEBHOOK_SECRET,
            "ADMIN_PASSWORD": self.ADMIN_PASSWORD,
        }
        for key, value in secret_fields.items():
            if value in disallowed or len(value.strip()) < 16:
                raise ValueError(f"{key} is not secure enough for production")
        return self

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
    )


settings = Settings()
