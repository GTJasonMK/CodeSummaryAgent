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

6. API接口识别（重要）：
   请**仔细检查**代码，判断此文件是否包含API接口定义（HTTP端点、路由、RPC接口、WebSocket等）。

   **识别API接口的特征**：
   - Flask: @app.route, @blueprint.route, @bp.route
   - FastAPI: @app.get, @app.post, @router.get, @router.post 等
   - Express: app.get, app.post, router.get, router.post 等
   - Django: path(), re_path(), urlpatterns
   - Spring: @GetMapping, @PostMapping, @RequestMapping, @DeleteMapping, @PutMapping
   - Gin (Go): r.GET, r.POST, router.Handle 等
   - 其他框架的路由/端点装饰器或注册函数

   **如果包含API接口**，请在文档末尾添加以下格式的标记（确保每个接口都列出，不要遗漏）：

   <!-- API_START -->
   包含API接口: 是
   接口列表:
   - [GET] /api/users - 获取用户列表
   - [POST] /api/users - 创建新用户
   - [DELETE] /api/users/{{id}} - 删除指定用户
   <!-- API_END -->

   **如果不包含API接口**，请添加：
   <!-- API_START -->
   包含API接口: 否
   <!-- API_END -->

   **注意**：
   - 只列出代码中明确定义的接口，不要推测或编造
   - 路径中的动态参数用 {{param}} 格式表示
   - 确保不遗漏任何接口

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

请生成一份完整、实用的README文档，让用户能够快速上手使用该项目。

## 必须包含的内容

### 1. 项目简介
- 项目名称和一句话描述
- 项目解决什么问题
- 主要特性列表（用 ✅ 标记）

### 2. 快速开始

#### 2.1 环境要求
根据代码分析推断需要的环境：
- Python/Node.js/其他运行时版本
- 操作系统要求（如果有）
- 其他依赖（数据库、Redis等，如果有）

#### 2.2 安装步骤
```bash
# 克隆项目
git clone <repository_url>
cd {project_name}

# 安装依赖（根据项目类型推断）
pip install -r requirements.txt  # Python项目
# 或
npm install  # Node.js项目
```

#### 2.3 配置说明
- 列出需要配置的环境变量或配置文件
- 提供配置示例
- 说明每个配置项的作用

#### 2.4 运行项目
```bash
# 提供具体的启动命令
python main.py [参数]
```

### 3. 使用方法

#### 3.1 命令行使用（如果是CLI工具）
```bash
# 基本用法
python main.py <参数>

# 常用示例
python main.py --help
python main.py <具体示例>
```

#### 3.2 作为库使用（如果可以作为模块导入）
```python
from xxx import YYY

# 使用示例
```

#### 3.3 API接口（如果是Web服务）
列出主要的API端点和用法

### 4. 项目结构
```
{project_name}/
├── src/           # 源代码目录
│   ├── core/      # 核心模块
│   └── utils/     # 工具函数
├── config.yaml    # 配置文件
└── main.py        # 程序入口
```

### 5. 核心功能说明
简要描述各个核心模块的功能

### 6. 配置参数详解
以表格形式列出所有配置项：

| 参数名 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| xxx    | str  | ""     | 描述 |

### 7. 常见问题（FAQ）
根据项目特点，预测可能的问题并提供解答

### 8. 开发指南（可选）
- 如何运行测试
- 代码规范
- 如何贡献

## 注意事项
- 所有代码块都要标注语言类型
- 配置示例要完整可用
- 命令要可以直接复制执行
- 如果某些信息无法从代码中推断，用 `<待补充>` 标记

请用中文回答，格式清晰，适合作为项目文档。
"""

READING_GUIDE_PROMPT = """请根据以下项目文档，生成一份项目文档阅读顺序指南。

项目名称: {project_name}

项目结构:
{project_structure}

所有模块文档:
{all_documents}

请生成一份详细的阅读顺序指南，帮助新人系统性地理解整个项目。

## 核心要求：生成明确的阅读顺序链条

你必须生成一个**完整的阅读顺序链条**，用箭头连接所有重要文件，形成一条清晰的阅读路径。

### 格式要求

1. **阅读顺序链条**（必须包含）

   用以下格式展示完整的阅读路径：
   ```
   📖 推荐阅读顺序：

   config.py → main.py → core/analyzer.py → services/scanner.py → ...
   ```

   这个链条必须：
   - 包含项目中所有重要的文件
   - 按照从基础到高级的顺序排列
   - 用 `→` 箭头连接每个文件

