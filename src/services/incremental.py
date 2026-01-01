"""
增量分析服务模块
检测文件变更，只重新分析修改的文件
"""
import os
import hashlib
import json
from pathlib import Path
from typing import Dict, Set, List, Optional, Tuple
from dataclasses import dataclass, asdict, field
from datetime import datetime
from enum import Enum

from src.models.file_node import FileNode, AnalysisStatus
from src.utils.logger import get_logger

logger = get_logger(__name__)


class ChangeType(str, Enum):
    """变更类型"""
    ADDED = "added"         # 新增
    MODIFIED = "modified"   # 修改
    DELETED = "deleted"     # 删除
    UNCHANGED = "unchanged" # 无变化


@dataclass
class FileFingerprint:
    """文件指纹"""
    path: str                       # 相对路径
    size: int                       # 文件大小
    mtime: float                    # 修改时间
    content_hash: str               # 内容哈希（MD5）
    last_analyzed: Optional[str]    # 上次分析时间


@dataclass
class FileChange:
    """文件变更记录"""
    path: str
    change_type: ChangeType
    old_fingerprint: Optional[FileFingerprint] = None
    new_fingerprint: Optional[FileFingerprint] = None


@dataclass
class IncrementalState:
    """增量分析状态"""
    source_root: str
    last_scan_time: str
    fingerprints: Dict[str, FileFingerprint] = field(default_factory=dict)
    version: str = "1.0"


