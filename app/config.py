from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


class Settings:
    def __init__(self) -> None:
        self.E621_USERNAME: str = os.getenv("E621_USERNAME", "")
        self.E621_API_KEY: str = os.getenv("E621_API_KEY", "")
        self.TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")
        self.TELEGRAM_INGEST_CHAT_ID: str = os.getenv("TELEGRAM_INGEST_CHAT_ID", "")

        self.HOST: str = os.getenv("HOST", "0.0.0.0")
        self.PORT: int = int(os.getenv("PORT", "8000"))

        self.MIN_INTERVAL_SECONDS: int = int(os.getenv("MIN_INTERVAL_SECONDS", "3600"))
        self.MAX_INTERVAL_SECONDS: int = int(os.getenv("MAX_INTERVAL_SECONDS", "5400"))

        self.DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./auto_yiff.db")

        self.E621_LIMIT: int = 100

        self.BALANCE_IMAGE_THRESHOLD: float = 0.60
        self.BALANCE_MIN_QUEUE_SIZE: int = 15
        self.DISLIKE_THRESHOLD: int = 10

    @property
    def user_agent(self) -> str:
        return f"AutoYiff/1.0 (by {self.E621_USERNAME} on e621)"


settings = Settings()