2. **每一步的阅读理由**（必须包含）

   对链条中的每一个"箭头"解释原因，说明为什么按这个顺序阅读：

   ```
   📍 第1步：config.py
      为什么先读：了解项目的配置结构，这是理解后续模块的基础

   📍 第2步：main.py（在 config.py 之后）
      为什么这个顺序：config.py 定义了配置，main.py 是程序入口，会加载和使用这些配置

   📍 第3步：core/analyzer.py（在 main.py 之后）
      为什么这个顺序：main.py 调用了 analyzer，理解入口后再看核心逻辑
   ```

3. **阅读链条设计原则**

   按以下优先级设计阅读顺序：
   - 先读被依赖的模块，后读依赖它的模块
   - 先读配置和模型定义，后读业务逻辑
   - 先读入口文件，后读核心实现
   - 先读基础工具，后读高级功能
   - 考虑认知负荷：简单模块在前，复杂模块在后

4. **模块分层概览**

   将文件按层次分类，帮助理解架构：
   - 🏠 入口层：程序启动入口
   - ⚙️ 配置层：配置和常量定义
   - 📦 模型层：数据结构定义
   - 🔧 服务层：业务逻辑实现
   - 🛠️ 工具层：辅助工具函数

5. **快速阅读路径**（可选）

   如果读者时间有限，提供一个精简版的阅读路径：
   ```
   ⚡ 快速理解路径（5个核心文件）：
   config.py → main.py → core/analyzer.py → 完成！
   ```

请用中文回答，格式清晰，使用Markdown格式。确保阅读链条是连贯的、有逻辑的。
"""

# ============ 两阶段API文档生成 ============
# 第一阶段：对每个API文件提取接口详情（中间结果）
# 第二阶段：汇总所有中间结果生成最终API文档

API_EXTRACT_PROMPT = """请从以下代码文件分析文档中**精确提取**所有API接口信息。

文件路径: {file_path}

文件分析文档:
{file_doc}

## 严格要求

1. **只提取明确存在的接口**：只输出文档中明确提到的接口，禁止推测或编造
2. **必须标注认证要求**：每个接口必须明确标注是否需要认证
3. **保持信息原貌**：接口路径、方法必须与文档描述完全一致

## 认证判断规则

根据以下特征判断接口是否需要认证：
- 使用了 `@require_auth`、`@require_admin`、`@login_required` 等装饰器 → 需要认证
- 使用了 `Depends(require_api_auth)`、`Depends(get_current_user)` 等依赖 → 需要认证
- 路由定义中明确提到 "无需认证"、"公开接口" → 无需认证
- 登录接口（如 `/login`）本身 → 无需认证
- 健康检查、静态资源接口 → 通常无需认证
- 如果文档未明确说明，标注为"未明确"

## 输出格式（严格按此格式）

如果文件包含API接口，按以下格式输出：

### {file_path} 的接口列表

| 序号 | 方法 | 路径 | 功能描述 | 认证要求 |
|------|------|------|----------|----------|
| 1 | GET/POST/... | /api/xxx | 简要描述 | 需要/无需/未明确 |

如果文件中没有API接口，只输出一行：
**该文件未定义API接口**

## 禁止事项
- 禁止编造文档中未提及的接口
- 禁止生成请求/响应示例
- 禁止遗漏认证要求信息
"""

# 第二阶段：汇总提示词（固定模板格式）
API_SUMMARY_PROMPT = """请根据以下各文件提取的API接口信息，生成一份**精确、完整**的接口清单。

项目名称: {project_name}

各文件的接口信息:
{api_details}

## 严格要求

1. **不重不漏**：确保每个接口只出现一次，同时不遗漏任何接口
2. **禁止幻觉**：只输出上述信息中明确存在的接口，禁止编造
3. **保持原貌**：接口路径、方法、认证要求必须与原文完全一致
4. **固定格式**：严格按照下方模板输出，不要改变格式结构

## 输出模板（严格按此格式，不要修改结构）

