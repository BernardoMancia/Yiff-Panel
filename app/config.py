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

    MIN_INTERVAL_SECONDS: int = int(os.getenv("MIN_INTERVAL_SECONDS", "3600"))
    MAX_INTERVAL_SECONDS: int = int(os.getenv("MAX_INTERVAL_SECONDS", "5400"))

    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./auto_yiff.db")

    E621_LIMIT: int = 100

    # Busca: AND obrigatórios + OR temático (~ = OR no e621) + novos fetishes + blacklist
    E621_TAGS: str = (
        "male gay "
        "knotted_penis cum_inflation "
        "~feral ~animal_genitalia ~animal_penis "
        "~equine_penis ~equine_genitalia ~canine_genitalia "
        "-road -machine -car -aircraft -airplane -radiation -gore "
        "order:random rating:e"
    )

    # Mesma query mas forçando tipo animado (GIF)
    E621_TAGS_GIF: str = (
        "male gay "
        "knotted_penis cum_inflation "
        "~feral ~animal_genitalia ~animal_penis "
        "~equine_penis ~equine_genitalia ~canine_genitalia "
        "-road -machine -car -aircraft -airplane -radiation -gore "
        "type:gif order:random rating:e"
    )

    # Mesma query mas forçando tipo animado (WebM/vídeo)
    E621_TAGS_VIDEO: str = (
        "male gay "
        "knotted_penis cum_inflation "
        "~feral ~animal_genitalia ~animal_penis "
        "~equine_penis ~equine_genitalia ~canine_genitalia "
        "-road -machine -car -aircraft -airplane -radiation -gore "
        "type:webm order:random rating:e"
    )

    # Balanceamento de tipos na fila:
    # Se imagens estáticas > BALANCE_IMAGE_THRESHOLD da fila, busca animados
    BALANCE_IMAGE_THRESHOLD: float = 0.70   # 70% de imagens = excessivo
    BALANCE_MIN_QUEUE_SIZE: int = 15         # só analisa quando fila tiver pelo menos 15 itens

    # Tags que DEVEM existir em pelo menos um subgrupo (verificação client-side)
    E621_REQUIRED_ANY: frozenset = frozenset({
        "feral", "animal_genitalia", "animal_penis",
        "equine_penis", "equine_genitalia", "canine_genitalia",
    })

    # Tags que sempre excluem o post (verificação client-side)
    E621_BLACKLIST: frozenset = frozenset({
        "female", "human", "cub", "young", "juvenile", "gore",
        "road", "machine", "car", "aircraft", "airplane", "radiation",
    })

    @property
    def user_agent(self) -> str:
        return f"AutoYiff/1.0 (by {self.E621_USERNAME} on e621)"


settings = Settings()
