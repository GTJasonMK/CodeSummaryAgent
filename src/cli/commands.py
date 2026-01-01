"""
CLI命令模块
使用Typer实现命令行界面
"""
import asyncio
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel

from src.core.analyzer import CodeAnalyzer, analyze_codebase
from src.models.config import AppConfig, load_config, get_config
from src.services.directory_scanner import DirectoryScanner
from src.utils.tree_printer import print_tree
from src.utils.logger import setup_logger

# 创建Typer应用
app = typer.Typer(
    name="code-summary",
    help="CodeSummaryAgent - 基于LLM的代码库分析工具",
    add_completion=False,
)

console = Console()


@app.command()
def analyze(
    source: str = typer.Argument(
        ...,
        help="源代码目录路径",
    ),
    config: Optional[str] = typer.Option(
        None,
        "--config", "-c",
        help="配置文件路径",
    ),
    output: Optional[str] = typer.Option(
        None,
        "--output", "-o",
        help="文档输出目录路径",
    ),
    no_resume: bool = typer.Option(
        False,
        "--no-resume",
        help="禁用断点续传，重新开始分析",
    ),
    log_level: str = typer.Option(
        "INFO",
        "--log-level", "-l",
        help="日志级别 (DEBUG/INFO/WARNING/ERROR)",
    ),
    concurrent: Optional[int] = typer.Option(
        None,
        "--concurrent", "-n",
        help="LLM最大并发数",
    ),
) -> None:
    """
    分析代码库，生成文档

    示例：
        code-summary analyze ./my-project
        code-summary analyze ./my-project -c config.yaml
        code-summary analyze ./my-project -o ./my-project-docs
    """
    # 配置日志
    setup_logger(log_level=log_level)

    # 验证路径
    source_path = Path(source).resolve()
    if not source_path.exists():
        console.print(f"[red]错误: 目录不存在: {source}[/red]")
        raise typer.Exit(1)

    if not source_path.is_dir():
        console.print(f"[red]错误: 路径不是目录: {source}[/red]")
        raise typer.Exit(1)

    # 加载配置
    if config:
        config_path = Path(config).resolve()
        if not config_path.exists():
            console.print(f"[red]错误: 配置文件不存在: {config}[/red]")
            raise typer.Exit(1)
        app_config = load_config(str(config_path))
    else:
        # 自动查找配置文件
        config_candidates = [
            Path.cwd() / "config.yaml",                    # 当前目录
            Path(__file__).parent.parent.parent / "config.yaml",  # 项目根目录
            Path.home() / ".code-summary" / "config.yaml",  # 用户主目录
        ]

        config_found = None
        for candidate in config_candidates:
            if candidate.exists():
                config_found = candidate
                break

        if config_found:
            console.print(f"[dim]使用配置文件: {config_found}[/dim]")
            app_config = load_config(str(config_found))
        else:
            app_config = get_config()

    # 覆盖并发数配置
    if concurrent:
        app_config.llm.max_concurrent = concurrent

    # 显示启动信息
    console.print(Panel(
        "[bold blue]CodeSummaryAgent[/bold blue]\n"
        "基于LLM的代码库分析工具",
        expand=False,
    ))
    console.print(f"[bold]源代码目录:[/bold] {source_path}")
    console.print(f"[bold]LLM提供商:[/bold] {app_config.llm.provider}")
    console.print(f"[bold]模型:[/bold] {app_config.llm.model}")
    console.print(f"[bold]最大并发:[/bold] {app_config.llm.max_concurrent}")
    console.print()

    # 创建分析器
    try:
        analyzer = CodeAnalyzer(
            source_path=str(source_path),
            config_path=config,
            docs_path=output,
        )
    except Exception as e:
        console.print(f"[red]初始化失败: {e}[/red]")
        raise typer.Exit(1)

    # 执行分析
    try:
        success = asyncio.run(analyzer.analyze(resume=not no_resume))

        if success:
            console.print()
            console.print(f"[green]分析完成！文档已保存到: {analyzer.docs_root}[/green]")
            raise typer.Exit(0)
        else:
            console.print()
            console.print("[yellow]分析过程中有文件处理失败，请检查日志[/yellow]")
            raise typer.Exit(1)

    except KeyboardInterrupt:
        console.print()
        console.print("[yellow]用户中断，进度已保存[/yellow]")
        raise typer.Exit(130)
    except Exception as e:
        console.print(f"[red]分析失败: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def scan(
    source: str = typer.Argument(
        ...,
        help="源代码目录路径",
    ),
    config: Optional[str] = typer.Option(
        None,
        "--config", "-c",
        help="配置文件路径",
    ),
) -> None:
    """
    扫描目录结构（不进行分析）

    用于预览将要分析的文件

    示例：
        code-summary scan ./my-project
    """
    # 验证路径
    source_path = Path(source).resolve()
    if not source_path.exists():
        console.print(f"[red]错误: 目录不存在: {source}[/red]")
        raise typer.Exit(1)

    # 加载配置
    if config:
        load_config(config)
    app_config = get_config()

    # 扫描目录
    scanner = DirectoryScanner(app_config.analysis)

    try:
        root = scanner.scan(str(source_path))
    except Exception as e:
        console.print(f"[red]扫描失败: {e}[/red]")
        raise typer.Exit(1)

    # 打印目录树
    print_tree(root, show_status=False)

    # 打印统计
    all_files = root.get_all_files()
    all_dirs = root.get_all_dirs()

    console.print()
    console.print(f"[bold]统计:[/bold]")
    console.print(f"  文件数: {len(all_files)}")
    console.print(f"  目录数: {len(all_dirs)}")

    # 按扩展名统计
    ext_count = {}
    for f in all_files:
        ext = f.extension or "(无扩展名)"
        ext_count[ext] = ext_count.get(ext, 0) + 1

    console.print(f"\n[bold]文件类型分布:[/bold]")
    for ext, count in sorted(ext_count.items(), key=lambda x: -x[1]):
        console.print(f"  {ext}: {count}")


