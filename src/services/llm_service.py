# -*- coding: utf-8 -*-
"""
LLM服务模块 - 完全仿照 E:/code/AFN/backend/app/utils/llm_tool.py 实现

提供统一的LLM调用接口，支持：
1. OpenAI Chat Completions API格式（GPT、通义千问、DeepSeek等）
2. Anthropic Messages API格式（Claude系列模型）

自动检测：根据模型名称自动选择API格式
- 模型名包含'claude' -> 使用Anthropic格式 (/v1/messages)
- 其他模型 -> 使用OpenAI格式 (/v1/chat/completions)
"""

import json
import os
from dataclasses import asdict, dataclass
from enum import Enum
from typing import AsyncGenerator, Dict, List, Optional

import httpx
from openai import AsyncOpenAI

from src.models.config import LLMConfig, get_config
from src.utils.api_format import (
    APIFormat,
    build_anthropic_endpoint,
    detect_api_format,
    get_browser_headers,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)


# ============ Prompt模板 ============

CODE_ANALYSIS_PROMPT = """请分析以下代码文件，生成详细的技术文档。

文件路径: {file_path}

代码内容:
```
{code_content}
```

请提供以下内容：
1. 文件概述：简要描述这个文件的主要功能和用途
2. 主要组件：列出文件中的类、函数、常量等主要组件
3. 依赖关系：列出该文件依赖的其他模块
4. 关键逻辑：解释核心算法或业务逻辑
5. 使用示例：如果适用，提供简单的使用示例

请用中文回答，保持专业和简洁。
"""

DIRECTORY_SUMMARY_PROMPT = """请根据以下子模块的文档，生成该目录的总结文档。

目录名称: {dir_name}
目录路径: {dir_path}

子模块文档:
{sub_documents}

请提供以下内容：
1. 目录概述：这个目录的整体功能和职责
2. 模块关系：子模块之间的关系和依赖
3. 核心功能：该目录提供的主要功能
4. 设计模式：如果有明显的设计模式，请指出

请用中文回答，保持专业和简洁。
"""

README_PROMPT = """请根据以下所有模块的文档，生成项目的README文档。

项目名称: {project_name}
项目路径: {project_path}

所有模块文档:
{all_documents}

请生成一份完整的README文档，包含：
1. 项目简介
2. 项目结构
3. 主要功能
4. 技术栈
5. 模块说明
6. 快速开始（如果能推断出来）

请用中文回答，格式清晰，适合作为项目文档。
"""

READING_GUIDE_PROMPT = """请根据以下项目文档，生成一份项目文档阅读顺序指南。

项目名称: {project_name}

项目结构:
{project_structure}

所有模块文档:
{all_documents}

请生成一份阅读顺序指南，帮助新人快速理解项目。指南应包含：

1. **推荐阅读顺序**：按照从入门到深入的顺序，列出建议的文档阅读路径
   - 考虑模块之间的依赖关系
   - 先基础模块，后业务模块
   - 先核心功能，后辅助功能

2. **模块分类**：将文档按功能/层次分类
   - 入口文件
   - 核心模块
   - 工具/辅助模块
   - 配置相关
   - 测试相关

3. **阅读建议**：
   - 快速了解项目：应该先看哪些文档
   - 深入理解架构：应该重点关注哪些文档
   - 开始开发：需要掌握哪些文档

4. **模块依赖图**：用文本形式描述主要模块之间的依赖关系

请用中文回答，格式清晰，使用Markdown格式。
"""


class ContentCollectMode(Enum):
    """流式响应收集模式"""
    CONTENT_ONLY = "content_only"
    WITH_REASONING = "with_reasoning"
    REASONING_ONLY = "reasoning_only"


@dataclass
class ChatMessage:
    """聊天消息"""
    role: str
    content: str

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, str]) -> "ChatMessage":
        return cls(role=data["role"], content=data["content"])

    @classmethod
    def from_list(cls, messages: List[Dict[str, str]]) -> List["ChatMessage"]:
        return [cls.from_dict(msg) for msg in messages]


