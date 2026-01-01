"""
日志工具模块
基于loguru实现统一的日志管理
"""
import sys
from pathlib import Path
from typing import Optional

from loguru import logger


# 移除默认的handler
logger.remove()

# 日志格式
LOG_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
    "<level>{message}</level>"
)

# 简洁格式（用于控制台）
CONSOLE_FORMAT = (
    "<green>{time:HH:mm:ss}</green> | "
    "<level>{level: <8}</level> | "
    "<level>{message}</level>"
)


def setup_logger(
    log_level: str = "INFO",
    log_file: Optional[str] = None,
    rotation: str = "10 MB",
    retention: str = "7 days",
) -> None:
    """
    配置日志系统

    Args:
        log_level: 日志级别 (DEBUG/INFO/WARNING/ERROR/CRITICAL)
        log_file: 日志文件路径，None则不输出到文件
        rotation: 日志轮转大小
        retention: 日志保留时间
    """
    # 控制台输出
    logger.add(
        sys.stderr,
        format=CONSOLE_FORMAT,
        level=log_level,
        colorize=True,
    )

    # 文件输出
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        logger.add(
            log_file,
            format=LOG_FORMAT,
            level=log_level,
            rotation=rotation,
            retention=retention,
            encoding="utf-8",
        )


def get_logger(name: str = "CodeSummaryAgent"):
    """
    获取带有模块名的logger实例

    Args:
        name: 模块名称

    Returns:
        绑定了模块名的logger实例
    """
    return logger.bind(name=name)


# 导出logger供直接使用
__all__ = ["logger", "setup_logger", "get_logger"]
