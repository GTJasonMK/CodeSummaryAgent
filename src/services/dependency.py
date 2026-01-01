"""
代码依赖关系分析模块
分析代码文件之间的导入/依赖关系
"""
import re
import ast
from pathlib import Path
from typing import Dict, List, Set, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

from src.models.file_node import FileNode
from src.utils.logger import get_logger

logger = get_logger(__name__)


class DependencyType(str, Enum):
    """依赖类型"""
    IMPORT = "import"           # 导入依赖
    EXTENDS = "extends"         # 继承关系
    IMPLEMENTS = "implements"   # 实现接口
    USES = "uses"               # 使用关系
    CALLS = "calls"             # 调用关系


@dataclass
class Dependency:
    """依赖关系"""
    source: str                 # 源文件（相对路径）
    target: str                 # 目标文件/模块
    dep_type: DependencyType    # 依赖类型
    line: Optional[int] = None  # 行号
    detail: Optional[str] = None  # 详细信息


@dataclass
class DependencyGraph:
    """依赖关系图"""
    nodes: Set[str] = field(default_factory=set)
    edges: List[Dependency] = field(default_factory=list)

    # 邻接表表示
    outgoing: Dict[str, List[Dependency]] = field(default_factory=dict)  # 出边（依赖什么）
    incoming: Dict[str, List[Dependency]] = field(default_factory=dict)  # 入边（被什么依赖）

    def add_node(self, node: str) -> None:
        """添加节点"""
        self.nodes.add(node)
        if node not in self.outgoing:
            self.outgoing[node] = []
        if node not in self.incoming:
            self.incoming[node] = []

    def add_edge(self, dep: Dependency) -> None:
        """添加边"""
        self.add_node(dep.source)
        self.add_node(dep.target)
        self.edges.append(dep)
        self.outgoing[dep.source].append(dep)
        self.incoming[dep.target].append(dep)

    def get_dependencies(self, node: str) -> List[Dependency]:
        """获取节点的所有依赖（出边）"""
        return self.outgoing.get(node, [])

    def get_dependents(self, node: str) -> List[Dependency]:
        """获取依赖该节点的所有文件（入边）"""
        return self.incoming.get(node, [])

    def to_dict(self) -> Dict:
        """转换为字典（用于序列化）"""
        return {
            "nodes": list(self.nodes),
            "edges": [
                {
                    "source": e.source,
                    "target": e.target,
                    "type": e.dep_type.value,
                    "line": e.line,
                    "detail": e.detail,
                }
                for e in self.edges
            ],
        }

    def to_mermaid(self) -> str:
        """生成Mermaid流程图代码"""
        lines = ["graph LR"]

        # 为节点生成短ID
        node_ids = {node: f"N{i}" for i, node in enumerate(self.nodes)}

        # 添加节点定义
        for node, nid in node_ids.items():
            # 使用文件名作为显示名
            display_name = Path(node).name if "/" in node or "\\" in node else node
            lines.append(f'    {nid}["{display_name}"]')

        # 添加边
        for edge in self.edges:
            source_id = node_ids.get(edge.source, edge.source)
            target_id = node_ids.get(edge.target, edge.target)
            lines.append(f"    {source_id} --> {target_id}")

        return "\n".join(lines)


