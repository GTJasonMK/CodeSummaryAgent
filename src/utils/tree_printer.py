"""
目录树打印工具模块
提供美观的目录树结构打印功能
"""
from typing import Dict, List, Optional, Callable

from rich.console import Console
from rich.tree import Tree
from rich.text import Text
from rich.panel import Panel

from src.models.file_node import FileNode, NodeType, AnalysisStatus

console = Console()


# 状态图标映射
STATUS_ICONS = {
    AnalysisStatus.PENDING: "[dim][ ][/dim]",
    AnalysisStatus.IN_PROGRESS: "[yellow][~][/yellow]",
    AnalysisStatus.COMPLETED: "[green][+][/green]",
    AnalysisStatus.FAILED: "[red][x][/red]",
    AnalysisStatus.SKIPPED: "[dim][-][/dim]",
}

# 状态颜色映射
STATUS_COLORS = {
    AnalysisStatus.PENDING: "dim",
    AnalysisStatus.IN_PROGRESS: "yellow",
    AnalysisStatus.COMPLETED: "green",
    AnalysisStatus.FAILED: "red",
    AnalysisStatus.SKIPPED: "dim",
}


def print_tree(
    root: FileNode,
    show_status: bool = True,
    show_files: bool = True,
    max_depth: Optional[int] = None,
    title: Optional[str] = None,
) -> None:
    """
    打印目录树结构

    Args:
        root: 根节点
        show_status: 是否显示分析状态
        show_files: 是否显示文件（False则只显示目录）
        max_depth: 最大显示深度，None则显示全部
        title: 树的标题
    """
    tree_title = title or f"[bold blue]{root.name}[/bold blue]"
    tree = Tree(tree_title)

    _build_tree(tree, root, show_status, show_files, max_depth, 0)

    console.print(tree)


def _build_tree(
    tree: Tree,
    node: FileNode,
    show_status: bool,
    show_files: bool,
    max_depth: Optional[int],
    current_depth: int,
) -> None:
    """
    递归构建Rich树

    Args:
        tree: Rich Tree对象
        node: 当前节点
        show_status: 是否显示状态
        show_files: 是否显示文件
        max_depth: 最大深度
        current_depth: 当前深度
    """
    if max_depth is not None and current_depth > max_depth:
        return

    for child in node.children:
        # 如果不显示文件，跳过文件节点
        if not show_files and child.is_file:
            continue

        # 构建节点标签
        label = _format_node_label(child, show_status)

        if child.is_dir:
            # 目录节点，递归添加子树
            branch = tree.add(label)
            _build_tree(branch, child, show_status, show_files, max_depth, current_depth + 1)
        else:
            # 文件节点
            tree.add(label)


def _format_node_label(node: FileNode, show_status: bool) -> str:
    """
    格式化节点标签

    Args:
        node: 文件节点
        show_status: 是否显示状态

    Returns:
        格式化后的标签字符串
    """
    if node.is_dir:
        icon = "[bold blue]📁[/bold blue]"
        name = f"[bold]{node.name}[/bold]"
    else:
        icon = "📄"
        name = node.name

    if show_status:
        status_icon = STATUS_ICONS.get(node.status, "")
        color = STATUS_COLORS.get(node.status, "")
        return f"{status_icon} {icon} [{color}]{name}[/{color}]"
    else:
        return f"{icon} {name}"


def print_level_summary(
    root: FileNode,
    depth: int,
    completed: List[FileNode],
    failed: List[FileNode],
    total: int,
) -> None:
    """
    打印层级处理摘要

    Args:
        root: 根节点
        depth: 当前处理的层级深度
        completed: 已完成的节点列表
        failed: 失败的节点列表
        total: 总节点数
    """
    console.print()
    console.print(Panel(
        f"[bold]层级 {depth} 处理完成[/bold]",
        border_style="blue",
        expand=False,
    ))

    # 统计信息
    success_count = len(completed)
    failed_count = len(failed)

    stats = Text()
    stats.append("统计: ", style="bold")
    stats.append(f"成功 {success_count}", style="green")
    stats.append(" / ")
    stats.append(f"失败 {failed_count}", style="red" if failed_count > 0 else "dim")
    stats.append(" / ")
    stats.append(f"总计 {total}", style="bold")

    console.print(stats)

    # 如果有失败的，列出失败文件
    if failed:
        console.print()
        console.print("[red]失败文件:[/red]")
        for node in failed:
            error_msg = node.error_message or "未知错误"
            console.print(f"  [red]✗[/red] {node.relative_path}: {error_msg}")

    console.print()


def print_progress(
    current: int,
    total: int,
    current_file: str,
    status: str = "处理中",
) -> None:
    """
    打印进度信息

    Args:
        current: 当前进度
        total: 总数
        current_file: 当前处理的文件
        status: 状态文字
    """
    percentage = (current / total * 100) if total > 0 else 0
    console.print(
        f"[{current}/{total}] ({percentage:.1f}%) {status}: [cyan]{current_file}[/cyan]",
        end="\r"
    )


def print_final_summary(
    root: FileNode,
    total_files: int,
    completed_files: int,
    failed_files: int,
    elapsed_time: float,
) -> None:
    """
    打印最终分析摘要

    Args:
        root: 根节点
        total_files: 总文件数
        completed_files: 完成文件数
        failed_files: 失败文件数
        elapsed_time: 总耗时（秒）
    """
    console.print()
    console.print(Panel(
        "[bold green]分析完成[/bold green]",
        border_style="green",
        expand=False,
    ))

    console.print(f"[bold]项目:[/bold] {root.name}")
    console.print(f"[bold]总文件数:[/bold] {total_files}")
    console.print(f"[bold]成功:[/bold] [green]{completed_files}[/green]")
    console.print(f"[bold]失败:[/bold] [{'red' if failed_files > 0 else 'dim'}]{failed_files}[/]")
    console.print(f"[bold]耗时:[/bold] {elapsed_time:.2f} 秒")
    console.print()


def create_simple_tree_str(root: FileNode, show_status: bool = True) -> str:
    """
    创建简单的文本格式目录树（不使用Rich，用于日志输出）

    Args:
        root: 根节点
        show_status: 是否显示状态

    Returns:
        文本格式的目录树
    """
    lines = []

    def build(node: FileNode, prefix: str = "", is_last: bool = True) -> None:
        # 状态标记
        status_mark = ""
        if show_status:
            marks = {
                AnalysisStatus.PENDING: "[ ]",
                AnalysisStatus.IN_PROGRESS: "[~]",
                AnalysisStatus.COMPLETED: "[+]",
                AnalysisStatus.FAILED: "[x]",
                AnalysisStatus.SKIPPED: "[-]",
            }
            status_mark = marks.get(node.status, "") + " "

        # 连接符
        connector = "└── " if is_last else "├── "

        # 节点名称
        name = node.name + ("/" if node.is_dir else "")

        lines.append(f"{prefix}{connector}{status_mark}{name}")

        # 子节点前缀
        child_prefix = prefix + ("    " if is_last else "│   ")

        # 递归处理子节点
        children = node.children
        for i, child in enumerate(children):
            build(child, child_prefix, i == len(children) - 1)

    # 根节点
    lines.append(root.name + "/")
    for i, child in enumerate(root.children):
        build(child, "", i == len(root.children) - 1)

    return "\n".join(lines)
