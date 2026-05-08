#!/usr/bin/env python3
"""确定性 serve 脚本生成器。

从基础 serve 脚本解析结构化参数，应用修改，生成新脚本。

用法:
  # 应用 default_opts_checker 输出的 config 字符串
  python3 gen_serve_script.py --base serve.sh --apply 'export FOO=1' -o out.sh
  python3 gen_serve_script.py --base serve.sh --apply-json check_result.json --pick forced -o out.sh

  # 精确修改
  python3 gen_serve_script.py --base serve.sh --set-env KEY=VALUE --set-arg "--gpu-memory-utilization 0.9" -o out.sh
  python3 gen_serve_script.py --base serve.sh --remove-env TASK_QUEUE_ENABLE --remove-compilation -o out.sh
  python3 gen_serve_script.py --base serve.sh --merge-additional '{"key":true}' -o out.sh

  # 从 stdin 读取基础脚本
  docker exec ctr cat /path/serve.sh | python3 gen_serve_script.py --base - --apply '...' -o out.sh
"""
import argparse
import copy
import json
import os
import re
import sys
from collections import OrderedDict


# ---------------------------------------------------------------------------
# 1. 解析 serve 脚本
# ---------------------------------------------------------------------------

def _tokenize_bash_array(content: str) -> list:
    """从 ARGS=(...) 中提取 token，正确处理单/双引号。"""
    # 找到 ARGS=( ... ) 块
    m = re.search(r'\w+\s*=\s*\((.*?)\)', content, re.DOTALL)
    if not m:
        return []
    body = m.group(1)

    tokens = []
    i = 0
    lines = body.split('\n')
    for line in lines:
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        # 去掉行尾注释（不在引号内）
        clean = _strip_inline_comment(line)
        if not clean:
            continue
        tokens.extend(_split_respecting_quotes(clean))
    return tokens


def _strip_inline_comment(line: str) -> str:
    """去掉不在引号内的 # 注释。"""
    in_sq = False
    in_dq = False
    for i, c in enumerate(line):
        if c == "'" and not in_dq:
            in_sq = not in_sq
        elif c == '"' and not in_sq:
            in_dq = not in_dq
        elif c == '#' and not in_sq and not in_dq:
            # 确保 # 前有空白
            if i == 0 or line[i-1] in (' ', '\t'):
                return line[:i].rstrip()
    return line


def _split_respecting_quotes(s: str) -> list:
    """按空白分割，但保留引号内的内容（去掉外层引号）。"""
    tokens = []
    current = []
    in_sq = False
    in_dq = False
    i = 0
    while i < len(s):
        c = s[i]
        if c == "'" and not in_dq:
            in_sq = not in_sq
            current.append(c)
        elif c == '"' and not in_sq:
            in_dq = not in_dq
            current.append(c)
        elif c in (' ', '\t') and not in_sq and not in_dq:
            if current:
                tokens.append(''.join(current))
                current = []
        else:
            current.append(c)
        i += 1
    if current:
        tokens.append(''.join(current))
    # 去掉外层引号
    result = []
    for t in tokens:
        if (t.startswith("'") and t.endswith("'")) or \
           (t.startswith('"') and t.endswith('"')):
            result.append(t[1:-1])
        else:
            result.append(t)
    return result


def _expand_vars(text: str, variables: dict) -> str:
    """展开 $VAR 和 ${VAR} 引用。"""
    for k, v in variables.items():
        text = text.replace(f'"${k}"', v).replace(f"'${k}'", v)
        text = text.replace(f'"${{{k}}}"', v).replace(f"'${{{k}}}'", v)
        text = text.replace(f'${k}', v).replace(f'${{{k}}}', v)
    return text


