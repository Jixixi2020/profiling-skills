#!/usr/bin/env python3
"""
从 serve_script 提取已启用的配置（serve 参数、环境变量、additional-config、compile-config）。
改进版：支持嵌套 JSON（括号计数）、输出 additional_config_flat（dot-path 展平）。

用法:
  python3 extract_enabled_configs.py <serve_script_path> [applied_config_json] [container]

输出 JSON 到 stdout。
"""
import sys
import json
import re
import subprocess


def read_script_content(serve_script, container=None):
    if container:
        try:
            result = subprocess.run(
                ["docker", "exec", container, "cat", serve_script],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                return result.stdout
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
    try:
        with open(serve_script, 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        return ""


def extract_serve_args(content):
    args = []
    for m in re.finditer(r'(--[\w-]+)(?:\s+[\'"]([^\'"]*)[\'"]|\s+(\S+))?', content):
        arg = m.group(1)
        value = m.group(2) or m.group(3) or ""
        if value.startswith('--'):
            value = ""
        args.append({"arg": arg, "value": value})
    return args


def extract_env_vars(content):
    env_vars = {}
    for m in re.finditer(r'export\s+(\w+)=(["\']?)([^"\';\n]*)\2', content):
        env_vars[m.group(1)] = m.group(3)
    return env_vars


def extract_nested_json(content, prefix):
    """用括号计数提取嵌套 JSON，支持 --additional-config 和 -cc"""
    pattern = re.compile(re.escape(prefix) + r"""\s+['"]?(\{)""")
    m = pattern.search(content)
    if not m:
        return {}
    start = m.start(1)
    depth = 0
    for i in range(start, len(content)):
        if content[i] == '{':
            depth += 1
        elif content[i] == '}':
            depth -= 1
            if depth == 0:
                json_str = content[start:i+1]
                try:
                    return json.loads(json_str)
                except json.JSONDecodeError:
                    return {}
    return {}


def flatten_dict(d, prefix=""):
    """将嵌套 dict 展平为 dot-path 键"""
    flat = {}
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            flat.update(flatten_dict(v, key))
        else:
            flat[key] = v
    return flat


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <serve_script> [applied_config_json] [container]",
              file=sys.stderr)
        sys.exit(1)

    serve_script = sys.argv[1]
    applied_config_json = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] else None
    container = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] else None

    content = read_script_content(serve_script, container)

    additional_config = extract_nested_json(content, '--additional-config')
    compile_config = extract_nested_json(content, '-cc')

    result = {
        "serve_args": extract_serve_args(content),
        "env_vars": extract_env_vars(content),
        "additional_config": additional_config,
        "additional_config_flat": flatten_dict(additional_config),
        "compile_config": compile_config,
    }

    if applied_config_json:
        try:
            with open(applied_config_json, 'r', encoding='utf-8') as f:
                applied = json.load(f)
            if isinstance(applied, dict):
                result["additional_config"].update(applied)
                result["additional_config_flat"] = flatten_dict(result["additional_config"])
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    print()


if __name__ == '__main__':
    main()