class IncrementalAnalyzer:
    """
    增量分析器

    功能：
    - 计算文件指纹
    - 检测文件变更
    - 确定需要重新分析的文件
    - 处理级联更新（文件变更导致父目录需要更新）
    """

    STATE_FILE = ".incremental_state.json"

    def __init__(self, source_root: str, docs_root: str):
        """
        初始化增量分析器

        Args:
            source_root: 源代码根目录
            docs_root: 文档根目录
        """
        self.source_root = Path(source_root).resolve()
        self.docs_root = Path(docs_root).resolve()
        self.state_file = self.docs_root / self.STATE_FILE

        # 当前状态
        self.state: Optional[IncrementalState] = None

        # 变更记录
        self.changes: List[FileChange] = []

    def load_state(self) -> bool:
        """
        加载增量状态

        Returns:
            是否成功加载
        """
        if not self.state_file.exists():
            logger.info("未找到增量状态文件，将进行全量分析")
            return False

        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            # 重建状态
            fingerprints = {}
            for path, fp_data in data.get("fingerprints", {}).items():
                fingerprints[path] = FileFingerprint(**fp_data)

            self.state = IncrementalState(
                source_root=data["source_root"],
                last_scan_time=data["last_scan_time"],
                fingerprints=fingerprints,
                version=data.get("version", "1.0"),
            )

            # 验证源目录
            if self.state.source_root != str(self.source_root):
                logger.warning("源目录不匹配，将进行全量分析")
                self.state = None
                return False

            logger.info(f"已加载增量状态: {len(self.state.fingerprints)} 个文件指纹")
            return True

        except Exception as e:
            logger.warning(f"加载增量状态失败: {e}")
            return False

    def save_state(self) -> None:
        """保存增量状态"""
        if not self.state:
            self.state = IncrementalState(
                source_root=str(self.source_root),
                last_scan_time=datetime.now().isoformat(),
                fingerprints={},
            )

        # 更新扫描时间
        self.state.last_scan_time = datetime.now().isoformat()

        # 序列化
        data = {
            "source_root": self.state.source_root,
            "last_scan_time": self.state.last_scan_time,
            "version": self.state.version,
            "fingerprints": {
                path: asdict(fp) for path, fp in self.state.fingerprints.items()
            },
        }

        self.docs_root.mkdir(parents=True, exist_ok=True)
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def compute_fingerprint(self, file_path: Path) -> FileFingerprint:
        """
        计算文件指纹

        Args:
            file_path: 文件路径

        Returns:
            文件指纹
        """
        stat = file_path.stat()
        relative_path = str(file_path.relative_to(self.source_root))

        # 计算内容哈希
        content_hash = self._compute_hash(file_path)

        return FileFingerprint(
            path=relative_path,
            size=stat.st_size,
            mtime=stat.st_mtime,
            content_hash=content_hash,
            last_analyzed=None,
        )

    def _compute_hash(self, file_path: Path) -> str:
        """计算文件内容的MD5哈希"""
        hasher = hashlib.md5()
        try:
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    hasher.update(chunk)
            return hasher.hexdigest()
        except Exception:
            return ""

    def detect_changes(self, root: FileNode) -> List[FileChange]:
        """
        检测文件变更

        Args:
            root: 文件树根节点

        Returns:
            变更列表
        """
        self.changes = []

        if not self.state:
            # 没有历史状态，所有文件都是新增
            for file_node in root.get_all_files():
                fp = self.compute_fingerprint(Path(file_node.path))
                self.changes.append(FileChange(
                    path=file_node.relative_path,
                    change_type=ChangeType.ADDED,
                    new_fingerprint=fp,
                ))
            return self.changes

        # 当前文件集合
        current_files: Dict[str, FileNode] = {
            node.relative_path: node for node in root.get_all_files()
        }

        # 历史文件集合
        old_paths = set(self.state.fingerprints.keys())
        new_paths = set(current_files.keys())

        # 检测新增文件
        for path in new_paths - old_paths:
            node = current_files[path]
            fp = self.compute_fingerprint(Path(node.path))
            self.changes.append(FileChange(
                path=path,
                change_type=ChangeType.ADDED,
                new_fingerprint=fp,
            ))

        # 检测删除文件
        for path in old_paths - new_paths:
            self.changes.append(FileChange(
                path=path,
                change_type=ChangeType.DELETED,
                old_fingerprint=self.state.fingerprints[path],
            ))

        # 检测修改文件
        for path in old_paths & new_paths:
            node = current_files[path]
            old_fp = self.state.fingerprints[path]
            new_fp = self.compute_fingerprint(Path(node.path))

            # 比较内容哈希
            if old_fp.content_hash != new_fp.content_hash:
                self.changes.append(FileChange(
                    path=path,
                    change_type=ChangeType.MODIFIED,
                    old_fingerprint=old_fp,
                    new_fingerprint=new_fp,
                ))

        logger.info(
            f"变更检测完成: "
            f"新增 {len([c for c in self.changes if c.change_type == ChangeType.ADDED])}, "
            f"修改 {len([c for c in self.changes if c.change_type == ChangeType.MODIFIED])}, "
            f"删除 {len([c for c in self.changes if c.change_type == ChangeType.DELETED])}"
        )

        return self.changes

    def get_affected_nodes(self, root: FileNode) -> Tuple[Set[str], Set[str]]:
        """
        获取受影响的节点

        基于文件变更，确定需要重新分析的文件和目录

        Args:
            root: 文件树根节点

        Returns:
            (需要重新分析的文件路径集合, 需要重新分析的目录路径集合)
        """
        affected_files: Set[str] = set()
        affected_dirs: Set[str] = set()

        for change in self.changes:
            if change.change_type in (ChangeType.ADDED, ChangeType.MODIFIED):
                affected_files.add(change.path)

                # 添加所有父目录
                path = Path(change.path)
                for parent in path.parents:
                    if str(parent) == ".":
                        continue
                    affected_dirs.add(str(parent))

            elif change.change_type == ChangeType.DELETED:
                # 删除的文件，其父目录也需要更新
                path = Path(change.path)
                for parent in path.parents:
                    if str(parent) == ".":
                        continue
                    affected_dirs.add(str(parent))

        # 根目录始终需要更新（如果有任何变更）
        if self.changes:
            affected_dirs.add("")

        return affected_files, affected_dirs

    def mark_nodes_for_reanalysis(self, root: FileNode) -> int:
        """
        标记需要重新分析的节点

        Args:
            root: 文件树根节点

        Returns:
            标记的节点数
        """
        affected_files, affected_dirs = self.get_affected_nodes(root)
        count = 0

        def mark(node: FileNode) -> None:
            nonlocal count

            if node.is_file:
                if node.relative_path in affected_files:
                    node.status = AnalysisStatus.PENDING
                    count += 1
                else:
                    node.status = AnalysisStatus.COMPLETED
            else:
                if node.relative_path in affected_dirs:
                    node.status = AnalysisStatus.PENDING
                    count += 1
                else:
                    node.status = AnalysisStatus.COMPLETED

            for child in node.children:
                mark(child)

        mark(root)
        return count

    def update_fingerprint(self, file_path: str) -> None:
        """
        更新单个文件的指纹

        Args:
            file_path: 相对文件路径
        """
        if not self.state:
            self.state = IncrementalState(
                source_root=str(self.source_root),
                last_scan_time=datetime.now().isoformat(),
            )

        full_path = self.source_root / file_path
        if full_path.exists():
            fp = self.compute_fingerprint(full_path)
            fp.last_analyzed = datetime.now().isoformat()
            self.state.fingerprints[file_path] = fp
        else:
            # 文件已删除，移除指纹
            self.state.fingerprints.pop(file_path, None)

    def get_change_summary(self) -> Dict[str, int]:
        """获取变更摘要"""
        return {
            "added": len([c for c in self.changes if c.change_type == ChangeType.ADDED]),
            "modified": len([c for c in self.changes if c.change_type == ChangeType.MODIFIED]),
            "deleted": len([c for c in self.changes if c.change_type == ChangeType.DELETED]),
            "total": len(self.changes),
        }
