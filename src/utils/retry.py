"""
重试机制模块
提供带指数退避的重试功能
"""
import asyncio
from functools import wraps
from typing import TypeVar, Callable, Any, Optional, Type, Tuple, Union
import random

from src.utils.logger import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


class RetryExhaustedError(Exception):
    """重试次数耗尽异常"""

    def __init__(
        self,
        message: str,
        last_exception: Optional[Exception] = None,
        attempts: int = 0
    ):
        super().__init__(message)
        self.last_exception = last_exception
        self.attempts = attempts


class RetryHandler:
    """
    重试处理器

    支持指数退避、最大重试次数、可配置的异常类型
    """

    def __init__(
        self,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        exponential_base: float = 2.0,
        jitter: bool = True,
        retry_exceptions: Optional[Tuple[Type[Exception], ...]] = None,
    ):
        """
        初始化重试处理器

        Args:
            max_retries: 最大重试次数
            base_delay: 基础延迟时间（秒）
            max_delay: 最大延迟时间（秒）
            exponential_base: 指数增长基数
            jitter: 是否添加随机抖动
            retry_exceptions: 需要重试的异常类型，None表示所有异常
        """
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base
        self.jitter = jitter
        self.retry_exceptions = retry_exceptions or (Exception,)

    def _calculate_delay(self, attempt: int) -> float:
        """
        计算延迟时间

        Args:
            attempt: 当前尝试次数（从0开始）

        Returns:
            延迟时间（秒）
        """
        delay = self.base_delay * (self.exponential_base ** attempt)
        delay = min(delay, self.max_delay)

        if self.jitter:
            # 添加 0-25% 的随机抖动
            jitter_amount = delay * 0.25 * random.random()
            delay += jitter_amount

        return delay

    async def execute(
        self,
        func: Callable[..., T],
        *args: Any,
        **kwargs: Any
    ) -> T:
        """
        执行函数，失败时自动重试

        Args:
            func: 要执行的异步函数
            *args: 函数参数
            **kwargs: 函数关键字参数

        Returns:
            函数返回值

        Raises:
            RetryExhaustedError: 重试次数耗尽时抛出
        """
        last_exception: Optional[Exception] = None

        for attempt in range(self.max_retries + 1):
            try:
                if asyncio.iscoroutinefunction(func):
                    return await func(*args, **kwargs)
                else:
                    return func(*args, **kwargs)

            except self.retry_exceptions as e:
                last_exception = e
                remaining = self.max_retries - attempt

                if remaining > 0:
                    delay = self._calculate_delay(attempt)
                    logger.warning(
                        f"执行失败 (尝试 {attempt + 1}/{self.max_retries + 1}): {e}. "
                        f"{delay:.2f}秒后重试..."
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        f"执行失败，已达最大重试次数 ({self.max_retries + 1}次): {e}"
                    )

        raise RetryExhaustedError(
            f"重试 {self.max_retries + 1} 次后仍然失败",
            last_exception=last_exception,
            attempts=self.max_retries + 1
        )

    def __call__(self, func: Callable[..., T]) -> Callable[..., T]:
        """
        装饰器用法

        Example:
            @RetryHandler(max_retries=3)
            async def my_func():
                ...
        """
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            return await self.execute(func, *args, **kwargs)
        return wrapper


def with_retry(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    exponential_base: float = 2.0,
    jitter: bool = True,
    retry_exceptions: Optional[Tuple[Type[Exception], ...]] = None,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    重试装饰器工厂函数

    Example:
        @with_retry(max_retries=3, base_delay=1.0)
        async def call_api():
            ...

    Args:
        max_retries: 最大重试次数
        base_delay: 基础延迟时间
        max_delay: 最大延迟时间
        exponential_base: 指数增长基数
        jitter: 是否添加随机抖动
        retry_exceptions: 需要重试的异常类型

    Returns:
        装饰器函数
    """
    handler = RetryHandler(
        max_retries=max_retries,
        base_delay=base_delay,
        max_delay=max_delay,
        exponential_base=exponential_base,
        jitter=jitter,
        retry_exceptions=retry_exceptions,
    )
    return handler


async def retry_async(
    func: Callable[..., T],
    *args: Any,
    max_retries: int = 3,
    base_delay: float = 1.0,
    **kwargs: Any
) -> T:
    """
    简便的异步重试函数

    Example:
        result = await retry_async(call_api, arg1, arg2, max_retries=3)

    Args:
        func: 要执行的异步函数
        *args: 函数参数
        max_retries: 最大重试次数
        base_delay: 基础延迟时间
        **kwargs: 函数关键字参数

    Returns:
        函数返回值
    """
    handler = RetryHandler(max_retries=max_retries, base_delay=base_delay)
    return await handler.execute(func, *args, **kwargs)