```markdown
## 一、接口总览

| 序号 | 模块 | 方法 | 路径 | 功能描述 | 认证 |
|------|------|------|------|----------|------|
| 1 | 模块名 | GET | /xxx | 描述 | 是/否 |
| 2 | ... | ... | ... | ... | ... |

## 二、按模块分类

按以下固定顺序组织模块（如果该模块无接口则跳过）：

### 2.1 核心业务接口
（聊天、对话、主要业务功能相关的接口）

| 方法 | 路径 | 功能描述 | 认证 |
|------|------|----------|------|

### 2.2 资源管理接口
（文件、图片、模型等资源相关的接口）

| 方法 | 路径 | 功能描述 | 认证 |
|------|------|----------|------|

### 2.3 用户与认证接口
（登录、注册、Token管理等认证相关的接口）

| 方法 | 路径 | 功能描述 | 认证 |
|------|------|----------|------|

### 2.4 系统管理接口
（账号管理、配置管理、系统维护等管理接口）

| 方法 | 路径 | 功能描述 | 认证 |
|------|------|----------|------|

### 2.5 辅助接口
（健康检查、页面路由、静态资源等辅助接口）

| 方法 | 路径 | 功能描述 | 认证 |
|------|------|----------|------|

## 三、认证要求汇总

### 无需认证的接口
- `GET /xxx` - 描述
- `POST /xxx` - 描述

### 需要认证的接口
- 核心业务接口：全部需要认证
- 资源管理接口：除 xxx 外全部需要认证
- ...（按模块说明）
```

## 禁止事项
- 禁止编造不存在的接口
- 禁止生成请求/响应示例
- 禁止改变上述模板的结构
- 禁止添加模板中没有的章节
"""

# 最终文档（README、阅读指南、API文档）使用更大的max_tokens，避免截断
FINAL_DOC_MAX_TOKENS = 16384

# 代码分析的最小max_tokens，确保大型文件的API信息不被截断
# 文件分析文档可能很长（包含概述、组件、依赖、逻辑、示例、API接口列表等）
# 4096 tokens 对于大型文件（如api_server.py）明显不足
CODE_ANALYSIS_MIN_TOKENS = 8192

API_DOC_PROMPT = """请根据以下项目文档，生成一份完整的API接口文档。

项目名称: {project_name}

项目结构:
{project_structure}

所有模块文档:
{all_documents}

请分析代码，识别所有的API接口（HTTP端点、RPC接口、WebSocket等），生成详细的接口文档。

## 必须包含的内容

### 1. 接口概览
用表格列出所有接口：接口名称、方法、路径、描述

### 2. 认证说明
说明API的认证方式：认证类型、Token传递方式、Token格式

### 3. 接口详情
对每个接口提供：
- 路径和方法
- 接口描述
- 是否需要认证
- 请求参数表格（参数名、位置、类型、必填、描述）
- 请求体示例（使用JSON格式）
- 成功响应示例（使用JSON格式）
- 错误响应示例（使用JSON格式）

### 4. 数据模型
定义接口中使用的数据结构，用表格列出字段名、类型、描述

### 5. 错误码说明
用表格列出常见错误码：错误码、说明、处理建议

### 6. 接口调用示例
提供curl命令示例

### 7. 前后端对接检查清单
列出对接时需要检查的要点

## 注意事项
- 如果无法从代码中识别出API接口，请明确说明"未检测到API接口"
- 对于无法确定的信息，用"待确认"标记
- 参数类型要准确（string/number/boolean/object/array）

请用中文回答，格式清晰，使用Markdown格式。
"""

# ============ API使用文档生成（两阶段） ============
# 第一阶段：对每个API文件提取详细的使用信息
# 第二阶段：汇总生成完整的API使用文档

API_USAGE_EXTRACT_PROMPT = """请从以下代码文件分析文档中**提取详细的API使用信息**。

文件路径: {file_path}

文件分析文档:
{file_doc}

## 任务说明

你需要为每个API接口提取完整的使用信息，包括请求参数、请求示例、响应示例等。
这些信息将用于生成面向开发者的API使用文档。

## 提取要求

对于文档中提到的**每一个API接口**，请提取以下信息：

1. **基本信息**：方法、路径、功能描述、是否需要认证
2. **请求参数**：
   - 路径参数（如 /users/{{id}} 中的 id）
   - 查询参数（如 ?page=1&size=10）
   - 请求体参数（JSON字段）
3. **请求示例**：完整的JSON请求体示例
4. **响应格式**：成功响应的JSON结构
5. **错误响应**：常见错误的响应格式

