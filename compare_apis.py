"""
对比 API_DOC 和 API_USAGE 的接口列表，找出差异
"""
import json
import re
from pathlib import Path

# 添加项目路径
import sys
sys.path.insert(0, str(Path(__file__).parent))

from src.services.checkpoint import extract_all_apis_from_info_map


def extract_apis_from_usage_doc(doc_path: str) -> set:
    """从 API_USAGE.md 提取接口列表"""
    with open(doc_path, "r", encoding="utf-8") as f:
        content = f.read()

    apis = set()

    # 匹配标题形式的接口定义
    # ### GET /path 或 #### POST /path
    pattern = r'^#{3,4}\s+(GET|POST|PUT|DELETE|PATCH)\s+(/[^\s]*)'
    for match in re.finditer(pattern, content, re.MULTILINE):
        method = match.group(1)
        path = match.group(2)
        apis.add(f"{method} {path}")

    # 匹配 MCP工具 格式
    # #### MCP工具: func_name 或 ### [MCP工具] func_name
    mcp_pattern = r'^#{3,4}\s+(?:\[)?MCP[^\]]*(?:\])?\s*[:\s]+(\w+)'
    for match in re.finditer(mcp_pattern, content, re.MULTILINE | re.IGNORECASE):
        func_name = match.group(1)
        apis.add(f"MCP {func_name}")

    return apis


def main():
    checkpoint_path = Path("E:/code/NagaAgent/NagaAgent/NagaAgent_docs/.checkpoint.json")
    usage_doc_path = Path("E:/code/NagaAgent/NagaAgent/NagaAgent_docs/API_USAGE.md")

    # 从 checkpoint 提取期望的接口列表
    with open(checkpoint_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    api_info_map = data.get("api_info_map", {})
    expected_apis = extract_all_apis_from_info_map(api_info_map)

    # 构建期望的接口集合 (method + path)
    expected_set = set()
    expected_by_module = {}
    for api in expected_apis:
        key = f"{api['method']} {api['path']}"
        expected_set.add(key)
        module = api.get('source_file', 'unknown')
        if module not in expected_by_module:
            expected_by_module[module] = []
        expected_by_module[module].append(key)

    # 从 API_USAGE.md 提取实际的接口列表
    actual_set = extract_apis_from_usage_doc(str(usage_doc_path))

    print("=" * 70)
    print("API 接口对比分析")
    print("=" * 70)

    print(f"\n期望接口数 (checkpoint): {len(expected_set)}")
    print(f"实际接口数 (API_USAGE.md): {len(actual_set)}")

    # 找出缺失的接口
    missing = expected_set - actual_set
    extra = actual_set - expected_set

    print(f"\n缺失的接口 ({len(missing)} 个):")
    print("-" * 50)

    # 按模块分组显示缺失的接口
    missing_by_module = {}
    for api in expected_apis:
        key = f"{api['method']} {api['path']}"
        if key in missing:
            module = api.get('source_file', 'unknown')
            if module not in missing_by_module:
                missing_by_module[module] = []
            missing_by_module[module].append(key)

    for module, apis in sorted(missing_by_module.items()):
        print(f"\n  [{module}]")
        for api in apis:
            print(f"    - {api}")

    if extra:
        print(f"\n额外的接口 ({len(extra)} 个，可能是重复或格式问题):")
        print("-" * 50)
        for api in sorted(extra):
            print(f"  + {api}")

    # 检查 api_usage_details_map 是否完整
    print("\n" + "=" * 70)
    print("Checkpoint api_usage_details_map 检查")
    print("=" * 70)

    api_usage_details = data.get("api_usage_details_map", {})
    api_files = data.get("api_files", [])

    print(f"\nAPI 文件数: {len(api_files)}")
    print(f"使用详情数: {len(api_usage_details)}")

    missing_details = set(api_files) - set(api_usage_details.keys())
    if missing_details:
        print(f"\n缺少使用详情的文件 ({len(missing_details)} 个):")
        for f in missing_details:
            print(f"  - {f}")
    else:
        print("\n所有 API 文件都有使用详情")

    # 检查每个文件的使用详情是否包含预期的接口
    print("\n" + "=" * 70)
    print("各文件使用详情的接口覆盖检查")
    print("=" * 70)

    for file_path, expected_apis_list in expected_by_module.items():
        details = api_usage_details.get(file_path, "")

        covered = 0
        not_covered = []

        for api in expected_apis_list:
            # 检查接口是否在详情中被提及
            method, path = api.split(" ", 1)
            if path in details or api in details:
                covered += 1
            else:
                not_covered.append(api)

        total = len(expected_apis_list)
        if not_covered:
            print(f"\n  [{file_path}] 覆盖率: {covered}/{total}")
            for api in not_covered:
                print(f"    缺失: {api}")


if __name__ == "__main__":
    main()
