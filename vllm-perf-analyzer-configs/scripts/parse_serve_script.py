#!/usr/bin/env python3
"""解析 vLLM serve 启动脚本，提取关键参数。

用法:
  python3 parse_serve_script.py <serve_script_path>
  cat serve_script.sh | python3 parse_serve_script.py -

输出 JSON:
  {
    "port": 8800,
    "model_name": "qwen2.5-7B-instruct",
    "model_path": "/home/xxx/models/qwen2.5-7B-instruct",
    "max_model_len": 32768,
    "host": "0.0.0.0",
    "enforce_eager": true,
    "trust_remote_code": true,
    "raw_vllm_cmd": "vllm serve /path/to/model ...",
    "all_args": {"--port": "8800", "--served-model-name": "..."}
  }
"""
import json
import re
import shlex
import sys


def extract_vllm_serve_cmd(script_content: str) -> str:
    """从脚本内容中提取 vllm serve 命令行（支持多行续行和 bash 数组格式）。"""
    lines = script_content.splitlines()

    # --- 方式1: 直接 vllm serve 命令（支持续行符） ---
    cmd_lines = []
    capturing = False
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            if not capturing:
                continue
        if not capturing and 'vllm serve' in stripped:
            capturing = True
        if capturing:
            cmd_lines.append(stripped)
            if not stripped.endswith('\\'):
                break
            else:
                cmd_lines[-1] = stripped[:-1]
    if cmd_lines:
        return ' '.join(cmd_lines)

    # --- 方式2: bash 数组格式 ARGS=( vllm serve ... ) ---
    in_array = False
    array_elements = []
    for line in lines:
        stripped = line.strip()
        # 检测数组开始: VAR=( 或 VAR =(
        if not in_array and re.match(r'^[A-Za-z_]\w*\s*=\s*\(', stripped):
            in_array = True
            # 提取开括号后同行的内容
            after_paren = re.sub(r'^[A-Za-z_]\w*\s*=\s*\(\s*', '', stripped)
            if after_paren and after_paren != ')':
                for elem in _split_array_line(after_paren):
                    array_elements.append(elem)
            if ')' in stripped:
                in_array = False
            continue
        if in_array:
            if stripped.startswith('#') or not stripped:
                continue
            # 检测数组结束
            end = stripped.rstrip(')')
            for elem in _split_array_line(end):
                array_elements.append(elem)
            if ')' in stripped:
                in_array = False

    # 从数组元素中查找 vllm ... serve 序列
    found_vllm = False
    for i, elem in enumerate(array_elements):
        if elem == 'vllm' or elem.endswith('/vllm'):
            found_vllm = True
        elif found_vllm and elem == 'serve':
            # 重建命令行
            return ' '.join(array_elements[i - 1:])
        elif found_vllm and elem != 'serve':
            found_vllm = False

    return ''


def _split_array_line(line: str) -> list:
    """将 bash 数组的一行拆分为元素，保留引号内的空格。"""
    # 去掉行内注释（不在引号内的 #）
    clean = re.sub(r'''(?<=['"])\s*#.*$''', '', line)
    clean = re.sub(r'''\s+#\s+.*$''', '', clean)
    clean = clean.strip()
    if not clean:
        return []
    try:
        return shlex.split(clean)
    except ValueError:
        return clean.split()


def _expand_bash_vars(content: str) -> str:
    """提取脚本中的 bash 变量赋值，并在内容中展开引用。"""
    variables = {}
    for m in re.finditer(r'^([A-Za-z_]\w*)=["\']?([^"\'#\n]+?)["\']?\s*(?:#.*)?$', content, re.MULTILINE):
        variables[m.group(1)] = m.group(2).strip()
    # 迭代展开（变量值中可能引用其他变量）
    for _ in range(3):
        changed = False
        for k, v in variables.items():
            new_v = v
            for k2, v2 in variables.items():
                new_v = new_v.replace(f'${k2}', v2).replace(f'${{{k2}}}', v2)
            if new_v != v:
                variables[k] = new_v
                changed = True
        if not changed:
            break
    # 在内容中替换变量引用
    result = content
    for k, v in variables.items():
        result = result.replace(f'"${k}"', v)
        result = result.replace(f"'${k}'", v)
        result = result.replace(f'"${{{k}}}"', v)
        result = result.replace(f"'${{{k}}}'", v)
        result = result.replace(f'${k}', v)
        result = result.replace(f'${{{k}}}', v)
    return result