## 输出格式（严格按此格式）

### {file_path} 的API使用详情

#### 接口1: [方法] [路径]

**功能描述**：简要描述接口作用

**认证要求**：需要/无需

**请求参数**：

| 参数名 | 位置 | 类型 | 必填 | 描述 |
|--------|------|------|------|------|
| xxx | path/query/body | string/number/... | 是/否 | 描述 |

**请求示例**：
```json
{{
  "field": "value"
}}
```

**成功响应**：
```json
{{
  "code": 0,
  "data": {{}}
}}
```

**错误响应**：
```json
{{
  "code": 400,
  "message": "错误描述"
}}
```

---

（重复以上格式，直到所有接口都提取完毕）

## 注意事项
- 如果文档中信息不完整，根据常见RESTful API规范合理推断
- 请求/响应示例要符合JSON格式规范
- 如果文件中没有API接口，只输出：**该文件未定义API接口**
"""

API_USAGE_SUMMARY_PROMPT = """请根据以下各文件提取的API使用详情，生成一份**完整的API使用文档**。

项目名称: {project_name}

## 必须覆盖的接口清单（严格要求）

以下是API接口清单文档中已确认的所有接口，**你必须为每一个接口生成使用文档，不得遗漏任何一个**：

{api_reference_list}

## 各文件的API使用详情

{api_usage_details}

## 核心要求（必须遵守）

1. **完整覆盖**：必须为上述"必须覆盖的接口清单"中的每一个接口生成详细的使用说明
2. **路径一致**：接口路径必须与清单中的路径**完全一致**，不要添加或删除路径前缀
3. **不可遗漏**：如果某个接口在使用详情中没有信息，也要根据接口名称生成基础的使用说明

## 任务说明

生成一份面向开发者的API使用文档，帮助开发者快速上手调用这些API接口。

## 输出格式（严格按此模板）

```markdown
# {project_name} API使用文档

## 一、快速开始

### 1.1 Base URL
说明API的基础地址（如果能推断的话）

### 1.2 认证方式
说明如何进行API认证：
- Token类型（Bearer Token / API Key / 其他）
- Token传递方式（Header / Query / Cookie）
- 获取Token的方式

### 1.3 通用请求头
```
Content-Type: application/json
Authorization: Bearer <token>
```

### 1.4 通用响应格式
说明响应的通用结构

## 二、接口详情

按以下分类组织接口（无接口的分类跳过）：

### 2.1 核心业务接口

#### POST /v1/chat/completions
**功能**：描述
**认证**：需要

**请求参数**：
| 参数名 | 位置 | 类型 | 必填 | 描述 |
|--------|------|------|------|------|

**请求示例**：
```bash
curl -X POST "https://api.example.com/v1/chat/completions" \\
  -H "Authorization: Bearer <token>" \\
  -H "Content-Type: application/json" \\
  -d '{{
    "model": "gpt-4",
    "messages": [{{"role": "user", "content": "Hello"}}]
  }}'
```

**响应示例**：
```json
{{
  "id": "xxx",
  "choices": [...]
}}
```

---

### 2.2 资源管理接口
（文件、图片、模型等）

### 2.3 用户与认证接口
（登录、注册、Token管理等）

### 2.4 系统管理接口
（账号管理、配置管理等）

### 2.5 辅助接口
（健康检查、页面路由等）

## 三、错误处理

### 3.1 HTTP状态码
| 状态码 | 说明 |
|--------|------|
| 200 | 成功 |
| 400 | 请求参数错误 |
| 401 | 未认证 |
| 403 | 无权限 |
| 404 | 资源不存在 |
| 500 | 服务器内部错误 |

### 3.2 错误响应格式
```json
{{
  "error": {{
    "code": "ERROR_CODE",
    "message": "错误描述"
  }}
}}
```

## 四、调用示例

### 4.1 使用curl
提供几个典型的curl调用示例

### 4.2 使用Python
```python
import requests

response = requests.post(
    "https://api.example.com/v1/chat/completions",
    headers={{"Authorization": "Bearer <token>"}},
    json={{"model": "gpt-4", "messages": [...]}}
)
```

### 4.3 使用JavaScript
```javascript
const response = await fetch('/v1/chat/completions', {{
  method: 'POST',
  headers: {{
    'Authorization': 'Bearer <token>',
    'Content-Type': 'application/json'
  }},
  body: JSON.stringify({{...}})
}});
```
```

