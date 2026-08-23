from __future__ import annotations

import getpass
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

COOKIE_NAME = "personaforge_session"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT_ROOT / ".env"


def _upsert_env_value(path: Path, key: str, value: str) -> None:
    """Replace one dotenv key without printing or storing the password."""
    if "\r" in value or "\n" in value:
        raise ValueError("环境变量值不能包含换行符。")

    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    replacement = f"{key}={value}"
    updated: list[str] = []
    replaced = False
    for line in lines:
        stripped = line.lstrip()
        if not stripped.startswith("#") and stripped.startswith(f"{key}="):
            if not replaced:
                updated.append(replacement)
                replaced = True
            continue
        updated.append(line)
    if not replaced:
        updated.append(replacement)

    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text("\n".join(updated).rstrip("\n") + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    load_dotenv(ENV_FILE)
    base_url = os.getenv("PERSONCLONE_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    print(f"PersonClone: {base_url}")
    username = input("用户名: ").strip()
    if not username:
        print("用户名不能为空。")
        return 2
    password = getpass.getpass("密码（不会显示）: ")
    if not password:
        print("密码不能为空。")
        return 2

    try:
        with httpx.Client(base_url=base_url, timeout=15) as client:
            response = client.post(
                "/api/auth/login",
                json={"username": username, "password": password},
            )
            if response.status_code >= 400:
                try:
                    detail = response.json().get("detail")
                except ValueError:
                    detail = None
                print(f"登录失败（HTTP {response.status_code}）：{detail or '服务返回错误'}")
                return 1

            cookie = response.cookies.get(COOKIE_NAME)
            if not cookie:
                print("登录响应没有返回 PersonClone 会话 Cookie。")
                return 1
            _upsert_env_value(ENV_FILE, "PERSONCLONE_SESSION_COOKIE", cookie)

            state = client.get("/api/auth/state")
            authenticated = False
            if state.is_success:
                payload = state.json()
                authenticated = bool(payload.get("authenticated"))
    except httpx.RequestError as error:
        print(f"无法连接 PersonClone：{error}")
        return 1
    except OSError as error:
        print(f"写入本地 .env 失败：{error}")
        return 1

    if not authenticated:
        print("Cookie 已写入，但认证状态校验未通过；请检查 PersonClone 服务。")
        return 1
    print(f"登录成功，Cookie 已写入 {ENV_FILE}。")
    print("Cookie 未显示，也不会被提交到 Git。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
