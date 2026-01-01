"""
断点续传服务模块
检测已完成的分析，支持中断恢复
"""
import json
from pathlib import Path
from typing import Dict, Set, Optional, List
from dataclasses import dataclass, asdict

from src.models.file_node import FileNode, AnalysisStatus, NodeType
from src.models.config import OutputConfig, get_config
from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class CheckpointData:
    """断点数据"""
    source_root: str                    # 源代码根目录
    docs_root: str                      # 文档根目录
    completed_files: List[str]          # 已完成的文件列表（相对路径）
    completed_dirs: List[str]           # 已完成的目录列表（相对路径）
    failed_files: List[str]             # 失败的文件列表
    version: str = "1.0"                # 数据版本


class CheckpointService:
    """
    断点续传服务

    功能：
    - 检测已完成的分析文件
    - 保存/恢复分析进度
    - 支持中断后继续分析
    - 预创建文档目录结构（增量更新策略）
    """

    CHECKPOINT_FILE = ".checkpoint.json"

    def __init__(
        self,
        source_root: str,
        docs_root: Optional[str] = None,
        config: Optional[OutputConfig] = None,
    ):
        """
        初始化断点续传服务

        Args:
            source_root: 源代码根目录
            docs_root: 文档根目录，为None则自动生成
            config: 输出配置
        """
        self.config = config or get_config().output
        self.source_root = Path(source_root).resolve()

        if docs_root:
            self.docs_root = Path(docs_root).resolve()
        else:
            # 自动生成文档目录
            docs_dir_name = self.source_root.name + self.config.docs_suffix
            if self.config.docs_inside_source:
                # 文档目录在源代码内部: c:/a/b -> c:/a/b/b_docs
                self.docs_root = self.source_root / docs_dir_name
            else:
                # 文档目录与源代码平级: c:/a/b -> c:/a/b_docs
                self.docs_root = self.source_root.parent / docs_dir_name

        # 已完成文件的相对路径集合
        self._completed_files: Set[str] = set()
        self._completed_dirs: Set[str] = set()
        self._failed_files: Set[str] = set()

        # 文件路径映射：源文件相对路径 -> 文档路径
        self._doc_path_map: Dict[str, str] = {}

        # 是否启用实时保存
        self._auto_save = True

    def initialize(self) -> None:
        """初始化服务，创建必要的目录"""
        self.docs_root.mkdir(parents=True, exist_ok=True)
        logger.info(f"文档输出目录: {self.docs_root}")

    def create_doc_structure(self, root: FileNode) -> None:
        """
        预创建文档目录结构（增量更新策略的核心）

        根据源代码的目录结构，预先创建对应的文档目录，
        这样后续保存文档时不需要再创建目录。

        Args:
            root: 文件树根节点
        """
        logger.info("预创建文档目录结构...")

        # 获取所有目录节点
        all_dirs = root.get_all_dirs()

        created_count = 0
        for dir_node in all_dirs:
            # 计算对应的文档目录路径
            if dir_node.relative_path:
                doc_dir = self.docs_root / dir_node.relative_path
            else:
                doc_dir = self.docs_root

            # 创建目录
            if not doc_dir.exists():
                doc_dir.mkdir(parents=True, exist_ok=True)
                created_count += 1

        logger.info(f"已创建 {created_count} 个文档目录")

    def doc_exists(self, node: FileNode) -> bool:
        """
        检查节点对应的文档文件是否真实存在

        Args:
            node: 文件节点

        Returns:
            文档文件是否存在
        """
        doc_path = self.generate_doc_path(node)
        return Path(doc_path).exists()

    def is_completed(self, node: FileNode, verify_file: bool = True) -> bool:
        """
        检查节点是否已完成分析

        Args:
            node: 文件节点
            verify_file: 是否同时验证文档文件存在

        Returns:
            是否已完成
        """
        # 首先检查内存记录
        if node.is_file:
            in_record = node.relative_path in self._completed_files
        else:
            in_record = node.relative_path in self._completed_dirs

        if not in_record:
            return False

        # 如果需要验证文件存在性
        if verify_file:
            return self.doc_exists(node)

        return True

    def get_missing_nodes(self, root: FileNode) -> tuple[List[FileNode], List[FileNode]]:
        """
        获取缺失文档的节点列表

        Args:
            root: 文件树根节点

        Returns:
            (缺失文档的文件列表, 缺失文档的目录列表)
        """
        missing_files = []
        missing_dirs = []

        # 检查所有文件
        for file_node in root.get_all_files():
            if not self.doc_exists(file_node):
                missing_files.append(file_node)

        # 检查所有目录
        for dir_node in root.get_all_dirs():
            if not self.doc_exists(dir_node):
                missing_dirs.append(dir_node)

        logger.info(
            f"发现 {len(missing_files)} 个文件和 {len(missing_dirs)} 个目录缺少文档"
        )

        return missing_files, missing_dirs

    def load_checkpoint(self) -> bool:
        """
        加载断点数据

        Returns:
            是否成功加载
        """
        checkpoint_path = self.docs_root / self.CHECKPOINT_FILE

        if not checkpoint_path.exists():
            logger.info("未找到断点文件，将从头开始分析")
            return False

        try:
            with open(checkpoint_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            checkpoint = CheckpointData(**data)

            # 验证源目录是否匹配
            if checkpoint.source_root != str(self.source_root):
                logger.warning("断点文件的源目录不匹配，将从头开始分析")
                return False

            self._completed_files = set(checkpoint.completed_files)
            self._completed_dirs = set(checkpoint.completed_dirs)
            self._failed_files = set(checkpoint.failed_files)

            logger.info(
                f"已加载断点: {len(self._completed_files)} 个文件, "
                f"{len(self._completed_dirs)} 个目录已完成"
            )
            return True

        except Exception as e:
            logger.warning(f"加载断点文件失败: {e}")
            return False

    def save_checkpoint(self) -> None:
        """保存断点数据"""
        checkpoint = CheckpointData(
            source_root=str(self.source_root),
            docs_root=str(self.docs_root),
            completed_files=list(self._completed_files),
            completed_dirs=list(self._completed_dirs),
            failed_files=list(self._failed_files),
        )

        checkpoint_path = self.docs_root / self.CHECKPOINT_FILE

        try:
            with open(checkpoint_path, "w", encoding="utf-8") as f:
                json.dump(asdict(checkpoint), f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存断点文件失败: {e}")

    def scan_existing_docs(self) -> None:
        """
        扫描已存在的文档文件

        通过扫描文档目录来恢复已完成状态
        新的目录结构：
        - 文件文档: src/utils/helper.py.md
        - 目录文档: src/utils/_dir_summary.md
        """
        if not self.docs_root.exists():
            return

        dir_summary_name = self.config.dir_summary_name

        for doc_path in self.docs_root.rglob("*.md"):
            if not doc_path.is_file():
                continue

            # 获取相对于文档根目录的路径
            try:
                relative_doc_path = doc_path.relative_to(self.docs_root)
            except ValueError:
                continue

            name = doc_path.name

            # 检查是否是目录总结文档
            if name == dir_summary_name:
                # 目录的相对路径就是文档的父目录路径
                dir_relative = str(relative_doc_path.parent)
                if dir_relative == ".":
                    dir_relative = ""
                self._completed_dirs.add(dir_relative)
                self._doc_path_map[dir_relative] = str(doc_path)

            # 检查是否是文件总结文档 (xxx.py.md -> xxx.py)
            elif name.endswith(".md"):
                # 移除 .md 后缀得到原文件名
                original_name = name[:-3]
                # 构建源文件的相对路径
                file_relative = str(relative_doc_path.parent / original_name)
                if file_relative.startswith(".\\") or file_relative.startswith("./"):
                    file_relative = file_relative[2:]
                # 统一使用正斜杠
                file_relative = file_relative.replace("\\", "/")
                self._completed_files.add(file_relative)
                self._doc_path_map[file_relative] = str(doc_path)

        logger.info(
            f"扫描到 {len(self._completed_files)} 个已完成的文件文档, "
            f"{len(self._completed_dirs)} 个目录文档"
        )

    def mark_completed(self, node: FileNode, doc_path: str, auto_save: bool = True) -> None:
        """
        标记节点为已完成

        Args:
            node: 文件节点
            doc_path: 生成的文档路径
            auto_save: 是否立即保存checkpoint（增量更新策略）
        """
        if node.is_file:
            self._completed_files.add(node.relative_path)
        else:
            self._completed_dirs.add(node.relative_path)

        self._doc_path_map[node.relative_path] = doc_path
        node.doc_path = doc_path
        node.status = AnalysisStatus.COMPLETED

        # 移除失败记录（如果有）
        self._failed_files.discard(node.relative_path)

        # 实时保存checkpoint，确保进度不丢失
        if auto_save and self._auto_save:
            self.save_checkpoint()
            logger.debug(f"已保存checkpoint: {node.relative_path}")

    def mark_failed(self, node: FileNode, error: str, auto_save: bool = True) -> None:
        """
        标记节点为失败

        Args:
            node: 文件节点
            error: 错误信息
            auto_save: 是否立即保存checkpoint
        """
        self._failed_files.add(node.relative_path)
        node.status = AnalysisStatus.FAILED
        node.error_message = error

        # 实时保存checkpoint
        if auto_save and self._auto_save:
            self.save_checkpoint()

    def get_doc_path(self, node: FileNode) -> Optional[str]:
        """
        获取节点对应的文档路径

        Args:
            node: 文件节点

        Returns:
            文档路径，未生成则返回None
        """
        return self._doc_path_map.get(node.relative_path)

    def generate_doc_path(self, node: FileNode) -> str:
        """
        生成节点对应的文档保存路径

        新的目录结构：
        - 文件: src/utils/helper.py -> docs/src/utils/helper.py.md
        - 目录: src/utils -> docs/src/utils/_dir_summary.md

        Args:
            node: 文件节点

        Returns:
            文档保存路径
        """
        if node.is_file:
            # 文件文档：保持目录结构，文件名加 .md
            doc_name = f"{node.name}.md"
            parent_path = Path(node.relative_path).parent
            if parent_path == Path("."):
                return str(self.docs_root / doc_name)
            else:
                return str(self.docs_root / parent_path / doc_name)
        else:
            # 目录文档：在目录下生成 _dir_summary.md
            dir_summary_name = self.config.dir_summary_name
            if not node.relative_path:
                return str(self.docs_root / dir_summary_name)
            else:
                return str(self.docs_root / node.relative_path / dir_summary_name)

    def get_readme_path(self) -> str:
        """获取README文档路径"""
        return str(self.docs_root / self.config.readme_name)

    def update_node_status(self, root: FileNode) -> int:
        """
        更新文件树节点的状态（根据已完成记录）

        Args:
            root: 根节点

        Returns:
            已标记为完成的节点数
        """
        count = 0

        def update(node: FileNode) -> None:
            nonlocal count
            if self.is_completed(node):
                node.status = AnalysisStatus.COMPLETED
                node.doc_path = self._doc_path_map.get(node.relative_path)
                count += 1
            elif node.relative_path in self._failed_files:
                node.status = AnalysisStatus.FAILED

            for child in node.children:
                update(child)

        update(root)
        return count

    @property
    def completed_count(self) -> int:
        """已完成的文件数"""
        return len(self._completed_files)

    @property
    def failed_count(self) -> int:
        """失败的文件数"""
        return len(self._failed_files)
