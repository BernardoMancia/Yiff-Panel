from __future__ import annotations

import asyncio
import json
import random
from typing import Any

import httpx

from app.config import settings


class E621Client:
    BASE_URL = "https://e621.net"

    def __init__(self) -> None:
        pass

    def _build_client(self) -> httpx.AsyncClient:
        auth = None
        if settings.E621_USERNAME and settings.E621_API_KEY:
            auth = (settings.E621_USERNAME, settings.E621_API_KEY)
        return httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers={"User-Agent": settings.user_agent},
            auth=auth,
            timeout=30.0,
            follow_redirects=True,
        )

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        async with self._build_client() as client:
            await asyncio.sleep(1.1)
            response = await client.get(path, params=params)
            response.raise_for_status()
            return response.json()

    async def fetch_posts(self, page: int = 1, limit: int = 100, custom_tags: str | None = None) -> list[dict]:
        tags = custom_tags if custom_tags else settings.E621_TAGS
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
        if custom_tags:
            if file_type == "gif":
                tags = custom_tags.replace("order:random rating:e", "type:gif order:random rating:e")
            elif file_type == "webm":
                tags = custom_tags.replace("order:random rating:e", "type:webm order:random rating:e")
            else:
                return []
        else:
            if file_type == "gif":
                tags = settings.E621_TAGS_GIF
            elif file_type == "webm":
                tags = settings.E621_TAGS_VIDEO
            else:
                return []
        page = random.randint(1, 4)
        async with self._build_client() as client:
            await asyncio.sleep(1.1)
            response = await client.get(
                "/posts.json",
                params={"tags": tags, "limit": limit, "page": page},
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

        tags_all = post.get("tags", {})
        tag_set: set[str] = set()
        for v in tags_all.values():
            if isinstance(v, list):
                tag_set.update(v)

        # Deve ter ao menos uma tag do grupo temático (OR)
        if not (settings.E621_REQUIRED_ANY & tag_set):
            return False

        # Blacklist client-side (reforço além da query API)
        if tag_set & settings.E621_BLACKLIST:
            return False

        # Garante que é conteúdo entre machos
        if "female" in tag_set:
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
