"""
层级处理器模块
按层级顺序处理文件和目录
"""
import asyncio
import re
from typing import List, Dict, Optional, Callable, Any, Tuple
import time

from src.models.file_node import FileNode, AnalysisTask, AnalysisResult, AnalysisStatus
from src.models.config import get_config
from src.services.directory_scanner import get_nodes_by_depth, get_max_depth
from src.services.llm_service import LLMService, get_llm_service, API_USAGE_BATCH_THRESHOLD
from src.services.llm_queue import LLMQueue
from src.services.checkpoint import (
    CheckpointService,
    parse_api_info_from_doc,
    count_api_in_summary_doc,
    count_api_in_usage_doc,
    compare_api_counts,
    parse_api_by_module,
    extract_all_apis_from_info_map,
    generate_api_summary_table,
)
from src.core.document_generator import DocumentGenerator
from src.utils.retry import RetryHandler, RetryExhaustedError
from src.utils.progress_manager import ProgressManager
from src.utils.logger import get_logger

logger = get_logger(__name__)


# 使用共享的parse_api_info函数，重命名以保持向后兼容
parse_api_info = parse_api_info_from_doc


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

        # 注意：scan_existing_docs() 已在 analyzer.py 中调用，这里不再重复调用

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

                    # 解析API接口信息
                    has_api, api_info = parse_api_info(result.summary)
                    if has_api and api_info:
                        self.checkpoint.mark_has_api(node, api_info)
                        logger.debug(f"检测到API接口: {node.relative_path}")

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
                # 仍然需要生成一个空的目录文档，避免重复处理
                empty_summary = f"此目录下没有可分析的子文档。"
                doc_path = await self.doc_generator.save_dir_summary(node, empty_summary)
                self.checkpoint.mark_completed(node, doc_path)
                return True

            # 使用共享信号量控制LLM调用并发
            async with self._semaphore:
                # 更新进度状态
                if progress_manager:
                    await progress_manager.update_task(node, "生成总结...")

                if self._on_progress:
                    self._on_progress(node, "生成目录总结...")

                # 带验证的目录总结生成（空内容会触发重试）
                async def summarize_with_validation() -> str:
                    summary = await self.llm_service.summarize_directory(
                        node.name,
                        node.relative_path or node.name,
                        sub_docs,
                    )
                    if not summary or not summary.strip():
                        raise ValueError("LLM返回了空的目录总结内容")
                    return summary

                # 调用LLM生成目录总结（带重试和空内容验证）
                summary = await self.retry_handler.execute(
                    summarize_with_validation,
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
                # 带验证的README生成（空内容会触发重试）
                async def generate_readme_with_validation() -> str:
                    content = await self.llm_service.generate_readme(
                        self.root.name,
                        self.root.path,
                        sub_docs,
                    )
                    if not content or not content.strip():
                        raise ValueError("LLM返回空的README内容")
                    return content

                # 调用LLM生成README（带重试和空内容验证）
                readme_content = await self.retry_handler.execute(
                    generate_readme_with_validation,
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
                # 带验证的阅读指南生成（空内容会触发重试）
                async def generate_guide_with_validation() -> str:
                    content = await self.llm_service.generate_reading_guide(
                        self.root.name,
                        project_structure,
                        sub_docs,
                    )
                    if not content or not content.strip():
                        raise ValueError("LLM返回空的阅读指南内容")
                    return content

                # 调用LLM生成阅读指南（带重试和空内容验证）
                guide_content = await self.retry_handler.execute(
                    generate_guide_with_validation,
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

    async def generate_api_doc(self) -> Optional[str]:
        """
        生成API接口文档（两阶段策略）

        阶段一：对每个包含API的文件，调用LLM提取接口详情（中间结果）
        阶段二：汇总所有中间结果，调用LLM生成最终API文档

        这样可以避免单次LLM调用上下文过长的问题，每个文件独立分析。

        Returns:
            API文档路径，失败返回None
        """
        # 检查是否启用API文档生成
        config = get_config()
        if not config.output.generate_api_doc:
            logger.info("API文档生成已禁用，跳过")
            return None

        # 检查是否有包含API的文件
        if not self.checkpoint.has_api_files():
            logger.info("未检测到API接口，跳过API文档生成")
            return None

        api_files = self.checkpoint.get_api_files()
        logger.info(f"开始两阶段API文档生成，共 {len(api_files)} 个文件包含API...")

        try:
            # ============ 阶段一：提取每个文件的接口详情 ============
            logger.info("阶段一：提取各文件的API接口详情...")

            # 找出需要提取详情的文件（跳过已有中间结果的）
            pending_files = []
            for file_path in api_files:
                if not self.checkpoint.has_api_details(file_path):
                    pending_files.append(file_path)
                else:
                    logger.debug(f"跳过已提取详情: {file_path}")

            if pending_files:
                logger.info(f"需要提取详情的文件: {len(pending_files)} 个")
                # 并发提取各文件的接口详情
                await self._extract_api_details_batch(pending_files)
            else:
                logger.info("所有API文件详情已提取，直接进入汇总阶段")

            # ============ 阶段二：汇总生成最终API文档 ============
            logger.info("阶段二：汇总生成最终API文档...")

            # 获取所有已提取的接口详情
            all_details = self.checkpoint.get_all_api_details()

            if not all_details:
                logger.error("无法生成API文档：没有提取到任何接口详情")
                return None

            # 严格检查：所有API文件都必须有详情，否则中断生成
            missing_files = []
            for file_path in api_files:
                if file_path not in all_details:
                    missing_files.append(file_path)

            if missing_files:
                logger.error(
                    f"API文档生成中断：{len(missing_files)} 个API文件的详情提取失败"
                )
                for mf in missing_files:
                    logger.error(f"  缺失: {mf}")
                logger.error(
                    f"已成功提取 {len(all_details)}/{len(api_files)} 个文件，"
                    f"请检查上述文件的分析文档是否存在，或手动删除checkpoint后重新运行"
                )
                return None

            # ============ 程序化生成接口总览表（解决LLM汇总遗漏问题） ============
            # 从 checkpoint 的 api_info_map 中程序化提取所有接口
            # 这确保了接口列表的完整性，不会因 LLM 汇总而遗漏任何接口
            api_info_map = self.checkpoint.get_all_api_info()
            all_apis = extract_all_apis_from_info_map(api_info_map)
            summary_table = generate_api_summary_table(all_apis)
            logger.info(f"程序化生成接口总览表: {len(all_apis)} 个接口")

            # 组装所有详情（用于 LLM 生成按模块分类的详细描述）
            details_content = []
            for file_path, details in all_details.items():
                details_content.append(f"## 文件: {file_path}\n\n{details}")

            combined_details = "\n\n---\n\n".join(details_content)
            logger.info(f"已汇总 {len(all_details)} 个文件的接口详情")

            # 使用共享信号量控制LLM调用并发
            async with self._semaphore:
                # 带验证的API文档生成（空内容会触发重试）
                async def summarize_api_with_validation() -> str:
                    content = await self.llm_service.summarize_api_docs(
                        self.root.name,
                        combined_details,
                    )
                    if not content or not content.strip():
                        raise ValueError("LLM返回空的API文档内容")
                    return content

                # 调用LLM生成最终API文档（带重试和空内容验证）
                llm_content = await self.retry_handler.execute(
                    summarize_api_with_validation,
                )

            # ============ 组装最终API文档 ============
            # 用程序化生成的接口总览表替换 LLM 生成的总览表
            # LLM 生成的内容可能遗漏接口，程序化生成的保证完整

            # 尝试从 LLM 内容中提取"二、按模块分类"及之后的部分
            # 如果 LLM 输出格式标准，则只保留"按模块分类"和"认证要求汇总"部分
            module_section_match = re.search(
                r'(##\s*二、按模块分类.*)',
                llm_content,
                re.DOTALL | re.IGNORECASE
            )

            if module_section_match:
                # 提取"二、按模块分类"及之后的内容
                rest_content = module_section_match.group(1)
                # 组装最终文档：程序化总览表 + LLM的模块分类和认证汇总
                api_content = f"# {self.root.name} API接口文档\n\n{summary_table}\n{rest_content}"
                logger.info("成功组装API文档：程序化总览表 + LLM详情")
            else:
                # 如果 LLM 输出格式不标准，将程序化总览表放在最前面
                # 然后附上 LLM 的完整输出（作为补充）
                logger.warning("LLM输出格式不标准，无法提取模块分类部分，使用拼接方式")
                api_content = f"# {self.root.name} API接口文档\n\n{summary_table}\n\n{llm_content}"

            # 保存API文档
            api_path = await self.doc_generator.save_api_doc(
                self.root.name,
                api_content,
            )

            logger.info(f"API文档生成完成: {api_path}")
            return api_path

        except Exception as e:
            logger.error(f"生成API文档失败: {e}")
            return None

    async def _extract_api_details_batch(self, file_paths: List[str]) -> None:
        """
        并发提取多个文件的API接口详情

        Args:
            file_paths: 需要提取详情的文件路径列表
        """
        async def extract_single(file_path: str) -> None:
            """提取单个文件的接口详情"""
            try:
                # 读取文件的分析文档
                doc_path = self.checkpoint.get_doc_path_by_relative(file_path)
                if not doc_path:
                    # 尝试从文件节点获取
                    for file_node in self.root.get_all_files():
                        if file_node.relative_path == file_path and file_node.doc_path:
                            doc_path = file_node.doc_path
                            break

                if not doc_path:
                    logger.error(f"API详情提取失败 - 找不到文件文档: {file_path}")
                    logger.debug(f"  检查 _doc_path_map 和 node.doc_path 是否正确设置")
                    return

                # 读取文档内容
                file_doc = await self.doc_generator.read_document(doc_path)
                if not file_doc:
                    logger.error(f"API详情提取失败 - 文档内容为空: {file_path} ({doc_path})")
                    return

                # 带验证的API详情提取（空内容会触发重试）
                async def extract_with_validation() -> str:
                    details = await self.llm_service.extract_api_details(file_path, file_doc)
                    if not details or not details.strip():
                        raise ValueError(f"LLM返回空的API详情")
                    return details

                # 使用共享信号量控制并发
                async with self._semaphore:
                    logger.debug(f"提取API详情: {file_path}")
                    # 调用LLM提取接口详情（带重试和空内容验证）
                    details = await self.retry_handler.execute(
                        extract_with_validation,
                    )

                # 保存中间结果到checkpoint
                self.checkpoint.save_api_details(file_path, details)
                logger.info(f"已提取API详情: {file_path}")

            except Exception as e:
                logger.error(f"API详情提取失败 - LLM调用错误: {file_path}, {e}")

        # 并发执行所有提取任务
        tasks = [extract_single(fp) for fp in file_paths]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def generate_api_usage_doc(self) -> Optional[str]:
        """
        生成API使用文档（支持分批生成策略）

        当接口数量超过阈值时，采用分批生成策略：
        1. 按模块分批生成接口详情
        2. 单独生成通用部分
        3. 程序化合并所有部分

        Returns:
            API使用文档路径，失败返回None
        """
        # 检查是否启用API使用文档生成
        config = get_config()
        if not config.output.generate_api_usage_doc:
            logger.info("API使用文档生成已禁用，跳过")
            return None

        # 检查是否有包含API的文件
        if not self.checkpoint.has_api_files():
            logger.info("未检测到API接口，跳过API使用文档生成")
            return None

        api_files = self.checkpoint.get_api_files()
        logger.info(f"开始API使用文档生成，共 {len(api_files)} 个文件包含API...")

        try:
            # ============ 阶段一：提取每个文件的API使用详情 ============
            logger.info("阶段一：提取各文件的API使用详情...")

            # 找出需要提取详情的文件（跳过已有中间结果的）
            pending_files = []
            for file_path in api_files:
                if not self.checkpoint.has_api_usage_details(file_path):
                    pending_files.append(file_path)
                else:
                    logger.debug(f"跳过已提取使用详情: {file_path}")

            if pending_files:
                logger.info(f"需要提取使用详情的文件: {len(pending_files)} 个")
                # 并发提取各文件的使用详情
                await self._extract_api_usage_details_batch(pending_files)
            else:
                logger.info("所有API文件使用详情已提取，直接进入汇总阶段")

            # ============ 阶段二：汇总生成最终API使用文档 ============
            logger.info("阶段二：汇总生成最终API使用文档...")

            # 获取所有已提取的使用详情
            all_details = self.checkpoint.get_all_api_usage_details()

            if not all_details:
                logger.warning("无法生成API使用文档：没有提取到使用详情")
                return None

            # 严格检查：所有API文件都必须有使用详情，否则中断生成
            missing_files = []
            for file_path in api_files:
                if file_path not in all_details:
                    missing_files.append(file_path)

            if missing_files:
                logger.error(
                    f"API使用文档生成中断：{len(missing_files)} 个API文件的使用详情提取失败"
                )
                for mf in missing_files:
                    logger.error(f"  缺失: {mf}")
                logger.error(
                    f"已成功提取 {len(all_details)}/{len(api_files)} 个文件，"
                    f"请检查上述文件的分析文档是否存在，或手动删除checkpoint后重新运行"
                )
                return None

            # 组装所有详情
            details_content = []
            for file_path, details in all_details.items():
                details_content.append(f"## 文件: {file_path}\n\n{details}")

            combined_details = "\n\n---\n\n".join(details_content)
            logger.info(f"已汇总 {len(all_details)} 个文件的使用详情")

            # 读取API清单文档，获取接口数量和模块分布
            api_count, api_list, api_modules = await self._get_api_info_for_usage_doc()

            # 根据接口数量决定生成策略
            if api_count <= API_USAGE_BATCH_THRESHOLD:
                # 接口数量较少，使用单次生成
                logger.info(f"接口数量({api_count})未超过阈值({API_USAGE_BATCH_THRESHOLD})，使用单次生成策略")
                api_usage_content = await self._generate_api_usage_single(
                    combined_details, api_list
                )
            else:
                # 接口数量较多，使用程序化组装策略
                # 直接使用已提取的详情，避免LLM重新生成时遗漏接口
                logger.info(f"接口数量({api_count})超过阈值({API_USAGE_BATCH_THRESHOLD})，使用程序化组装策略")
                api_usage_content = await self._generate_api_usage_programmatic(
                    all_details, api_count
                )

            # 保存API使用文档
            api_usage_path = await self.doc_generator.save_api_usage_doc(
                self.root.name,
                api_usage_content,
            )

            logger.info(f"API使用文档生成完成: {api_usage_path}")
            return api_usage_path

        except Exception as e:
            logger.error(f"生成API使用文档失败: {e}")
            return None

    async def _get_api_info_for_usage_doc(self) -> Tuple[int, str, Dict[str, List[str]]]:
        """
        获取API信息用于使用文档生成

        Returns:
            (接口总数, 接口列表字符串, 按模块分组的接口字典)
        """
        config = get_config()
        api_doc_path = self.checkpoint.docs_root / config.output.api_doc_name

        if not api_doc_path.exists():
            logger.warning("API清单文档不存在")
            return 0, "", {}

        # 读取API清单文档
        api_doc_content = await self.doc_generator.read_document(str(api_doc_path))
        if not api_doc_content:
            return 0, "", {}

        # 提取接口列表
        count, apis = count_api_in_summary_doc(api_doc_content)

        # 格式化接口列表
        api_list_lines = [f"共 {count} 个接口："]
        for i, api in enumerate(apis, 1):
            api_list_lines.append(f"{i}. {api}")
        api_list_str = "\n".join(api_list_lines)

        # 按模块解析接口
        api_modules = parse_api_by_module(api_doc_content)

        return count, api_list_str, api_modules

    async def _generate_api_usage_single(
        self,
        combined_details: str,
        api_reference_list: str
    ) -> str:
        """
        单次生成API使用文档（接口数量较少时使用）

        Args:
            combined_details: 汇总的API使用详情
            api_reference_list: 接口参考列表

        Returns:
            API使用文档内容
        """
        async with self._semaphore:
            async def summarize_with_validation() -> str:
                content = await self.llm_service.summarize_api_usage_docs(
                    self.root.name,
                    combined_details,
                    api_reference_list,
                )
                if not content or not content.strip():
                    raise ValueError("LLM返回空的API使用文档内容")
                return content

            return await self.retry_handler.execute(summarize_with_validation)

    async def _generate_api_usage_batched(
        self,
        combined_details: str,
        api_modules: Dict[str, List[str]],
        total_count: int
    ) -> str:
        """
        分批生成API使用文档（接口数量较多时使用）

        策略：
        1. 按模块分批生成接口详情
        2. 单独生成通用部分（快速开始、错误处理、调用示例）
        3. 程序化合并所有部分

        Args:
            combined_details: 汇总的API使用详情
            api_modules: 按模块分组的接口字典
            total_count: 接口总数

        Returns:
            完整的API使用文档内容
        """
        module_docs: Dict[str, str] = {}
        module_order = [
            "核心业务接口",
            "会话管理接口",
            "资源管理接口",
            "用户与认证接口",
            "系统管理接口",
            "辅助接口",
        ]

        # 按模块生成接口详情
        logger.info(f"分批生成: 共 {len(api_modules)} 个模块")

        for module_name, module_apis in api_modules.items():
            if not module_apis:
                continue

            logger.info(f"生成模块文档: {module_name} ({len(module_apis)} 个接口)")

            # 格式化模块接口列表
            module_api_list = "\n".join([f"- {api}" for api in module_apis])

            # 使用局部变量捕获当前循环的值，避免闭包陷阱
            current_module_name = module_name
            current_module_api_list = module_api_list

            async with self._semaphore:
                async def generate_module_with_validation(
                    _module_name: str = current_module_name,
                    _module_api_list: str = current_module_api_list
                ) -> str:
                    content = await self.llm_service.generate_api_usage_module(
                        self.root.name,
                        _module_name,
                        _module_api_list,
                        combined_details,
                    )
                    if not content or not content.strip():
                        raise ValueError(f"LLM返回空的{_module_name}文档内容")
                    return content

                module_doc = await self.retry_handler.execute(generate_module_with_validation)
                module_docs[module_name] = module_doc

        # 生成通用部分
        logger.info("生成API使用文档通用部分...")

        # 构建API概况
        api_overview_lines = [f"项目共有 {total_count} 个API接口，分布如下："]
        for module_name in module_order:
            if module_name in api_modules:
                api_overview_lines.append(f"- {module_name}: {len(api_modules[module_name])} 个")
        api_overview = "\n".join(api_overview_lines)

        # 只取部分详情用于推断认证方式等（避免上下文过长）
        details_sample = combined_details[:8000] if len(combined_details) > 8000 else combined_details

        async with self._semaphore:
            async def generate_common_with_validation() -> str:
                content = await self.llm_service.generate_api_usage_common(
                    self.root.name,
                    api_overview,
                    details_sample,
                )
                if not content or not content.strip():
                    raise ValueError("LLM返回空的通用部分内容")
                return content

            common_doc = await self.retry_handler.execute(generate_common_with_validation)

        # 程序化合并所有部分
        logger.info("合并所有部分...")
        return self._merge_api_usage_docs(common_doc, module_docs, module_order)

    async def _generate_api_usage_programmatic(
        self,
        all_details: Dict[str, str],
        total_count: int
    ) -> str:
        """
        程序化组装API使用文档（避免LLM重新生成时遗漏接口）

        策略：
        1. 直接使用已提取的 api_usage_details_map 内容（Phase 1的成果）
        2. 只让 LLM 生成通用部分（快速开始、错误处理、调用示例）
        3. 程序化拼接所有接口详情

        这样可以保证：
        - Phase 1 提取的所有接口详情都会被包含
        - 不会因为 LLM 二次处理而遗漏任何接口

        Args:
            all_details: 所有文件的使用详情字典 {文件路径: 详情内容}
            total_count: 接口总数

        Returns:
            完整的API使用文档内容
        """
        logger.info(f"程序化组装API使用文档: {len(all_details)} 个文件, {total_count} 个接口")

        # ============ 1. 生成通用部分（快速开始、错误处理、调用示例） ============
        # 只取部分详情用于推断认证方式等（避免上下文过长）
        sample_details = []
        for file_path, details in list(all_details.items())[:3]:  # 取前3个文件作为样本
            sample_details.append(f"## 文件: {file_path}\n\n{details[:2000]}")  # 每个文件取前2000字符
        details_sample = "\n\n---\n\n".join(sample_details)

        # 构建API概况
        api_overview = f"项目共有 {total_count} 个API接口，分布在 {len(all_details)} 个文件中"

        async with self._semaphore:
            async def generate_common_with_validation() -> str:
                content = await self.llm_service.generate_api_usage_common(
                    self.root.name,
                    api_overview,
                    details_sample,
                )
                if not content or not content.strip():
                    raise ValueError("LLM返回空的通用部分内容")
                return content

            common_doc = await self.retry_handler.execute(generate_common_with_validation)

        # ============ 2. 程序化组装最终文档 ============
        parts = []

        # 文档标题
        parts.append(f"# {self.root.name} API使用文档\n")

        # 提取通用部分的"快速开始"
        quick_start_added = False
        if "## 一、快速开始" in common_doc:
            start_idx = common_doc.find("## 一、快速开始")
            # 查找结束位置（到下一个主要章节）
            end_idx = len(common_doc)
            for marker in ["## 二、", "## 三、", "## 四、"]:
                idx = common_doc.find(marker)
                if idx > start_idx and idx < end_idx:
                    end_idx = idx
            quick_start_content = common_doc[start_idx:end_idx].strip()
            if quick_start_content:
                parts.append(quick_start_content)
                quick_start_added = True

        if not quick_start_added:
            parts.append("## 一、快速开始\n\n请参考各接口的请求示例进行调用。\n")

        # ============ 3. 接口详情部分（直接使用已提取的内容） ============
        parts.append("\n\n## 二、接口详情\n")

        # 按文件路径排序，确保顺序稳定
        sorted_files = sorted(all_details.keys())

        # 按目录分组
        file_groups: Dict[str, List[str]] = {}
        for file_path in sorted_files:
            # 提取目录名作为分组
            parts_path = file_path.replace("\\", "/").split("/")
            if len(parts_path) > 1:
                group_name = parts_path[0]
            else:
                group_name = "root"
            if group_name not in file_groups:
                file_groups[group_name] = []
            file_groups[group_name].append(file_path)

        # 按分组输出接口详情
        group_index = 1
        for group_name, files in sorted(file_groups.items()):
            # 分组标题
            display_name = self._get_group_display_name(group_name)
            parts.append(f"\n### 2.{group_index} {display_name}\n")

            # 输出该分组下所有文件的接口详情
            for file_path in files:
                details = all_details[file_path]
                # 添加文件来源注释
                parts.append(f"\n> 来源: `{file_path}`\n")
                parts.append(details)
                parts.append("\n")

            group_index += 1

        # ============ 4. 错误处理和调用示例 ============
        error_section_added = False
        if "## 三、错误处理" in common_doc:
            error_idx = common_doc.find("## 三、错误处理")
            parts.append("\n\n" + common_doc[error_idx:].strip())
            error_section_added = True
        elif "## 四、调用示例" in common_doc:
            example_idx = common_doc.find("## 四、调用示例")
            parts.append("\n\n" + common_doc[example_idx:].strip())
            error_section_added = True

        if not error_section_added:
            parts.append("\n\n## 三、错误处理\n\n请参考各接口文档中的错误响应说明。\n")

        logger.info(f"程序化组装完成: {group_index - 1} 个分组")
        return "\n".join(parts)

    def _get_group_display_name(self, group_name: str) -> str:
        """
        将目录名转换为友好的显示名

        Args:
            group_name: 目录名

        Returns:
            友好的显示名
        """
        # 常见目录名映射
        name_map = {
            "apiserver": "API服务接口",
            "agentserver": "Agent服务接口",
            "mcpserver": "MCP服务接口",
            "mqtt_tool": "MQTT工具接口",
            "voice": "语音服务接口",
            "root": "根目录接口",
        }

        if group_name in name_map:
            return name_map[group_name]

        # 通用转换：下划线转空格，首字母大写
        display = group_name.replace("_", " ").replace("-", " ").title()
        return f"{display}接口"

    def _merge_api_usage_docs(
        self,
        common_doc: str,
        module_docs: Dict[str, str],
        module_order: List[str]
    ) -> str:
        """
        合并分批生成的API使用文档

        Args:
            common_doc: 通用部分（快速开始、错误处理、调用示例）
            module_docs: 各模块的接口文档
            module_order: 模块顺序

        Returns:
            完整的API使用文档
        """
        parts = []

        # 文档标题
        parts.append(f"# {self.root.name} API使用文档\n")

        # 提取通用部分的"快速开始"
        quick_start_added = False
        if "## 一、快速开始" in common_doc:
            start_idx = common_doc.find("## 一、快速开始")
            end_idx = common_doc.find("## 三、错误处理")
            if end_idx == -1:
                end_idx = common_doc.find("## 四、调用示例")
            if end_idx == -1:
                end_idx = len(common_doc)
            quick_start_content = common_doc[start_idx:end_idx].strip()
            if quick_start_content:
                parts.append(quick_start_content)
                quick_start_added = True

        if not quick_start_added:
            # fallback: 尝试提取开头部分直到错误处理或调用示例
            lines = common_doc.split("\n")
            quick_start_lines = []
            for i, line in enumerate(lines):
                if line.startswith("## 三") or line.startswith("## 四"):
                    break
                quick_start_lines.append(line)
            if quick_start_lines:
                parts.append("\n".join(quick_start_lines).strip())
                quick_start_added = True

        # 如果仍然没有快速开始内容，添加一个默认的占位
        if not quick_start_added:
            parts.append("## 一、快速开始\n\n（快速开始内容待补充）")

        # 接口详情部分
        parts.append("\n\n## 二、接口详情\n")

        module_index = 1
        for module_name in module_order:
            if module_name in module_docs:
                parts.append(f"\n### 2.{module_index} {module_name}\n")
                parts.append(module_docs[module_name])
                module_index += 1

        # 处理不在预定义顺序中的模块
        for module_name, doc in module_docs.items():
            if module_name not in module_order:
                parts.append(f"\n### 2.{module_index} {module_name}\n")
                parts.append(doc)
                module_index += 1

        # 错误处理和调用示例
        error_and_example_added = False
        if "## 三、错误处理" in common_doc:
            error_idx = common_doc.find("## 三、错误处理")
            parts.append("\n\n" + common_doc[error_idx:].strip())
            error_and_example_added = True
        elif "## 四、调用示例" in common_doc:
            example_idx = common_doc.find("## 四、调用示例")
            parts.append("\n\n" + common_doc[example_idx:].strip())
            error_and_example_added = True

        # 如果没有找到错误处理和调用示例，添加默认内容
        if not error_and_example_added:
            parts.append("\n\n## 三、错误处理\n\n（错误处理内容待补充）")
            parts.append("\n\n## 四、调用示例\n\n（调用示例内容待补充）")

        return "\n".join(parts)

    async def _extract_api_usage_details_batch(self, file_paths: List[str]) -> None:
        """
        并发提取多个文件的API使用详情

        Args:
            file_paths: 需要提取详情的文件路径列表
        """
        async def extract_single(file_path: str) -> None:
            """提取单个文件的使用详情"""
            try:
                # 读取文件的分析文档
                doc_path = self.checkpoint.get_doc_path_by_relative(file_path)
                if not doc_path:
                    # 尝试从文件节点获取
                    for file_node in self.root.get_all_files():
                        if file_node.relative_path == file_path and file_node.doc_path:
                            doc_path = file_node.doc_path
                            break

                if not doc_path:
                    logger.error(f"API使用详情提取失败 - 找不到文件文档: {file_path}")
                    logger.debug(f"  检查 _doc_path_map 和 node.doc_path 是否正确设置")
                    return

                # 读取文档内容
                file_doc = await self.doc_generator.read_document(doc_path)
                if not file_doc:
                    logger.error(f"API使用详情提取失败 - 文档内容为空: {file_path} ({doc_path})")
                    return

                # 带验证的API使用详情提取（空内容会触发重试）
                async def extract_with_validation() -> str:
                    details = await self.llm_service.extract_api_usage_details(file_path, file_doc)
                    if not details or not details.strip():
                        raise ValueError(f"LLM返回空的API使用详情")
                    return details

                # 使用共享信号量控制并发
                async with self._semaphore:
                    logger.debug(f"提取API使用详情: {file_path}")
                    # 调用LLM提取使用详情（带重试和空内容验证）
                    details = await self.retry_handler.execute(
                        extract_with_validation,
                    )

                # 保存中间结果到checkpoint
                self.checkpoint.save_api_usage_details(file_path, details)
                logger.info(f"已提取API使用详情: {file_path}")

            except Exception as e:
                logger.error(f"API使用详情提取失败 - LLM调用错误: {file_path}, {e}")

        # 并发执行所有提取任务
        tasks = [extract_single(fp) for fp in file_paths]
        await asyncio.gather(*tasks, return_exceptions=True)

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
