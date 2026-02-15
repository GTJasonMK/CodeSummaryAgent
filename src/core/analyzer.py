"""
主分析器模块
程序核心调度器，整合各模块完成代码库分析
"""
import asyncio
import time
from pathlib import Path
from typing import Optional, Callable, Any, Dict

from src.models.file_node import FileNode
from src.models.config import AppConfig, load_config, get_config
from src.services.directory_scanner import DirectoryScanner
from src.services.llm_service import LLMService, set_llm_service
from src.services.checkpoint import (
    CheckpointService,
    count_api_in_summary_doc,
    count_api_in_usage_doc,
    compare_api_counts,
)
from src.core.document_generator import DocumentGenerator
from src.core.level_processor import LevelProcessor
from src.utils.tree_printer import print_tree
from src.utils.logger import get_logger, setup_logger

logger = get_logger(__name__)


class CodeAnalyzer:
    """
    代码分析器

    主调度器，负责：
    1. 初始化各服务组件
    2. 扫描目标代码库
    3. 调度层级处理
    4. 生成最终文档
    """

    def __init__(
        self,
        source_path: str,
        config_path: Optional[str] = None,
        docs_path: Optional[str] = None,
    ):
        """
        初始化代码分析器

        Args:
            source_path: 源代码目录路径
            config_path: 配置文件路径，为None则使用默认配置
            docs_path: 文档输出目录路径，为None则自动生成
        """
        self.source_path = Path(source_path).resolve()
        if not self.source_path.exists():
            raise FileNotFoundError(f"源代码目录不存在: {source_path}")
        if not self.source_path.is_dir():
            raise NotADirectoryError(f"路径不是目录: {source_path}")

        # 加载配置
        if config_path:
            self.config = load_config(config_path)
        else:
            self.config = get_config()

        # 计算文档输出目录
        if docs_path:
            self.docs_path = Path(docs_path).resolve()
        else:
            docs_dir_name = self.source_path.name + self.config.output.docs_suffix
            if self.config.output.docs_inside_source:
                # 文档目录在源代码内部: c:/a/b -> c:/a/b/b_docs
                self.docs_path = self.source_path / docs_dir_name
            else:
                # 文档目录与源代码平级: c:/a/b -> c:/a/b_docs
                self.docs_path = self.source_path.parent / docs_dir_name

        # 组件（延迟初始化）
        self._scanner: Optional[DirectoryScanner] = None
        self._llm_service: Optional[LLMService] = None
        self._checkpoint: Optional[CheckpointService] = None
        self._doc_generator: Optional[DocumentGenerator] = None
        self._processor: Optional[LevelProcessor] = None

        # 文件树
        self._root: Optional[FileNode] = None

        # 统计
        self._start_time: float = 0
        self._end_time: float = 0

        # 回调
        self._on_progress: Optional[Callable[[str, float], None]] = None

    def set_progress_callback(
        self,
        callback: Callable[[str, float], None]
    ) -> None:
        """
        设置进度回调

        Args:
            callback: 回调函数 (status_message, progress_percentage)
        """
        self._on_progress = callback

    def _initialize_components(self) -> None:
        """初始化各服务组件"""
        logger.info("初始化组件...")

        # 如果文档目录在源代码内部，需要动态添加忽略规则
        if self.config.output.docs_inside_source:
            docs_dir_name = self.source_path.name + self.config.output.docs_suffix
            docs_pattern = f"{docs_dir_name}/**"
            if docs_pattern not in self.config.analysis.ignore_patterns:
                self.config.analysis.ignore_patterns.append(docs_pattern)
                logger.info(f"自动添加忽略规则: {docs_pattern}")

        # 目录扫描器
        self._scanner = DirectoryScanner(self.config.analysis)

        # LLM服务
        self._llm_service = LLMService(self.config.llm)
        set_llm_service(self._llm_service)

        # 断点续传服务
        self._checkpoint = CheckpointService(
            source_root=str(self.source_path),
            docs_root=str(self.docs_path),
            config=self.config.output,
        )
        self._checkpoint.initialize()

        # 文档生成器
        self._doc_generator = DocumentGenerator(
            docs_root=str(self.docs_path),
            config=self.config.output,
        )

    async def analyze(self, resume: bool = True) -> bool:
        """
        执行代码库分析

        Args:
            resume: 是否启用断点续传（检查已完成的文件）

        Returns:
            是否成功完成
        """
        self._start_time = time.time()

        try:
            # 初始化组件
            self._initialize_components()

            # 扫描目录
            self._notify_progress("扫描目录结构...", 0)
            self._root = self._scanner.scan(str(self.source_path))

            # 打印初始目录树
            print_tree(self._root, show_status=False, title="[bold]代码库结构[/bold]")

            # 加载断点（如果启用）
            if resume:
                self._notify_progress("检查已完成的分析...", 5)
                checkpoint_loaded = self._checkpoint.load_checkpoint()
                if checkpoint_loaded:
                    logger.info(f"已加载断点文件")

                # 无论是否加载了checkpoint，都需要扫描已存在的文档
                # 这样才能正确填充 _doc_path_map，用于恢复 node.doc_path
                self._checkpoint.scan_existing_docs()
                restored = self._checkpoint.update_node_status(self._root)
                if restored > 0:
                    logger.info(f"从断点恢复: {restored} 个节点已完成")

            # 创建层级处理器
            self._processor = LevelProcessor(
                root=self._root,
                checkpoint=self._checkpoint,
                doc_generator=self._doc_generator,
                llm_service=self._llm_service,
            )

            # 执行层级处理
            self._notify_progress("分析代码文件...", 10)
            success = await self._processor.process_all_levels()

            if not success:
                logger.error("分析过程中有文件处理失败")
                self._end_time = time.time()
                self._print_summary(success=False)
                return False

            # 扫描已存在的最终文档（用于断点续传）
            self._checkpoint.scan_final_docs()

            # 生成README（如果未完成）
            self._notify_progress("生成README文档...", 85)
            if self._checkpoint.is_readme_completed():
                logger.info("README文档已存在，跳过生成")
                readme_path = str(self.docs_path / self.config.output.readme_name)
            else:
                readme_path = await self._processor.generate_readme()
                if readme_path:
                    self._checkpoint.mark_readme_completed()
                    logger.info(f"README已生成: {readme_path}")

            # 生成阅读顺序指南（如果未完成）
            self._notify_progress("生成阅读顺序指南...", 90)
            if self._checkpoint.is_reading_guide_completed():
                logger.info("阅读顺序指南已存在，跳过生成")
                guide_path = str(self.docs_path / self.config.output.reading_guide_name)
            else:
                guide_path = await self._processor.generate_reading_guide()
                if guide_path:
                    self._checkpoint.mark_reading_guide_completed()
                    logger.info(f"阅读指南已生成: {guide_path}")

            # 生成API接口文档（如果未完成）
            self._notify_progress("生成API接口文档...", 93)
            if self._checkpoint.is_api_doc_completed():
                logger.info("API接口文档已存在，跳过生成")
                api_path = str(self.docs_path / self.config.output.api_doc_name)
            else:
                api_path = await self._processor.generate_api_doc()
                if api_path:
                    self._checkpoint.mark_api_doc_completed()
                    logger.info(f"API文档已生成: {api_path}")

            # 生成API使用文档（如果未完成）
            self._notify_progress("生成API使用文档...", 97)
            if self._checkpoint.is_api_usage_doc_completed():
                logger.info("API使用文档已存在，跳过生成")
                api_usage_path = str(self.docs_path / self.config.output.api_usage_doc_name)
            else:
                api_usage_path = await self._processor.generate_api_usage_doc()
                if api_usage_path:
                    self._checkpoint.mark_api_usage_doc_completed()
                    logger.info(f"API使用文档已生成: {api_usage_path}")

            # 验证API文档接口数量一致性
            await self._verify_api_counts(api_path, api_usage_path)

            self._end_time = time.time()
            self._notify_progress("分析完成", 100)

            # 打印最终摘要
            self._print_summary(success=True)

            return True

        except Exception as e:
            logger.error(f"分析过程出错: {e}")
            self._end_time = time.time()
            raise

    def _notify_progress(self, message: str, percentage: float) -> None:
        """发送进度通知"""
        logger.info(f"[{percentage:.0f}%] {message}")
        if self._on_progress:
            self._on_progress(message, percentage)

    async def _verify_api_counts(
        self,
        api_doc_path: Optional[str],
        api_usage_doc_path: Optional[str]
    ) -> None:
        """
        验证两个API文档中的接口数量是否一致

        通过程序化提取（而非LLM）比较两个文档中的接口数量，
        帮助发现可能遗漏的接口。

        Args:
            api_doc_path: API接口清单文档路径
            api_usage_doc_path: API使用文档路径
        """
        # 检查文件是否都存在
        if not api_doc_path or not api_usage_doc_path:
            logger.debug("API文档未全部生成，跳过接口数量验证")
            return

        api_doc_file = Path(api_doc_path)
        api_usage_file = Path(api_usage_doc_path)

        if not api_doc_file.exists() or not api_usage_file.exists():
            logger.debug("API文档文件不存在，跳过接口数量验证")
            return

        try:
            # 读取文档内容
            with open(api_doc_file, "r", encoding="utf-8") as f:
                api_doc_content = f.read()

            with open(api_usage_file, "r", encoding="utf-8") as f:
                api_usage_content = f.read()

            # 程序化提取接口数量
            summary_count, summary_apis = count_api_in_summary_doc(api_doc_content)
            usage_count, usage_apis = count_api_in_usage_doc(api_usage_content)

            # 比较并输出结果
            is_consistent, report = compare_api_counts(
                summary_count, summary_apis,
                usage_count, usage_apis
            )

            if is_consistent:
                logger.info(f"[接口验证] {report}")
            else:
                logger.warning(f"[接口验证] {report}")
                # 输出详细接口列表供调试
                logger.debug(f"API接口清单中的接口: {summary_apis}")
                logger.debug(f"API使用文档中的接口: {usage_apis}")

        except Exception as e:
            logger.warning(f"接口数量验证失败: {e}")

    def _print_summary(self, success: bool) -> None:
        """打印分析摘要"""
        elapsed = self._end_time - self._start_time

        if self._processor and self._processor.progress_manager:
            # 使用进度管理器打印最终摘要
            self._processor.progress_manager.print_final_summary(elapsed)

    def get_stats(self) -> Dict[str, Any]:
        """获取分析统计"""
        stats = {
            "source_path": str(self.source_path),
            "docs_path": str(self.docs_path),
            "elapsed_time": self._end_time - self._start_time,
        }

        if self._processor:
            stats.update(self._processor.get_stats())

        return stats

    @property
    def root(self) -> Optional[FileNode]:
        """获取文件树根节点"""
        return self._root

    @property
    def docs_root(self) -> str:
        """获取文档输出目录"""
        return str(self.docs_path)


async def analyze_codebase(
    source_path: str,
    config_path: Optional[str] = None,
    docs_path: Optional[str] = None,
    resume: bool = True,
    log_level: str = "INFO",
) -> bool:
    """
    分析代码库的便捷函数

    Args:
        source_path: 源代码目录路径
        config_path: 配置文件路径
        docs_path: 文档输出目录路径
        resume: 是否启用断点续传
        log_level: 日志级别

    Returns:
        是否成功完成
    """
    # 配置日志
    setup_logger(log_level=log_level)

    # 创建分析器并执行
    analyzer = CodeAnalyzer(
        source_path=source_path,
        config_path=config_path,
        docs_path=docs_path,
    )

    return await analyzer.analyze(resume=resume)
