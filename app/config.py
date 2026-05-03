from __future__ import annotations

import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    E621_USERNAME: str = os.getenv("E621_USERNAME", "")
    E621_API_KEY: str = os.getenv("E621_API_KEY", "")
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))

    MIN_INTERVAL_SECONDS: int = int(os.getenv("MIN_INTERVAL_SECONDS", "3000"))
    MAX_INTERVAL_SECONDS: int = int(os.getenv("MAX_INTERVAL_SECONDS", "3600"))

    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./auto_yiff.db")

    E621_LIMIT: int = 100

    # Busca: AND obrigatórios + OR temático (~ = OR no e621) + novos fetishes + blacklist
    E621_TAGS: str = (
        "male gay "
        "knotted_penis cum_inflation "
        "~feral ~animal_genitalia ~animal_penis "
        "~equine_penis ~equine_genitalia ~canine_genitalia "
        "~femboy ~knot "
        "-road -machine -car -aircraft -airplane -radiation -gore "
        "-vore -imminent_vore -anal_vore -soft_vore -diaper "
        "order:random rating:e"
    )

    E621_TAGS_GIF: str = (
        "male gay "
        "knotted_penis cum_inflation "
        "~feral ~animal_genitalia ~animal_penis "
        "~equine_penis ~equine_genitalia ~canine_genitalia "
        "~femboy ~knot "
        "-road -machine -car -aircraft -airplane -radiation -gore "
        "-vore -imminent_vore -anal_vore -soft_vore -diaper "
        "type:gif order:random rating:e"
    )

    E621_TAGS_VIDEO: str = (
        "male gay "
        "knotted_penis cum_inflation "
        "~feral ~animal_genitalia ~animal_penis "
        "~equine_penis ~equine_genitalia ~canine_genitalia "
        "~femboy ~knot "
        "-road -machine -car -aircraft -airplane -radiation -gore "
        "-vore -imminent_vore -anal_vore -soft_vore -diaper "
        "type:webm order:random rating:e"
    )

    BALANCE_IMAGE_THRESHOLD: float = 0.60
    BALANCE_MIN_QUEUE_SIZE: int = 15
    DISLIKE_THRESHOLD: int = 10

    # Tags que DEVEM existir em pelo menos um subgrupo (verificação client-side)
    E621_REQUIRED_ANY: frozenset = frozenset({
        "feral", "animal_genitalia", "animal_penis",
        "equine_penis", "equine_genitalia", "canine_genitalia",
    })

    E621_BLACKLIST: frozenset = frozenset({
        "female", "human", "cub", "young", "juvenile", "gore",
        "road", "machine", "car", "aircraft", "airplane", "radiation",
        "vore", "imminent_vore", "anal_vore", "soft_vore", "diaper",
    })

    @property
    def user_agent(self) -> str:
        return f"AutoYiff/1.0 (by {self.E621_USERNAME} on e621)"


settings = Settings()
