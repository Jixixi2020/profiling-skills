#!/usr/bin/env python3
"""parse_benchmark_script.py — 从用户 benchmark 脚本中解析请求参数。

从 stdin 读取脚本内容，输出 JSON:
  {"num_requests": 128, "input_len": null, "output_len": 4096, "port": 8011, "parsed_fields": ["num_requests", "output_len", "port"]}
"""
import re
import sys
import json


# 变量名模式 → 字段映射（bash 变量赋值）
FIELD_PATTERNS = {
    "num_requests": [
        "TOTAL_REQUESTS", "NUM_REQUESTS", "NUM_PROMPTS", "CONCURRENCY",
        "num_prompts", "n_requests", "total_requests", "num_requests",
    ],
    "input_len": [
        "INPUT_LEN", "INPUT_LENGTH", "MAX_INPUT_LEN", "MAX_INPUT_TOKENS",
        "input_len", "input_length", "max_input_len", "input_tokens",
    ],
    "output_len": [
        "MAX_TOKENS", "OUTPUT_LEN", "OUTPUT_LENGTH", "MAX_OUTPUT_LEN",
        "MAX_OUTPUT_TOKENS", "max_tokens", "output_len", "output_length",
        "max_output_len", "max_output_tokens",
    ],
}

# 命令行参数模式 → 字段映射（--arg value 或 --arg=value）
CLI_ARG_PATTERNS = {
    "num_requests": [
        "--num-requests", "--num-prompts", "--concurrency", "--total-requests",
        "-n",
    ],
    "input_len": [
        "--input-len", "--input-length", "--max-input-len", "--max-input-tokens",
    ],
    "output_len": [
        "--max-tokens", "--output-len", "--output-length", "--max-output-len",
        "--max-output-tokens",
    ],
}

# 匹配 bash 变量赋值: VAR=123 或 VAR="${VAR:-123}" 或 VAR=${VAR:-123}
_RE_ASSIGN = re.compile(
    r'''(?:^|\n)\s*(\w+)\s*=\s*(?:"\$\{\w+:-(\d+)\}"|'\$\{\w+:-(\d+)\}'|\$\{\w+:-(\d+)\}|"(\d+)"|'(\d+)'|(\d+))'''
)


def parse(content: str) -> dict:
    result = {"num_requests": None, "input_len": None, "output_len": None, "port": None}
    parsed_fields = []

    # 方法 0: URL 端口解析 (http://host:port/path)
    port_match = re.search(r'https?://[^:/]+:(\d+)', content)
    if port_match:
        result["port"] = int(port_match.group(1))
        parsed_fields.append("port")

    # 方法 1: bash 变量赋值
    assignments = {}
    for m in _RE_ASSIGN.finditer(content):
        var_name = m.group(1)
        value = next((g for g in m.groups()[1:] if g is not None), None)
        if value is not None:
            assignments[var_name] = int(value)

    for field, var_names in FIELD_PATTERNS.items():
        for var_name in var_names:
            if var_name in assignments:
                result[field] = assignments[var_name]
                parsed_fields.append(field)
                break

    # 方法 2: 命令行参数 (--arg value 或 --arg=value)
    for field, arg_names in CLI_ARG_PATTERNS.items():
        if result[field] is not None:
            continue  # 已从变量赋值中获取
        for arg_name in arg_names:
            # --arg value
            pattern = re.escape(arg_name) + r'\s+(\d+)'
            m = re.search(pattern, content)
            if m:
                result[field] = int(m.group(1))
                parsed_fields.append(field)
                break
            # --arg=value
            pattern = re.escape(arg_name) + r'=(\d+)'
            m = re.search(pattern, content)
            if m:
                result[field] = int(m.group(1))
                parsed_fields.append(field)
                break

    result["parsed_fields"] = parsed_fields
    return result


if __name__ == "__main__":
    content = sys.stdin.read()
    result = parse(content)
    print(json.dumps(result, indent=2))
