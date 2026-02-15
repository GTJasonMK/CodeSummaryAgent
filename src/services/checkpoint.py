"""
断点续传服务模块
检测已完成的分析，支持中断恢复
"""
import json
import re
from pathlib import Path
from typing import Dict, Set, Optional, List, Tuple

from src.models.file_node import FileNode, AnalysisStatus, NodeType
from src.models.config import OutputConfig, get_config
from src.utils.logger import get_logger

logger = get_logger(__name__)


def parse_api_info_from_doc(doc_content: str) -> Tuple[bool, Optional[str]]:
    """
    从文档内容中解析API接口信息

    查找 <!-- API_START --> 和 <!-- API_END --> 之间的内容
    如果文档被截断导致结束标记缺失，也能正确识别

    Args:
        doc_content: 文档内容

    Returns:
        (是否包含API, API信息摘要)
    """
    # 方案1: 尝试匹配完整的 API 标记块（开始+结束）
    pattern_complete = r'<!--\s*API_START\s*-->(.*?)<!--\s*API_END\s*-->'
    match = re.search(pattern_complete, doc_content, re.DOTALL | re.IGNORECASE)

    if match:
        api_block = match.group(1).strip()
    else:
        # 方案2: 如果没有结束标记（可能被截断），尝试只匹配开始标记后的内容
        pattern_start_only = r'<!--\s*API_START\s*-->(.*?)$'
        match = re.search(pattern_start_only, doc_content, re.DOTALL | re.IGNORECASE)

        if not match:
            return False, None

        api_block = match.group(1).strip()
        # 记录警告：结束标记缺失，可能是输出被截断
        logger.warning(f"API标记块缺少结束标记(<!-- API_END -->)，可能是LLM输出被截断")

    # 检查是否明确标注不包含API接口
    if "包含API接口: 否" in api_block or "包含API接口：否" in api_block:
        return False, None

    # 检查是否明确标注包含API接口
    if "包含API接口: 是" in api_block or "包含API接口：是" in api_block:
        return True, api_block

    # 检查是否有接口列表关键词
    if "接口列表" in api_block:
        return True, api_block

    # 检查方括号方法标记（通用模式，不硬编码具体方法）
    # 格式: [GET], [POST], [MCP工具], [GraphQL] 等
    bracket_method_pattern = r'\[[A-Za-z\u4e00-\u9fa5_]+\]\s*[/\w]'
    if re.search(bracket_method_pattern, api_block):
        return True, api_block

    # 检查 "- 方法 路径" 格式的列表项
    # 格式: - GET /api/xxx 或 - MCP工具 func_name
    list_item_pattern = r'-\s+[A-Za-z\u4e00-\u9fa5_]+\s+[/\w]'
    if re.search(list_item_pattern, api_block):
        return True, api_block

    # 检查常见的路由模式（作为后备检测）
    route_patterns = [
        r'/api/',           # REST API路径
        r'/v\d+/',          # 版本化API路径 (如 /v1/, /v2/)
        r'@\w+Mapping',     # Spring注解 (@GetMapping, @PostMapping等)
        r'@app\.\w+',       # Flask/FastAPI装饰器
        r'@router\.\w+',    # FastAPI router
    ]

    for route_pattern in route_patterns:
        if re.search(route_pattern, api_block, re.IGNORECASE):
            return True, api_block

    return False, None


def parse_apis_from_info_text(api_info_text: str, source_file: str = "") -> List[Dict[str, str]]:
    """
    从api_info_map的文本值中解析接口列表

    api_info_map的值格式示例：
    ```
    包含API接口: 是
    接口列表:
    - [POST] /download - 下载漫画
    - [MCP工具] switch_devices - 控制三个设备的开关状态
    ```

    Args:
        api_info_text: api_info_map中某个文件的API信息文本
        source_file: 来源文件路径（用于生成模块名）

    Returns:
        接口列表，每个接口包含: {"method": "POST", "path": "/download", "desc": "下载漫画", "module": "xxx"}
    """
    apis = []

    # 从source_file推断模块名
    module = ""
    if source_file:
        # 示例: "apiserver/api_server.py" -> "API服务"
        # 示例: "mqtt_tool/device_switch.py" -> "MQTT设备控制"
        parts = source_file.replace("\\", "/").split("/")
        if len(parts) >= 2:
            module = parts[0]  # 使用目录名作为模块
        else:
            module = Path(source_file).stem

    # 匹配接口行: - [METHOD] path - description
    # 支持: - [POST] /download - 下载漫画
    # 支持: - [MCP工具] switch_devices - 控制设备
    # 支持: - [MCP工具] switch_devices(device1: int, device2: int) - 带参数的函数
    # 支持: - [GET] /api/users/{id} - 获取用户 (带路径参数)
    # 支持: - `[GET] /api/sessions` → description (带反引号格式)
    # 路径部分匹配：非空格非减号的起始部分，加上可选的括号内参数或花括号路径参数
    api_pattern = r'-\s*`?\[([^\]]+)\]\s*([^\s(`-]+(?:\([^)]*\)|(?:\s*\{[^}]+\}))?)`?\s*(?:[-→]\s*(.*))?$'

    for line in api_info_text.split('\n'):
        line = line.strip()
        match = re.match(api_pattern, line)
        if match:
            method = match.group(1).strip()
            path = match.group(2).strip()
            desc = match.group(3).strip() if match.group(3) else ""

            apis.append({
                "method": method,
                "path": path,
                "desc": desc,
                "module": module,
                "source_file": source_file,
            })

    return apis


def extract_all_apis_from_info_map(api_info_map: Dict[str, str]) -> List[Dict[str, str]]:
    """
    从完整的api_info_map中提取所有接口

    Args:
        api_info_map: {文件路径: API信息文本} 的字典

    Returns:
        所有接口的列表，保持顺序
    """
    all_apis = []

    for file_path, info_text in api_info_map.items():
        apis = parse_apis_from_info_text(info_text, file_path)
        all_apis.extend(apis)

    return all_apis


