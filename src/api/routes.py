"""
API路由模块
提供RESTful API和WebSocket端点
"""
import asyncio
import uuid
from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime
from enum import Enum

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.core.analyzer import CodeAnalyzer
from src.services.directory_scanner import DirectoryScanner, get_nodes_by_depth, get_max_depth
from src.services.checkpoint import CheckpointService
from src.models.config import get_config, load_config, AppConfig
from src.models.file_node import FileNode, AnalysisStatus
from src.api.websocket import manager, ProgressNotifier, WSMessage, MessageType
from src.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter()


# ==================== 数据模型 ====================

class TaskStatus(str, Enum):
    """任务状态"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AnalyzeRequest(BaseModel):
    """分析请求"""
    source_path: str = Field(..., description="源代码目录路径")
    docs_path: Optional[str] = Field(None, description="文档输出目录")
    resume: bool = Field(True, description="是否启用断点续传")
    config_overrides: Optional[Dict[str, Any]] = Field(None, description="配置覆盖")


class ScanRequest(BaseModel):
    """扫描请求"""
    source_path: str = Field(..., description="源代码目录路径")


class TaskInfo(BaseModel):
    """任务信息"""
    task_id: str
    status: TaskStatus
    source_path: str
    docs_path: Optional[str]
    created_at: str
    started_at: Optional[str]
    completed_at: Optional[str]
    progress: float
    total_files: int
    processed_files: int
    failed_files: int
    error: Optional[str]


class FileNodeDTO(BaseModel):
    """文件节点DTO"""
    path: str
    name: str
    type: str
    depth: int
    relative_path: str
    status: str
    doc_path: Optional[str]
    children: List["FileNodeDTO"] = []

    class Config:
        from_attributes = True


FileNodeDTO.model_rebuild()


# ==================== 任务管理 ====================

class TaskManager:
    """任务管理器"""

    def __init__(self):
        self.tasks: Dict[str, TaskInfo] = {}
        self.analyzers: Dict[str, CodeAnalyzer] = {}
        self.running_tasks: Dict[str, asyncio.Task] = {}

    def create_task(self, source_path: str, docs_path: Optional[str] = None) -> str:
        """创建新任务"""
        task_id = str(uuid.uuid4())[:8]

        self.tasks[task_id] = TaskInfo(
            task_id=task_id,
            status=TaskStatus.PENDING,
            source_path=source_path,
            docs_path=docs_path,
            created_at=datetime.now().isoformat(),
            started_at=None,
            completed_at=None,
            progress=0,
            total_files=0,
            processed_files=0,
            failed_files=0,
            error=None,
        )

        return task_id

    def get_task(self, task_id: str) -> Optional[TaskInfo]:
        """获取任务信息"""
        return self.tasks.get(task_id)

    def update_task(self, task_id: str, **kwargs) -> None:
        """更新任务信息"""
        if task_id in self.tasks:
            task = self.tasks[task_id]
            for key, value in kwargs.items():
                if hasattr(task, key):
                    setattr(task, key, value)

    def list_tasks(self) -> List[TaskInfo]:
        """列出所有任务"""
        return list(self.tasks.values())

    async def cancel_task(self, task_id: str) -> bool:
        """取消任务"""
        if task_id in self.running_tasks:
            self.running_tasks[task_id].cancel()
            self.update_task(task_id, status=TaskStatus.CANCELLED)
            return True
        return False


task_manager = TaskManager()


# ==================== API端点 ====================

@router.get("/health")
async def health_check():
    """健康检查"""
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


@router.post("/analyze", response_model=Dict[str, str])
async def start_analysis(
    request: AnalyzeRequest,
    background_tasks: BackgroundTasks
):
    """
    启动代码分析任务

    Returns:
        task_id: 任务ID，用于查询进度
    """
    # 验证路径
    source_path = Path(request.source_path).resolve()
    if not source_path.exists():
        raise HTTPException(status_code=400, detail=f"目录不存在: {request.source_path}")
    if not source_path.is_dir():
        raise HTTPException(status_code=400, detail=f"路径不是目录: {request.source_path}")

    # 创建任务
    task_id = task_manager.create_task(
        source_path=str(source_path),
        docs_path=request.docs_path
    )

    # 后台执行分析
    background_tasks.add_task(
        run_analysis_task,
        task_id,
        str(source_path),
        request.docs_path,
        request.resume,
        request.config_overrides
    )

    return {"task_id": task_id, "message": "分析任务已创建"}


async def run_analysis_task(
    task_id: str,
    source_path: str,
    docs_path: Optional[str],
    resume: bool,
    config_overrides: Optional[Dict[str, Any]]
):
    """后台运行分析任务"""
    notifier = ProgressNotifier(task_id)

    try:
        task_manager.update_task(
            task_id,
            status=TaskStatus.RUNNING,
            started_at=datetime.now().isoformat()
        )

        # 创建分析器
        analyzer = CodeAnalyzer(
            source_path=source_path,
            docs_path=docs_path,
        )
        task_manager.analyzers[task_id] = analyzer

        # 设置进度回调
        def on_progress(message: str, percentage: float):
            task_manager.update_task(task_id, progress=percentage)
            asyncio.create_task(notifier.notify_progress(
                current=int(percentage),
                total=100,
                current_file="",
                status=message
            ))

        analyzer.set_progress_callback(on_progress)

        # 执行分析
        success = await analyzer.analyze(resume=resume)

        # 更新任务状态
        stats = analyzer.get_stats()
        task_manager.update_task(
            task_id,
            status=TaskStatus.COMPLETED if success else TaskStatus.FAILED,
            completed_at=datetime.now().isoformat(),
            progress=100,
            total_files=stats.get("total_files", 0),
            processed_files=stats.get("processed_files", 0),
            failed_files=stats.get("failed_count", 0),
            docs_path=analyzer.docs_root,
        )

        await notifier.notify_complete(stats)

    except asyncio.CancelledError:
        task_manager.update_task(task_id, status=TaskStatus.CANCELLED)
        await notifier.notify_error("任务已取消")

    except Exception as e:
        logger.error(f"分析任务失败: {e}")
        task_manager.update_task(
            task_id,
            status=TaskStatus.FAILED,
            error=str(e),
            completed_at=datetime.now().isoformat()
        )
        await notifier.notify_error(str(e))


@router.get("/tasks", response_model=List[TaskInfo])
async def list_tasks():
    """列出所有任务"""
    return task_manager.list_tasks()


@router.get("/tasks/{task_id}", response_model=TaskInfo)
async def get_task(task_id: str):
    """获取任务详情"""
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str):
    """取消任务"""
    success = await task_manager.cancel_task(task_id)
    if not success:
        raise HTTPException(status_code=400, detail="无法取消任务")
    return {"message": "任务已取消"}


@router.post("/scan", response_model=Dict[str, Any])
async def scan_directory(request: ScanRequest):
    """
    扫描目录结构（不进行分析）

    Returns:
        文件树结构
    """
    source_path = Path(request.source_path).resolve()
    if not source_path.exists():
        raise HTTPException(status_code=400, detail=f"目录不存在: {request.source_path}")

    scanner = DirectoryScanner()
    root = scanner.scan(str(source_path))

    return {
        "root": _node_to_dto(root),
        "stats": {
            "total_files": len(root.get_all_files()),
            "total_dirs": len(root.get_all_dirs()),
            "max_depth": get_max_depth(root),
        }
    }


@router.get("/tasks/{task_id}/tree")
async def get_task_tree(task_id: str):
    """获取任务的文件树"""
    analyzer = task_manager.analyzers.get(task_id)
    if not analyzer or not analyzer.root:
        raise HTTPException(status_code=404, detail="任务不存在或尚未扫描")

    return {"root": _node_to_dto(analyzer.root)}


@router.get("/config")
async def get_current_config():
    """获取当前配置"""
    config = get_config()
    return config.model_dump()


@router.put("/config")
async def update_config(config_updates: Dict[str, Any]):
    """更新配置"""
    # 这里可以实现配置的动态更新
    # 目前只返回当前配置
    return {"message": "配置更新功能待实现", "current": get_config().model_dump()}


# ==================== WebSocket端点 ====================

@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """全局WebSocket连接"""
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            # 可以处理客户端发送的消息
            logger.debug(f"收到WebSocket消息: {data}")
    except WebSocketDisconnect:
        await manager.disconnect(websocket)


@router.websocket("/ws/{task_id}")
async def websocket_task_endpoint(websocket: WebSocket, task_id: str):
    """任务专用WebSocket连接"""
    await manager.connect(websocket, task_id=task_id)
    try:
        while True:
            data = await websocket.receive_text()
            logger.debug(f"收到任务 {task_id} 的WebSocket消息: {data}")
    except WebSocketDisconnect:
        await manager.disconnect(websocket)


# ==================== 辅助函数 ====================

def _node_to_dto(node: FileNode) -> Dict[str, Any]:
    """将FileNode转换为DTO"""
    return {
        "path": node.path,
        "name": node.name,
        "type": node.node_type.value,
        "depth": node.depth,
        "relative_path": node.relative_path,
        "status": node.status.value,
        "doc_path": node.doc_path,
        "children": [_node_to_dto(child) for child in node.children],
    }
