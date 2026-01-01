"""
层级处理器模块
按层级顺序处理文件和目录
"""
import asyncio
from typing import List, Dict, Optional, Callable, Any
import time

from src.models.file_node import FileNode, AnalysisTask, AnalysisResult, AnalysisStatus
from src.models.config import get_config
from src.services.directory_scanner import get_nodes_by_depth, get_max_depth
from src.services.llm_service import LLMService, get_llm_service
from src.services.llm_queue import LLMQueue
from src.services.checkpoint import CheckpointService
from src.core.document_generator import DocumentGenerator
from src.utils.retry import RetryHandler, RetryExhaustedError
from src.utils.progress_manager import ProgressManager
from src.utils.logger import get_logger

logger = get_logger(__name__)


class LevelProcessor:
    """
    层级处理器

    按照从深到浅的顺序处理文件树：
    1. 先处理最深层的文件
    2. 然后处理包含这些文件的目录
    3. 逐层向上直到根目录

    所有LLM调用共享同一个并发池，确保不会超过配置的最大并发数。
    """

    def __init__(
        self,
        root: FileNode,
        checkpoint: CheckpointService,
        doc_generator: DocumentGenerator,
        llm_service: Optional[LLMService] = None,
    ):
        """
        初始化层级处理器

        Args:
            root: 文件树根节点
            checkpoint: 断点续传服务
            doc_generator: 文档生成器
            llm_service: LLM服务
        """
        self.root = root
        self.checkpoint = checkpoint
        self.doc_generator = doc_generator
        self.llm_service = llm_service or get_llm_service()

        config = get_config().llm

        # 创建共享的并发控制信号量 - 所有LLM调用共用
        self._semaphore = asyncio.Semaphore(config.max_concurrent)
        logger.info(f"LLM并发池初始化: max_concurrent={config.max_concurrent}")

        # 将共享信号量传递给LLMQueue
        self.llm_queue = LLMQueue(
            llm_service=self.llm_service,
            semaphore=self._semaphore,
        )

        self.retry_handler = RetryHandler(
            max_retries=config.max_retries,
            base_delay=config.retry_delay,
        )

        # 按深度分组的节点
        self.depth_map = get_nodes_by_depth(root)
        self.max_depth = get_max_depth(root)

        # 统计
        self.total_files = len(root.get_all_files())
        self.total_dirs = len(root.get_all_dirs())
        self.processed_files = 0
        self.processed_dirs = 0
        self.failed_nodes: List[FileNode] = []

        # 进度管理器
        self._progress_manager: Optional[ProgressManager] = None

        # 回调
        self._on_level_complete: Optional[Callable[[int, List[FileNode], List[FileNode]], None]] = None
        self._on_progress: Optional[Callable[[FileNode, str], None]] = None

    def set_callbacks(
        self,
        on_level_complete: Optional[Callable[[int, List[FileNode], List[FileNode]], None]] = None,
        on_progress: Optional[Callable[[FileNode, str], None]] = None,
    ) -> None:
        """
        设置回调函数

        Args:
            on_level_complete: 层级完成回调 (depth, completed_nodes, failed_nodes)
            on_progress: 进度回调 (node, status)
        """
        self._on_level_complete = on_level_complete
        self._on_progress = on_progress

    async def process_all_levels(self) -> bool:
        """
        处理所有层级

        采用增量更新策略：
        1. 预创建文档目录结构
        2. 检查已存在的文档，跳过已完成的节点
        3. 每完成一个节点立即保存，确保进度不丢失

        Returns:
            是否全部成功（无失败节点）
        """
        logger.info(f"开始处理，共 {self.max_depth + 1} 个层级")
        logger.info(f"文件总数: {self.total_files}, 目录总数: {self.total_dirs}")

        # 增量更新策略：预创建文档目录结构
        self.checkpoint.create_doc_structure(self.root)

        # 扫描已存在的文档，恢复进度
        self.checkpoint.scan_existing_docs()

        # 获取缺失文档的节点，更新统计
        missing_files, missing_dirs = self.checkpoint.get_missing_nodes(self.root)
        logger.info(
            f"需要处理: {len(missing_files)} 个文件, {len(missing_dirs)} 个目录"
        )

        # 创建进度管理器
        self._progress_manager = ProgressManager(
            total_files=self.total_files,
            total_dirs=self.total_dirs,
            max_depth=self.max_depth,
        )

        # 使用进度管理器的Live显示
        async with self._progress_manager.live_progress():
            # 从最深层开始，逐层处理
            for depth in range(self.max_depth, -1, -1):
                success = await self._process_level(depth)

                if not success:
                    # 有节点失败，检查是否应该中断
                    if self.failed_nodes:
                        logger.error(f"层级 {depth} 有 {len(self.failed_nodes)} 个节点处理失败")
                        # 保存断点
                        self.checkpoint.save_checkpoint()
                        return False

        # 全部完成
        self.checkpoint.save_checkpoint()
        return True

    async def _process_level(self, depth: int) -> bool:
        """
        处理单个层级

        Args:
            depth: 层级深度

        Returns:
            是否成功
        """
        nodes = self.depth_map.get(depth, [])
        if not nodes:
            return True

        logger.info(f"处理层级 {depth}: {len(nodes)} 个节点")

        # 通知进度管理器开始新层级
        if self._progress_manager:
            self._progress_manager.start_level(depth, len(nodes))

        # 分离文件和目录
        files = [n for n in nodes if n.is_file]
        dirs = [n for n in nodes if n.is_dir]

        completed: List[FileNode] = []
        failed: List[FileNode] = []

        # 1. 处理文件（并发）
        if files:
            file_results = await self._process_files(files)
            for node, success in file_results:
                if success:
                    completed.append(node)
                    self.processed_files += 1
                else:
                    failed.append(node)
                    self.failed_nodes.append(node)

        # 2. 处理目录（并发，但每个目录需要先读取子文档）
        if dirs:
            dir_results = await self._process_directories(dirs)
            for node, success in dir_results:
                if success:
                    completed.append(node)
                    self.processed_dirs += 1
                else:
                    failed.append(node)
                    self.failed_nodes.append(node)

        # 通知进度管理器层级完成
        if self._progress_manager:
            self._progress_manager.complete_level(depth)
            self._progress_manager.print_level_summary(
                depth, len(completed), len(failed), len(nodes)
            )

        # 触发回调
        if self._on_level_complete:
            self._on_level_complete(depth, completed, failed)

        # 如果有失败且不能继续，返回False
        return len(failed) == 0

    async def _process_files(
        self,
        files: List[FileNode]
    ) -> List[tuple[FileNode, bool]]:
        """
        并发处理文件节点，每个文件完成后立即保存

        采用增量保存策略：每个文件分析完成后立即保存文档，
        而不是等待所有文件完成后批量保存。

        Args:
            files: 文件节点列表

        Returns:
            (节点, 是否成功) 的列表
        """
        results: List[tuple[FileNode, bool]] = []
        results_lock = asyncio.Lock()  # 用于保护results列表的并发访问
        progress_manager = self._progress_manager  # 本地引用

        # 过滤已完成的文件
        pending_files = []
        for node in files:
            if self.checkpoint.is_completed(node):
                logger.debug(f"跳过已完成: {node.relative_path}")
                node.status = AnalysisStatus.COMPLETED
                node.doc_path = self.checkpoint.get_doc_path(node)
                results.append((node, True))
                # 更新进度管理器（已完成的也要计入）
                if progress_manager:
                    await progress_manager.complete_task(node, success=True)
            else:
                pending_files.append(node)

        if not pending_files:
            return results

        # 创建分析任务
        tasks = [AnalysisTask(node=f, priority=f.depth) for f in pending_files]

        # 进度回调 - 更新进度管理器中的任务状态
        def on_progress(node: FileNode, status: str) -> None:
            if self._on_progress:
                self._on_progress(node, status)
            # 使用asyncio.create_task来处理异步更新
            if progress_manager:
                asyncio.create_task(progress_manager.update_task(node, status))

        # 异步完成回调 - 每个文件完成后立即保存
        async def on_complete(result: AnalysisResult) -> None:
            node = result.node
            success = False

            if result.success:
                # 更新进度状态
                if progress_manager:
                    await progress_manager.update_task(node, "保存中...")

                # 立即保存文档
                try:
                    doc_path = await self.doc_generator.save_file_summary(
                        node, result.summary
                    )
                    self.checkpoint.mark_completed(node, doc_path)
                    success = True
                    logger.debug(f"已即时保存: {node.relative_path}")
                except Exception as e:
                    logger.error(f"保存文档失败: {node.relative_path}, {e}")
                    node.status = AnalysisStatus.FAILED
                    node.error_message = str(e)
                    self.checkpoint.mark_failed(node, str(e))
            else:
                error = result.error or "未知错误"
                self.checkpoint.mark_failed(node, error)

            # 更新进度管理器
            if progress_manager:
                await progress_manager.complete_task(node, success)

            # 线程安全地添加结果
            async with results_lock:
                results.append((node, success))

        # 在开始处理前，将所有待处理文件添加到进度管理器
        if progress_manager:
            for node in pending_files:
                await progress_manager.start_task(node, "等待中")

        self.llm_queue.set_callbacks(on_progress=on_progress, on_complete=on_complete)

        # 提交任务并处理
        await self.llm_queue.submit_batch(tasks)
        await self.llm_queue.process_all()

        # 重置队列
        self.llm_queue.reset()

        return results

    async def _process_directories(
        self,
        dirs: List[FileNode]
    ) -> List[tuple[FileNode, bool]]:
        """
        处理目录节点

        目录处理需要：
        1. 读取所有子节点的文档
        2. 调用LLM合并总结
        3. 保存目录文档

        Args:
            dirs: 目录节点列表

        Returns:
            (节点, 是否成功) 的列表
        """
        results: List[tuple[FileNode, bool]] = []
        progress_manager = self._progress_manager

        # 将目录添加到进度管理器
        if progress_manager:
            for node in dirs:
                if not self.checkpoint.is_completed(node):
                    await progress_manager.start_task(node, "等待中")

        # 并发处理目录
        tasks = [self._process_single_directory(d) for d in dirs]
        task_results = await asyncio.gather(*tasks, return_exceptions=True)

        for node, result in zip(dirs, task_results):
            if isinstance(result, Exception):
                logger.error(f"处理目录失败: {node.relative_path}, {result}")
                node.status = AnalysisStatus.FAILED
                node.error_message = str(result)
                results.append((node, False))
                if progress_manager:
                    await progress_manager.complete_task(node, success=False)
            else:
                results.append((node, result))
                if progress_manager:
                    await progress_manager.complete_task(node, success=result)

        return results

    async def _process_single_directory(self, node: FileNode) -> bool:
        """
        处理单个目录

        使用共享信号量控制LLM调用并发

        Args:
            node: 目录节点

        Returns:
            是否成功
        """
        progress_manager = self._progress_manager

        # 检查是否已完成
        if self.checkpoint.is_completed(node):
            logger.debug(f"跳过已完成目录: {node.relative_path}")
            node.status = AnalysisStatus.COMPLETED
            node.doc_path = self.checkpoint.get_doc_path(node)
            return True

        # 更新进度状态
        if progress_manager:
            await progress_manager.update_task(node, "读取子文档...")

        if self._on_progress:
            self._on_progress(node, "读取子文档...")

        try:
            # 读取子节点文档（不需要信号量，只是IO操作）
            sub_docs = await self.doc_generator.read_child_summaries(node)

            if not sub_docs:
                # 没有子文档（空目录或子节点都失败了）
                logger.warning(f"目录无子文档: {node.relative_path}")
                node.status = AnalysisStatus.SKIPPED
                return True

            # 使用共享信号量控制LLM调用并发
            async with self._semaphore:
                # 更新进度状态
                if progress_manager:
                    await progress_manager.update_task(node, "生成总结...")

                if self._on_progress:
                    self._on_progress(node, "生成目录总结...")

                # 调用LLM生成目录总结
                summary = await self.retry_handler.execute(
                    self.llm_service.summarize_directory,
                    node.name,
                    node.relative_path or node.name,
                    sub_docs,
                )

            # 更新进度状态
            if progress_manager:
                await progress_manager.update_task(node, "保存中...")

            # 保存目录文档
            doc_path = await self.doc_generator.save_dir_summary(node, summary)
            self.checkpoint.mark_completed(node, doc_path)

            if self._on_progress:
                self._on_progress(node, "完成")

            return True

        except RetryExhaustedError as e:
            logger.error(f"目录处理失败（重试耗尽）: {node.relative_path}, {e}")
            self.checkpoint.mark_failed(node, str(e.last_exception))
            return False

        except Exception as e:
            logger.error(f"目录处理失败: {node.relative_path}, {e}")
            self.checkpoint.mark_failed(node, str(e))
            return False

    async def generate_readme(self) -> Optional[str]:
        """
        生成最终的README文档

        使用共享信号量控制LLM调用并发

        Returns:
            README文档路径，失败返回None
        """
        logger.info("生成README文档...")

        try:
            # 读取根目录的子文档
            sub_docs = await self.doc_generator.read_child_summaries(self.root)

            if not sub_docs:
                logger.warning("无法生成README：没有子文档")
                return None

            # 使用共享信号量控制LLM调用并发
            async with self._semaphore:
                # 调用LLM生成README
                readme_content = await self.retry_handler.execute(
                    self.llm_service.generate_readme,
                    self.root.name,
                    self.root.path,
                    sub_docs,
                )

            # 保存README
            readme_path = await self.doc_generator.save_readme(
                self.root.name,
                readme_content,
            )

            return readme_path

        except Exception as e:
            logger.error(f"生成README失败: {e}")
            return None

    async def generate_reading_guide(self) -> Optional[str]:
        """
        生成项目文档阅读顺序指南

        使用共享信号量控制LLM调用并发

        Returns:
            阅读指南文档路径，失败返回None
        """
        logger.info("生成阅读顺序指南...")

        try:
            # 读取根目录的子文档
            sub_docs = await self.doc_generator.read_child_summaries(self.root)

            if not sub_docs:
                logger.warning("无法生成阅读指南：没有子文档")
                return None

            # 生成项目结构字符串
            project_structure = self._generate_structure_string(self.root)

            # 使用共享信号量控制LLM调用并发
            async with self._semaphore:
                # 调用LLM生成阅读指南
                guide_content = await self.retry_handler.execute(
                    self.llm_service.generate_reading_guide,
                    self.root.name,
                    project_structure,
                    sub_docs,
                )

            # 保存阅读指南
            guide_path = await self.doc_generator.save_reading_guide(
                self.root.name,
                guide_content,
            )

            return guide_path

        except Exception as e:
            logger.error(f"生成阅读指南失败: {e}")
            return None

    def _generate_structure_string(self, node: FileNode, prefix: str = "") -> str:
        """
        生成项目结构的字符串表示

        Args:
            node: 文件节点
            prefix: 前缀（用于缩进）

        Returns:
            结构字符串
        """
        lines = []

        if prefix == "":
            lines.append(f"{node.name}/")

        children = sorted(node.children, key=lambda x: (x.is_file, x.name.lower()))

        for i, child in enumerate(children):
            is_last = (i == len(children) - 1)
            connector = "└── " if is_last else "├── "

            if child.is_file:
                lines.append(f"{prefix}{connector}{child.name}")
            else:
                lines.append(f"{prefix}{connector}{child.name}/")
                # 递归处理子目录
                extension = "    " if is_last else "│   "
                sub_structure = self._generate_structure_string(child, prefix + extension)
                if sub_structure:
                    lines.append(sub_structure)

        return "\n".join(lines)

    def get_stats(self) -> Dict[str, Any]:
        """获取处理统计"""
        return {
            "total_files": self.total_files,
            "total_dirs": self.total_dirs,
            "processed_files": self.processed_files,
            "processed_dirs": self.processed_dirs,
            "failed_count": len(self.failed_nodes),
            "max_depth": self.max_depth,
        }

    @property
    def progress_manager(self) -> Optional[ProgressManager]:
        """获取进度管理器"""
        return self._progress_manager