def parse_serve_script(content: str) -> dict:
    """解析 serve 脚本为结构化表示。

    Returns:
        {
            "env_vars": OrderedDict,       # KEY -> VALUE
            "model_path": str,
            "host": str,
            "port": str,
            "simple_args": OrderedDict,    # --key -> value ('' for flags)
            "additional_config": dict,     # parsed JSON
            "compilation_config": dict|None,
        }
    """
    # 提取变量定义
    variables = {}
    for m in re.finditer(
        r'^([A-Za-z_]\w*)=["\']?([^"\'#\n]+?)["\']?\s*(?:#.*)?$',
        content, re.MULTILINE
    ):
        variables[m.group(1)] = m.group(2).strip()
    # 迭代展开
    for _ in range(3):
        changed = False
        for k, v in list(variables.items()):
            nv = v
            for k2, v2 in variables.items():
                nv = nv.replace(f'${k2}', v2).replace(f'${{{k2}}}', v2)
            if nv != v:
                variables[k] = nv
                changed = True
        if not changed:
            break

    # 提取 env vars
    env_vars = OrderedDict()
    for m in re.finditer(r'^export\s+(\w+)=(.*?)$', content, re.MULTILINE):
        key = m.group(1)
        val = m.group(2).strip().strip('"').strip("'")
        env_vars[key] = val

    # 提取 vllm 命令 tokens
    expanded = _expand_vars(content, variables)
    tokens = _tokenize_bash_array(expanded)
    if not tokens:
        # 尝试直接命令格式
        for line in expanded.split('\n'):
            if 'vllm serve' in line and not line.strip().startswith('#'):
                tokens = _split_respecting_quotes(line.strip().rstrip('\\'))
                break

    # 从 tokens 解析 vllm args
    model_path = ''
    host = '0.0.0.0'
    port = '8011'
    simple_args = OrderedDict()
    additional_config = {}
    compilation_config = None
    speculative_config = None

    # 跳过 'vllm' 'serve'
    start = 0
    for i, t in enumerate(tokens):
        if t == 'serve':
            start = i + 1
            break

    i = start
    while i < len(tokens):
        t = tokens[i]
        if not t.startswith('-'):
            if not model_path:
                model_path = t
            i += 1
            continue

        # --additional-config 特殊处理
        if t in ('--additional-config',):
            if i + 1 < len(tokens):
                try:
                    additional_config = json.loads(tokens[i + 1])
                except (json.JSONDecodeError, TypeError):
                    simple_args[t] = tokens[i + 1]
                i += 2
                continue

        # --speculative-config 特殊处理
        if t in ('--speculative-config', '--speculative_config'):
            if i + 1 < len(tokens):
                try:
                    speculative_config = json.loads(tokens[i + 1])
                except (json.JSONDecodeError, TypeError):
                    speculative_config = {"raw": tokens[i + 1]}
                i += 2
                continue

        # --compilation_config / --compilation-config 特殊处理
        if t in ('--compilation_config', '--compilation-config'):
            if i + 1 < len(tokens):
                try:
                    compilation_config = json.loads(tokens[i + 1])
                except (json.JSONDecodeError, TypeError):
                    compilation_config = {"raw": tokens[i + 1]}
                i += 2
                continue

        # --host / --port
        if t == '--host' and i + 1 < len(tokens):
            host = tokens[i + 1]
            i += 2
            continue
        if t == '--port' and i + 1 < len(tokens):
            port = tokens[i + 1]
            i += 2
            continue

        # 普通 key-value 或 flag
        if i + 1 < len(tokens) and not tokens[i + 1].startswith('-'):
            simple_args[t] = tokens[i + 1]
            i += 2
        else:
            simple_args[t] = ''
            i += 1

    return {
        "env_vars": env_vars,
        "model_path": model_path,
        "host": host,
        "port": port,
        "simple_args": simple_args,
        "additional_config": additional_config,
        "compilation_config": compilation_config,
        "speculative_config": speculative_config,
    }


# ---------------------------------------------------------------------------
# 2. 解析 config 字符串（default_opts_checker 输出格式）
# ---------------------------------------------------------------------------

