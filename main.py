#!/usr/bin/env python3
"""
CodeSummaryAgent - 基于LLM的代码库分析工具

程序入口
"""
import sys
from pathlib import Path

# 将项目根目录添加到Python路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from src.cli.commands import main

if __name__ == "__main__":
    main()
