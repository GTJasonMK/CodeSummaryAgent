"""
LLM调用队列模块
管理LLM并发调用，控制请求速率
"""
import asyncio
from typing import Dict, List, Optional, Callable, Any, Union, Coroutine
from dataclasses import dataclass
from enum import Enum
import time

from src.models.file_node import FileNode, AnalysisTask, AnalysisResult, AnalysisStatus
from src.models.config import LLMConfig, get_config
from src.services.llm_service import LLMService, get_llm_service
from src.utils.retry import RetryHandler, RetryExhaustedError
from src.utils.logger import get_logger

logger = get_logger(__name__)


class TaskStatus(Enum):
    """任务状态"""
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class QueueStats:
    """队列统计信息"""
    total: int = 0
    queued: int = 0
    running: int = 0
    completed: int = 0
    failed: int = 0


class LLMQueue:
    """
    LLM调用队列

    管理并发的LLM调用，支持：
    - 并发数控制（通过共享Semaphore）
    - 任务优先级
    - 重试机制
    - 进度回调
    """

    def __init__(
        self,
        llm_service: Optional[LLMService] = None,
        config: Optional[LLMConfig] = None,
        semaphore: Optional[asyncio.Semaphore] = None,
    ):
        """
        初始化LLM队列

        Args:
            llm_service: LLM服务实例
            config: LLM配置
            semaphore: 外部共享的信号量，为None则内部创建
        """
        self.llm_service = llm_service or get_llm_service()
        self.config = config or get_config().llm

        # 并发控制 - 支持外部传入共享Semaphore
        if semaphore is not None:
            self.semaphore = semaphore
            self._external_semaphore = True
        else:
            self.semaphore = asyncio.Semaphore(self.config.max_concurrent)
            self._external_semaphore = False

        self.queue: asyncio.Queue[AnalysisTask] = asyncio.Queue()

        # 任务追踪
        self.tasks: Dict[str, AnalysisTask] = {}
        self.results: Dict[str, AnalysisResult] = {}

        # 重试处理器
        self.retry_handler = RetryHandler(
            max_retries=self.config.max_retries,
            base_delay=self.config.retry_delay,
        )

        # 统计
        self.stats = QueueStats()

        # 回调 - 支持同步和异步回调
        self._on_progress: Optional[Callable[[FileNode, str], None]] = None
        self._on_complete: Optional[Callable[[AnalysisResult], Union[None, Coroutine[Any, Any, None]]]] = None

        # 运行状态
        self._running = False
        self._workers: List[asyncio.Task] = []

    def set_callbacks(
        self,
        on_progress: Optional[Callable[[FileNode, str], None]] = None,
        on_complete: Optional[Callable[[AnalysisResult], Union[None, Coroutine[Any, Any, None]]]] = None,
    ) -> None:
        """
        设置回调函数

        Args:
            on_progress: 进度回调 (node, status_message)
            on_complete: 完成回调 (result)，支持同步和异步函数
        """
        self._on_progress = on_progress
        self._on_complete = on_complete

    async def submit(self, task: AnalysisTask) -> None:
        """
        提交分析任务

        Args:
            task: 分析任务
        """
        task_id = task.node.path
        self.tasks[task_id] = task
        await self.queue.put(task)
        self.stats.total += 1
        self.stats.queued += 1

    async def submit_batch(self, tasks: List[AnalysisTask]) -> None:
        """
        批量提交分析任务

        Args:
            tasks: 任务列表
        """
        for task in tasks:
            await self.submit(task)

    async def process_all(self) -> Dict[str, AnalysisResult]:
        """
        处理队列中的所有任务

        Returns:
            任务路径到结果的映射
        """
        if self.queue.empty():
            return {}

        self._running = True

        # 启动工作协程
        worker_count = min(self.config.max_concurrent, self.stats.queued)
        self._workers = [
            asyncio.create_task(self._worker(i))
            for i in range(worker_count)
        ]

        # 等待队列处理完成
        await self.queue.join()

        # 停止工作协程
        self._running = False
        for worker in self._workers:
            worker.cancel()

        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers = []

        return dict(self.results)

    async def _worker(self, worker_id: int) -> None:
        """
        工作协程

        Args:
            worker_id: 工作协程ID
        """
        logger.debug(f"Worker {worker_id} started")

        while self._running:
            task = None
            try:
                # 从队列获取任务（带超时）
                try:
                    task = await asyncio.wait_for(
                        self.queue.get(),
                        timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue

                # 获取信号量
                async with self.semaphore:
                    self.stats.queued -= 1
                    self.stats.running += 1

                    # 执行任务
                    result = await self._execute_task(task)

                    # 更新统计
                    self.stats.running -= 1
                    if result.success:
                        self.stats.completed += 1
                    else:
                        self.stats.failed += 1

                    # 保存结果
                    self.results[task.node.path] = result

                    # 触发回调 - 支持同步和异步回调
                    if self._on_complete:
                        try:
                            callback_result = self._on_complete(result)
                            # 如果回调返回协程，则等待它完成
                            if asyncio.iscoroutine(callback_result):
                                await callback_result
                        except Exception as cb_err:
                            logger.error(f"Worker {worker_id} callback error: {cb_err}")

            except asyncio.CancelledError:
                # 取消时退出循环，finally块会负责调用task_done
                break
            except Exception as e:
                logger.error(f"Worker {worker_id} error: {e}")
            finally:
                # 确保task_done()总是被调用（当有任务时）
                if task is not None:
                    self.queue.task_done()

        logger.debug(f"Worker {worker_id} stopped")

    async def _execute_task(self, task: AnalysisTask) -> AnalysisResult:
        """
        执行单个分析任务

        Args:
            task: 分析任务

        Returns:
            分析结果
        """
        node = task.node
        start_time = time.time()

        # 更新节点状态
        node.status = AnalysisStatus.IN_PROGRESS
        if self._on_progress:
            self._on_progress(node, "分析中...")

        try:
            # 使用重试机制执行LLM调用
            summary = await self.retry_handler.execute(
                self._analyze_node,
                node
            )

            # 成功
            elapsed = time.time() - start_time
            node.status = AnalysisStatus.COMPLETED

            if self._on_progress:
                self._on_progress(node, f"完成 ({elapsed:.1f}s)")

            return AnalysisResult(
                node=node,
                success=True,
                summary=summary,
                elapsed_time=elapsed,
            )

        except RetryExhaustedError as e:
            # 重试耗尽
            elapsed = time.time() - start_time
            node.status = AnalysisStatus.FAILED
            node.error_message = str(e.last_exception)

            if self._on_progress:
                self._on_progress(node, f"失败: {e.last_exception}")

            return AnalysisResult(
                node=node,
                success=False,
                error=str(e.last_exception),
                elapsed_time=elapsed,
            )

        except Exception as e:
            # 其他异常
            elapsed = time.time() - start_time
            node.status = AnalysisStatus.FAILED
            node.error_message = str(e)

            if self._on_progress:
                self._on_progress(node, f"失败: {e}")

            return AnalysisResult(
                node=node,
                success=False,
                error=str(e),
                elapsed_time=elapsed,
            )

    async def _analyze_node(self, node: FileNode) -> str:
        """
        分析节点（调用LLM）

        Args:
            node: 文件节点

        Returns:
            分析结果文本

        Raises:
            ValueError: 当LLM返回空内容时
        """
        if node.is_file:
            # 读取文件内容
            with open(node.path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            # 调用LLM分析代码
            result = await self.llm_service.analyze_code(
                file_path=node.relative_path,
                code_content=content
            )

            # 检查结果是否为空
            if not result or not result.strip():
                logger.error(f"LLM返回空内容: {node.relative_path}")
                raise ValueError(f"LLM返回了空的分析内容: {node.relative_path}")

            return result
        else:
            # 目录节点的分析在LevelProcessor中处理
            raise ValueError("目录节点不应该通过队列直接分析")

    def get_stats(self) -> QueueStats:
        """获取队列统计信息"""
        return self.stats

    def reset(self) -> None:
        """重置队列状态"""
        self.tasks.clear()
        self.results.clear()
        self.stats = QueueStats()

    @property
    def is_empty(self) -> bool:
        """队列是否为空"""
        return self.queue.empty()

    @property
    def pending_count(self) -> int:
        """待处理任务数"""
        return self.stats.queued + self.stats.running
