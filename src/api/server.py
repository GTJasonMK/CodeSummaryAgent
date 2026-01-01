"""
FastAPI应用模块
Web服务入口
"""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from src.api.routes import router as api_router
from src.models.config import get_config
from src.utils.logger import setup_logger, get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时
    setup_logger(log_level="INFO")
    logger.info("CodeSummaryAgent Web服务启动")
    yield
    # 关闭时
    logger.info("CodeSummaryAgent Web服务关闭")


def create_app() -> FastAPI:
    """创建FastAPI应用"""

    app = FastAPI(
        title="CodeSummaryAgent API",
        description="基于LLM的代码库分析工具 - Web API",
        version="1.0.0",
        lifespan=lifespan,
    )

    # CORS配置
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # 生产环境应该限制
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # API路由
    app.include_router(api_router, prefix="/api")

    # 静态文件（前端构建产物）
    static_path = Path(__file__).parent.parent.parent / "web" / "dist"
    if static_path.exists():
        app.mount("/static", StaticFiles(directory=str(static_path / "assets")), name="static")

        @app.get("/")
        async def serve_frontend():
            """服务前端页面"""
            return FileResponse(str(static_path / "index.html"))

        @app.get("/{path:path}")
        async def serve_frontend_routes(path: str):
            """处理前端路由"""
            index_file = static_path / "index.html"
            file_path = static_path / path
            if file_path.exists() and file_path.is_file():
                return FileResponse(str(file_path))
            return FileResponse(str(index_file))

    return app


app = create_app()


def run_server(host: str = "127.0.0.1", port: int = 8000):
    """运行Web服务器"""
    import uvicorn
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    config = get_config()
    run_server(
        host=config.server.host,
        port=config.server.port
    )
