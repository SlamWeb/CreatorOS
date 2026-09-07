"""Local Studio client: submission is not completion; writes are never retried."""
from __future__ import annotations

import os
from urllib.parse import quote, urlsplit

import httpx


class StudioClientError(RuntimeError):
    def __init__(self, message: str, code: str, *, run_id: str | None = None):
        super().__init__(message)
        self.code = code
        self.run_id = run_id


class StudioClient:
    def __init__(self, base_url: str, *, client: httpx.Client | None = None):
        parsed = urlsplit(base_url)
        if (parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
                or parsed.username or parsed.password or parsed.path not in {"", "/"}
                or parsed.query or parsed.fragment):
            raise ValueError("Studio 地址必须是本机 HTTP 地址，例如 http://127.0.0.1:8765。")
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=30, trust_env=False, follow_redirects=False)

    @classmethod
    def from_defaults(cls):
        return cls(os.environ.get("CREATOROS_STUDIO_URL", "http://127.0.0.1:8765"))

    def close(self):
        self.client.close()

    def request(self, method: str, path: str, *, params=None, payload=None) -> dict:
        try:
            response = self.client.request(method, self.base_url + path, params=params, json=payload)
        except httpx.ConnectError as error:
            raise StudioClientError(
                "无法连接 Studio。请先运行 python -m creatoros.web，并核对 Studio 地址。",
                "studio_unavailable",
            ) from error
        except httpx.RequestError as error:
            raise StudioClientError(
                "请求结果尚不确定，请先在 Studio 查询任务；不要自动重新提交。" if method == "POST"
                else "读取 Studio 超时或连接中断，可稍后重新查询。",
                "studio_outcome_unknown" if method == "POST" else "studio_read_failed",
            ) from error
        try:
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("object required")
        except ValueError as error:
            raise StudioClientError(
                "Studio 未返回有效 JSON；写请求可能已生效，请先查询任务。",
                "studio_invalid_response",
            ) from error
        if not response.is_success:
            detail = data.get("error")
            detail = detail if isinstance(detail, dict) else {}
            raise StudioClientError(
                str(detail.get("message") or "Studio 拒绝请求，请检查当前状态。"),
                str(detail.get("code") or "studio_request_failed"),
                run_id=detail.get("run_id"),
            )
        return data

    def creators(self, offset: int, limit: int) -> dict:
        return self.request("GET", "/api/creators", params={"offset": offset, "limit": limit})

    def creator_series(self, creator_id: str) -> dict:
        data = self.request("GET", f"/api/creators/{quote(creator_id, safe='')}")
        return {"creator_id": data["id"], "creator_name": data["display_name"], "items": data["series"]}

    def topics(self, series_id: str, offset: int, limit: int) -> dict:
        return self.request("GET", f"/api/series/{quote(series_id, safe='')}/topics",
                            params={"offset": offset, "limit": limit})

    def get_run(self, run_id: str) -> dict:
        return self.request("GET", f"/api/runs/{quote(run_id, safe='')}")

    def run_summary(self, run: dict, *, accepted: bool = False) -> dict:
        fields = ("id", "creator_id", "creator_name", "series_id", "series_name", "topic_id",
                  "topic_title", "status", "version", "active_revision_number", "allowed_actions",
                  "error_type", "error_message", "card_count")
        result = {key: run.get(key) for key in fields}
        result["run_id"] = result.pop("id")
        result["accepted"] = accepted
        result["url"] = f"{self.base_url}/runs/{quote(run['id'], safe='')}"
        result["message"] = (
            "已提交后台执行；以 status 为准，accepted 不表示内容已完成。" if accepted
            else "返回已有任务状态；本次没有重新执行。仅可使用 allowed_actions 列出的动作；"
                 "cancelled/approved 为只读终态，不可恢复或返工，approved 也不代表已发布。"
        )
        return result

    def start(self, topic_id: str) -> dict:
        # Creation is idempotent on the server. Never automatically resume an old attempt/revision.
        run = self.request("POST", "/api/runs", payload={"topic_id": topic_id})
        revisions = run["revisions"]
        fresh = (run["status"] == "queued" and run["active_revision_number"] == 1
                 and len(revisions) == 1 and not revisions[0]["attempts"])
        if not fresh:
            return self.run_summary(run)
        try:
            result = self.request("POST", f"/api/runs/{quote(run['id'], safe='')}/execute",
                                  payload={"expected_version": run["version"]})
        except StudioClientError as error:
            # Preserve the requested Run even when the response is lost; busy may point to another Run.
            if error.run_id is None:
                error.run_id = run["id"]
            raise
        return self.run_summary(result, accepted=True)