class DependencyAnalyzer:
    """
    依赖关系分析器

    支持多种编程语言的依赖分析
    """

    def __init__(self, source_root: str):
        """
        初始化依赖分析器

        Args:
            source_root: 源代码根目录
        """
        self.source_root = Path(source_root).resolve()
        self.graph = DependencyGraph()

        # 语言解析器映射
        self.parsers = {
            ".py": self._parse_python,
            ".js": self._parse_javascript,
            ".ts": self._parse_typescript,
            ".jsx": self._parse_javascript,
            ".tsx": self._parse_typescript,
            ".java": self._parse_java,
            ".go": self._parse_go,
        }

    def analyze(self, root: FileNode) -> DependencyGraph:
        """
        分析文件树的依赖关系

        Args:
            root: 文件树根节点

        Returns:
            依赖关系图
        """
        self.graph = DependencyGraph()

        for file_node in root.get_all_files():
            self._analyze_file(file_node)

        logger.info(
            f"依赖分析完成: {len(self.graph.nodes)} 个节点, "
            f"{len(self.graph.edges)} 条依赖关系"
        )

        return self.graph

    def _analyze_file(self, node: FileNode) -> None:
        """分析单个文件的依赖"""
        ext = node.extension.lower()
        parser = self.parsers.get(ext)

        if not parser:
            return

        try:
            with open(node.path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            dependencies = parser(content, node.relative_path)

            for dep in dependencies:
                self.graph.add_edge(dep)

        except Exception as e:
            logger.debug(f"分析文件依赖失败: {node.path}, {e}")

    def _parse_python(self, content: str, file_path: str) -> List[Dependency]:
        """解析Python文件的导入"""
        dependencies = []

        try:
            tree = ast.parse(content)

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        dependencies.append(Dependency(
                            source=file_path,
                            target=alias.name,
                            dep_type=DependencyType.IMPORT,
                            line=node.lineno,
                            detail=f"import {alias.name}",
                        ))

                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    for alias in node.names:
                        target = f"{module}.{alias.name}" if module else alias.name
                        dependencies.append(Dependency(
                            source=file_path,
                            target=target,
                            dep_type=DependencyType.IMPORT,
                            line=node.lineno,
                            detail=f"from {module} import {alias.name}",
                        ))

        except SyntaxError:
            # 如果AST解析失败，使用正则表达式
            dependencies.extend(self._parse_python_regex(content, file_path))

        return dependencies

    def _parse_python_regex(self, content: str, file_path: str) -> List[Dependency]:
        """使用正则表达式解析Python导入（备用方案）"""
        dependencies = []

        # import xxx
        import_pattern = r"^\s*import\s+([\w.]+)"
        for match in re.finditer(import_pattern, content, re.MULTILINE):
            dependencies.append(Dependency(
                source=file_path,
                target=match.group(1),
                dep_type=DependencyType.IMPORT,
            ))

        # from xxx import yyy
        from_pattern = r"^\s*from\s+([\w.]+)\s+import"
        for match in re.finditer(from_pattern, content, re.MULTILINE):
            dependencies.append(Dependency(
                source=file_path,
                target=match.group(1),
                dep_type=DependencyType.IMPORT,
            ))

        return dependencies

    def _parse_javascript(self, content: str, file_path: str) -> List[Dependency]:
        """解析JavaScript文件的导入"""
        dependencies = []

        # ES6 import
        # import xxx from 'yyy'
        # import { xxx } from 'yyy'
        es6_pattern = r"import\s+.*?\s+from\s+['\"]([^'\"]+)['\"]"
        for match in re.finditer(es6_pattern, content):
            dependencies.append(Dependency(
                source=file_path,
                target=match.group(1),
                dep_type=DependencyType.IMPORT,
                detail=match.group(0),
            ))

        # import 'xxx'
        side_effect_pattern = r"import\s+['\"]([^'\"]+)['\"]"
        for match in re.finditer(side_effect_pattern, content):
            dependencies.append(Dependency(
                source=file_path,
                target=match.group(1),
                dep_type=DependencyType.IMPORT,
            ))

        # CommonJS require
        require_pattern = r"require\s*\(\s*['\"]([^'\"]+)['\"]\s*\)"
        for match in re.finditer(require_pattern, content):
            dependencies.append(Dependency(
                source=file_path,
                target=match.group(1),
                dep_type=DependencyType.IMPORT,
            ))

        return dependencies

    def _parse_typescript(self, content: str, file_path: str) -> List[Dependency]:
        """解析TypeScript文件的导入"""
        # TypeScript导入语法与JavaScript类似
        dependencies = self._parse_javascript(content, file_path)

        # 额外处理TypeScript特有的语法
        # import type { xxx } from 'yyy'
        type_import_pattern = r"import\s+type\s+.*?\s+from\s+['\"]([^'\"]+)['\"]"
        for match in re.finditer(type_import_pattern, content):
            dependencies.append(Dependency(
                source=file_path,
                target=match.group(1),
                dep_type=DependencyType.IMPORT,
                detail="type import",
            ))

        return dependencies

    def _parse_java(self, content: str, file_path: str) -> List[Dependency]:
        """解析Java文件的导入"""
        dependencies = []

        # import xxx.xxx.xxx;
        import_pattern = r"^\s*import\s+([\w.]+)\s*;"
        for match in re.finditer(import_pattern, content, re.MULTILINE):
            dependencies.append(Dependency(
                source=file_path,
                target=match.group(1),
                dep_type=DependencyType.IMPORT,
            ))

        # extends/implements
        extends_pattern = r"class\s+\w+\s+extends\s+(\w+)"
        for match in re.finditer(extends_pattern, content):
            dependencies.append(Dependency(
                source=file_path,
                target=match.group(1),
                dep_type=DependencyType.EXTENDS,
            ))

        implements_pattern = r"class\s+\w+.*?implements\s+([\w,\s]+)"
        for match in re.finditer(implements_pattern, content):
            interfaces = [i.strip() for i in match.group(1).split(",")]
            for iface in interfaces:
                dependencies.append(Dependency(
                    source=file_path,
                    target=iface,
                    dep_type=DependencyType.IMPLEMENTS,
                ))

        return dependencies

    def _parse_go(self, content: str, file_path: str) -> List[Dependency]:
        """解析Go文件的导入"""
        dependencies = []

        # import "xxx"
        single_import = r'import\s+"([^"]+)"'
        for match in re.finditer(single_import, content):
            dependencies.append(Dependency(
                source=file_path,
                target=match.group(1),
                dep_type=DependencyType.IMPORT,
            ))

        # import ( "xxx" "yyy" )
        multi_import = r'import\s+\((.*?)\)'
        for match in re.finditer(multi_import, content, re.DOTALL):
            imports_block = match.group(1)
            for imp in re.findall(r'"([^"]+)"', imports_block):
                dependencies.append(Dependency(
                    source=file_path,
                    target=imp,
                    dep_type=DependencyType.IMPORT,
                ))

        return dependencies

    def get_import_stats(self) -> Dict[str, int]:
        """获取导入统计"""
        stats = {}
        for dep in self.graph.edges:
            target = dep.target
            # 提取顶级模块名
            top_module = target.split(".")[0].split("/")[0]
            stats[top_module] = stats.get(top_module, 0) + 1

        return dict(sorted(stats.items(), key=lambda x: -x[1]))

    def find_circular_dependencies(self) -> List[List[str]]:
        """
        查找循环依赖

        Returns:
            循环依赖路径列表
        """
        cycles = []
        visited = set()
        rec_stack = set()

        def dfs(node: str, path: List[str]) -> None:
            visited.add(node)
            rec_stack.add(node)
            path.append(node)

            for dep in self.graph.outgoing.get(node, []):
                target = dep.target
                if target in self.graph.nodes:  # 只考虑项目内的文件
                    if target not in visited:
                        dfs(target, path.copy())
                    elif target in rec_stack:
                        # 找到循环
                        cycle_start = path.index(target)
                        cycle = path[cycle_start:] + [target]
                        cycles.append(cycle)

            rec_stack.remove(node)

        for node in self.graph.nodes:
            if node not in visited:
                dfs(node, [])

        return cycles
