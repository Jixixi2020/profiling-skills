#!/usr/bin/env python3
"""层间状态管理 — 管理 state.json 的 CRUD 操作。

用法:
  python3 layer_state.py init <base_dir> <serve_script> <benchmark_script> <container> <hardware_type>
  python3 layer_state.py set_baseline <base_dir> <throughput_tps> [benchmark_duration_s]
  python3 layer_state.py set_model_info <base_dir> <model_name> <is_moe> <tp_size>
  python3 layer_state.py update_layer <base_dir> <layer> <key> <value_json>
  python3 layer_state.py set_layer_status <base_dir> <layer> <status>
  python3 layer_state.py update_best <base_dir> <serve_script> <tps> <opts_json> [duration]
  python3 layer_state.py set <base_dir> <json_path> <value>
  python3 layer_state.py get <base_dir> [json_path]
  python3 layer_state.py dump <base_dir>
"""
import json, os, sys, copy

STATE_FILE = "state.json"

def _load(base_dir):
    path = os.path.join(base_dir, STATE_FILE)
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}

def _save(base_dir, state):
    path = os.path.join(base_dir, STATE_FILE)
    os.makedirs(base_dir, exist_ok=True)
    with open(path, 'w') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def cmd_init(base_dir, serve_script, benchmark_script, container, hardware_type):
    state = {
        "input": {
            "serve_script": serve_script,
            "benchmark_script": benchmark_script,
            "container": container,
            "hardware_type": hardware_type,
        },
        "model_info": {"name": "", "is_moe": False, "tp_size": 1},
        "baseline": {"benchmark_duration_s": 0, "throughput_tps": 0},
        "current_best": {
            "serve_script": serve_script,
            "benchmark_duration_s": 0,
            "throughput_tps": 0,
            "cumulative_opts": [],
        },
        "layers": {
            "layer1": {"status": "pending", "forced_opts": [], "tested_opts": [], "kept_opts": []},
            "layer2": {"status": "pending", "profiling_dir": "", "kept_opts": []},
            "layer3": {"status": "pending", "best_combination": {}},
            "layer4": {"status": "pending"},
        },
    }
    _save(base_dir, state)
    print(json.dumps({"ok": True, "path": os.path.join(base_dir, STATE_FILE)}))

def cmd_set_baseline(base_dir, tps, duration="0"):
    state = _load(base_dir)
    state["baseline"] = {"benchmark_duration_s": float(duration), "throughput_tps": float(tps)}
    if state["current_best"]["throughput_tps"] == 0:
        state["current_best"]["benchmark_duration_s"] = float(duration)
        state["current_best"]["throughput_tps"] = float(tps)
    _save(base_dir, state)

def cmd_set_model_info(base_dir, name, is_moe, tp_size):
    state = _load(base_dir)
    state["model_info"] = {"name": name, "is_moe": is_moe.lower() in ("true","1","yes"), "tp_size": int(tp_size)}
    _save(base_dir, state)

def cmd_update_layer(base_dir, layer, key, value_json):
    state = _load(base_dir)
    if layer not in state.get("layers", {}):
        print(json.dumps({"error": f"unknown layer: {layer}"})); sys.exit(1)
    state["layers"][layer][key] = json.loads(value_json)
    _save(base_dir, state)

def cmd_set_layer_status(base_dir, layer, status):
    state = _load(base_dir)
    if layer not in state.get("layers", {}):
        print(json.dumps({"error": f"unknown layer: {layer}"})); sys.exit(1)
    state["layers"][layer]["status"] = status
    _save(base_dir, state)

def cmd_update_best(base_dir, serve_script, tps, opts_json, duration="0"):
    state = _load(base_dir)
    prev_opts = state.get("current_best", {}).get("cumulative_opts", [])
    new_opts = json.loads(opts_json)
    # 追加新优化项，按 name 去重（后来的覆盖同名项）
    merged = {opt["name"]: opt for opt in prev_opts if isinstance(opt, dict) and "name" in opt}
    for opt in new_opts:
        if isinstance(opt, dict) and "name" in opt:
            merged[opt["name"]] = opt
    state["current_best"] = {
        "serve_script": serve_script,
        "benchmark_duration_s": float(duration),
        "throughput_tps": float(tps),
        "cumulative_opts": list(merged.values()),
    }
    _save(base_dir, state)

def cmd_set(base_dir, json_path, value):
    """通过 dot-separated JSON path 设置任意字段。value 先尝试 JSON 解析，失败则作为字符串。"""
    state = _load(base_dir)
    try:
        parsed_value = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        parsed_value = value
    keys = json_path.split('.')
    obj = state
    for k in keys[:-1]:
        if k not in obj or not isinstance(obj[k], dict):
            obj[k] = {}
        obj = obj[k]
    obj[keys[-1]] = parsed_value
    _save(base_dir, state)

def cmd_get(base_dir, json_path=None):
    state = _load(base_dir)
    if json_path:
        for key in json_path.split('.'):
            if isinstance(state, dict):
                state = state.get(key, {})
            elif isinstance(state, list) and key.isdigit():
                state = state[int(key)]
    print(json.dumps(state, ensure_ascii=False, indent=2))

def cmd_dump(base_dir):
    state = _load(base_dir)
    print(json.dumps(state, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    cmds = {"init": cmd_init, "set_baseline": cmd_set_baseline, "set_model_info": cmd_set_model_info,
            "update_layer": cmd_update_layer, "set_layer_status": cmd_set_layer_status,
            "update_best": cmd_update_best, "set": cmd_set, "get": cmd_get, "dump": cmd_dump}
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        print(__doc__); sys.exit(1)
    cmd = sys.argv[1]
    args = sys.argv[2:]
    cmds[cmd](*args)
