# CodeSummaryAgent - README 文档

> 🤖 一个智能代码分析系统，能够自动扫描、分析代码库，并生成结构化的代码文档

## 📌 项目简介

**CodeSummaryAgent** 是一个全栈的代码智能分析平台，集成了现代化的后端服务和前端界面。它能够对任意代码库进行深度分析，利用 LLM 能力自动生成高质量的代码文档、依赖关系图和项目摘要。

### 🎯 解决的问题

- 📚 **文档缺失**：自动为没有文档的项目生成结构化文档
- 🔍 **代码难懂**：用 AI 生成易读的代码摘要和分析
- 📊 **依赖复杂**：自动分析多语言项目的依赖关系
- ⏱️ **手工费时**：完全自动化分析流程，支持断点续传
- 🌐 **缺乏可视化**：提供交互式 Web UI 和实时进度反馈

### ✨ 主要特性

- ✅ **全栈架构**：后端 Python (FastAPI) + 前端 Vue 3，开箱即用
- ✅ **AI 驱动分析**：支持 OpenAI、Claude 等多个 LLM API，可轻松扩展
- ✅ **分层递进处理**：从文件级→目录级→项目级，逐层深化分析
- ✅ **高并发异步**：基于 asyncio 的队列管理，充分利用网络 I/O
- ✅ **断点续传**：支持分析中断后恢复，保存进度检查点
- ✅ **增量更新**：MD5 指纹追踪，只分析新增/修改文件
- ✅ **依赖分析**：支持 Python/JavaScript/Go/Java 等多语言依赖解析
- ✅ **实时反馈**：WebSocket 推送分析进度，前端实时显示
- ✅ **双界面**：同时提供 CLI 命令行 + Web 管理平台
- ✅ **文档生成**：自动生成 README、阅读指南、目录树等多类文档

---

## 🚀 快速开始

### 📋 环境要求

| 组件 | 版本要求 | 说明 |
|------|---------|------|
| **Python** | 3.8+ | 后端运行时 |
| **Node.js** | 16+ | 前端构建工具链 |
| **npm** | 7+ | 前端包管理器 |
| **LLM API Key** | - | OpenAI/Claude/其他 API 密钥 |
| **操作系统** | Windows/macOS/Linux | 跨平台支持 |

### 💻 安装步骤

#### 第一步：克隆项目

```bash
# 克隆项目
git clone <repository_url>
cd CodeSummaryAgent

# 进入项目根目录
ls -la  # 应该看到 src/, web/, main.py 等
```

#### 第二步：安装后端依赖

```bash
# 创建并激活 Python 虚拟环境（推荐）
python -m venv venv

# 激活虚拟环境
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

# 安装后端依赖
pip install -r requirements.txt
```

#### 第三步：安装前端依赖

```bash
# 进入前端目录
cd web

# 安装 npm 依赖
npm install

# 返回项目根目录
cd ..
```

#### 第四步：配置 LLM API

编辑或创建配置文件 `config.yaml`：

```yaml
# LLM 服务配置
llm:
  # 选择 LLM 提供商
  provider: "openai"  # 可选: openai, claude, azure
  
  # OpenAI 配置
  openai:
    api_key: "sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    api_base: "https://api.openai.com/v1"  # 可选，用于国内代理
    model: "gpt-4-turbo"  # 推荐 gpt-4-turbo 或 gpt-3.5-turbo
    temperature: 0.3
    timeout: 30
  
  # Claude 配置（可选）
  claude:
    api_key: "sk-ant-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    model: "claude-3-sonnet-20240229"
    timeout: 30

# 分析器配置
analyzer:
  # 最大并发请求数（根据 API 限额调整）
  max_workers: 5
  
  # 单个文件最大字符数（超过则分片处理）
  max_file_size: 50000
  
  # 输出目录（相对或绝对路径）
  output_dir: "./output"
  
  # 是否启用增量更新
  incremental: true
  
  # 扫描时忽略的目录（支持正则表达式）
  ignore_patterns:
    - "node_modules"
    - ".git"
    - "__pycache__"
    - "venv"
    - "dist"
    - "build"

# Web 服务配置
server:
  host: "0.0.0.0"
  port: 8000
  reload: true  # 开发模式，改动后自动重启
```

**获取 API Key 的方法**：