def generate_api_summary_table(apis: List[Dict[str, str]]) -> str:
    """
    程序化生成API接口总览表（Markdown格式）

    Args:
        apis: 接口列表，每个接口包含 method, path, desc, module

    Returns:
        Markdown格式的接口总览表
    """
    if not apis:
        return "## 一、接口总览\n\n无API接口。\n"

    lines = [
        "## 一、接口总览",
        "",
        "| 序号 | 模块 | 方法 | 路径 | 功能描述 | 认证 |",
        "|------|------|------|------|----------|------|",
    ]

    for i, api in enumerate(apis, 1):
        method = api.get("method", "")
        path = api.get("path", "")
        desc = api.get("desc", "")
        module = api.get("module", "")

        # 根据模块名推断更友好的显示名
        module_display = _get_module_display_name(module)

        # 认证默认为"否"，后续可以从详情中提取
        auth = "否"

        lines.append(f"| {i} | {module_display} | {method} | {path} | {desc} | {auth} |")

    lines.append("")
    lines.append(f"**接口总计：{len(apis)} 个**")
    lines.append("")

    return "\n".join(lines)


def _get_module_display_name(module: str) -> str:
    """
    将模块目录名转换为更友好的显示名

    通用规则（无硬编码）：
    - 将下划线替换为空格
    - 首字母大写
    - 保留原有的大小写结构

    Args:
        module: 模块目录名（如 "apiserver", "mqtt_tool"）

    Returns:
        友好的显示名
    """
    if not module:
        return "其他"

    # 将下划线和连字符替换为空格，然后首字母大写
    display = module.replace("_", " ").replace("-", " ")

    # 如果是全小写的驼峰式命名（如 "apiserver"），尝试分割
    # 例如: "apiserver" -> "Api Server"
    if display.islower() and len(display) > 5:
        # 尝试在常见后缀处分割
        for suffix in ["server", "service", "tool", "client", "handler", "manager"]:
            if display.endswith(suffix) and len(display) > len(suffix):
                prefix = display[:-len(suffix)]
                display = f"{prefix} {suffix}"
                break

    # 每个单词首字母大写
    return display.title()


def count_api_in_summary_doc(doc_content: str) -> Tuple[int, List[str]]:
    """
    从API接口清单文档中计算接口数量（程序化提取）

    解析"接口总览"Markdown表格，统计数据行数量。
    表格格式：| 序号 | 模块 | 方法 | 路径 | 功能描述 | 认证 |

    设计原则：
    - 不假设方法类型，任何方法（GET/POST/MCP工具/GraphQL等）都能识别
    - 通过表格结构（序号列为数字）来识别数据行
    - 自动跳过表头和分隔行
    - 不进行去重：同一路径在不同模块中算作不同接口

    Args:
        doc_content: API接口清单文档内容

    Returns:
        (接口数量, 接口列表)
        接口列表格式: ["模块|方法 路径", ...] 包含模块信息以区分不同服务的同名接口
    """
    apis = []
    seen_rows = set()  # 用于去除完全相同的行（防止重复匹配）

    # 方法1: 匹配"接口总览"表格的数据行
    # 表格格式: | 序号 | 模块 | 方法 | 路径 | 功能描述 | 认证 |
    # 数据行特征: 第一列是数字（序号）
    # 正则说明: 匹配 | 数字 | 任意内容 | 方法 | 路径 | 的行
    # 捕获组: (序号) (模块) (方法) (路径)
    table_row_pattern = r'^\|\s*(\d+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|'

    for match in re.finditer(table_row_pattern, doc_content, re.MULTILINE):
        seq_num = match.group(1).strip()   # 序号
        module = match.group(2).strip()    # 模块
        method = match.group(3).strip()    # 方法（任意类型）
        path = match.group(4).strip()      # 路径（URL或函数名）

        # 跳过无效行
        if not method or not path:
            continue

        # 用序号作为唯一标识，防止同一行被重复匹配
        row_key = seq_num
        if row_key in seen_rows:
            continue
        seen_rows.add(row_key)

        # 构建接口标识: "方法 路径"（不包含模块，保持与使用文档一致的格式）
        api_str = f"{method} {path}"
        apis.append(api_str)  # 不去重，保留所有接口

    # 方法2: 如果没有匹配到带序号的表格，尝试匹配简单表格
    # 格式: | 方法 | 路径 | 描述 | ... |
    if not apis:
        # 匹配任意表格行，提取前两个非空列作为方法和路径
        simple_table_pattern = r'^\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|'
        for match in re.finditer(simple_table_pattern, doc_content, re.MULTILINE):
            col1 = match.group(1).strip()
            col2 = match.group(2).strip()

            # 跳过表头行和分隔行
            if col1 in ('方法', '序号', '---', '') or col1.startswith('-'):
                continue
            if col2 in ('路径', '---', '') or col2.startswith('-'):
                continue

            api_str = f"{col1} {col2}"
            apis.append(api_str)  # 不去重

    # 方法3: 如果表格都匹配不到，尝试匹配列表格式
    # 格式: - `GET /api/xxx` 或 - GET /api/xxx
    if not apis:
        list_pattern = r'-\s*`?([A-Za-z\u4e00-\u9fa5]+)\s+([^\s`]+)`?'
        list_matches = re.findall(list_pattern, doc_content)
        for method, path in list_matches:
            # 跳过明显不是接口的行
            if method.lower() in ('the', 'a', 'an', 'is', 'are'):
                continue
            api_str = f"{method} {path}"
            apis.append(api_str)  # 不去重

    return len(apis), apis