## 禁止事项
- 禁止编造不存在的接口
- 禁止改变上述模板的结构
- 禁止遗漏"必须覆盖的接口清单"中的任何接口
- 禁止修改接口路径（必须与清单一致）
- 如果信息不足，标注"待补充"而非编造
"""

# ============ 分批生成API使用文档（解决接口过多导致截断问题） ============
# 当接口数量超过阈值时，按模块分批生成，最后合并

# 分批生成阈值：超过此数量的接口将采用分批生成策略
API_USAGE_BATCH_THRESHOLD = 20

# 单个模块的接口使用文档生成
API_USAGE_MODULE_PROMPT = """请为以下模块的API接口生成详细的使用文档。

项目名称: {project_name}
模块名称: {module_name}

## 本模块必须覆盖的接口

{module_api_list}

## 相关的API使用详情

{api_usage_details}

## 核心要求

1. **完整覆盖**：必须为上述列表中的每一个接口生成详细的使用说明
2. **路径一致**：接口路径必须与列表中的路径**完全一致**
3. **不可遗漏**：即使详情中信息不足，也要生成基础的使用说明

## 输出格式

对于每个接口，按以下格式输出：

#### [METHOD] [PATH]
**功能**：简要描述
**认证**：需要/无需

**请求参数**：
| 参数名 | 位置 | 类型 | 必填 | 描述 |
|--------|------|------|------|------|
| xxx | path/query/body | string | 是/否 | 描述 |

**请求示例**：
```bash
curl -X METHOD "https://api.example.com/path" \\
  -H "Authorization: Bearer <token>" \\
  -H "Content-Type: application/json" \\
  -d '{{...}}'
```

**响应示例**：
```json
{{
  "code": 0,
  "data": {{...}}
}}
```

---

## 注意事项
- 只输出接口详情部分，不要输出文档标题、快速开始、错误处理等通用部分
- 每个接口之间用 `---` 分隔
- 如果信息不足，标注"待补充"
"""

# 通用部分生成（快速开始、错误处理、调用示例）
API_USAGE_COMMON_PROMPT = """请为项目生成API使用文档的通用部分。

项目名称: {project_name}

## 项目API概况

{api_overview}

## 相关的API使用详情（用于推断认证方式等）

{api_usage_details}

## 任务说明

生成API使用文档的通用部分，包括：
1. 快速开始（Base URL、认证方式、通用请求头、通用响应格式）
2. 错误处理（HTTP状态码、错误响应格式）
3. 调用示例（curl、Python、JavaScript）

## 输出格式

```markdown
## 一、快速开始

### 1.1 Base URL
说明API的基础地址

### 1.2 认证方式
说明如何进行API认证：
- Token类型（Bearer Token / API Key / 其他）
- Token传递方式（Header / Query / Cookie）
- 获取Token的方式

### 1.3 通用请求头
```
Content-Type: application/json
Authorization: Bearer <token>
```

### 1.4 通用响应格式
说明响应的通用结构

## 三、错误处理

### 3.1 HTTP状态码
| 状态码 | 说明 |
|--------|------|
| 200 | 成功 |
| 400 | 请求参数错误 |
| 401 | 未认证 |
| 403 | 无权限 |
| 404 | 资源不存在 |
| 500 | 服务器内部错误 |

### 3.2 错误响应格式
```json
{{
  "error": {{
    "code": "ERROR_CODE",
    "message": "错误描述"
  }}
}}
```

## 四、调用示例

### 4.1 使用curl
提供几个典型的curl调用示例

### 4.2 使用Python
```python
import requests

response = requests.post(
    "https://api.example.com/endpoint",
    headers={{"Authorization": "Bearer <token>"}},
    json={{...}}
)
```

### 4.3 使用JavaScript
```javascript
const response = await fetch('/endpoint', {{
  method: 'POST',
  headers: {{
    'Authorization': 'Bearer <token>',
    'Content-Type': 'application/json'
  }},
  body: JSON.stringify({{...}})
}});
```
```

