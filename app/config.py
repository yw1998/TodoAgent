from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class EnvSettings(BaseSettings):
    """环境变量/.env 默认值；DB settings 表中的同名配置优先级更高。"""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str = "sqlite+aiosqlite:///./data/todoagent.db"
    secret_key: str = "change-me-in-production"

    admin_init_username: str = "admin"
    admin_init_password: str = "admin123"

    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_temperature: float = 0.1
    llm_timeout: int = 30

    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    feishu_verification_token: str = ""
    feishu_encrypt_key: str = ""

    scheduler_timezone: str = "Asia/Shanghai"
    scheduler_tick_seconds: int = 30
    scheduler_max_retry: int = 3

    reminder_max_times: int = 3
    reminder_interval_minutes: int = 5

    worker_concurrency: int = 4
    context_rounds: int = 10
    log_level: str = "INFO"


@lru_cache
def get_env() -> EnvSettings:
    return EnvSettings()
