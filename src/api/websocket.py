"""
WebSocket管理器模块
处理实时进度推送
"""
import asyncio
import json
from typing import Dict, List, Set, Optional, Any
from dataclasses import dataclass, asdict
from enum import Enum
from datetime import datetime

from fastapi import WebSocket
from src.utils.logger import get_logger

logger = get_logger(__name__)


class MessageType(str, Enum):
    """WebSocket消息类型"""
    # 连接相关
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"

    # 分析进度
    ANALYSIS_STARTED = "analysis_started"
    ANALYSIS_PROGRESS = "analysis_progress"
    ANALYSIS_LEVEL_COMPLETE = "analysis_level_complete"
    ANALYSIS_FILE_COMPLETE = "analysis_file_complete"
    ANALYSIS_FILE_FAILED = "analysis_file_failed"
    ANALYSIS_COMPLETE = "analysis_complete"
    ANALYSIS_ERROR = "analysis_error"

    # 扫描
    SCAN_COMPLETE = "scan_complete"

    # 状态
    STATUS_UPDATE = "status_update"


@dataclass
class WSMessage:
    """WebSocket消息"""
    type: MessageType
    data: Dict[str, Any]
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()

    def to_json(self) -> str:
        return json.dumps({
            "type": self.type.value,
            "data": self.data,
            "timestamp": self.timestamp,
        }, ensure_ascii=False)


class ConnectionManager:
    """
    WebSocket连接管理器

    管理所有WebSocket连接，支持：
    - 多客户端连接
    - 按任务ID分组
    - 广播消息
    """

    def __init__(self):
        # 所有活跃连接
        self.active_connections: Set[WebSocket] = set()

        # 按任务ID分组的连接
        self.task_connections: Dict[str, Set[WebSocket]] = {}

        # 连接锁
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, task_id: Optional[str] = None) -> None:
        """
        接受新的WebSocket连接

        Args:
            websocket: WebSocket连接
            task_id: 可选的任务ID，用于订阅特定任务的更新
        """
        await websocket.accept()

        async with self._lock:
            self.active_connections.add(websocket)

            if task_id:
                if task_id not in self.task_connections:
                    self.task_connections[task_id] = set()
                self.task_connections[task_id].add(websocket)

        logger.info(f"WebSocket连接已建立, 当前连接数: {len(self.active_connections)}")

        # 发送连接确认
        await self.send_personal(websocket, WSMessage(
            type=MessageType.CONNECTED,
            data={"message": "连接成功", "task_id": task_id}
        ))

    async def disconnect(self, websocket: WebSocket) -> None:
        """
        断开WebSocket连接

        Args:
            websocket: WebSocket连接
        """
        async with self._lock:
            self.active_connections.discard(websocket)

            # 从所有任务组中移除
            for task_id in list(self.task_connections.keys()):
                self.task_connections[task_id].discard(websocket)
                if not self.task_connections[task_id]:
                    del self.task_connections[task_id]

        logger.info(f"WebSocket连接已断开, 当前连接数: {len(self.active_connections)}")

    async def send_personal(self, websocket: WebSocket, message: WSMessage) -> None:
        """
        发送消息给特定连接

        Args:
            websocket: 目标WebSocket连接
            message: 消息内容
        """
        try:
            await websocket.send_text(message.to_json())
        except Exception as e:
            logger.error(f"发送WebSocket消息失败: {e}")
            await self.disconnect(websocket)

    async def broadcast(self, message: WSMessage) -> None:
        """
        广播消息给所有连接

        Args:
            message: 消息内容
        """
        disconnected = []

        for connection in self.active_connections.copy():
            try:
                await connection.send_text(message.to_json())
            except Exception:
                disconnected.append(connection)

        # 清理断开的连接
        for conn in disconnected:
            await self.disconnect(conn)

    async def broadcast_to_task(self, task_id: str, message: WSMessage) -> None:
        """
        广播消息给订阅特定任务的连接

        Args:
            task_id: 任务ID
            message: 消息内容
        """
        connections = self.task_connections.get(task_id, set())
        disconnected = []

        for connection in connections.copy():
            try:
                await connection.send_text(message.to_json())
            except Exception:
                disconnected.append(connection)

        # 清理断开的连接
        for conn in disconnected:
            await self.disconnect(conn)

    @property
    def connection_count(self) -> int:
        """当前连接数"""
        return len(self.active_connections)


# 全局连接管理器
manager = ConnectionManager()


class ProgressNotifier:
    """
    进度通知器

    在分析过程中发送进度更新
    """

    def __init__(self, task_id: str):
        self.task_id = task_id
        self.manager = manager

    async def notify_started(self, source_path: str, total_files: int, total_dirs: int) -> None:
        """通知分析开始"""
        await self.manager.broadcast_to_task(self.task_id, WSMessage(
            type=MessageType.ANALYSIS_STARTED,
            data={
                "task_id": self.task_id,
                "source_path": source_path,
                "total_files": total_files,
                "total_dirs": total_dirs,
            }
        ))

    async def notify_progress(
        self,
        current: int,
        total: int,
        current_file: str,
        status: str
    ) -> None:
        """通知处理进度"""
        await self.manager.broadcast_to_task(self.task_id, WSMessage(
            type=MessageType.ANALYSIS_PROGRESS,
            data={
                "task_id": self.task_id,
                "current": current,
                "total": total,
                "percentage": round(current / total * 100, 1) if total > 0 else 0,
                "current_file": current_file,
                "status": status,
            }
        ))

    async def notify_level_complete(
        self,
        depth: int,
        completed: int,
        failed: int,
        total: int
    ) -> None:
        """通知层级处理完成"""
        await self.manager.broadcast_to_task(self.task_id, WSMessage(
            type=MessageType.ANALYSIS_LEVEL_COMPLETE,
            data={
                "task_id": self.task_id,
                "depth": depth,
                "completed": completed,
                "failed": failed,
                "total": total,
            }
        ))

    async def notify_file_complete(self, file_path: str, doc_path: str) -> None:
        """通知文件处理完成"""
        await self.manager.broadcast_to_task(self.task_id, WSMessage(
            type=MessageType.ANALYSIS_FILE_COMPLETE,
            data={
                "task_id": self.task_id,
                "file_path": file_path,
                "doc_path": doc_path,
            }
        ))

    async def notify_file_failed(self, file_path: str, error: str) -> None:
        """通知文件处理失败"""
        await self.manager.broadcast_to_task(self.task_id, WSMessage(
            type=MessageType.ANALYSIS_FILE_FAILED,
            data={
                "task_id": self.task_id,
                "file_path": file_path,
                "error": error,
            }
        ))

    async def notify_complete(self, stats: Dict[str, Any]) -> None:
        """通知分析完成"""
        await self.manager.broadcast_to_task(self.task_id, WSMessage(
            type=MessageType.ANALYSIS_COMPLETE,
            data={
                "task_id": self.task_id,
                "stats": stats,
            }
        ))

    async def notify_error(self, error: str) -> None:
        """通知分析错误"""
        await self.manager.broadcast_to_task(self.task_id, WSMessage(
            type=MessageType.ANALYSIS_ERROR,
            data={
                "task_id": self.task_id,
                "error": error,
            }
        ))