## 注意事项
- 只输出通用部分，不要输出接口详情
- 根据提供的API概况和详情推断认证方式等信息
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
        verify_ssl: bool = True,
    ):
        """
        初始化LLM客户端

        Args:
            api_key: API密钥
            base_url: API基础URL（中转站地址）
            simulate_browser: 是否模拟浏览器请求头
            verify_ssl: 是否验证SSL证书
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
        self._verify_ssl = verify_ssl

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
            f"LLM客户端初始化: base_url={url or '官方API'}, simulate_browser={simulate_browser}, verify_ssl={verify_ssl}"
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
            "max_tokens": max_tokens or 8192,  # 默认8192，避免大型文件分析被截断
        }

        if system_content:
            payload["system"] = system_content
        if temperature is not None:
            payload["temperature"] = temperature

        logger.info(f"Anthropic API请求: endpoint={endpoint}, model={model}")

        try:
            async with httpx.AsyncClient(timeout=float(timeout), verify=self._verify_ssl) as client:
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

        except httpx.TimeoutException as e:
            logger.error(f"Anthropic API超时: model={model}, timeout={timeout}, error={type(e).__name__}")
            raise
        except httpx.HTTPStatusError as e:
            logger.error(f"Anthropic API HTTP错误: model={model}, status={e.response.status_code}, response={e.response.text[:500]}")
            raise
        except Exception as e:
            error_detail = str(e) or repr(e) or type(e).__name__
            if "Anthropic API错误" not in error_detail:
                logger.error(f"Anthropic API失败: model={model}, type={type(e).__name__}, error={error_detail}")
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
            verify_ssl=self.config.verify_ssl,
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

        # 使用配置的max_tokens，但确保不低于CODE_ANALYSIS_MIN_TOKENS
        # 这是为了防止大型文件（如api_server.py）的API信息因token限制被截断
        effective_max_tokens = max(
            self.max_tokens or CODE_ANALYSIS_MIN_TOKENS,
            CODE_ANALYSIS_MIN_TOKENS
        )

        return await self.client.complete(
            prompt=prompt,
            model=self.model,
            temperature=self.temperature,
            max_tokens=effective_max_tokens,
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

        # 最终文档使用更大的max_tokens避免截断
        final_max_tokens = self.max_tokens or FINAL_DOC_MAX_TOKENS

        return await self.client.complete(
            prompt=prompt,
            model=self.model,
            temperature=self.temperature,
            max_tokens=final_max_tokens,
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

        # 最终文档使用更大的max_tokens避免截断
        final_max_tokens = self.max_tokens or FINAL_DOC_MAX_TOKENS

        return await self.client.complete(
            prompt=prompt,
            model=self.model,
            temperature=self.temperature,
            max_tokens=final_max_tokens,
            timeout=self.timeout,
            api_format=self.api_format,
        )

    async def generate_api_doc(
        self,
        project_name: str,
        project_structure: str,
        all_documents: str
    ) -> str:
        """生成API接口文档（旧方法，保留兼容性）"""
        prompt = API_DOC_PROMPT.format(
            project_name=project_name,
            project_structure=project_structure,
            all_documents=all_documents
        )

        # 最终文档使用更大的max_tokens避免截断
        final_max_tokens = self.max_tokens or FINAL_DOC_MAX_TOKENS

        return await self.client.complete(
            prompt=prompt,
            model=self.model,
            temperature=self.temperature,
            max_tokens=final_max_tokens,
            timeout=self.timeout,
            api_format=self.api_format,
        )

    async def extract_api_details(
        self,
        file_path: str,
        file_doc: str
    ) -> str:
        """
        第一阶段：从单个文件提取API接口详情

        Args:
            file_path: 文件路径
            file_doc: 文件的分析文档内容

        Returns:
            提取的接口详情（结构化文本）
        """
        prompt = API_EXTRACT_PROMPT.format(
            file_path=file_path,
            file_doc=file_doc
        )

        # 单文件提取使用较大的max_tokens，避免大型API文件被截断
        return await self.client.complete(
            prompt=prompt,
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens or FINAL_DOC_MAX_TOKENS,
            timeout=self.timeout,
            api_format=self.api_format,
        )

    async def summarize_api_docs(
        self,
        project_name: str,
        api_details: str
    ) -> str:
        """
        第二阶段：汇总所有接口详情生成最终API文档

        Args:
            project_name: 项目名称
            api_details: 所有文件的接口详情汇总

        Returns:
            最终的API接口文档
        """
        prompt = API_SUMMARY_PROMPT.format(
            project_name=project_name,
            api_details=api_details
        )

        # 最终文档使用更大的max_tokens避免截断
        final_max_tokens = self.max_tokens or FINAL_DOC_MAX_TOKENS

        return await self.client.complete(
            prompt=prompt,
            model=self.model,
            temperature=self.temperature,
            max_tokens=final_max_tokens,
            timeout=self.timeout,
            api_format=self.api_format,
        )

    # ============ API使用文档生成方法 ============

    async def extract_api_usage_details(
        self,
        file_path: str,
        file_doc: str
    ) -> str:
        """
        第一阶段：从单个文件提取API使用详情

        Args:
            file_path: 文件路径
            file_doc: 文件的分析文档内容

        Returns:
            提取的API使用详情（包含请求示例、响应示例等）
        """
        prompt = API_USAGE_EXTRACT_PROMPT.format(
            file_path=file_path,
            file_doc=file_doc
        )

        # 使用较大的max_tokens，因为需要生成详细的示例
        # 大型API文件（如agentserver/agent_server.py含27个接口）需要更多tokens
        # 每个接口详情约需400-600 tokens，使用FINAL_DOC_MAX_TOKENS更安全
        return await self.client.complete(
            prompt=prompt,
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens or FINAL_DOC_MAX_TOKENS,
            timeout=self.timeout,
            api_format=self.api_format,
        )

    async def summarize_api_usage_docs(
        self,
        project_name: str,
        api_usage_details: str,
        api_reference_list: str = ""
    ) -> str:
        """
        第二阶段：汇总所有API使用详情生成最终使用文档

        Args:
            project_name: 项目名称
            api_usage_details: 所有文件的API使用详情汇总
            api_reference_list: API接口清单中的接口列表（用于确保完整覆盖）

        Returns:
            最终的API使用文档
        """
        # 如果没有提供接口清单，使用默认提示
        if not api_reference_list:
            api_reference_list = "（未提供接口清单，请根据使用详情生成文档）"

        prompt = API_USAGE_SUMMARY_PROMPT.format(
            project_name=project_name,
            api_usage_details=api_usage_details,
            api_reference_list=api_reference_list
        )

        # 最终文档使用更大的max_tokens避免截断
        final_max_tokens = self.max_tokens or FINAL_DOC_MAX_TOKENS

        return await self.client.complete(
            prompt=prompt,
            model=self.model,
            temperature=self.temperature,
            max_tokens=final_max_tokens,
            timeout=self.timeout,
            api_format=self.api_format,
        )

    # ============ 分批生成API使用文档的方法 ============

    async def generate_api_usage_module(
        self,
        project_name: str,
        module_name: str,
        module_api_list: str,
        api_usage_details: str
    ) -> str:
        """
        生成单个模块的API使用文档

        Args:
            project_name: 项目名称
            module_name: 模块名称（如"核心业务接口"）
            module_api_list: 该模块的接口列表
            api_usage_details: 相关的API使用详情

        Returns:
            该模块的接口使用文档
        """
        prompt = API_USAGE_MODULE_PROMPT.format(
            project_name=project_name,
            module_name=module_name,
            module_api_list=module_api_list,
            api_usage_details=api_usage_details
        )

        # 单个模块也使用较大的max_tokens，避免接口较多时被截断
        # 每个接口的详细描述（功能、认证、参数表、请求示例、响应示例）约需400-600 tokens
        # 10个接口的模块可能需要6000+ tokens，使用FINAL_DOC_MAX_TOKENS更安全
        return await self.client.complete(
            prompt=prompt,
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens or FINAL_DOC_MAX_TOKENS,
            timeout=self.timeout,
            api_format=self.api_format,
        )

    async def generate_api_usage_common(
        self,
        project_name: str,
        api_overview: str,
        api_usage_details: str
    ) -> str:
        """
        生成API使用文档的通用部分

        Args:
            project_name: 项目名称
            api_overview: API概况（接口数量、模块分布等）
            api_usage_details: 部分API使用详情（用于推断认证方式等）

        Returns:
            通用部分的文档内容
        """
        prompt = API_USAGE_COMMON_PROMPT.format(
            project_name=project_name,
            api_overview=api_overview,
            api_usage_details=api_usage_details
        )

        return await self.client.complete(
            prompt=prompt,
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens or 4096,
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
