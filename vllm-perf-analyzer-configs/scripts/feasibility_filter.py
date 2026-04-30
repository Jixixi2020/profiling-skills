#!/usr/bin/env python3
"""优化措施前置可行性过滤 — 在执行前过滤不适用的优化项。

用法:
  python3 feasibility_filter.py <options_md> <state_json> <output_md> [filter_log]

读取 optimization_options.md，结合 state.json 中的模型/硬件信息，
过滤掉不可行的优化项，输出 filtered_optimization_options.md。
"""
import json
import re
import sys
import os


# 过滤规则定义
FILTER_RULES = {
    # MoE 专属优化：非 MoE 模型不适用
    "moe_only": {
        "keywords": [
            "expert-parallel", "enable-expert-parallel", "moe", "topk_optimize",
            "FUSED_MC2", "shared_expert_dp", "multistream_overlap_gate",
            "multistream_overlap_shared_expert", "DYNAMIC_EPLB",
        ],
        "check": lambda state: not state.get("model_info", {}).get("is_moe", False),
        "reason": "非 MoE 模型，不适用 MoE 专属优化",
    },
    # TP=1 时不适用的优化
    "tp_gt_1": {
        "keywords": [
            "MATMUL_ALLREDUCE", "fuse_allreduce_rms", "FLASHCOMM1",
            "BALANCE_SCHEDULING", "CONTEXT_PARALLEL",
        ],
        "check": lambda state: state.get("model_info", {}).get("tp_size", 1) <= 1,
        "reason": "TP=1，不适用多卡通信/并行优化",
    },
    # 图模式与 eager 冲突
    "graph_vs_eager": {
        "keywords": ["cudagraph", "FULL_DECODE_ONLY", "FULL_GRAPH", "PIECE"],
        "check": lambda state: "--enforce-eager" in state.get("_serve_content", ""),
        "reason": "当前配置为 enforce-eager，与图模式冲突",
    },
    # A2 硬件限制
    "a3_only": {
        "keywords": ["MLAPO", "DBO"],
        "check": lambda state: state.get("input", {}).get("hardware_type", "").upper() == "A2",
        "reason": "A2 硬件不支持此优化（仅 A3 适用）",
    },
}


def parse_options_md(content):
    """解析 optimization_options.md，提取优化项列表。"""
    options = []
    current = None
    for line in content.splitlines():
        # 检测优化项标题（## 或 ### 开头）
        m = re.match(r'^#{2,3}\s+(?:优化项?\s*\d+[:：]?\s*)?(.+)', line)
        if m:
            if current:
                options.append(current)
            current = {"title": m.group(1).strip(), "lines": [line], "raw": line}
        elif current:
            current["lines"].append(line)
    if current:
        options.append(current)
    return options


def check_feasibility(option, state):
    """检查单个优化项的可行性，返回 (feasible, reason)。"""
    text = option["title"] + "\n" + "\n".join(option["lines"])
    text_lower = text.lower()

    for rule_id, rule in FILTER_RULES.items():
        if rule["check"](state):
            for kw in rule["keywords"]:
                if kw.lower() in text_lower:
                    return False, rule["reason"]
    return True, ""


def main():
    options_md = sys.argv[1]
    state_json = sys.argv[2]
    output_md = sys.argv[3]
    filter_log = sys.argv[4] if len(sys.argv) > 4 else None

    with open(options_md) as f:
        content = f.read()
    with open(state_json) as f:
        state = json.load(f)

    # 加载 serve_script 内容，供过滤规则直接判断实际配置
    serve_path = state.get('current_best', {}).get('serve_script', '')
    if serve_path and os.path.exists(serve_path):
        with open(serve_path) as f:
            state['_serve_content'] = f.read()
    else:
        state['_serve_content'] = ''

    options = parse_options_md(content)
    kept = []
    filtered = []

    for opt in options:
        feasible, reason = check_feasibility(opt, state)
        if feasible:
            kept.append(opt)
        else:
            filtered.append({"title": opt["title"], "reason": reason})

    # 写入过滤后的 md
    with open(output_md, 'w') as f:
        f.write("# 可行优化项（已过滤不适用项）\n\n")
        for opt in kept:
            f.write("\n".join(opt["lines"]) + "\n\n")

    # 写入过滤日志
    if filter_log:
        with open(filter_log, 'w') as f:
            json.dump({
                "total": len(options),
                "kept": len(kept),
                "filtered": len(filtered),
                "filtered_items": filtered,
            }, f, ensure_ascii=False, indent=2)

    print(json.dumps({
        "total": len(options), "kept": len(kept), "filtered": len(filtered),
        "filtered_items": [f["title"] for f in filtered],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
