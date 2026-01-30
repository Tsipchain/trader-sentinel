from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Server
    host: str = "0.0.0.0"
    port: int = 8081

    # Market venues
    cex_venues: str = "binance,bybit,okx,mexc"  # comma-separated
    cex_min_interval_ms: int = 600

    # DEX
    dexscreener_enabled: bool = True

    # Optional Google TTS
    google_tts_enabled: bool = False
    google_application_credentials: str | None = None
    google_sa_json_base64: str | None = None
    google_tts_voice: str = "en-US-Neural2-D"
    google_tts_language: str = "en-US"


settings = Settings()
