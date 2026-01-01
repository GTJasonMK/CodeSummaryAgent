"""
目录扫描服务模块
负责扫描目标代码库，构建层级化的文件树
"""
import fnmatch
from pathlib import Path
from typing import Dict, List, Optional, Set

from src.models.config import AnalysisConfig, get_config
from src.models.file_node import FileNode, NodeType, AnalysisStatus
from src.utils.logger import get_logger

logger = get_logger(__name__)


class DirectoryScanner:
    """
    目录扫描器

    扫描目标目录，构建文件树结构
    """

    def __init__(self, config: Optional[AnalysisConfig] = None):
        """
        初始化扫描器

        Args:
            config: 分析配置，为None则使用全局配置
        """
        self.config = config or get_config().analysis

    def scan(self, root_path: str) -> FileNode:
        """
        扫描目录，构建文件树

        Args:
            root_path: 根目录路径

        Returns:
            根节点FileNode
        """
        root = Path(root_path).resolve()
        if not root.exists():
            raise FileNotFoundError(f"目录不存在: {root_path}")
        if not root.is_dir():
            raise NotADirectoryError(f"路径不是目录: {root_path}")

        logger.info(f"开始扫描目录: {root}")

        # 创建根节点
        root_node = FileNode(
            path=str(root),
            name=root.name,
            node_type=NodeType.DIRECTORY,
            depth=0,
            relative_path="",
        )

        # 递归扫描
        self._scan_directory(root, root_node, root)

        # 剪枝：移除没有任何可分析文件的空目录
        pruned_count = self._prune_empty_directories(root_node)
        if pruned_count > 0:
            logger.info(f"已跳过 {pruned_count} 个空目录（无可分析文件）")

        # 统计信息
        all_files = root_node.get_all_files()
        all_dirs = root_node.get_all_dirs()
        logger.info(f"扫描完成: {len(all_files)} 个文件, {len(all_dirs)} 个目录")

        return root_node

    def _prune_empty_directories(self, node: FileNode) -> int:
        """
        剪枝：移除没有任何可分析文件的空目录

        递归检查每个目录：
        - 如果目录没有文件子节点，且所有目录子节点也都是空的，则移除该目录
        - 从最深层开始向上剪枝

        Args:
            node: 当前节点

        Returns:
            被移除的空目录数量
        """
        pruned_count = 0

        # 先递归处理子目录
        children_to_remove = []
        for child in node.children:
            if child.is_dir:
                # 递归剪枝子目录
                pruned_count += self._prune_empty_directories(child)

                # 检查子目录是否为空（没有任何子节点了）
                if not child.children:
                    children_to_remove.append(child)
                    pruned_count += 1

        # 移除空的子目录
        for child in children_to_remove:
            node.children.remove(child)
            logger.debug(f"跳过空目录: {child.relative_path}")

        return pruned_count

    def _scan_directory(
        self,
        current_path: Path,
        parent_node: FileNode,
        root_path: Path
    ) -> None:
        """
        递归扫描目录

        Args:
            current_path: 当前扫描的目录路径
            parent_node: 父节点
            root_path: 根目录路径（用于计算相对路径）
        """
        try:
            entries = sorted(current_path.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
        except PermissionError:
            logger.warning(f"无权限访问目录: {current_path}")
            return

        for entry in entries:
            relative_path = str(entry.relative_to(root_path))

            # 检查是否应该忽略
            if self._should_ignore(entry, relative_path):
                continue

            if entry.is_dir():
                # 创建目录节点
                dir_node = FileNode(
                    path=str(entry),
                    name=entry.name,
                    node_type=NodeType.DIRECTORY,
                    depth=parent_node.depth + 1,
                    relative_path=relative_path,
                )
                parent_node.add_child(dir_node)

                # 递归扫描子目录
                self._scan_directory(entry, dir_node, root_path)

            elif entry.is_file():
                # 检查文件扩展名
                if not self._is_supported_extension(entry):
                    continue

                # 检查文件大小
                try:
                    file_size = entry.stat().st_size
                    if file_size > self.config.max_file_size:
                        logger.debug(f"文件过大，跳过: {relative_path} ({file_size} bytes)")
                        continue
                except OSError:
                    continue

                # 创建文件节点
                file_node = FileNode(
                    path=str(entry),
                    name=entry.name,
                    node_type=NodeType.FILE,
                    depth=parent_node.depth + 1,
                    relative_path=relative_path,
                )
                parent_node.add_child(file_node)

    def _should_ignore(self, path: Path, relative_path: str) -> bool:
        """
        检查路径是否应该被忽略

        Args:
            path: 文件/目录路径
            relative_path: 相对路径

        Returns:
            是否应该忽略
        """
        # 忽略隐藏文件/目录
        if path.name.startswith("."):
            return True

        # 检查忽略模式
        for pattern in self.config.ignore_patterns:
            # 目录模式（以/或**结尾）
            if pattern.endswith("/**") or pattern.endswith("/"):
                dir_pattern = pattern.rstrip("/*")
                if path.is_dir() and fnmatch.fnmatch(path.name, dir_pattern):
                    return True
                if fnmatch.fnmatch(relative_path, pattern):
                    return True
            else:
                # 普通模式
                if fnmatch.fnmatch(path.name, pattern):
                    return True
                if fnmatch.fnmatch(relative_path, pattern):
                    return True

        return False

    def _is_supported_extension(self, path: Path) -> bool:
        """
        检查文件扩展名是否支持

        Args:
            path: 文件路径

        Returns:
            是否支持该扩展名
        """
        ext = path.suffix.lower()
        return ext in self.config.include_extensions


def get_nodes_by_depth(root: FileNode) -> Dict[int, List[FileNode]]:
    """
    按深度分组获取所有节点

    Args:
        root: 根节点

    Returns:
        深度到节点列表的映射，例如 {0: [root], 1: [dir1, dir2], 2: [file1, file2]}
    """
    depth_map: Dict[int, List[FileNode]] = {}

    def collect(node: FileNode) -> None:
        depth = node.depth
        if depth not in depth_map:
            depth_map[depth] = []
        depth_map[depth].append(node)

        for child in node.children:
            collect(child)

    collect(root)
    return depth_map


def get_max_depth(root: FileNode) -> int:
    """
    获取文件树的最大深度

    Args:
        root: 根节点

    Returns:
        最大深度值
    """
    def calc_depth(node: FileNode) -> int:
        if not node.children:
            return node.depth
        return max(calc_depth(child) for child in node.children)

    return calc_depth(root)


def get_files_at_depth(root: FileNode, depth: int) -> List[FileNode]:
    """
    获取指定深度的所有文件节点

    Args:
        root: 根节点
        depth: 目标深度

    Returns:
        该深度的文件节点列表
    """
    depth_map = get_nodes_by_depth(root)
    nodes = depth_map.get(depth, [])
    return [node for node in nodes if node.is_file]


def get_dirs_at_depth(root: FileNode, depth: int) -> List[FileNode]:
    """
    获取指定深度的所有目录节点

    Args:
        root: 根节点
        depth: 目标深度

    Returns:
        该深度的目录节点列表
    """
    depth_map = get_nodes_by_depth(root)
    nodes = depth_map.get(depth, [])
    return [node for node in nodes if node.is_dir]


def get_pending_nodes(root: FileNode) -> List[FileNode]:
    """
    获取所有待处理的节点（断点续传用）

    Args:
        root: 根节点

    Returns:
        状态为PENDING的节点列表
    """
    pending = []

    def collect(node: FileNode) -> None:
        if node.status == AnalysisStatus.PENDING:
            pending.append(node)
        for child in node.children:
            collect(child)

    collect(root)
    return pending