def count_api_in_usage_doc(doc_content: str) -> Tuple[int, List[str]]:
    """
    从API使用文档中计算接口数量（语义结构检测 + 表格检测）

    核心设计原则：
    - 基于文档语义结构检测，而非硬编码方法类型
    - API接口标题的本质特征：后面紧跟 **功能** 和 **认证** 等元数据字段
    - 同时检测表格中的接口作为补充
    - 不同模块的相同路径视为不同接口（不去重）

    检测策略：
    1. 语义检测：找到所有Markdown标题，检查后面是否有API元数据
    2. 表格检测：匹配"接口列表"表格中的方法和路径（补充策略）
    3. 表格检测结果与标题检测结果合并时去重，避免重复计数

    Args:
        doc_content: API使用文档内容

    Returns:
        (接口数量, 接口列表)
        接口列表格式: ["GET /api/users", "MCP工具 switch_devices", ...]
    """
    apis = []
    heading_apis_set = set()  # 用于记录标题检测到的接口（避免表格重复）

    # ========== 策略1: 语义结构检测 ==========
    # 将文档按行分割，便于检查标题后的内容
    lines = doc_content.split('\n')

    # API元数据特征：这些是API接口文档必有的描述字段
    # 只要标题后面出现这些字段，就说明这是一个API接口
    # 注意：Markdown加粗格式是 **文字**，冒号在后面
    api_metadata_patterns = [
        r'\*\*功能(?:描述)?\*\*(?:：|:)',  # **功能描述**： 或 **功能**：
        r'\*\*认证(?:要求)?\*\*(?:：|:)',  # **认证要求**： 或 **认证**：
    ]
    # 编译为单个正则表达式
    api_metadata_regex = re.compile('|'.join(api_metadata_patterns), re.IGNORECASE)

    # 遍历每一行，找到标题
    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # 检查是否是2-4级标题
        heading_match = re.match(r'^(#{2,4})\s+(.+)$', line)
        if not heading_match:
            i += 1
            continue

        heading_content = heading_match.group(2).strip()

        # 检查标题后的内容（最多看50行，足够覆盖API描述块）
        lookahead_text = '\n'.join(lines[i+1:i+51])

        # 检查是否包含API元数据特征
        if not api_metadata_regex.search(lookahead_text):
            i += 1
            continue

        # 确认这是API标题，现在提取方法和路径
        # 支持多种格式：
        # - "POST /schedule"
        # - "MCP工具: switch_devices" 或 "MCP工具：switch_devices"
        # - "GET /detail/{comic_id}"
        # - "接口1: [GET] /health"  （程序化组装格式，带方括号）
        # - "接口1: [MCP工具] switch_devices"  （程序化组装格式，带方括号）
        # - "接口1: GET /api/sessions"  （LLM生成格式，无方括号）

        method = None
        path = None

        # 模式1: "接口N: [METHOD] /path" 或 "接口N: [METHOD] func_name"（程序化组装格式，带方括号）
        programmatic_pattern = r'^接口\d+\s*[:\s]\s*\[([^\]]+)\]\s*(.+)$'
        prog_match = re.match(programmatic_pattern, heading_content)
        if prog_match:
            method = prog_match.group(1).strip()
            path = prog_match.group(2).strip()
        else:
            # 模式2: "接口N: METHOD /path"（LLM生成格式，无方括号）
            # 例如: "接口1: GET /api/sessions" 或 "接口1: POST /download"
            llm_format_pattern = r'^接口\d+\s*[:\s]\s*(GET|POST|PUT|DELETE|PATCH|MCP工具)\s+(.+)$'
            llm_match = re.match(llm_format_pattern, heading_content, re.IGNORECASE)

            if llm_match:
                method = llm_match.group(1).strip()
                path = llm_match.group(2).strip()
            else:
                # 模式3: 方法 + 路径（用空格或冒号分隔）
                # [^\s:]+ 匹配方法名（不含空格和冒号）
                # [:\s]+ 匹配分隔符（冒号或空格）
                # 路径可以是 /xxx 或 函数名
                api_pattern = r'^([^\s:]+)[:\s]+(/[^\s]*|[a-zA-Z_][a-zA-Z0-9_]*)(?:\s|$)'
                api_match = re.match(api_pattern, heading_content)

                if api_match:
                    method = api_match.group(1).strip()
                    path = api_match.group(2).strip()

        # 基本验证 - 不去重，每个标题都计为一个接口
        if method and path:
            api_str = f"{method} {path}"
            apis.append(api_str)
            heading_apis_set.add(api_str.upper())

        i += 1

    # ========== 策略2: 表格检测（补充） ==========
    # 匹配接口列表表格：| 方法 | 路径 | 描述 | 或 | 序号 | 模块 | 方法 | 路径 |
    # 注意：只添加不在标题中出现的接口，避免重复

    # 格式1: | 方法 | 路径 | ... |（简单两列）
    table_pattern_simple = r'^\|\s*([^|]+)\s*\|\s*(/[^|]+)\s*\|'
    for match in re.finditer(table_pattern_simple, doc_content, re.MULTILINE):
        method = match.group(1).strip()
        path = match.group(2).strip()

        # 跳过表头行和分隔行
        if method.lower() in ('方法', 'method', '---', '序号') or method.startswith('-'):
            continue
        if path.lower() in ('路径', 'path', '---') or path.startswith('-'):
            continue

        if method and path:
            api_str = f"{method} {path}"
            # 只添加表格中未在标题中出现的接口
            if api_str.upper() not in heading_apis_set:
                apis.append(api_str)

    return len(apis), apis


