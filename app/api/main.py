"""FastAPI 应用入口：挂载认证/会话/问答接口，并托管前端构建产物。

前端与 API **同源**（同一个服务）：省掉 CORS 配置，也让 HttpOnly Cookie
天然不会被跨站携带（配合 SameSite=Lax 抵御 CSRF）。
启动：`uv run python -m app.api.main`
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes_auth import router as auth_router
from app.api.routes_chat import router as chat_router
from app.config import WEB_DIST_DIR


def create_app() -> FastAPI:
    app = FastAPI(title="llm-wiki 法律知识问答", version="1.0")
    app.include_router(auth_router)
    app.include_router(chat_router)

    @app.get("/api/health")
    def health():
        return {"ok": True}

    _mount_frontend(app)
    return app


def _mount_frontend(app: FastAPI) -> None:
    """托管 Vite 构建产物；未构建时给出明确提示而不是 404 一片空白。"""
    index = Path(WEB_DIST_DIR) / "index.html"
    assets = Path(WEB_DIST_DIR) / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        if index.exists():
            return FileResponse(str(index))     # SPA 前端路由交给前端处理
        return JSONResponse(
            {"detail": "前端尚未构建：请在 web/ 下执行 npm install && npm run build"},
            status_code=503,
        )


app = create_app()


def main() -> None:
    import uvicorn

    from app.config import API_HOST, API_PORT
    uvicorn.run("app.api.main:app", host=API_HOST, port=API_PORT, reload=False)


if __name__ == "__main__":
    main()
