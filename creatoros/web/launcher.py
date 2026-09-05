from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


class StudioLaunchError(RuntimeError):
    pass


def ensure_studio_build(project_root: Path) -> Path:
    project_root = Path(project_root).resolve()
    web_root = project_root / "web"
    output = web_root / "dist" / "index.html"
    sources = [
        web_root / "index.html",
        web_root / "package.json",
        web_root / "package-lock.json",
        web_root / "vite.config.ts",
        *web_root.glob("tsconfig*.json"),
    ]
    sources.extend((web_root / "src").rglob("*"))
    newest_source = max((path.stat().st_mtime for path in sources if path.is_file()), default=0)
    if output.is_file() and output.stat().st_mtime >= newest_source:
        return output.parent
    if not (web_root / "node_modules").is_dir():
        raise StudioLaunchError("缺少 Studio 前端依赖；请先运行：npm --prefix web ci")
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if npm is None:
        raise StudioLaunchError("未找到 npm；请先安装 Node.js，再运行 npm --prefix web ci。")
    print("Studio 前端有更新，正在构建…", flush=True)
    try:
        subprocess.run([npm, "--prefix", str(web_root), "run", "build"], cwd=project_root, check=True)
    except subprocess.CalledProcessError as error:
        raise StudioLaunchError("Studio 前端构建失败；请查看上方 npm 输出。") from error
    if not output.is_file():
        raise StudioLaunchError("前端构建结束，但没有生成 web/dist/index.html。")
    return output.parent