def parse_api_by_module(doc_content: str) -> Dict[str, List[str]]:
    """
    从API接口清单文档中按模块提取接口列表

    解析Markdown文档结构，识别模块标题和对应的接口列表。
    支持多种模块标题格式：
    - ### 2.1 核心业务接口（带编号，以"接口"结尾）
    - ### 2.1 核心业务（带编号，不以"接口"结尾）
    - ### 核心业务接口（不带编号）

    Args:
        doc_content: API接口清单文档内容

    Returns:
        模块名 -> 接口列表 的字典
        例如: {"核心业务接口": ["POST /v1/chat/completions"], "会话管理接口": [...]}
    """
    modules: Dict[str, List[str]] = {}
    current_module = None
    current_apis: List[str] = []

    lines = doc_content.split('\n')

    for line in lines:
        stripped_line = line.strip()

        # 检查是否是模块标题（三级标题，且不是表格行）
        if stripped_line.startswith('###') and '|' not in stripped_line:
            # 尝试多种格式匹配

            # 格式1: ### 2.1 模块名 或 ### 2.1.1 模块名（带编号）
            module_match = re.match(r'^###\s+\d+(?:\.\d+)+\s+(.+)$', stripped_line)

            # 格式2: ### 模块名（不带编号，排除纯数字开头）
            if not module_match:
                module_match = re.match(r'^###\s+([^\d#].*)$', stripped_line)

            if module_match:
                # 保存前一个模块的接口
                if current_module and current_apis:
                    modules[current_module] = current_apis

                current_module = module_match.group(1).strip()
                current_apis = []
                continue

        # 如果在模块内，尝试匹配表格行中的接口
        if current_module:
            # 匹配表格行: | 方法 | 路径 | 描述 | 认证 |
            # 不假设方法类型，匹配任意非空的方法和路径
            table_match = re.match(
                r'\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|',
                stripped_line
            )
            if table_match:
                method = table_match.group(1).strip()
                path = table_match.group(2).strip()

                # 跳过表头行和分隔行
                if method in ('方法', '---', '') or method.startswith('-'):
                    continue
                if path in ('路径', '---', '') or path.startswith('-'):
                    continue

                # 构建接口标识
                if method and path:
                    api_str = f"{method} {path}"
                    if api_str not in current_apis:
                        current_apis.append(api_str)

    # 保存最后一个模块
    if current_module and current_apis:
        modules[current_module] = current_apis

    return modules


def _normalize_api_for_comparison(api: str) -> str:
    """
    标准化API接口名称用于比较

    处理以下情况：
    1. 去除MCP工具的参数签名: switch_devices(device1: int) -> switch_devices
    2. 去除路径参数的类型标注: /detail/{comic_id} -> /detail/{comic_id}
    3. 统一大小写

    Args:
        api: API接口字符串，格式为 "方法 路径"

    Returns:
        标准化后的字符串
    """
    # 转换为大写
    api_upper = api.upper()

    # 分离方法和路径
    parts = api_upper.split(None, 1)
    if len(parts) != 2:
        return api_upper

    method, path = parts

    # 去除函数参数签名 (适用于MCP工具)
    # 例如: SWITCH_DEVICES(DEVICE1: INT, DEVICE2: INT) -> SWITCH_DEVICES
    if '(' in path:
        path = path.split('(')[0]

    return f"{method} {path}"


def compare_api_counts(
    summary_count: int,
    summary_apis: List[str],
    usage_count: int,
    usage_apis: List[str]
) -> Tuple[bool, str]:
    """
    比较两个文档中的接口数量，检测差异

    比较逻辑：
    1. 首先标准化接口名称（去除MCP工具的参数签名）
    2. 使用计数器比较每个接口的出现次数
    3. 不同模块的相同路径视为不同接口（如 agentserver 和 apiserver 各有 GET /health）

    Args:
        summary_count: API接口清单中的接口数量
        summary_apis: API接口清单中的接口列表
        usage_count: API使用文档中的接口数量
        usage_apis: API使用文档中的接口列表

    Returns:
        (是否一致, 差异报告)
    """
    from collections import Counter

    # 标准化后统计每个接口的出现次数
    summary_normalized = [_normalize_api_for_comparison(api) for api in summary_apis]
    usage_normalized = [_normalize_api_for_comparison(api) for api in usage_apis]

    summary_counter = Counter(summary_normalized)
    usage_counter = Counter(usage_normalized)

    # 比较计数器是否相等
    if summary_counter == usage_counter:
        return True, f"接口数量一致: {summary_count} 个接口"

    # 找出差异
    all_apis = set(summary_counter.keys()) | set(usage_counter.keys())

    report_lines = [
        f"接口数量不一致:",
        f"  - API接口清单: {summary_count} 个接口",
        f"  - API使用文档: {usage_count} 个接口",
        f"",
        f"  详细差异:",
    ]

    missing_in_usage = []  # 在 API_USAGE 中缺少的接口
    extra_in_usage = []    # 在 API_USAGE 中多余的接口

    for api in sorted(all_apis):
        summary_num = summary_counter.get(api, 0)
        usage_num = usage_counter.get(api, 0)

        if summary_num > usage_num:
            diff = summary_num - usage_num
            missing_in_usage.append(f"{api} (缺少 {diff} 个)")
        elif usage_num > summary_num:
            diff = usage_num - summary_num
            extra_in_usage.append(f"{api} (多出 {diff} 个)")

    if missing_in_usage:
        report_lines.append(f"  API使用文档中缺少的接口:")
        for item in missing_in_usage:
            report_lines.append(f"    - {item}")

    if extra_in_usage:
        report_lines.append(f"  API使用文档中多余的接口:")
        for item in extra_in_usage:
            report_lines.append(f"    - {item}")

    return False, "\n".join(report_lines)


from dataclasses import dataclass, asdict, field


@dataclass
class CheckpointData:
    """断点数据"""
    source_root: str                    # 源代码根目录
    docs_root: str                      # 文档根目录
    completed_files: List[str]          # 已完成的文件列表（相对路径）
    completed_dirs: List[str]           # 已完成的目录列表（相对路径）
    failed_files: List[str]             # 失败的文件列表
    # 最终文档完成状态
    readme_completed: bool = False      # README文档是否已完成
    reading_guide_completed: bool = False  # 阅读指南是否已完成
    api_doc_completed: bool = False     # API接口清单文档是否已完成
    api_usage_doc_completed: bool = False  # API使用文档是否已完成
    # API接口文件信息
    api_files: List[str] = field(default_factory=list)  # 包含API接口的文件列表
    api_info_map: Dict[str, str] = field(default_factory=dict)  # 文件路径 -> API信息摘要
    # 两阶段API文档生成：存储每个文件的接口详情（中间结果）
    api_details_map: Dict[str, str] = field(default_factory=dict)  # 文件路径 -> 接口详情
    # 两阶段API使用文档生成：存储每个文件的使用详情（中间结果）
    api_usage_details_map: Dict[str, str] = field(default_factory=dict)  # 文件路径 -> 使用详情
    version: str = "1.4"                # 数据版本（升级以支持新字段）