- **OpenAI**：访问 [https://platform.openai.com/api-keys](https://platform.openai.com/api-keys)
- **Claude**：访问 [https://console.anthropic.com/](https://console.anthropic.com/)
- **Azure**：访问 Azure 门户配置 OpenAI 服务

### ▶️ 运行项目

#### 方式一：使用主 CLI 命令启动完整系统

```bash
# 启动 Web 服务器（同时支持 API 和前端）
python main.py server

# 服务启动后访问：
# 前端：http://localhost:3000
# 后端：http://localhost:8000
# API 文档：http://localhost:8000/docs
```

#### 方式二：分开启动前后端（开发模式）

```bash
# 终端1：启动后端 API 服务
python main.py server --port 8000

# 终端2：启动前端开发服务器
cd web
npm run dev
# 前端访问：http://localhost:3000（自动代理 API 请求到 :8000）
```

#### 方式三：生产环境部署

```bash
# 前端构建
cd web
npm run build
# 生成 dist/ 目录（可部署到 Nginx/Apache）

# 后端生产启动
python main.py server --port 8000 --reload false
```

---

## 📖 使用方法

### 🖥️ 方式一：Web UI（推荐）

1. **打开浏览器**：访问 http://localhost:3000

2. **创建分析任务**：
   - 填写代码库路径（本地路径或 Git URL）
   - 配置分析深度和选项
   - 点击"开始分析"

3. **实时监控进度**：
   - 页面实时显示分析进度条
   - 查看已完成的文件数和估算剩余时间
   - 实时推送分析状态

4. **查看结果**：
   - 任务完成后自动生成文档预览
   - 下载完整的 Markdown 文档集
   - 查看依赖关系图

### 💻 方式二：CLI 命令行

#### 2.1 分析代码库

```bash
# 最简单的用法
python main.py analyze --input /path/to/project

# 完整参数示例
python main.py analyze \
  --input /path/to/project \
  --output ./docs \
  --depth 3 \
  --llm-provider openai \
  --max-workers 5 \
  --incremental true

# 查看所有参数
python main.py analyze --help
```

**常用参数说明**：

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--input` | str | 必需 | 代码库路径 |
| `--output` | str | `./output` | 文档输出目录 |
| `--depth` | int | 3 | 分析深度（1-5，越深越详细） |
| `--llm-provider` | str | `openai` | LLM 提供商选择 |
| `--max-workers` | int | 5 | 并发处理数 |
| `--incremental` | bool | true | 是否启用增量更新 |

#### 2.2 扫描目录结构

```bash
# 仅扫描目录，生成文件树
python main.py scan --input /path/to/project

# 输出示例
Project Structure
├── src/
│   ├── core/
│   │   ├── analyzer.py (850 lines)
│   │   └── document_generator.py (420 lines)
│   ├── api/
│   └── cli/
└── web/
    ├── src/
    ├── vite.config.js
    └── package.json
```

#### 2.3 初始化配置文件

```bash
# 生成默认配置文件
python main.py init-config

# 配置文件将保存到 config.yaml
# 之后手动编辑配置
```

#### 2.4 查看分析状态

```bash
# 显示最后一次分析的状态
python main.py status

# 输出示例
[Analysis Status]
Last Task: /home/user/myproject
Status: COMPLETED
Files Analyzed: 127
Duration: 2m 34s
Output: /home/user/output/myproject
```

#### 2.5 分析项目依赖

```bash
# 仅分析依赖关系（不生成文档）
python main.py deps --input /path/to/project

# 输出示例
[Python Dependencies]
- requests: 2.28.0
- pydantic: 2.0
- sqlalchemy: 2.0

[JavaScript Dependencies]
- vue: 3.3.0
- vite: 4.4.0
```

#### 2.6 启动 Web 服务

```bash
# 启动完整的 Web 服务
python main.py server

# 指定端口
python main.py server --port 9000

# 生产模式（关闭热重载）
python main.py server --reload false
```

#### 2.7 查看版本

```bash
python main.py version
# 输出: CodeSummaryAgent v1.0.0
```

### 🔌 方式三：Python 代码集成

```python
from src.core.analyzer import CodeAnalyzer
from src.models.config import AppConfig

# 创建配置
config = AppConfig(
    input_path="/path/to/project",
    output_dir="./output",
    llm_provider="openai",
    analysis_depth=3
)

# 创建分析器实例
analyzer = CodeAnalyzer(config)

# 异步执行分析
import asyncio
result = asyncio.run(analyzer.analyze())

# 获取结果
print(f"分析完成！处理了 {result.files_count} 个文件")
print(f"生成文档存储在：{result.output_dir}")
```

### 🌐 方式四：HTTP API 调用

#### 4.1 启动服务

```bash
python main.py server
```

#### 4.2 创建分析任务

```bash
curl -X POST http://localhost:8000/api/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "input_path": "/path/to/project",
    "output_dir": "./output",
    "analysis_depth": 3,
    "llm_provider": "openai",
    "max_workers": 5
  }'

# 返回示例
{
  "task_id": "task-20240101-001",
  "status": "RUNNING",
  "created_at": "2024-01-01T10:00:00Z"
}
```

#### 4.3 查询任务状态

```bash
curl http://localhost:8000/api/tasks/task-20240101-001

# 返回示例
{
  "task_id": "task-20240101-001",
  "status": "COMPLETED",
  "progress": 100,
  "files_processed": 127,
  "output_path": "/path/to/output"
}
```

#### 4.4 WebSocket 实时监听进度

```javascript
// 在前端 JavaScript 中
const ws = new WebSocket('ws://localhost:8000/ws/task-20240101-001');

ws.onmessage = (event) => {
  const message = JSON.parse(event.data);
  console.log('Progress:', message.progress);
  console.log('Status:', message.status);
};
```

---

## 📁 项目结构详解

```
CodeSummaryAgent/
│
├── 🔴 src/                              # 后端源代码核心
│   ├── models/                          # 数据模型层
│   │   ├── config.py                    # 配置管理（Pydantic 验证）
│   │   ├── file_node.py                 # 文件树数据结构
│   │   └── __init__.py
│   │
│   ├── utils/                           # 工具函数库
│   │   ├── logger.py                    # 日志系统（核心工具）
│   │   ├── api_format.py                # API 响应格式适配
│   │   ├── retry.py                     # 异步重试机制
│   │   ├── progress_manager.py          # 进度实时跟踪
│   │   ├── tree_printer.py              # 目录树美化输出
│   │   └── __init__.py
│   │
│   ├── services/                        # 服务层（核心业务逻辑）
│   │   ├── directory_scanner.py         # 目录扫描 + 文件树构建
│   │   ├── llm_service.py               # LLM API 统一接口
│   │   ├── llm_queue.py                 # 异步任务

---

*本文档由 CodeSummaryAgent 自动生成*
*生成时间: 2026-01-01 18:16:35*