@dataclass
class StreamCollectResult:
    """流式收集结果"""
    content: str
    reasoning: str
    finish_reason: Optional[str]
    chunk_count: int


class LLMClient:
    """
    异步流式调用封装，支持OpenAI和Anthropic API格式。

    完全仿照 llm_tool.py 实现
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        simulate_browser: bool = True,
    ):
        """
        初始化LLM客户端

        Args:
            api_key: API密钥
            base_url: API基础URL（中转站地址）
            simulate_browser: 是否模拟浏览器请求头
        """
        # 解析API密钥
        key = self._resolve_env_var(api_key) or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise ValueError("缺少API密钥，请在配置文件或环境变量中设置")

        # 解析base_url
        url = self._resolve_env_var(base_url) or os.environ.get("OPENAI_API_BASE")

        # 保存供直接HTTP调用使用
        self._api_key = key
        self._base_url = url
        self._simulate_browser = simulate_browser

        # 构建浏览器模拟请求头
        default_headers = {}
        if simulate_browser:
            default_headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Accept-Encoding": "gzip, deflate, br",
                "DNT": "1",
                "Connection": "keep-alive",
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-origin",
            }

        # 创建OpenAI客户端
        self._client = AsyncOpenAI(
            api_key=key,
            base_url=url,
            default_headers=default_headers if default_headers else None,
        )

        logger.info(
            f"LLM客户端初始化: base_url={url or '官方API'}, simulate_browser={simulate_browser}"
        )

    def _resolve_env_var(self, value: Optional[str]) -> Optional[str]:
        """解析环境变量格式的值"""
        if value and value.startswith("${") and value.endswith("}"):
            env_var = value[2:-1]
            return os.environ.get(env_var)
        return value

    def _get_anthropic_headers(self) -> Dict[str, str]:
        """获取Anthropic API请求头"""
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
            "anthropic-version": "2023-06-01",
        }

        if self._simulate_browser:
            headers.update(get_browser_headers())

        return headers

    async def _stream_chat_anthropic(
        self,
        messages: List[ChatMessage],
        model: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout: int = 120,
        **kwargs,
    ) -> AsyncGenerator[Dict[str, str], None]:
        """
        使用Anthropic Messages API进行流式聊天请求
        """
        endpoint = build_anthropic_endpoint(self._base_url)
        headers = self._get_anthropic_headers()

        # 分离system消息
        system_content = None
        anthropic_messages = []

        for msg in messages:
            if msg.role == "system":
                system_content = msg.content
            else:
                anthropic_messages.append({
                    "role": msg.role,
                    "content": msg.content
                })

        # 构建请求体
        payload = {
            "model": model,
            "messages": anthropic_messages,
            "stream": True,
            "max_tokens": max_tokens or 4096,
        }

        if system_content:
            payload["system"] = system_content
        if temperature is not None:
            payload["temperature"] = temperature

        logger.info(f"Anthropic API请求: endpoint={endpoint}, model={model}")

        try:
            async with httpx.AsyncClient(timeout=float(timeout)) as client:
                async with client.stream(
                    "POST",
                    endpoint,
                    headers=headers,
                    json=payload
                ) as response:
                    if response.status_code != 200:
                        error_text = await response.aread()
                        error_msg = error_text.decode("utf-8", errors="replace")[:500]
                        logger.error(f"Anthropic API错误: status={response.status_code}, response={error_msg}")
                        raise Exception(f"Anthropic API错误({response.status_code}): {error_msg}")

                    # 解析SSE流式响应
                    async for line in response.aiter_lines():
                        if not line or not line.startswith("data: "):
                            continue

                        data_str = line[6:]

                        if data_str == "[DONE]":
                            break

                        try:
                            chunk = json.loads(data_str)
                            event_type = chunk.get("type", "")

                            if event_type == "content_block_delta":
                                delta = chunk.get("delta", {})
                                if delta.get("type") == "text_delta":
                                    text = delta.get("text", "")
                                    if text:
                                        yield {
                                            "content": text,
                                            "finish_reason": None,
                                        }
                            elif event_type == "message_delta":
                                stop_reason = chunk.get("delta", {}).get("stop_reason")
                                if stop_reason:
                                    yield {
                                        "content": None,
                                        "finish_reason": stop_reason,
                                    }
                            elif event_type == "message_stop":
                                yield {
                                    "content": None,
                                    "finish_reason": "stop",
                                }

                        except json.JSONDecodeError:
                            continue

        except httpx.TimeoutException:
            logger.error(f"Anthropic API超时: model={model}, timeout={timeout}")
            raise
        except Exception as e:
            if "Anthropic API错误" not in str(e):
                logger.error(f"Anthropic API失败: model={model}, error={e}")
            raise

    async def _stream_chat_openai(
        self,
        messages: List[ChatMessage],
        model: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout: int = 120,
        **kwargs,
    ) -> AsyncGenerator[Dict[str, str], None]:
        """
        使用OpenAI Chat Completions API进行流式聊天请求
        """
        payload = {
            "model": model,
            "messages": [msg.to_dict() for msg in messages],
            "stream": True,
        }

        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        logger.info(f"OpenAI API请求: base_url={self._client.base_url}, model={model}")

        try:
            stream = await self._client.with_options(timeout=float(timeout)).chat.completions.create(**payload)
            async for chunk in stream:
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]

                result = {
                    "content": choice.delta.content,
                    "finish_reason": choice.finish_reason,
                }

                # 支持DeepSeek R1的reasoning_content
                if hasattr(choice.delta, "reasoning_content") and choice.delta.reasoning_content:
                    result["reasoning_content"] = choice.delta.reasoning_content

                yield result

        except Exception as e:
            logger.error(f"OpenAI API失败: model={model}, error={e}")
            raise

    async def stream_chat(
        self,
        messages: List[ChatMessage],
        model: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout: int = 120,
        api_format: Optional[str] = None,
        **kwargs,
    ) -> AsyncGenerator[Dict[str, str], None]:
        """
        流式聊天请求（自动检测API格式）

        Args:
            messages: 消息列表
            model: 模型名称
            temperature: 温度参数
            max_tokens: 最大token数
            timeout: 超时时间
            api_format: 强制指定API格式，为None则自动检测
        """
        # 确定API格式
        if api_format:
            detected_format = APIFormat(api_format)
        else:
            detected_format = detect_api_format(model)

        logger.info(f"LLM请求: model={model}, api_format={detected_format.value}")

        if detected_format == APIFormat.ANTHROPIC:
            async for chunk in self._stream_chat_anthropic(
                messages=messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
                **kwargs,
            ):
                yield chunk
        else:
            async for chunk in self._stream_chat_openai(
                messages=messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
                **kwargs,
            ):
                yield chunk

    async def stream_and_collect(
        self,
        messages: List[ChatMessage],
        model: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout: int = 120,
        api_format: Optional[str] = None,
        collect_mode: ContentCollectMode = ContentCollectMode.CONTENT_ONLY,
        **kwargs,
    ) -> StreamCollectResult:
        """
        流式请求并收集完整响应
        """
        content = ""
        reasoning = ""
        finish_reason = None
        chunk_count = 0

        try:
            async for chunk in self.stream_chat(
                messages=messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
                api_format=api_format,
                **kwargs,
            ):
                chunk_count += 1

                if collect_mode in (ContentCollectMode.CONTENT_ONLY, ContentCollectMode.WITH_REASONING):
                    if chunk.get("content"):
                        content += chunk["content"]

                if collect_mode in (ContentCollectMode.WITH_REASONING, ContentCollectMode.REASONING_ONLY):
                    if chunk.get("reasoning_content"):
                        reasoning += chunk["reasoning_content"]

                if chunk.get("finish_reason"):
                    finish_reason = chunk["finish_reason"]

        except Exception as e:
            logger.error(f"stream_and_collect失败: model={model}, chunks={chunk_count}, error={e}")
            raise

        return StreamCollectResult(
            content=content,
            reasoning=reasoning,
            finish_reason=finish_reason,
            chunk_count=chunk_count,
        )

    async def complete(
        self,
        prompt: str,
        model: str,
        system: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout: int = 120,
        api_format: Optional[str] = None,
    ) -> str:
        """
        便捷方法：发送单轮对话请求并收集结果
        """
        messages = []

        if system:
            messages.append(ChatMessage(role="system", content=system))

        messages.append(ChatMessage(role="user", content=prompt))

        result = await self.stream_and_collect(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            api_format=api_format,
        )

        return result.content


class LLMService:
    """
    LLM服务类

    提供代码分析相关的高级接口
    """

    def __init__(self, config: Optional[LLMConfig] = None):
        """初始化LLM服务"""
        self.config = config or get_config().llm

        # 创建LLM客户端
        self.client = LLMClient(
            api_key=self.config.api_key,
            base_url=self.config.base_url,
            simulate_browser=self.config.simulate_browser,
        )

        # 保存配置
        self.model = self.config.model
        self.api_format = self.config.api_format
        self.timeout = self.config.timeout
        self.temperature = self.config.temperature
        self.max_tokens = self.config.max_tokens

    async def analyze_code(self, file_path: str, code_content: str) -> str:
        """分析代码文件"""
        prompt = CODE_ANALYSIS_PROMPT.format(
            file_path=file_path,
            code_content=code_content
        )

        return await self.client.complete(
            prompt=prompt,
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            timeout=self.timeout,
            api_format=self.api_format,
        )

    async def summarize_directory(
        self,
        dir_name: str,
        dir_path: str,
        sub_documents: str
    ) -> str:
        """合并子模块文档，生成目录级总结"""
        prompt = DIRECTORY_SUMMARY_PROMPT.format(
            dir_name=dir_name,
            dir_path=dir_path,
            sub_documents=sub_documents
        )

        return await self.client.complete(
            prompt=prompt,
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            timeout=self.timeout,
            api_format=self.api_format,
        )

    async def generate_readme(
        self,
        project_name: str,
        project_path: str,
        all_documents: str
    ) -> str:
        """生成最终的README文档"""
        prompt = README_PROMPT.format(
            project_name=project_name,
            project_path=project_path,
            all_documents=all_documents
        )

        return await self.client.complete(
            prompt=prompt,
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            timeout=self.timeout,
            api_format=self.api_format,
        )

    async def generate_reading_guide(
        self,
        project_name: str,
        project_structure: str,
        all_documents: str
    ) -> str:
        """生成项目文档阅读顺序指南"""
        prompt = READING_GUIDE_PROMPT.format(
            project_name=project_name,
            project_structure=project_structure,
            all_documents=all_documents
        )

        return await self.client.complete(
            prompt=prompt,
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            timeout=self.timeout,
            api_format=self.api_format,
        )


class MockLLMService(LLMService):
    """模拟LLM服务（用于测试）"""

    def __init__(self):
        self.config = None
        self.client = None

    async def analyze_code(self, file_path: str, code_content: str) -> str:
        return f"[模拟分析] 文件: {file_path}\n代码行数: {len(code_content.splitlines())}"

    async def summarize_directory(
        self,
        dir_name: str,
        dir_path: str,
        sub_documents: str
    ) -> str:
        return f"[模拟总结] 目录: {dir_name}\n子文档数: {sub_documents.count('---')}"

    async def generate_readme(
        self,
        project_name: str,
        project_path: str,
        all_documents: str
    ) -> str:
        return f"# {project_name}\n\n[模拟README]\n\n路径: {project_path}"


# ============ 全局服务实例管理 ============

_llm_service: Optional[LLMService] = None


def get_llm_service() -> LLMService:
    """获取全局LLM服务实例"""
    global _llm_service
    if _llm_service is None:
        _llm_service = LLMService()
    return _llm_service


def set_llm_service(service: LLMService) -> None:
    """设置全局LLM服务实例"""
    global _llm_service
    _llm_service = service
