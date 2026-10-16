"""Read-only MCP connection checks plus explicit interactive QR login."""
import argparse
import asyncio
import base64
from datetime import timedelta
from pathlib import Path

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

ROOT = Path(__file__).resolve().parents[2] / "tmp" / "xhs-mcp-smoke"
URL = "http://127.0.0.1:18060/mcp"


async def probe(action):
    token = (ROOT / "auth-token.txt").read_text(encoding="utf-8").strip()
    async with httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"},
                                 timeout=90, trust_env=False) as client:
        async with streamable_http_client(URL, http_client=client) as (read, write, _):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=90)) as session:
                initialized = await session.initialize()
                print(f"Connected: {initialized.serverInfo.name}; protocol: {initialized.protocolVersion}")
                if action == "list":
                    listing = await session.list_tools()
                    for tool in listing.tools:
                        print(tool.name)
                    return
                name = {"status": "check_login_status", "qr": "get_login_qrcode"}[action]
                result = await session.call_tool(name, {})
                if result.isError:
                    raise RuntimeError(f"{name} failed; inspect the private local server log")
                for item in result.content:
                    if item.type == "image" and action == "qr":
                        if item.mimeType != "image/png":
                            raise RuntimeError("Unexpected QR image format")
                        output = ROOT / "login-qr.png"
                        output.write_bytes(base64.b64decode(item.data, validate=True))
                        print(f"QR image: {output}")
                    elif item.type == "text":
                        print(item.text[:1000])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("list", "status", "qr"))
    args = parser.parse_args()
    asyncio.run(asyncio.wait_for(probe(args.action), timeout=120))