def parse_serve_cmd(cmd: str) -> dict:
    """解析 vllm serve 命令，提取参数。"""
    result = {
        "port": 8800,
        "model_name": "",
        "model_path": "",
        "max_model_len": 0,
        "host": "0.0.0.0",
        "enforce_eager": False,
        "trust_remote_code": False,
        "raw_vllm_cmd": cmd,
        "all_args": {},
    }

    # 移除 "$@" 和变量引用以便解析
    clean_cmd = cmd.replace('"$@"', '').replace("'$@'", '').replace('$@', '')
    try:
        tokens = shlex.split(clean_cmd)
    except ValueError:
        # shlex 解析失败时用简单分割
        tokens = clean_cmd.split()

    # 找到 vllm serve 后的 tokens
    serve_idx = -1
    for i, t in enumerate(tokens):
        if t == 'serve' and i > 0 and tokens[i - 1].endswith('vllm'):
            serve_idx = i
            break
    if serve_idx < 0:
        return result

    args = tokens[serve_idx + 1:]

    # 第一个非 -- 参数是模型路径
    i = 0
    while i < len(args):
        if not args[i].startswith('-'):
            result["model_path"] = args[i]
            args = args[:i] + args[i + 1:]
            break
        elif args[i] in ('--host', '--port', '--served-model-name', '--max-model-len',
                         '--tensor-parallel-size', '--pipeline-parallel-size',
                         '--compilation-config', '--backend', '--tokenizer'):
            i += 2  # 跳过值
        else:
            i += 1

    # 解析 key-value 和 flag 参数
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == '--port' and i + 1 < len(args):
            result["port"] = int(args[i + 1])
            result["all_args"]["--port"] = args[i + 1]
            i += 2
        elif arg == '--served-model-name' and i + 1 < len(args):
            result["model_name"] = args[i + 1]
            result["all_args"]["--served-model-name"] = args[i + 1]
            i += 2
        elif arg == '--max-model-len' and i + 1 < len(args):
            result["max_model_len"] = int(args[i + 1])
            result["all_args"]["--max-model-len"] = args[i + 1]
            i += 2
        elif arg == '--host' and i + 1 < len(args):
            result["host"] = args[i + 1]
            result["all_args"]["--host"] = args[i + 1]
            i += 2
        elif arg == '--enforce-eager':
            result["enforce_eager"] = True
            result["all_args"]["--enforce-eager"] = "true"
            i += 1
        elif arg == '--trust-remote-code':
            result["trust_remote_code"] = True
            result["all_args"]["--trust-remote-code"] = "true"
            i += 1
        elif arg.startswith('--') and i + 1 < len(args) and not args[i + 1].startswith('--'):
            result["all_args"][arg] = args[i + 1]
            i += 2
        elif arg.startswith('--'):
            result["all_args"][arg] = "true"
            i += 1
        else:
            i += 1

    return result


def main():
    if len(sys.argv) < 2:
        print("用法: python3 parse_serve_script.py <serve_script_path | ->", file=sys.stderr)
        sys.exit(1)

    path = sys.argv[1]
    if path == '-':
        content = sys.stdin.read()
    else:
        with open(path) as f:
            content = f.read()

    # 展开脚本中定义的 bash 变量（如 MODEL_PATH, HOST, PORT）
    content = _expand_bash_vars(content)

    cmd = extract_vllm_serve_cmd(content)
    if not cmd:
        print(json.dumps({"error": "未找到 vllm serve 命令"}))
        sys.exit(1)

    result = parse_serve_cmd(cmd)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
