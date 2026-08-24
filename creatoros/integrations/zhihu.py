from __future__ import annotations

import time
from typing import Any

import httpx

from ..config import (
    ZHIHU_ACCESS_SECRET,
    ZHIHU_OPENAPI_BASE_URL,
    ZHIHU_TIMEOUT_SECONDS,
)
from ..discovery import HotListSnapshot, HotTopic


class ZhihuOpenAPIError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        error_type: str = "zhihu_openapi_error",
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.error_type = error_type
        self.retryable = retryable
        self.details = details or {}


class ZhihuOpenAPIClient:
    """Thin client for the official Zhihu Open Platform."""

    def __init__(
        self,
        access_secret: str = ZHIHU_ACCESS_SECRET,
        base_url: str = ZHIHU_OPENAPI_BASE_URL,
        timeout: float = ZHIHU_TIMEOUT_SECONDS,
        transport: httpx.BaseTransport | None = None,
    ):
        self._access_secret = access_secret.strip()
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            transport=transport,
        )

    @classmethod
    def from_env(cls) -> "ZhihuOpenAPIClient":
        return cls()

    def close(self) -> None:
        self._http.close()

    def get_hot_list(self, limit: int = 10) -> HotListSnapshot:
        if not 1 <= limit <= 30:
            raise ValueError("知乎热榜 limit 必须在 1 到 30 之间。")
        if not self._access_secret:
            raise ZhihuOpenAPIError(
                "缺少 ZHIHU_ACCESS_SECRET，无法调用知乎官方热榜。",
                error_type="zhihu_auth",
            )

        headers = {
            "Authorization": f"Bearer {self._access_secret}",
            "X-Request-Timestamp": str(int(time.time())),
            "Content-Type": "application/json",
        }
        try:
            response = self._http.get(
                "/api/v1/content/hot_list",
                params={"Limit": limit},
                headers=headers,
            )
        except httpx.TimeoutException as error:
            raise ZhihuOpenAPIError(
                "知乎官方热榜请求超时。",
                error_type="zhihu_timeout",
                retryable=True,
            ) from error
        except httpx.RequestError as error:
            raise ZhihuOpenAPIError(
                f"无法连接知乎开放平台：{error}",
                error_type="zhihu_unavailable",
                retryable=True,
            ) from error

        payload = self._read_payload(response)
        code = payload.get("Code")
        if code != 0:
            message = str(payload.get("Message") or "未知错误")
            error_type = "zhihu_auth" if code == 20001 else "zhihu_api_error"
            raise ZhihuOpenAPIError(
                f"知乎开放平台返回错误：{message}",
                error_type=error_type,
                details={"code": code, "status_code": response.status_code},
            )

        data = payload.get("Data")
        if not isinstance(data, dict) or not isinstance(data.get("Items"), list):
            raise ZhihuOpenAPIError(
                "知乎官方热榜返回的数据结构无效。",
                error_type="zhihu_protocol_error",
            )

        topics = tuple(
            self._parse_topic(rank, item)
            for rank, item in enumerate(data["Items"], start=1)
            if isinstance(item, dict)
        )
        total = data.get("Total")
        return HotListSnapshot(
            source="zhihu",
            total=total if isinstance(total, int) else len(topics),
            topics=topics,
        )

    @staticmethod
    def _read_payload(response: httpx.Response) -> dict[str, Any]:
        if response.status_code == 429 or response.status_code >= 500:
            raise ZhihuOpenAPIError(
                f"知乎开放平台暂时不可用（HTTP {response.status_code}）。",
                error_type="zhihu_unavailable",
                retryable=True,
                details={"status_code": response.status_code},
            )
        try:
            payload = response.json()
        except ValueError as error:
            raise ZhihuOpenAPIError(
                "知乎官方热榜返回的不是 JSON。",
                error_type="zhihu_protocol_error",
            ) from error
        if not isinstance(payload, dict):
            raise ZhihuOpenAPIError(
                "知乎官方热榜返回的 JSON 不是 object。",
                error_type="zhihu_protocol_error",
            )
        return payload

    @staticmethod
    def _parse_topic(rank: int, item: dict[str, Any]) -> HotTopic:
        title = item.get("Title")
        url = item.get("Url")
        if not isinstance(title, str) or not isinstance(url, str):
            raise ZhihuOpenAPIError(
                "知乎官方热榜条目缺少 Title 或 Url。",
                error_type="zhihu_protocol_error",
            )
        summary = item.get("Summary")
        thumbnail_url = item.get("ThumbnailUrl")
        return HotTopic(
            rank=rank,
            title=title,
            url=url,
            summary=summary if isinstance(summary, str) else "",
            thumbnail_url=thumbnail_url if isinstance(thumbnail_url, str) else "",
        )
