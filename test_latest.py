"""Test latest docs"""
import sys
sys.path.insert(0, 'E:/code/CodeSummaryAgent')

from src.services.checkpoint import count_api_in_summary_doc, count_api_in_usage_doc

# Latest docs
api_doc_path = r"E:\code\NagaAgent\NagaAgent\NagaAgent_docs\API_DOC.md"
api_usage_path = r"E:\code\NagaAgent\NagaAgent\NagaAgent_docs\API_USAGE.md"

with open(api_doc_path, 'r', encoding='utf-8') as f:
    api_doc_content = f.read()

with open(api_usage_path, 'r', encoding='utf-8') as f:
    api_usage_content = f.read()

summary_count, summary_apis = count_api_in_summary_doc(api_doc_content)
usage_count, usage_apis = count_api_in_usage_doc(api_usage_content)

print(f"API_DOC.md: {summary_count}")
print(f"API_USAGE.md: {usage_count}")

summary_set = {api.upper() for api in summary_apis}
usage_set = {api.upper() for api in usage_apis}

only_in_summary = summary_set - usage_set
only_in_usage = usage_set - summary_set

print(f"\nonly_in_summary: {len(only_in_summary)}")
print(f"only_in_usage: {len(only_in_usage)}")

if only_in_summary:
    print(f"\nOnly in API_DOC.md:")
    for api in sorted(only_in_summary):
        print(f"  - {api}")

if only_in_usage:
    print(f"\nOnly in API_USAGE.md:")
    for api in sorted(only_in_usage):
        print(f"  - {api}")