@app.command()
def init_config(
    output: str = typer.Option(
        "./config.yaml",
        "--output", "-o",
        help="配置文件输出路径",
    ),
) -> None:
    """
    生成默认配置文件

    示例：
        code-summary init-config
        code-summary init-config -o my-config.yaml
    """
    output_path = Path(output).resolve()

    if output_path.exists():
        overwrite = typer.confirm(f"文件 {output_path} 已存在，是否覆盖？")
        if not overwrite:
            console.print("[yellow]已取消[/yellow]")
            raise typer.Exit(0)

    # 生成默认配置
    config = AppConfig()
    config.to_yaml(str(output_path))

    console.print(f"[green]配置文件已生成: {output_path}[/green]")
    console.print("\n请编辑配置文件，设置你的LLM API密钥和其他选项")


@app.command()
def status(
    docs_path: str = typer.Argument(
        ...,
        help="文档目录路径",
    ),
) -> None:
    """
    查看分析状态

    示例：
        code-summary status ./my-project_docs
    """
    from src.services.checkpoint import CheckpointService

    docs_dir = Path(docs_path).resolve()
    if not docs_dir.exists():
        console.print(f"[red]错误: 目录不存在: {docs_path}[/red]")
        raise typer.Exit(1)

    # 尝试加载断点文件
    checkpoint_file = docs_dir / ".checkpoint.json"
    if not checkpoint_file.exists():
        console.print("[yellow]未找到断点文件，无法显示状态[/yellow]")
        raise typer.Exit(1)

    import json
    with open(checkpoint_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    console.print(Panel(
        "[bold]分析状态[/bold]",
        expand=False,
    ))

    console.print(f"[bold]源代码目录:[/bold] {data.get('source_root', '未知')}")
    console.print(f"[bold]文档目录:[/bold] {data.get('docs_root', '未知')}")
    console.print(f"[bold]已完成文件:[/bold] {len(data.get('completed_files', []))}")
    console.print(f"[bold]已完成目录:[/bold] {len(data.get('completed_dirs', []))}")
    console.print(f"[bold]失败文件:[/bold] {len(data.get('failed_files', []))}")

    # 显示失败文件
    failed = data.get("failed_files", [])
    if failed:
        console.print("\n[red]失败的文件:[/red]")
        for f in failed[:10]:  # 最多显示10个
            console.print(f"  - {f}")
        if len(failed) > 10:
            console.print(f"  ... 还有 {len(failed) - 10} 个")


@app.command()
def server(
    host: str = typer.Option(
        "127.0.0.1",
        "--host", "-h",
        help="服务器监听地址",
    ),
    port: int = typer.Option(
        8000,
        "--port", "-p",
        help="服务器监听端口",
    ),
    reload: bool = typer.Option(
        False,
        "--reload",
        help="启用热重载（开发模式）",
    ),
) -> None:
    """
    启动Web服务器

    提供Web界面和API服务

    示例：
        code-summary server
        code-summary server -h 0.0.0.0 -p 8080
        code-summary server --reload
    """
    import uvicorn

    console.print(Panel(
        "[bold blue]CodeSummaryAgent Web Server[/bold blue]\n"
        f"服务地址: http://{host}:{port}",
        expand=False,
    ))

    console.print(f"\n[dim]API文档: http://{host}:{port}/docs[/dim]")
    console.print(f"[dim]按 Ctrl+C 停止服务器[/dim]\n")

    try:
        uvicorn.run(
            "src.api.server:app",
            host=host,
            port=port,
            reload=reload,
        )
    except KeyboardInterrupt:
        console.print("\n[yellow]服务器已停止[/yellow]")


@app.command()
def deps(
    source: str = typer.Argument(
        ...,
        help="源代码目录路径",
    ),
    output: Optional[str] = typer.Option(
        None,
        "--output", "-o",
        help="输出文件路径（JSON格式）",
    ),
    mermaid: bool = typer.Option(
        False,
        "--mermaid", "-m",
        help="输出Mermaid图形代码",
    ),
) -> None:
    """
    分析代码依赖关系

    示例：
        code-summary deps ./my-project
        code-summary deps ./my-project -o deps.json
        code-summary deps ./my-project --mermaid
    """
    import json
    from src.services.directory_scanner import DirectoryScanner
    from src.services.dependency import DependencyAnalyzer

    # 验证路径
    source_path = Path(source).resolve()
    if not source_path.exists():
        console.print(f"[red]错误: 目录不存在: {source}[/red]")
        raise typer.Exit(1)

    # 扫描目录
    scanner = DirectoryScanner()
    root = scanner.scan(str(source_path))

    # 分析依赖
    console.print("[bold]分析依赖关系...[/bold]")
    analyzer = DependencyAnalyzer(str(source_path))
    graph = analyzer.analyze(root)

    # 输出结果
    if mermaid:
        console.print("\n[bold]Mermaid图形代码:[/bold]")
        console.print(graph.to_mermaid())
    elif output:
        output_path = Path(output).resolve()
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(graph.to_dict(), f, ensure_ascii=False, indent=2)
        console.print(f"[green]依赖关系已保存到: {output_path}[/green]")
    else:
        # 打印统计
        console.print(f"\n[bold]依赖统计:[/bold]")
        console.print(f"  节点数: {len(graph.nodes)}")
        console.print(f"  依赖数: {len(graph.edges)}")

        # 导入统计
        stats = analyzer.get_import_stats()
        console.print(f"\n[bold]最常用的依赖 (Top 10):[/bold]")
        for module, count in list(stats.items())[:10]:
            console.print(f"  {module}: {count}")

        # 检测循环依赖
        cycles = analyzer.find_circular_dependencies()
        if cycles:
            console.print(f"\n[yellow]检测到 {len(cycles)} 个循环依赖:[/yellow]")
            for cycle in cycles[:5]:
                console.print(f"  {' -> '.join(cycle)}")


@app.command()
def version() -> None:
    """显示版本信息"""
    from src import __version__
    console.print(f"CodeSummaryAgent v{__version__}")


def main() -> None:
    """CLI入口函数"""
    app()


if __name__ == "__main__":
    main()
