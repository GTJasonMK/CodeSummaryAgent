"""
进度管理器模块
使用Rich Progress提供美观的实时进度显示
"""
import asyncio
from typing import Optional, Dict, Set
from dataclasses import dataclass, field
from contextlib import asynccontextmanager
import time

from rich.console import Console
from rich.progress import (
    Progress,
    TextColumn,
    BarColumn,
    TaskProgressColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
    SpinnerColumn,
    MofNCompleteColumn,
)
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.layout import Layout
from rich.console import Group

from src.models.file_node import FileNode, AnalysisStatus
from src.utils.logger import get_logger

logger = get_logger(__name__)

console = Console()


@dataclass
class TaskInfo:
    """并发任务信息"""
    node: FileNode
    status: str = "等待中"
    start_time: float = field(default_factory=time.time)


class ProgressManager:
    """
    进度管理器

    提供美观的实时进度显示：
    - 总体进度条（所有文件和目录）
    - 当前层级进度
    - 并发任务状态显示
    - 处理速率和预计剩余时间
    """

    def __init__(
        self,
        total_files: int,
        total_dirs: int,
        max_depth: int,
    ):
        """
        初始化进度管理器

        Args:
            total_files: 总文件数
            total_dirs: 总目录数
            max_depth: 最大层级深度
        """
        self.total_files = total_files
        self.total_dirs = total_dirs
        self.max_depth = max_depth
        self.total_items = total_files + total_dirs

        # 进度统计
        self.completed_files = 0
        self.completed_dirs = 0
        self.failed_count = 0
        self.current_depth = max_depth

        # 并发任务追踪
        self._active_tasks: Dict[str, TaskInfo] = {}
        self._lock = asyncio.Lock()

        # Rich Progress 组件
        self._progress: Optional[Progress] = None
        self._live: Optional[Live] = None

        # 任务ID
        self._overall_task_id = None
        self._level_task_id = None
        self._current_level_total = 0
        self._current_level_completed = 0

        # 启动时间
        self._start_time = time.time()

    def _create_progress(self) -> Progress:
        """创建Progress组件"""
        return Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(bar_width=40),
            TaskProgressColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            console=console,
            expand=False,
        )

    def _build_display(self) -> Group:
        """构建显示内容"""
        elements = []

        # 1. 进度条
        elements.append(self._progress)

        # 2. 当前并发任务表格
        if self._active_tasks:
            table = Table(
                title="[bold cyan]并发任务[/bold cyan]",
                show_header=True,
                header_style="bold magenta",
                border_style="dim",
                expand=False,
                padding=(0, 1),
            )
            table.add_column("状态", width=8, justify="center")
            table.add_column("文件", width=50, no_wrap=True, overflow="ellipsis")
            table.add_column("耗时", width=8, justify="right")

            for path, task_info in list(self._active_tasks.items()):
                elapsed = time.time() - task_info.start_time
                status_icon = self._get_status_icon(task_info.status)
                # 截断过长的路径
                display_path = task_info.node.relative_path
                if len(display_path) > 48:
                    display_path = "..." + display_path[-45:]
                table.add_row(
                    status_icon,
                    display_path,
                    f"{elapsed:.1f}s"
                )

            elements.append(table)

        # 3. 统计信息
        stats = Text()
        stats.append("\n")
        stats.append("统计: ", style="bold")
        stats.append(f"文件 {self.completed_files}/{self.total_files}", style="green")
        stats.append(" | ")
        stats.append(f"目录 {self.completed_dirs}/{self.total_dirs}", style="blue")
        if self.failed_count > 0:
            stats.append(" | ")
            stats.append(f"失败 {self.failed_count}", style="red")

        elements.append(stats)

        return Group(*elements)

    def _get_status_icon(self, status: str) -> str:
        """获取状态图标"""
        if "分析" in status or "处理" in status:
            return "[yellow]⚡[/yellow]"
        elif "保存" in status:
            return "[cyan]💾[/cyan]"
        elif "完成" in status:
            return "[green]✓[/green]"
        elif "失败" in status or "错误" in status:
            return "[red]✗[/red]"
        elif "读取" in status:
            return "[blue]📖[/blue]"
        else:
            return "[dim]○[/dim]"

    @asynccontextmanager
    async def live_progress(self):
        """
        进度显示上下文管理器

        使用示例:
            async with progress_manager.live_progress():
                await process_files()
        """
        self._progress = self._create_progress()
        self._start_time = time.time()

        # 创建总体进度任务
        self._overall_task_id = self._progress.add_task(
            "[cyan]总体进度",
            total=self.total_items,
            completed=0,
        )

        # 创建层级进度任务
        self._level_task_id = self._progress.add_task(
            f"[yellow]层级 {self.current_depth}",
            total=0,
            completed=0,
            visible=False,
        )

        with Live(
            self._build_display(),
            console=console,
            refresh_per_second=4,
            transient=False,
        ) as live:
            self._live = live
            try:
                yield self
            finally:
                self._live = None
                self._progress = None

    def start_level(self, depth: int, total_nodes: int) -> None:
        """
        开始处理新层级

        Args:
            depth: 层级深度
            total_nodes: 该层级的节点总数
        """
        self.current_depth = depth
        self._current_level_total = total_nodes
        self._current_level_completed = 0

        if self._progress and self._level_task_id is not None:
            self._progress.update(
                self._level_task_id,
                description=f"[yellow]层级 {depth}",
                total=total_nodes,
                completed=0,
                visible=True,
            )
            self._refresh()

    def complete_level(self, depth: int) -> None:
        """
        完成层级处理

        Args:
            depth: 层级深度
        """
        if self._progress and self._level_task_id is not None:
            self._progress.update(
                self._level_task_id,
                visible=False,
            )
            self._refresh()

    async def start_task(self, node: FileNode, status: str = "等待中") -> None:
        """
        开始处理任务

        Args:
            node: 文件节点
            status: 初始状态
        """
        async with self._lock:
            self._active_tasks[node.path] = TaskInfo(
                node=node,
                status=status,
                start_time=time.time(),
            )
        self._refresh()

    async def update_task(self, node: FileNode, status: str) -> None:
        """
        更新任务状态

        Args:
            node: 文件节点
            status: 新状态
        """
        async with self._lock:
            if node.path in self._active_tasks:
                self._active_tasks[node.path].status = status
        self._refresh()

    async def complete_task(self, node: FileNode, success: bool) -> None:
        """
        完成任务

        Args:
            node: 文件节点
            success: 是否成功
        """
        async with self._lock:
            # 移除活动任务
            self._active_tasks.pop(node.path, None)

            # 更新统计
            if success:
                if node.is_file:
                    self.completed_files += 1
                else:
                    self.completed_dirs += 1
            else:
                self.failed_count += 1

            self._current_level_completed += 1

        # 更新进度条
        if self._progress:
            completed = self.completed_files + self.completed_dirs
            self._progress.update(
                self._overall_task_id,
                completed=completed,
            )
            self._progress.update(
                self._level_task_id,
                completed=self._current_level_completed,
            )

        self._refresh()

    def _refresh(self) -> None:
        """刷新显示"""
        if self._live:
            self._live.update(self._build_display())

    def print_level_summary(
        self,
        depth: int,
        completed: int,
        failed: int,
        total: int,
    ) -> None:
        """
        打印层级处理摘要

        Args:
            depth: 层级深度
            completed: 完成数
            failed: 失败数
            total: 总数
        """
        console.print()
        console.print(Panel(
            f"[bold]层级 {depth} 处理完成[/bold]  "
            f"[green]成功 {completed}[/green] / "
            f"[{'red' if failed > 0 else 'dim'}]失败 {failed}[/] / "
            f"总计 {total}",
            border_style="blue" if failed == 0 else "yellow",
            expand=False,
        ))

    def print_final_summary(self, elapsed_time: float) -> None:
        """
        打印最终摘要

        Args:
            elapsed_time: 总耗时
        """
        total_completed = self.completed_files + self.completed_dirs

        console.print()
        console.print(Panel(
            "[bold green]分析完成[/bold green]",
            border_style="green",
            expand=False,
        ))

        # 统计表格
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column("项目", style="bold")
        table.add_column("值")

        table.add_row("总文件数", f"[cyan]{self.total_files}[/cyan]")
        table.add_row("总目录数", f"[cyan]{self.total_dirs}[/cyan]")
        table.add_row("成功处理", f"[green]{total_completed}[/green]")
        if self.failed_count > 0:
            table.add_row("处理失败", f"[red]{self.failed_count}[/red]")
        table.add_row("总耗时", f"[yellow]{elapsed_time:.2f}秒[/yellow]")

        # 计算速率
        if elapsed_time > 0:
            rate = total_completed / elapsed_time
            table.add_row("平均速率", f"[blue]{rate:.2f} 项/秒[/blue]")

        console.print(table)
        console.print()


class SimpleProgressPrinter:
    """
    简单进度打印器

    用于不需要复杂进度显示的场景，或作为后备方案。
    """

    def __init__(self, total_files: int, total_dirs: int):
        self.total_files = total_files
        self.total_dirs = total_dirs
        self.completed_files = 0
        self.completed_dirs = 0
        self.failed_count = 0

    def print_progress(
        self,
        current: int,
        total: int,
        current_file: str,
        status: str = "处理中",
    ) -> None:
        """打印进度信息"""
        percentage = (current / total * 100) if total > 0 else 0
        console.print(
            f"[{current}/{total}] ({percentage:.1f}%) {status}: [cyan]{current_file}[/cyan]"
        )

    def update_completed(self, is_file: bool, success: bool) -> None:
        """更新完成计数"""
        if success:
            if is_file:
                self.completed_files += 1
            else:
                self.completed_dirs += 1
        else:
            self.failed_count += 1
