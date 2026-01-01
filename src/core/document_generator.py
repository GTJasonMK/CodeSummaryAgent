"""
文档生成器模块
负责生成和保存分析文档
"""
import aiofiles
from pathlib import Path
from typing import Optional, List
from datetime import datetime

from src.models.file_node import FileNode
from src.models.config import OutputConfig, get_config
from src.utils.logger import get_logger

logger = get_logger(__name__)


class DocumentGenerator:
    """
    文档生成器

    负责：
    - 生成规范的文档文件名
    - 保存分析结果到文件
    - 格式化文档内容

    文档目录结构与源代码目录结构保持一致：
    原项目:
        project/
        ├── src/
        │   ├── utils/
        │   │   └── helper.py
        │   └── main.py
        └── app.py

    生成的文档:
        project_docs/
        ├── src/
        │   ├── utils/
        │   │   ├── helper.py.md          # helper.py 的文档
        │   │   └── _dir_summary.md       # utils 目录的汇总文档
        │   ├── main.py.md                # main.py 的文档
        │   └── _dir_summary.md           # src 目录的汇总文档
        ├── app.py.md                     # app.py 的文档
        └── README.md                     # 项目README
    """

    def __init__(
        self,
        docs_root: str,
        config: Optional[OutputConfig] = None,
    ):
        """
        初始化文档生成器

        Args:
            docs_root: 文档根目录
            config: 输出配置
        """
        self.docs_root = Path(docs_root)
        self.config = config or get_config().output

        # 确保文档目录存在
        self.docs_root.mkdir(parents=True, exist_ok=True)

    def get_file_doc_path(self, node: FileNode) -> str:
        """
        生成代码文件的文档路径（保持目录结构）

        例如：
        - src/utils/helper.py -> docs_root/src/utils/helper.py.md
        - main.js -> docs_root/main.js.md

        Args:
            node: 文件节点

        Returns:
            完整的文档路径
        """
        # 保持原有目录结构，文件名加上 .md 后缀
        relative_path = node.relative_path
        doc_name = f"{node.name}.md"

        # 获取父目录路径
        parent_path = Path(relative_path).parent
        if parent_path == Path("."):
            # 文件在根目录
            return str(self.docs_root / doc_name)
        else:
            return str(self.docs_root / parent_path / doc_name)

    def get_dir_doc_path(self, node: FileNode) -> str:
        """
        生成目录的汇总文档路径

        例如：
        - src/utils -> docs_root/src/utils/_dir_summary.md
        - src -> docs_root/src/_dir_summary.md
        - (root) -> docs_root/_dir_summary.md

        Args:
            node: 目录节点

        Returns:
            完整的文档路径
        """
        dir_summary_name = self.config.dir_summary_name
        if not node.relative_path:
            # 根目录的汇总文档
            return str(self.docs_root / dir_summary_name)
        else:
            return str(self.docs_root / node.relative_path / dir_summary_name)

    def get_doc_path(self, node: FileNode) -> str:
        """
        获取节点的完整文档路径

        Args:
            node: 文件/目录节点

        Returns:
            完整的文档路径
        """
        if node.is_file:
            return self.get_file_doc_path(node)
        else:
            return self.get_dir_doc_path(node)

    async def save_file_summary(
        self,
        node: FileNode,
        summary: str,
    ) -> str:
        """
        保存代码文件的分析总结

        Args:
            node: 文件节点
            summary: 分析总结文本

        Returns:
            保存的文档路径
        """
        doc_path = self.get_doc_path(node)

        # 添加文档头部信息
        content = self._format_file_doc(node, summary)

        await self._save_document(doc_path, content)
        logger.debug(f"已保存文件总结: {doc_path}")

        return doc_path

    async def save_dir_summary(
        self,
        node: FileNode,
        summary: str,
    ) -> str:
        """
        保存目录的分析总结

        Args:
            node: 目录节点
            summary: 分析总结文本

        Returns:
            保存的文档路径
        """
        doc_path = self.get_doc_path(node)

        # 添加文档头部信息
        content = self._format_dir_doc(node, summary)

        await self._save_document(doc_path, content)
        logger.debug(f"已保存目录总结: {doc_path}")

        return doc_path

    async def save_readme(
        self,
        project_name: str,
        summary: str,
    ) -> str:
        """
        保存最终的README文档

        Args:
            project_name: 项目名称
            summary: README内容

        Returns:
            保存的文档路径
        """
        doc_path = str(self.docs_root / self.config.readme_name)

        # 添加生成信息
        content = self._format_readme(project_name, summary)

        await self._save_document(doc_path, content)
        logger.info(f"已保存README: {doc_path}")

        return doc_path

    async def save_reading_guide(
        self,
        project_name: str,
        content: str,
    ) -> str:
        """
        保存项目文档阅读顺序指南

        Args:
            project_name: 项目名称
            content: 阅读指南内容

        Returns:
            保存的文档路径
        """
        doc_path = str(self.docs_root / self.config.reading_guide_name)

        # 添加生成信息
        formatted_content = self._format_reading_guide(project_name, content)

        await self._save_document(doc_path, formatted_content)
        logger.info(f"已保存阅读指南: {doc_path}")

        return doc_path

    async def read_document(self, doc_path: str) -> str:
        """
        读取文档内容

        Args:
            doc_path: 文档路径

        Returns:
            文档内容
        """
        async with aiofiles.open(doc_path, "r", encoding="utf-8") as f:
            return await f.read()

    async def read_child_summaries(self, node: FileNode) -> str:
        """
        读取子节点的所有总结文档并合并

        Args:
            node: 目录节点

        Returns:
            合并后的子节点总结文本
        """
        summaries = []

        for child in node.children:
            if child.doc_path:
                try:
                    content = await self.read_document(child.doc_path)
                    # 添加分隔符
                    summaries.append(f"### {child.name}\n\n{content}")
                except Exception as e:
                    logger.warning(f"读取子节点文档失败: {child.doc_path}, {e}")

        return "\n\n---\n\n".join(summaries)

    async def _save_document(self, doc_path: str, content: str) -> None:
        """
        保存文档到文件

        Args:
            doc_path: 文档路径
            content: 文档内容
        """
        path = Path(doc_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        async with aiofiles.open(doc_path, "w", encoding="utf-8") as f:
            await f.write(content)

    def _format_file_doc(self, node: FileNode, summary: str) -> str:
        """格式化文件文档"""
        header = f"""# 文件分析: {node.name}

**源文件**: `{node.relative_path}`
**生成时间**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

---

"""
        return header + summary

    def _format_dir_doc(self, node: FileNode, summary: str) -> str:
        """格式化目录文档"""
        path_display = node.relative_path if node.relative_path else node.name
        header = f"""# 目录分析: {node.name}

**目录路径**: `{path_display}`
**子文件数**: {node.file_count}
**子目录数**: {node.dir_count}
**生成时间**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

---

"""
        return header + summary

    def _format_readme(self, project_name: str, summary: str) -> str:
        """格式化README文档"""
        footer = f"""

---

*本文档由 CodeSummaryAgent 自动生成*
*生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}*
"""
        return summary + footer

    def _format_reading_guide(self, project_name: str, content: str) -> str:
        """格式化阅读顺序指南文档"""
        header = f"""# {project_name} - 文档阅读顺序指南

> 本指南帮助你按照合理的顺序阅读项目文档，快速理解项目结构和核心逻辑。

---

"""
        footer = f"""

---

*本文档由 CodeSummaryAgent 自动生成*
*生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}*
"""
        return header + content + footer


def get_display_name(node: FileNode) -> str:
    """
    获取用于显示的节点名称

    让人能一眼猜出代码文件可能的功能

    Args:
        node: 文件节点

    Returns:
        显示名称
    """
    name = node.name

    # 对于常见的文件名进行语义化处理
    name_mapping = {
        "__init__.py": "[包初始化]",
        "setup.py": "[项目安装配置]",
        "main.py": "[程序入口]",
        "app.py": "[应用入口]",
        "index.js": "[模块入口]",
        "index.ts": "[模块入口]",
        "config": "[配置]",
        "utils": "[工具函数]",
        "helpers": "[辅助函数]",
        "constants": "[常量定义]",
        "types": "[类型定义]",
        "models": "[数据模型]",
        "services": "[服务层]",
        "controllers": "[控制器]",
        "routes": "[路由]",
        "middleware": "[中间件]",
        "tests": "[测试]",
    }

    # 检查完整文件名
    if name in name_mapping:
        return f"{name} {name_mapping[name]}"

    # 检查文件名前缀/后缀
    base_name = Path(name).stem.lower()
    for key, value in name_mapping.items():
        if base_name == key or base_name.endswith(f"_{key}") or base_name.startswith(f"{key}_"):
            return f"{name} {value}"

    return name
