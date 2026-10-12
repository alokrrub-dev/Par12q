from dataclasses import dataclass, field
import os

from dotenv import load_dotenv


@dataclass(frozen=True, slots=True)
class Config:
    bot_token: str = field(repr=False)
    api_id: int
    api_hash: str = field(repr=False)
    session_string: str = field(repr=False)
    admin_id: int
    database_url: str = field(repr=False)
    port: int = 8080
    poll_interval: float = 2.0
    request_interval: float = 0.15
    page_size: int = 20
    max_pages: int = 3
    queue_size: int = 500
    log_level: str = "INFO"

    @classmethod
    def load(cls) -> "Config":
        load_dotenv()
        def required(key: str) -> str:
            value = os.getenv(key, "").strip()
            if not value:
                raise ValueError(f"Не задана переменная {key}")
            return value

        config = cls(
            bot_token=required("BOT_TOKEN"),
            api_id=int(required("API_ID")),
            api_hash=required("API_HASH"),
            session_string=required("SESSION_STRING"),
            admin_id=int(required("ADMIN_ID")),
            database_url=required("DATABASE_URL"),
            port=int(os.getenv("PORT", "8080")),
            poll_interval=float(os.getenv("POLL_INTERVAL", "2")),
            request_interval=float(os.getenv("REQUEST_INTERVAL", "0.25")),
            page_size=int(os.getenv("PAGE_SIZE", "20")),
            max_pages=int(os.getenv("MAX_PAGES", "1")),
            queue_size=int(os.getenv("QUEUE_SIZE", "500")),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        )
        if config.api_id <= 0 or not 0 < config.admin_id < 2**63:
            raise ValueError("API_ID и ADMIN_ID должны быть положительными")
        if not 1 <= config.poll_interval <= 3:
            raise ValueError("POLL_INTERVAL должен быть в диапазоне 1–3")
        if not 0.05 <= config.request_interval <= 5:
            raise ValueError("REQUEST_INTERVAL должен быть в диапазоне 0.05–5")
        if not 1 <= config.port <= 65535 or not 1 <= config.page_size <= 100:
            raise ValueError("Неверные PORT или PAGE_SIZE")
        if not 1 <= config.max_pages <= 10 or not 1 <= config.queue_size <= 5000:
            raise ValueError("Неверные MAX_PAGES или QUEUE_SIZE")
        return config
