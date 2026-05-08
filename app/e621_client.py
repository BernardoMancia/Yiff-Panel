from __future__ import annotations

import asyncio
import json
import random

import httpx

from app.config import settings


class E621Client:
    BASE_URL = "https://e621.net"

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            auth = None
            if settings.E621_USERNAME and settings.E621_API_KEY:
                auth = (settings.E621_USERNAME, settings.E621_API_KEY)
            self._client = httpx.AsyncClient(
                base_url=self.BASE_URL,
                headers={"User-Agent": settings.user_agent},
                auth=auth,
                timeout=30.0,
                follow_redirects=True,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _get(self, path: str, params: dict | None = None) -> dict | list:
        client = self._get_client()
        await asyncio.sleep(1.1)
        response = await client.get(path, params=params)
        response.raise_for_status()
        return response.json()

    async def fetch_posts(self, page: int = 1, limit: int = 100, custom_tags: str | None = None) -> list[dict]:
        tags = custom_tags if custom_tags else ""
        data = await self._get(
            "/posts.json",
            params={"tags": tags, "limit": limit, "page": page},
        )
        posts = data.get("posts", []) if isinstance(data, dict) else []
        return [p for p in posts if self._is_valid(p)]

    async def fetch_random_posts(self, limit: int = 100, custom_tags: str | None = None) -> list[dict]:
        page = random.randint(1, 5)
        return await self.fetch_posts(page=page, limit=limit, custom_tags=custom_tags)

    async def fetch_by_type(self, file_type: str, limit: int = 50, custom_tags: str | None = None) -> list[dict]:
        if file_type not in ("gif", "webm"):
            return []

        base = custom_tags.strip() if custom_tags else ""
        type_tag = f"type:{file_type}"
        if type_tag not in base:
            base = f"{base} {type_tag}".strip()

        page = random.randint(1, 4)
        client = self._get_client()
        await asyncio.sleep(1.1)
        response = await client.get(
            "/posts.json",
            params={"tags": base, "limit": limit, "page": page},
        )
        if response.status_code != 200:
            return []
        data = response.json()
        posts = data.get("posts", []) if isinstance(data, dict) else []
        return [p for p in posts if self._is_valid(p)]

    @staticmethod
    def _is_valid(post: dict) -> bool:
        file_info = post.get("file", {})
        if not file_info.get("url"):
            return False
        if post.get("flags", {}).get("deleted"):
            return False
        if post.get("flags", {}).get("flagged"):
            return False
        ext = file_info.get("ext", "").lower()
        if ext not in ("jpg", "jpeg", "png", "gif", "webm", "mp4"):
            return False
        return True

    @staticmethod
    def normalize(post: dict) -> dict:
        file_info = post.get("file", {})
        sample_info = post.get("sample", {})
        preview_info = post.get("preview", {})
        tags_all = post.get("tags", {})
        all_tags: list[str] = []
        for v in tags_all.values():
            if isinstance(v, list):
                all_tags.extend(v)
        return {
            "e621_id": post["id"],
            "file_url": file_info.get("url"),
            "sample_url": sample_info.get("url"),
            "preview_url": preview_info.get("url"),
            "file_ext": file_info.get("ext", "").lower(),
            "file_size": file_info.get("size", 0),
            "score": post.get("score", {}).get("total", 0),
            "fav_count": post.get("fav_count", 0),
            "tags": json.dumps(all_tags),
        }


e621_client = E621Client()