def parse_config_string(config: str) -> list:
    """解析 checker 输出的 config 字符串为修改操作列表。

    config 格式示例:
      "export VLLM_ASCEND_ENABLE_NZ=2"
      "--compilation-config '{\"cudagraph_capture_sizes\":[1,2,4,8,16,32,64]}'"
      "--additional-config '{\"fuse_allreduce_rms\":true}'"
      "export FOO=1\n--additional-config '{\"bar\":true}'"

    Returns: [("set_env", "KEY", "VALUE"), ("set_arg", "--key", "val"),
              ("merge_additional", {dict}), ("set_compilation", {dict}), ...]
    """
    ops = []
    for line in config.strip().split('\n'):
        line = line.strip()
        if not line:
            continue

        # env var
        m = re.match(r'^export\s+(\w+)=(.*?)$', line)
        if m:
            key = m.group(1)
            val = m.group(2).strip().strip('"').strip("'")
            ops.append(("set_env", key, val))
            continue

        # --additional-config 'JSON'
        m = re.match(r"^--additional-config\s+'(.+)'$", line)
        if m:
            try:
                d = json.loads(m.group(1))
                ops.append(("merge_additional", d))
            except json.JSONDecodeError:
                pass
            continue

        # --compilation-config / --compilation_config 'JSON'
        m = re.match(r"^--compilation[-_]config\s+'(.+)'$", line)
        if m:
            try:
                d = json.loads(m.group(1))
                ops.append(("set_compilation", d))
            except json.JSONDecodeError:
                ops.append(("set_compilation", {"raw": m.group(1)}))
            continue

        # 伪指令: --remove-compilation（从 test_config 内部触发删除 compilation_config）
        if line == '--remove-compilation':
            ops.append(("remove_compilation",))
            continue

        # 普通 --arg value 或 --flag
        m = re.match(r'^(--[\w-]+)\s+(.+)$', line)
        if m:
            ops.append(("set_arg", m.group(1), m.group(2).strip("'\"")))
            continue
        m = re.match(r'^(--[\w-]+)$', line)
        if m:
            ops.append(("set_arg", m.group(1), ""))
            continue

    return ops


# ---------------------------------------------------------------------------
# 3. 应用修改
# ---------------------------------------------------------------------------

