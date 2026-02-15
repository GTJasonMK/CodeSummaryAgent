"""
文件节点模型模块
定义文件树的节点结构
"""
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import List, Optional, Dict, Any


class NodeType(Enum):
    """节点类型"""
    FILE = "file"
    DIRECTORY = "directory"


class AnalysisStatus(Enum):
    """分析状态"""
    PENDING = "pending"          # 待处理
    IN_PROGRESS = "in_progress"  # 处理中
    COMPLETED = "completed"      # 已完成
    FAILED = "failed"            # 失败
    SKIPPED = "skipped"          # 跳过


@dataclass
class FileNode:
    """
    文件/目录节点

    表示文件树中的一个节点，可以是文件或目录
    """
    path: str                                    # 完整路径
    name: str                                    # 文件/目录名
    node_type: NodeType                          # 节点类型
    depth: int                                   # 相对根目录的深度（根目录为0）
    relative_path: str                           # 相对于根目录的路径

    # 树结构关系
    parent: Optional["FileNode"] = field(default=None, repr=False)  # 父节点
    children: List["FileNode"] = field(default_factory=list)        # 子节点列表

    # 分析状态
    status: AnalysisStatus = AnalysisStatus.PENDING  # 分析状态
    doc_path: Optional[str] = None                   # 生成的文档路径
    error_message: Optional[str] = None              # 错误信息

    # API接口信息（用于两阶段API文档生成）
    has_api: bool = False                            # 是否包含API接口
    api_info: Optional[str] = None                   # 提取的API接口信息摘要

    @property
    def is_file(self) -> bool:
        """是否为文件"""
        return self.node_type == NodeType.FILE

    @property
    def is_dir(self) -> bool:
        """是否为目录"""
        return self.node_type == NodeType.DIRECTORY

    @property
    def extension(self) -> str:
        """获取文件扩展名（仅对文件有效）"""
        if self.is_file:
            return Path(self.name).suffix.lower()
        return ""

    @property
    def file_count(self) -> int:
        """获取子节点中的文件数量（仅对目录有效）"""
        if self.is_file:
            return 0
        return sum(1 for child in self.children if child.is_file)

    @property
    def dir_count(self) -> int:
        """获取子节点中的目录数量（仅对目录有效）"""
        if self.is_file:
            return 0
        return sum(1 for child in self.children if child.is_dir)

    def get_all_files(self) -> List["FileNode"]:
        """递归获取所有文件节点"""
        files = []
        if self.is_file:
            files.append(self)
        else:
            for child in self.children:
                files.extend(child.get_all_files())
        return files

    def get_all_dirs(self) -> List["FileNode"]:
        """递归获取所有目录节点（包括自身）"""
        dirs = []
        if self.is_dir:
            dirs.append(self)
            for child in self.children:
                dirs.extend(child.get_all_dirs())
        return dirs

    def get_api_files(self) -> List["FileNode"]:
        """递归获取所有包含API接口的文件节点"""
        api_files = []
        if self.is_file and self.has_api:
            api_files.append(self)
        else:
            for child in self.children:
                api_files.extend(child.get_api_files())
        return api_files

    def get_children_by_type(self, node_type: NodeType) -> List["FileNode"]:
        """获取指定类型的子节点"""
        return [child for child in self.children if child.node_type == node_type]

    def add_child(self, child: "FileNode") -> None:
        """添加子节点"""
        child.parent = self
        self.children.append(child)

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典（用于序列化）"""
        return {
            "path": self.path,
            "name": self.name,
            "type": self.node_type.value,
            "depth": self.depth,
            "relative_path": self.relative_path,
            "status": self.status.value,
            "doc_path": self.doc_path,
            "error_message": self.error_message,
            "has_api": self.has_api,
            "api_info": self.api_info,
            "children": [child.to_dict() for child in self.children],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any], parent: Optional["FileNode"] = None) -> "FileNode":
        """从字典创建节点"""
        node = cls(
            path=data["path"],
            name=data["name"],
            node_type=NodeType(data["type"]),
            depth=data["depth"],
            relative_path=data["relative_path"],
            parent=parent,
            status=AnalysisStatus(data.get("status", "pending")),
            doc_path=data.get("doc_path"),
            error_message=data.get("error_message"),
            has_api=data.get("has_api", False),
            api_info=data.get("api_info"),
        )

        # 递归创建子节点
        for child_data in data.get("children", []):
            child = cls.from_dict(child_data, parent=node)
            node.children.append(child)

        return node

    def __hash__(self) -> int:
        """使节点可哈希"""
        return hash(self.path)

    def __eq__(self, other: object) -> bool:
        """判断节点是否相等"""
        if not isinstance(other, FileNode):
            return False
        return self.path == other.path


@dataclass
class AnalysisTask:
    """
    分析任务

    代表一个待执行的分析任务
    """
    node: FileNode                              # 要分析的节点
    priority: int = 0                           # 优先级（数字越小优先级越高）
    retry_count: int = 0                        # 已重试次数

    def __lt__(self, other: "AnalysisTask") -> bool:
        """用于优先级队列比较"""
        return self.priority < other.priority


@dataclass
class AnalysisResult:
    """
    分析结果

    代表一个分析任务的执行结果
    """
    node: FileNode                              # 分析的节点
    success: bool                               # 是否成功
    summary: Optional[str] = None               # 生成的总结文本
    doc_path: Optional[str] = None              # 保存的文档路径
    error: Optional[str] = None                 # 错误信息
    elapsed_time: float = 0.0                   # 耗时（秒）
