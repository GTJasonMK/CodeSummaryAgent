"""
配置模型模块
使用Pydantic定义类型安全的配置模型
"""
from typing import List, Optional, Literal
from pathlib import Path
import os

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings
import yaml


def _resolve_env_var(value: Optional[str]) -> Optional[str]:
    """解析环境变量格式的值，如 ${OPENAI_API_KEY}"""
    if value and value.startswith("${") and value.endswith("}"):
        env_var = value[2:-1]
        return os.environ.get(env_var)
    return value


class LLMConfig(BaseModel):
    """LLM服务配置"""
    provider: str = Field(default="openai", description="LLM提供商: openai/anthropic/ollama")
    model: str = Field(default="gpt-4", description="模型名称")
    api_key: Optional[str] = Field(default=None, description="API密钥")
    base_url: Optional[str] = Field(default=None, description="自定义API地址(中转站)")
    max_concurrent: int = Field(default=5, ge=1, le=50, description="最大并发数")
    timeout: int = Field(default=120, ge=10, le=600, description="单次调用超时(秒)")
    max_retries: int = Field(default=3, ge=1, le=10, description="最大重试次数")
    retry_delay: float = Field(default=1.0, ge=0.1, le=30.0, description="初始重试延迟(秒)")

    # 新增: API格式相关配置
    api_format: Optional[Literal["openai", "anthropic"]] = Field(
        default=None,
        description="API格式(可选): openai/anthropic，为None则根据模型名自动检测"
    )
    simulate_browser: bool = Field(
        default=True,
        description="是否模拟浏览器请求头(用于绕过中转站的Cloudflare检测)"
    )
    temperature: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=2.0,
        description="生成温度参数(可选)"
    )
    max_tokens: Optional[int] = Field(
        default=None,
        ge=1,
        le=128000,
        description="最大生成token数(可选)"
    )
    # SSL验证配置
    verify_ssl: bool = Field(
        default=True,
        description="是否验证SSL证书(中转站可能需要禁用)"
    )

    @field_validator("api_key", "base_url", mode="before")
    @classmethod
    def resolve_env_var(cls, v: Optional[str]) -> Optional[str]:
        """解析环境变量格式的值，如 ${OPENAI_API_KEY}"""
        return _resolve_env_var(v)


class AnalysisConfig(BaseModel):
    """分析配置"""
    ignore_patterns: List[str] = Field(
        default=[
            "node_modules/**",
            "__pycache__/**",
            ".git/**",
            ".venv/**",
            "venv/**",
            "*.pyc",
            "*.pyo",
            "*.log",
            "*.tmp",
            ".DS_Store",
            "Thumbs.db",
            "*.egg-info/**",
            "dist/**",
            "build/**",
            ".idea/**",
            ".vscode/**",
        ],
        description="忽略的文件/目录模式"
    )

    include_extensions: List[str] = Field(
        default=[
            ".py", ".js", ".ts", ".jsx", ".tsx",
            ".java", ".go", ".rs", ".cpp", ".c", ".h",
            ".cs", ".rb", ".php", ".swift", ".kt",
            ".scala", ".vue", ".svelte",
        ],
        description="支持的文件扩展名"
    )

    max_file_size: int = Field(
        default=100 * 1024,  # 100KB
        description="最大文件大小(字节)"
    )


class OutputConfig(BaseModel):
    """输出配置"""
    docs_suffix: str = Field(default="_docs", description="文档目录后缀")
    docs_inside_source: bool = Field(
        default=True,
        description="文档目录是否在源代码目录内部。True: c:/a/b -> c:/a/b/b_docs，False: c:/a/b -> c:/a/b_docs"
    )
    readme_name: str = Field(default="README.md", description="最终README文档名")
    reading_guide_name: str = Field(default="READING_GUIDE.md", description="阅读顺序指南文档名")
    api_doc_name: str = Field(default="API_DOC.md", description="API接口清单文档名")
    api_usage_doc_name: str = Field(default="API_USAGE.md", description="API使用文档名")
    dir_summary_name: str = Field(default="_dir_summary.md", description="目录汇总文档名")
    generate_api_doc: bool = Field(default=True, description="是否生成API接口清单文档")
    generate_api_usage_doc: bool = Field(default=True, description="是否生成API使用文档")


class ServerConfig(BaseModel):
    """Web服务配置"""
    host: str = Field(default="127.0.0.1", description="服务器地址")
    port: int = Field(default=8000, ge=1, le=65535, description="服务器端口")


class AppConfig(BaseModel):
    """应用配置（完整配置）"""
    llm: LLMConfig = Field(default_factory=LLMConfig)
    analysis: AnalysisConfig = Field(default_factory=AnalysisConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)

    @classmethod
    def from_yaml(cls, config_path: str) -> "AppConfig":
        """从YAML文件加载配置"""
        path = Path(config_path)
        if not path.exists():
            # 配置文件不存在则使用默认配置
            return cls()

        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        return cls(**data)

    def to_yaml(self, config_path: str) -> None:
        """保存配置到YAML文件"""
        path = Path(config_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(
                self.model_dump(),
                f,
                allow_unicode=True,
                default_flow_style=False,
                sort_keys=False
            )


# 全局配置实例
_config: Optional[AppConfig] = None


def get_config() -> AppConfig:
    """获取全局配置实例"""
    global _config
    if _config is None:
        _config = AppConfig()
    return _config


def load_config(config_path: str) -> AppConfig:
    """加载配置文件并设置为全局配置"""
    global _config
    _config = AppConfig.from_yaml(config_path)
    return _config


def set_config(config: AppConfig) -> None:
    """设置全局配置"""
    global _config
    _config = config
