from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from starlette.responses import FileResponse
from starlette.staticfiles import StaticFiles


class StudioStaticFiles(StaticFiles):
    """Serve Vite assets with a browser-safe JavaScript MIME type on Windows."""

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        # Windows' registry may report JavaScript as text/plain, which browsers
        # reject for ES modules under strict MIME checking.
        if response.status_code == 200 and Path(path).suffix.lower() in {".js", ".mjs"}:
            response.headers["content-type"] = "text/javascript; charset=utf-8"
        return response


def mount_studio(app: FastAPI, directory: Path) -> bool:
    directory = Path(directory).resolve()
    if not (directory / "index.html").is_file():
        return False
    assets = directory / "assets"
    if assets.is_dir():
        app.mount("/assets", StudioStaticFiles(directory=assets), name="studio-assets")

    @app.get("/", include_in_schema=False)
    def studio_index():
        return FileResponse(directory / "index.html", media_type="text/html")

    @app.get("/{client_path:path}", include_in_schema=False)
    def studio_route(client_path: str):
        if client_path.startswith("api/") or "." in Path(client_path).name:
            raise HTTPException(status_code=404, detail="Not Found")
        return FileResponse(directory / "index.html", media_type="text/html")

    return True
