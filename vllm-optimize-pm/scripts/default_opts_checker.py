#!/usr/bin/env python3
"""Layer1 默认优化项检测 — 检查 serve_script 中未开启的默认优化项。

用法:
  python3 default_opts_checker.py <container> <serve_script> <state_json> <output_json>

输出 JSON:
  {
    "forced": [{"id": "...", "name": "...", "config": "...", "reason": "..."}],
    "to_test": [{"id": "...", "name": "...", "config": "...", "test_config": "..."}],
    "already_enabled": [{"id": "...", "name": "..."}]
  }
"""
import json, subprocess, sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'vllm-perf-analyzer-configs', 'knowledge'))
from default_opts import FORCED_OPTS, TESTABLE_OPTS

PARSE_SERVE = os.path.join(os.path.dirname(__file__), '..', '..', 'vllm-perf-analyzer-configs', 'scripts', 'parse_serve_script.py')


def get_serve_content(container, serve_script):
    """从容器中读取 serve_script 内容。"""
    result = subprocess.run(
        ['docker', 'exec', container, 'bash', '--norc', '--noprofile', '-c', f'cat {serve_script}'],
        capture_output=True, text=True)
    return result.stdout


def get_env_vars(container):
    """从容器中读取环境变量。"""
    result = subprocess.run(
        ['docker', 'exec', container, 'bash', '--norc', '--noprofile', '-c', 'env'],
        capture_output=True, text=True)
    env = {}
    for line in result.stdout.splitlines():
        if '=' in line:
            k, _, v = line.partition('=')
            env[k] = v
    return env


def check_env_enabled(env_vars, serve_content, check):
    """检查环境变量或 serve 参数是否已启用。"""
    check_type = check.get("type", "")
    if check_type == "env_var":
        var_name = check["var"]
        expected = check.get("value", None)
        actual = env_vars.get(var_name, "")
        if expected:
            return actual == expected
        return bool(actual)
    elif check_type == "serve_arg":
        return check["arg"] in serve_content
    elif check_type == "additional_config_key":
        return check["key"] in serve_content
    return False


def main():
    container = sys.argv[1]
    serve_script = sys.argv[2]
    state_json = sys.argv[3]
    output_json = sys.argv[4]

    # Load state for model info
    with open(state_json) as f:
        state = json.load(f)
    model_info = state.get("model_info", {})
    is_moe = model_info.get("is_moe", False)

    serve_content = get_serve_content(container, serve_script)
    env_vars = get_env_vars(container)

    forced = []
    to_test = []
    already_enabled = []

    # Check forced opts
    for opt in FORCED_OPTS:
        if opt.get("moe_only") and not is_moe:
            continue
        if check_env_enabled(env_vars, serve_content, opt.get("enabled_check", {})):
            already_enabled.append({"id": opt["id"], "name": opt["name"]})
        else:
            forced.append({
                "id": opt["id"], "name": opt["name"],
                "config": opt["config"], "reason": opt.get("reason", "默认强制开启")
            })

    # Check testable opts
    for opt in TESTABLE_OPTS:
        if opt.get("moe_only") and not is_moe:
            continue
        requires = opt.get("requires", {})
        # Check prerequisites
        skip = False
        if requires.get("ep") and "--enable-expert-parallel" not in serve_content:
            skip = True
        if requires.get("tp_gt_1") and model_info.get("tp_size", 1) <= 1:
            skip = True
        if skip:
            continue

        if check_env_enabled(env_vars, serve_content, opt.get("enabled_check", {})):
            already_enabled.append({"id": opt["id"], "name": opt["name"]})
        else:
            to_test.append({
                "id": opt["id"], "name": opt["name"],
                "config": opt["config"],
                "test_config": opt.get("test_config", opt["config"]),
                "conflicts_with": opt.get("conflicts_with", []),
                "special_handling": opt.get("special_handling", ""),
            })

    result = {"forced": forced, "to_test": to_test, "already_enabled": already_enabled}
    with open(output_json, 'w') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(json.dumps({"forced": len(forced), "to_test": len(to_test), "already_enabled": len(already_enabled)}))


if __name__ == "__main__":
    main()