def _deep_merge(base: dict, override: dict) -> dict:
    """深度合并两个 dict，override 优先。"""
    result = copy.deepcopy(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = copy.deepcopy(v)
    return result


def apply_ops(parsed: dict, ops: list) -> dict:
    """将操作列表应用到解析后的结构。"""
    p = copy.deepcopy(parsed)
    for op in ops:
        if op[0] == "set_env":
            p["env_vars"][op[1]] = op[2]
        elif op[0] == "remove_env":
            p["env_vars"].pop(op[1], None)
        elif op[0] == "set_arg":
            p["simple_args"][op[1]] = op[2]
        elif op[0] == "remove_arg":
            p["simple_args"].pop(op[1], None)
        elif op[0] == "merge_additional":
            p["additional_config"] = _deep_merge(p["additional_config"], op[1])
        elif op[0] == "set_compilation":
            p["compilation_config"] = op[1]
        elif op[0] == "remove_compilation":
            p["compilation_config"] = None
    return p


# ---------------------------------------------------------------------------
# 4. 生成 serve 脚本
# ---------------------------------------------------------------------------

TEMPLATE = '''#!/usr/bin/env bash
# Auto-generated serve script by gen_serve_script.py
{env_section}
set -e

ARGS=(
    vllm serve {model_path}
{args_section}
)

echo "${{ARGS[*]}}"
"${{ARGS[@]}}"
'''


def generate_script(parsed: dict) -> str:
    """从结构化表示生成 serve 脚本。"""
    # env vars
    env_lines = []
    for k, v in parsed["env_vars"].items():
        # 含空格或特殊字符时加引号
        if ' ' in v or '"' in v or "'" in v:
            env_lines.append(f'export {k}="{v}"')
        else:
            env_lines.append(f'export {k}={v}')
    env_section = '\n'.join(env_lines)

    # vllm args
    arg_lines = []
    for k, v in parsed["simple_args"].items():
        if v:
            arg_lines.append(f'    {k} {v}')
        else:
            arg_lines.append(f'    {k}')

    # additional-config
    if parsed["additional_config"]:
        j = json.dumps(parsed["additional_config"], ensure_ascii=False)
        arg_lines.append(f"    --additional-config '{j}'")

    # speculative_config
    if parsed.get("speculative_config") is not None:
        sc = parsed["speculative_config"]
        if "raw" in sc:
            arg_lines.append(f"    --speculative-config '{sc['raw']}'")
        else:
            j = json.dumps(sc, ensure_ascii=False)
            arg_lines.append(f"    --speculative-config '{j}'")

    # compilation_config
    if parsed["compilation_config"] is not None:
        cc = parsed["compilation_config"]
        if "raw" in cc:
            arg_lines.append(f"    --compilation_config '{cc['raw']}'")
        else:
            j = json.dumps(cc, ensure_ascii=False)
            arg_lines.append(f"    --compilation_config '{j}'")

    # host / port
    arg_lines.append(f'    --host {parsed["host"]}')
    arg_lines.append(f'    --port {parsed["port"]}')

    args_section = '\n'.join(arg_lines)

    return TEMPLATE.format(
        env_section=env_section,
        model_path=parsed["model_path"],
        args_section=args_section,
    )


# ---------------------------------------------------------------------------
# 5. CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='确定性 serve 脚本生成器',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument('--base', required=True,
                        help='基础 serve 脚本路径 (- 表示 stdin)')
    parser.add_argument('-o', '--output', required=True,
                        help='输出脚本路径')
    parser.add_argument('--apply', action='append', default=[],
                        help='应用 config 字符串 (checker 格式，可多次)')
    parser.add_argument('--apply-json', default=None,
                        help='从 check_result.json 应用配置')
    parser.add_argument('--pick', default=None,
                        help='与 --apply-json 配合: forced/to_test/all')
    parser.add_argument('--pick-ids', default=None,
                        help='与 --apply-json 配合: 逗号分隔的 id 列表')
    parser.add_argument('--set-env', action='append', default=[],
                        help='设置 env var: KEY=VALUE')
    parser.add_argument('--remove-env', action='append', default=[],
                        help='删除 env var: KEY')
    parser.add_argument('--set-arg', action='append', default=[],
                        help='设置 serve arg: "--key value"')
    parser.add_argument('--remove-arg', action='append', default=[],
                        help='删除 serve arg: --key')
    parser.add_argument('--merge-additional', action='append', default=[],
                        help='合并 additional-config JSON')
    parser.add_argument('--remove-compilation', action='store_true',
                        help='删除 compilation_config (图模式)')
    parser.add_argument('--dry-run', action='store_true',
                        help='只输出解析结果，不生成脚本')

    args = parser.parse_args()

    # 读取基础脚本
    if args.base == '-':
        content = sys.stdin.read()
    else:
        with open(args.base) as f:
            content = f.read()

    parsed = parse_serve_script(content)

    if args.dry_run:
        # 输出解析结果用于调试
        out = copy.deepcopy(parsed)
        out["env_vars"] = dict(out["env_vars"])
        out["simple_args"] = dict(out["simple_args"])
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    # 收集所有操作
    all_ops = []

    # --apply: 直接 config 字符串
    for cfg in args.apply:
        all_ops.extend(parse_config_string(cfg))

    # --apply-json: 从 check_result.json
    if args.apply_json:
        with open(args.apply_json) as f:
            check = json.load(f)
        items = []
        pick_ids = args.pick_ids.split(',') if args.pick_ids else None
        if pick_ids:
            for section in ('forced', 'to_test'):
                for item in check.get(section, []):
                    if item['id'] in pick_ids:
                        items.append(item)
        elif args.pick == 'forced':
            items = check.get('forced', [])
        elif args.pick == 'to_test':
            items = check.get('to_test', [])
        elif args.pick == 'all':
            items = check.get('forced', []) + check.get('to_test', [])
        for item in items:
            cfg = item.get('test_config') or item.get('config', '')
            all_ops.extend(parse_config_string(cfg))

    # 精确修改
    for kv in args.set_env:
        k, v = kv.split('=', 1)
        all_ops.append(("set_env", k, v))
    for k in args.remove_env:
        all_ops.append(("remove_env", k))
    for sa in args.set_arg:
        parts = sa.strip().split(' ', 1)
        all_ops.append(("set_arg", parts[0], parts[1] if len(parts) > 1 else ""))
    for k in args.remove_arg:
        all_ops.append(("remove_arg", k))
    for mj in args.merge_additional:
        all_ops.append(("merge_additional", json.loads(mj)))
    if args.remove_compilation:
        all_ops.append(("remove_compilation",))

    # 应用并生成
    modified = apply_ops(parsed, all_ops)
    script = generate_script(modified)

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, 'w') as f:
        f.write(script)
    os.chmod(args.output, 0o755)
    print(json.dumps({
        "ok": True,
        "output": args.output,
        "env_vars_count": len(modified["env_vars"]),
        "args_count": len(modified["simple_args"]),
        "has_additional_config": bool(modified["additional_config"]),
        "has_compilation_config": modified["compilation_config"] is not None,
        "ops_applied": len(all_ops),
    }))


if __name__ == '__main__':
    main()