class CheckpointService:
    """
    断点续传服务

    功能：
    - 检测已完成的分析文件
    - 保存/恢复分析进度
    - 支持中断后继续分析
    - 预创建文档目录结构（增量更新策略）
    """

    CHECKPOINT_FILE = ".checkpoint.json"

    def __init__(
        self,
        source_root: str,
        docs_root: Optional[str] = None,
        config: Optional[OutputConfig] = None,
    ):
        """
        初始化断点续传服务

        Args:
            source_root: 源代码根目录
            docs_root: 文档根目录，为None则自动生成
            config: 输出配置
        """
        self.config = config or get_config().output
        self.source_root = Path(source_root).resolve()

        if docs_root:
            self.docs_root = Path(docs_root).resolve()
        else:
            # 自动生成文档目录
            docs_dir_name = self.source_root.name + self.config.docs_suffix
            if self.config.docs_inside_source:
                # 文档目录在源代码内部: c:/a/b -> c:/a/b/b_docs
                self.docs_root = self.source_root / docs_dir_name
            else:
                # 文档目录与源代码平级: c:/a/b -> c:/a/b_docs
                self.docs_root = self.source_root.parent / docs_dir_name

        # 已完成文件的相对路径集合
        self._completed_files: Set[str] = set()
        self._completed_dirs: Set[str] = set()
        self._failed_files: Set[str] = set()

        # 最终文档完成状态
        self._readme_completed: bool = False
        self._reading_guide_completed: bool = False
        self._api_doc_completed: bool = False
        self._api_usage_doc_completed: bool = False

        # API接口文件信息
        self._api_files: Set[str] = set()  # 包含API接口的文件列表
        self._api_info_map: Dict[str, str] = {}  # 文件路径 -> API信息摘要
        # 两阶段API文档生成：存储每个文件的接口详情（中间结果）
        self._api_details_map: Dict[str, str] = {}  # 文件路径 -> 接口详情
        # 两阶段API使用文档生成：存储每个文件的使用详情（中间结果）
        self._api_usage_details_map: Dict[str, str] = {}  # 文件路径 -> 使用详情

        # 文件路径映射：源文件相对路径 -> 文档路径
        self._doc_path_map: Dict[str, str] = {}

        # 是否启用实时保存
        self._auto_save = True

    def initialize(self) -> None:
        """初始化服务，创建必要的目录"""
        self.docs_root.mkdir(parents=True, exist_ok=True)
        logger.info(f"文档输出目录: {self.docs_root}")

    def create_doc_structure(self, root: FileNode) -> None:
        """
        预创建文档目录结构（增量更新策略的核心）

        根据源代码的目录结构，预先创建对应的文档目录，
        这样后续保存文档时不需要再创建目录。

        Args:
            root: 文件树根节点
        """
        logger.info("预创建文档目录结构...")

        # 获取所有目录节点
        all_dirs = root.get_all_dirs()

        created_count = 0
        for dir_node in all_dirs:
            # 计算对应的文档目录路径
            if dir_node.relative_path:
                doc_dir = self.docs_root / dir_node.relative_path
            else:
                doc_dir = self.docs_root

            # 创建目录
            if not doc_dir.exists():
                doc_dir.mkdir(parents=True, exist_ok=True)
                created_count += 1

        logger.info(f"已创建 {created_count} 个文档目录")

    def doc_exists(self, node: FileNode) -> bool:
        """
        检查节点对应的文档文件是否真实存在

        Args:
            node: 文件节点

        Returns:
            文档文件是否存在
        """
        doc_path = self.generate_doc_path(node)
        return Path(doc_path).exists()

    def is_completed(self, node: FileNode, verify_file: bool = True) -> bool:
        """
        检查节点是否已完成分析

        Args:
            node: 文件节点
            verify_file: 是否同时验证文档文件存在

        Returns:
            是否已完成
        """
        # 首先检查内存记录
        if node.is_file:
            in_record = node.relative_path in self._completed_files
        else:
            in_record = node.relative_path in self._completed_dirs

        if not in_record:
            return False

        # 如果需要验证文件存在性
        if verify_file:
            return self.doc_exists(node)

        return True

    def get_missing_nodes(self, root: FileNode) -> tuple[List[FileNode], List[FileNode]]:
        """
        获取缺失文档的节点列表

        Args:
            root: 文件树根节点

        Returns:
            (缺失文档的文件列表, 缺失文档的目录列表)
        """
        missing_files = []
        missing_dirs = []

        # 检查所有文件
        for file_node in root.get_all_files():
            if not self.doc_exists(file_node):
                missing_files.append(file_node)

        # 检查所有目录
        for dir_node in root.get_all_dirs():
            if not self.doc_exists(dir_node):
                missing_dirs.append(dir_node)

        logger.info(
            f"发现 {len(missing_files)} 个文件和 {len(missing_dirs)} 个目录缺少文档"
        )

        return missing_files, missing_dirs

    def load_checkpoint(self) -> bool:
        """
        加载断点数据

        Returns:
            是否成功加载
        """
        checkpoint_path = self.docs_root / self.CHECKPOINT_FILE

        if not checkpoint_path.exists():
            logger.info("未找到断点文件，将从头开始分析")
            return False

        try:
            with open(checkpoint_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # 兼容旧版本数据（没有最终文档状态字段）
            data.setdefault("readme_completed", False)
            data.setdefault("reading_guide_completed", False)
            data.setdefault("api_doc_completed", False)
            data.setdefault("api_usage_doc_completed", False)
            data.setdefault("api_files", [])
            data.setdefault("api_info_map", {})
            data.setdefault("api_details_map", {})
            data.setdefault("api_usage_details_map", {})

            checkpoint = CheckpointData(**data)

            # 验证源目录是否匹配
            if checkpoint.source_root != str(self.source_root):
                logger.warning("断点文件的源目录不匹配，将从头开始分析")
                return False

            self._completed_files = set(checkpoint.completed_files)
            self._completed_dirs = set(checkpoint.completed_dirs)
            self._failed_files = set(checkpoint.failed_files)

            # 统一路径分隔符（兼容旧版本checkpoint文件）
            self._completed_files = {p.replace("\\", "/") for p in self._completed_files}
            self._completed_dirs = {p.replace("\\", "/") for p in self._completed_dirs}
            self._failed_files = {p.replace("\\", "/") for p in self._failed_files}

            # 加载最终文档状态
            self._readme_completed = checkpoint.readme_completed
            self._reading_guide_completed = checkpoint.reading_guide_completed
            self._api_doc_completed = checkpoint.api_doc_completed
            self._api_usage_doc_completed = checkpoint.api_usage_doc_completed

            # 加载API文件信息（统一路径分隔符）
            self._api_files = {p.replace("\\", "/") for p in checkpoint.api_files}
            self._api_info_map = {
                k.replace("\\", "/"): v for k, v in checkpoint.api_info_map.items()
            }
            # 加载API接口详情（两阶段生成的中间结果）
            self._api_details_map = {
                k.replace("\\", "/"): v for k, v in checkpoint.api_details_map.items()
            }
            # 加载API使用详情（两阶段生成的中间结果）
            self._api_usage_details_map = {
                k.replace("\\", "/"): v for k, v in checkpoint.api_usage_details_map.items()
            }

            logger.info(
                f"已加载断点: {len(self._completed_files)} 个文件, "
                f"{len(self._completed_dirs)} 个目录已完成, "
                f"{len(self._api_files)} 个文件包含API"
            )
            return True

        except Exception as e:
            logger.warning(f"加载断点文件失败: {e}")
            return False

    def save_checkpoint(self) -> None:
        """保存断点数据"""
        checkpoint = CheckpointData(
            source_root=str(self.source_root),
            docs_root=str(self.docs_root),
            completed_files=list(self._completed_files),
            completed_dirs=list(self._completed_dirs),
            failed_files=list(self._failed_files),
            readme_completed=self._readme_completed,
            reading_guide_completed=self._reading_guide_completed,
            api_doc_completed=self._api_doc_completed,
            api_usage_doc_completed=self._api_usage_doc_completed,
            api_files=list(self._api_files),
            api_info_map=self._api_info_map,
            api_details_map=self._api_details_map,
            api_usage_details_map=self._api_usage_details_map,
        )

        checkpoint_path = self.docs_root / self.CHECKPOINT_FILE

        try:
            with open(checkpoint_path, "w", encoding="utf-8") as f:
                json.dump(asdict(checkpoint), f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存断点文件失败: {e}")

    def scan_existing_docs(self) -> None:
        """
        扫描已存在的文档文件

        通过扫描文档目录来恢复已完成状态，同时从文档内容中恢复API信息。
        新的目录结构：
        - 文件文档: src/utils/helper.py.md
        - 目录文档: src/utils/_dir_summary.md
        """
        if not self.docs_root.exists():
            return

        dir_summary_name = self.config.dir_summary_name
        api_recovered_count = 0

        for doc_path in self.docs_root.rglob("*.md"):
            if not doc_path.is_file():
                continue

            # 获取相对于文档根目录的路径
            try:
                relative_doc_path = doc_path.relative_to(self.docs_root)
            except ValueError:
                continue

            name = doc_path.name

            # 检查是否是目录总结文档
            if name == dir_summary_name:
                # 目录的相对路径就是文档的父目录路径
                dir_relative = str(relative_doc_path.parent)
                if dir_relative == ".":
                    dir_relative = ""
                # 统一使用正斜杠
                dir_relative = dir_relative.replace("\\", "/")
                self._completed_dirs.add(dir_relative)
                self._doc_path_map[dir_relative] = str(doc_path)

            # 检查是否是文件总结文档 (xxx.py.md -> xxx.py)
            elif name.endswith(".md"):
                # 移除 .md 后缀得到原文件名
                original_name = name[:-3]
                # 构建源文件的相对路径
                file_relative = str(relative_doc_path.parent / original_name)
                if file_relative.startswith(".\\") or file_relative.startswith("./"):
                    file_relative = file_relative[2:]
                # 统一使用正斜杠
                file_relative = file_relative.replace("\\", "/")
                self._completed_files.add(file_relative)
                self._doc_path_map[file_relative] = str(doc_path)

                # 从文档内容中恢复API信息（仅当未从checkpoint加载时）
                if file_relative not in self._api_files:
                    try:
                        with open(doc_path, "r", encoding="utf-8") as f:
                            doc_content = f.read()

                        has_api, api_info = parse_api_info_from_doc(doc_content)
                        if has_api and api_info:
                            self._api_files.add(file_relative)
                            self._api_info_map[file_relative] = api_info
                            api_recovered_count += 1
                            logger.debug(f"从文档恢复API信息: {file_relative}")
                    except Exception as e:
                        logger.warning(f"读取文档恢复API信息失败: {doc_path}, {e}")

        logger.info(
            f"扫描到 {len(self._completed_files)} 个已完成的文件文档, "
            f"{len(self._completed_dirs)} 个目录文档"
        )
        if api_recovered_count > 0:
            logger.info(f"从文档内容恢复了 {api_recovered_count} 个文件的API信息")

    def mark_completed(self, node: FileNode, doc_path: str, auto_save: bool = True) -> None:
        """
        标记节点为已完成

        Args:
            node: 文件节点
            doc_path: 生成的文档路径
            auto_save: 是否立即保存checkpoint（增量更新策略）
        """
        if node.is_file:
            self._completed_files.add(node.relative_path)
        else:
            self._completed_dirs.add(node.relative_path)

        self._doc_path_map[node.relative_path] = doc_path
        node.doc_path = doc_path
        node.status = AnalysisStatus.COMPLETED

        # 移除失败记录（如果有）
        self._failed_files.discard(node.relative_path)

        # 实时保存checkpoint，确保进度不丢失
        if auto_save and self._auto_save:
            self.save_checkpoint()
            logger.debug(f"已保存checkpoint: {node.relative_path}")

    def mark_failed(self, node: FileNode, error: str, auto_save: bool = True) -> None:
        """
        标记节点为失败

        Args:
            node: 文件节点
            error: 错误信息
            auto_save: 是否立即保存checkpoint
        """
        self._failed_files.add(node.relative_path)
        node.status = AnalysisStatus.FAILED
        node.error_message = error

        # 实时保存checkpoint
        if auto_save and self._auto_save:
            self.save_checkpoint()

    def get_doc_path(self, node: FileNode) -> Optional[str]:
        """
        获取节点对应的文档路径

        Args:
            node: 文件节点

        Returns:
            文档路径，未生成则返回None
        """
        return self._doc_path_map.get(node.relative_path)

    def generate_doc_path(self, node: FileNode) -> str:
        """
        生成节点对应的文档保存路径

        新的目录结构：
        - 文件: src/utils/helper.py -> docs/src/utils/helper.py.md
        - 目录: src/utils -> docs/src/utils/_dir_summary.md

        Args:
            node: 文件节点

        Returns:
            文档保存路径
        """
        if node.is_file:
            # 文件文档：保持目录结构，文件名加 .md
            doc_name = f"{node.name}.md"
            parent_path = Path(node.relative_path).parent
            if parent_path == Path("."):
                return str(self.docs_root / doc_name)
            else:
                return str(self.docs_root / parent_path / doc_name)
        else:
            # 目录文档：在目录下生成 _dir_summary.md
            dir_summary_name = self.config.dir_summary_name
            if not node.relative_path:
                return str(self.docs_root / dir_summary_name)
            else:
                return str(self.docs_root / node.relative_path / dir_summary_name)

    def get_readme_path(self) -> str:
        """获取README文档路径"""
        return str(self.docs_root / self.config.readme_name)

    def update_node_status(self, root: FileNode) -> int:
        """
        更新文件树节点的状态（根据已完成记录）

        Args:
            root: 根节点

        Returns:
            已标记为完成的节点数
        """
        count = 0

        def update(node: FileNode) -> None:
            nonlocal count
            if self.is_completed(node):
                node.status = AnalysisStatus.COMPLETED
                node.doc_path = self._doc_path_map.get(node.relative_path)
                count += 1

                # 恢复API信息
                if node.relative_path in self._api_files:
                    node.has_api = True
                    node.api_info = self._api_info_map.get(node.relative_path)

            elif node.relative_path in self._failed_files:
                node.status = AnalysisStatus.FAILED

            for child in node.children:
                update(child)

        update(root)
        return count

    @property
    def completed_count(self) -> int:
        """已完成的文件数"""
        return len(self._completed_files)

    @property
    def failed_count(self) -> int:
        """失败的文件数"""
        return len(self._failed_files)

    # ============ 最终文档状态管理 ============

    def is_readme_completed(self, verify_file: bool = True) -> bool:
        """
        检查README文档是否已完成

        Args:
            verify_file: 是否验证文件存在

        Returns:
            是否已完成
        """
        if not self._readme_completed:
            return False
        if verify_file:
            readme_path = self.docs_root / self.config.readme_name
            return readme_path.exists()
        return True

    def is_reading_guide_completed(self, verify_file: bool = True) -> bool:
        """
        检查阅读指南是否已完成

        Args:
            verify_file: 是否验证文件存在

        Returns:
            是否已完成
        """
        if not self._reading_guide_completed:
            return False
        if verify_file:
            guide_path = self.docs_root / self.config.reading_guide_name
            return guide_path.exists()
        return True

    def is_api_doc_completed(self, verify_file: bool = True) -> bool:
        """
        检查API文档是否已完成

        Args:
            verify_file: 是否验证文件存在

        Returns:
            是否已完成
        """
        if not self._api_doc_completed:
            return False

        # 检查是否所有API文件都有详情（防止部分提取失败后跳过重新提取）
        for api_file in self._api_files:
            if api_file not in self._api_details_map:
                logger.debug(f"API文件缺少详情，需要重新生成: {api_file}")
                return False

        if verify_file:
            api_path = self.docs_root / self.config.api_doc_name
            return api_path.exists()
        return True

    def mark_readme_completed(self, auto_save: bool = True) -> None:
        """标记README文档为已完成"""
        self._readme_completed = True
        if auto_save and self._auto_save:
            self.save_checkpoint()

    def mark_reading_guide_completed(self, auto_save: bool = True) -> None:
        """标记阅读指南为已完成"""
        self._reading_guide_completed = True
        if auto_save and self._auto_save:
            self.save_checkpoint()

    def mark_api_doc_completed(self, auto_save: bool = True) -> None:
        """标记API文档为已完成"""
        self._api_doc_completed = True
        if auto_save and self._auto_save:
            self.save_checkpoint()

    def scan_final_docs(self) -> None:
        """
        扫描已存在的最终文档，更新完成状态

        在没有checkpoint文件时，通过扫描文件系统恢复状态
        """
        readme_path = self.docs_root / self.config.readme_name
        if readme_path.exists():
            self._readme_completed = True
            logger.debug(f"检测到已存在的README: {readme_path}")

        guide_path = self.docs_root / self.config.reading_guide_name
        if guide_path.exists():
            self._reading_guide_completed = True
            logger.debug(f"检测到已存在的阅读指南: {guide_path}")

        api_path = self.docs_root / self.config.api_doc_name
        if api_path.exists():
            self._api_doc_completed = True
            logger.debug(f"检测到已存在的API接口清单: {api_path}")

        api_usage_path = self.docs_root / self.config.api_usage_doc_name
        if api_usage_path.exists():
            self._api_usage_doc_completed = True
            logger.debug(f"检测到已存在的API使用文档: {api_usage_path}")

    # ============ API文件管理 ============

    def mark_has_api(self, node: FileNode, api_info: str, auto_save: bool = True) -> None:
        """
        标记文件包含API接口

        Args:
            node: 文件节点
            api_info: API接口信息摘要
            auto_save: 是否自动保存checkpoint
        """
        # 如果是新发现的API文件，需要重新生成API文档
        is_new_api_file = node.relative_path not in self._api_files

        self._api_files.add(node.relative_path)
        self._api_info_map[node.relative_path] = api_info
        node.has_api = True
        node.api_info = api_info

        # 新发现的API文件应该触发API文档和API使用文档重新生成
        if is_new_api_file:
            if self._api_doc_completed:
                self._api_doc_completed = False
                logger.info(f"发现新的API文件，API接口文档需要重新生成: {node.relative_path}")
            if self._api_usage_doc_completed:
                self._api_usage_doc_completed = False
                logger.info(f"发现新的API文件，API使用文档需要重新生成: {node.relative_path}")

        if auto_save and self._auto_save:
            self.save_checkpoint()

    def get_api_files(self) -> List[str]:
        """获取所有包含API接口的文件路径列表"""
        return list(self._api_files)

    def get_api_info(self, relative_path: str) -> Optional[str]:
        """获取指定文件的API信息摘要"""
        # 统一路径分隔符
        path = relative_path.replace("\\", "/")
        return self._api_info_map.get(path)

    def get_all_api_info(self) -> Dict[str, str]:
        """获取所有文件的API信息摘要（用于程序化生成接口总览）"""
        return dict(self._api_info_map)

    def get_doc_path_by_relative(self, relative_path: str) -> Optional[str]:
        """根据源文件相对路径获取文档路径"""
        # 统一路径分隔符
        path = relative_path.replace("\\", "/")
        return self._doc_path_map.get(path)

    def has_api_files(self) -> bool:
        """是否存在包含API接口的文件"""
        return len(self._api_files) > 0

    @property
    def api_file_count(self) -> int:
        """包含API接口的文件数量"""
        return len(self._api_files)

    # ============ 两阶段API文档生成：接口详情管理 ============

    def save_api_details(self, relative_path: str, details: str, auto_save: bool = True) -> None:
        """
        保存单个文件的API接口详情（两阶段生成的中间结果）

        Args:
            relative_path: 文件相对路径
            details: LLM提取的接口详情
            auto_save: 是否自动保存checkpoint
        """
        # 统一路径分隔符
        path = relative_path.replace("\\", "/")
        self._api_details_map[path] = details

        if auto_save and self._auto_save:
            self.save_checkpoint()
            logger.debug(f"已保存API详情: {path}")

    def get_api_details(self, relative_path: str) -> Optional[str]:
        """
        获取单个文件的API接口详情

        Args:
            relative_path: 文件相对路径

        Returns:
            接口详情，不存在则返回None
        """
        path = relative_path.replace("\\", "/")
        return self._api_details_map.get(path)

    def has_api_details(self, relative_path: str) -> bool:
        """
        检查是否已有该文件的API接口详情

        Args:
            relative_path: 文件相对路径

        Returns:
            是否已存在详情
        """
        path = relative_path.replace("\\", "/")
        return path in self._api_details_map

    def get_all_api_details(self) -> Dict[str, str]:
        """获取所有文件的API接口详情"""
        return dict(self._api_details_map)

    def clear_api_details(self, auto_save: bool = True) -> None:
        """
        清空所有API接口详情（用于强制重新生成）

        Args:
            auto_save: 是否自动保存checkpoint
        """
        self._api_details_map.clear()
        if auto_save and self._auto_save:
            self.save_checkpoint()

    # ============ API使用文档状态管理 ============

    def is_api_usage_doc_completed(self, verify_file: bool = True) -> bool:
        """
        检查API使用文档是否已完成

        Args:
            verify_file: 是否验证文件存在

        Returns:
            是否已完成
        """
        if not self._api_usage_doc_completed:
            return False

        # 检查是否所有API文件都有使用详情（防止部分提取失败后跳过重新提取）
        for api_file in self._api_files:
            if api_file not in self._api_usage_details_map:
                logger.debug(f"API文件缺少使用详情，需要重新生成: {api_file}")
                return False

        if verify_file:
            api_usage_path = self.docs_root / self.config.api_usage_doc_name
            return api_usage_path.exists()
        return True

    def mark_api_usage_doc_completed(self, auto_save: bool = True) -> None:
        """标记API使用文档为已完成"""
        self._api_usage_doc_completed = True
        if auto_save and self._auto_save:
            self.save_checkpoint()

    # ============ 两阶段API使用文档生成：使用详情管理 ============

    def save_api_usage_details(self, relative_path: str, details: str, auto_save: bool = True) -> None:
        """
        保存单个文件的API使用详情（两阶段生成的中间结果）

        Args:
            relative_path: 文件相对路径
            details: LLM提取的使用详情
            auto_save: 是否自动保存checkpoint
        """
        # 统一路径分隔符
        path = relative_path.replace("\\", "/")
        self._api_usage_details_map[path] = details

        if auto_save and self._auto_save:
            self.save_checkpoint()
            logger.debug(f"已保存API使用详情: {path}")

    def get_api_usage_details(self, relative_path: str) -> Optional[str]:
        """
        获取单个文件的API使用详情

        Args:
            relative_path: 文件相对路径

        Returns:
            使用详情，不存在则返回None
        """
        path = relative_path.replace("\\", "/")
        return self._api_usage_details_map.get(path)

    def has_api_usage_details(self, relative_path: str) -> bool:
        """
        检查是否已有该文件的API使用详情

        Args:
            relative_path: 文件相对路径

        Returns:
            是否已存在详情
        """
        path = relative_path.replace("\\", "/")
        return path in self._api_usage_details_map

    def get_all_api_usage_details(self) -> Dict[str, str]:
        """获取所有文件的API使用详情"""
        return dict(self._api_usage_details_map)

    def clear_api_usage_details(self, auto_save: bool = True) -> None:
        """
        清空所有API使用详情（用于强制重新生成）

        Args:
            auto_save: 是否自动保存checkpoint
        """
        self._api_usage_details_map.clear()
        if auto_save and self._auto_save:
            self.save_checkpoint()
