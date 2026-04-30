#!/usr/bin/env python3
"""Generate optimization summary report from base_dir/state.json.

Usage: python3 generate_loop_summary.py <base_dir>
Output: <base_dir>/summary.md
"""
from __future__ import annotations
import json
import glob
import os
import sys


def load_json(path: str) -> dict | None:
    if not os.path.isfile(path):
        return None
    with open(path) as f:
        return json.load(f)


def pct_str(before: float | None, after: float | None) -> str:
    if not before or not after or before == 0:
        return "N/A"
    pct = (after - before) / before * 100
    return f"{pct:+.1f}%"


def section_basic_info(state: dict) -> list[str]:
    """## 基本信息"""
    lines = ["## 基本信息", ""]
    inp = state.get("input", {})
    mi = state.get("model_info", {})
    rows = [
        ("模型", mi.get("name", "N/A")),
        ("MoE", "是" if mi.get("is_moe") else "否"),
        ("TP", mi.get("tp_size", "N/A")),
        ("硬件", inp.get("hardware_type", "N/A")),
        ("容器", inp.get("container", "N/A")),
        ("Serve 脚本", f"`{inp.get('serve_script', 'N/A')}`"),
        ("Benchmark 脚本", f"`{inp.get('benchmark_script', 'N/A')}`"),
    ]
    for k, v in rows:
        lines.append(f"- {k}: {v}")
    lines.append("")
    return lines


def section_overview(state: dict) -> list[str]:
    """## 总览"""
    lines = ["## 总览", ""]
    bl = state.get("baseline", {})
    cb = state.get("current_best", {})
    base_tps = bl.get("throughput_tps")
    final_tps = cb.get("throughput_tps")
    lines.append(f"| 指标 | Baseline | Final | 变化 |")
    lines.append(f"|------|----------|-------|------|")
    b = f"{base_tps:.2f}" if base_tps else "N/A"
    f_ = f"{final_tps:.2f}" if final_tps else "N/A"
    lines.append(f"| avg_tps | {b} | {f_} | {pct_str(base_tps, final_tps)} |")
    lines.append("")
    return lines


def section_layer1(state: dict, base_dir: str) -> list[str]:
    """## Layer 1"""
    lines = ["## Layer 1: 默认优化项", ""]
    l1 = state.get("layers", {}).get("layer1", {})
    if l1.get("status") != "completed":
        lines.append("*未完成*\n")
        return lines

    cr = load_json(os.path.join(base_dir, "layer1", "check_result.json"))

    # Already enabled
    if cr:
        ae = cr.get("already_enabled", [])
        if ae:
            lines.append("### 已开启项")
            lines.append("")
            for item in ae:
                lines.append(f"- {item.get('name', item.get('id', '?'))}")
            lines.append("")

    # Forced
    forced = l1.get("forced_opts", [])
    if cr:
        forced_detail = cr.get("forced", [])
        if forced_detail:
            lines.append("### 强制开启项")
            lines.append("")
            lines.append("| 名称 | 原因 |")
            lines.append("|------|------|")
            for item in forced_detail:
                lines.append(f"| {item.get('name', item.get('id'))} | {item.get('reason', '')} |")
            lines.append("")
    elif forced:
        lines.append(f"强制开启: {', '.join(forced)}")
        lines.append("")

    # Tested
    kept = set(l1.get("kept_opts", []))
    tested = l1.get("tested_opts", [])
    if tested:
        lines.append("### 测试项结果")
        lines.append("")
        lines.append("| 名称 | 结果 |")
        lines.append("|------|------|")
        for opt_id in tested:
            result = "保留" if opt_id in kept else "回滚"
            lines.append(f"| {opt_id} | {result} |")
        # Also include graph_mode if kept but not in tested list
        for opt_id in kept:
            if opt_id not in tested:
                lines.append(f"| {opt_id} | 保留 |")
        lines.append("")

    return lines


def section_layer2(state: dict, base_dir: str) -> list[str]:
    """## Layer 2"""
    lines = ["## Layer 2: Decode 时延优化", ""]
    l2 = state.get("layers", {}).get("layer2", {})
    if l2.get("status") != "completed":
        lines.append("*未完成*\n")
        return lines

    kept = l2.get("kept_opts", [])
    if kept:
        lines.append("### 保留的优化项")
        lines.append("")
        for opt in kept:
            lines.append(f"- {opt}")
        lines.append("")

    # Scan for perf files
    perf_files = sorted(glob.glob(os.path.join(base_dir, "layer2", "*_perf.json")))
    if perf_files:
        lines.append("### 测试数据")
        lines.append("")
        lines.append("| 文件 | decode_step_latency_us | avg_tps |")
        lines.append("|------|----------------------|---------|")
        for pf in perf_files:
            data = load_json(pf)
            if not data:
                continue
            name = os.path.basename(pf)
            lat = data.get("decode_step_latency_us", "N/A")
            tps = data.get("generation_throughput_avg_tps", "N/A")
            if isinstance(lat, (int, float)) and lat > 0:
                lat = f"{lat:.0f}"
            if isinstance(tps, (int, float)) and tps > 0:
                tps = f"{tps:.2f}"
            lines.append(f"| {name} | {lat} | {tps} |")
        lines.append("")

    return lines


def section_layer3(state: dict, base_dir: str) -> list[str]:
    """## Layer 3"""
    lines = ["## Layer 3: 调度参数搜索", ""]
    l3 = state.get("layers", {}).get("layer3", {})
    if l3.get("status") != "completed":
        lines.append("*未完成*\n")
        return lines

    sp = load_json(os.path.join(base_dir, "layer3", "schedule_params.json"))
    if sp:
        lines.append("### 调度参数")
        lines.append("")
        lines.append(f"- max_num_seqs: {sp.get('max_num_seqs', 'N/A')}")
        lines.append(f"- max_num_batched_tokens: {sp.get('max_num_batched_tokens', 'N/A')}")
        cands = sp.get("gpu_memory_utilization_candidates", [])
        if cands:
            lines.append(f"- gpu_memory_utilization 候选: {cands}")
        lines.append("")

    perf_files = sorted(glob.glob(os.path.join(base_dir, "layer3", "gpu_mem_*_perf.json")))
    if perf_files:
        lines.append("### gpu_memory_utilization 搜索")
        lines.append("")
        lines.append("| gpu_mem | avg_tps | 结果 |")
        lines.append("|--------|---------|------|")
        for pf in perf_files:
            data = load_json(pf)
            name = os.path.basename(pf).replace("gpu_mem_", "").replace("_perf.json", "")
            if data:
                tps = data.get("generation_throughput_avg_tps", "N/A")
                if isinstance(tps, (int, float)) and tps > 0:
                    lines.append(f"| {name} | {tps:.2f} | 可用 |")
                else:
                    lines.append(f"| {name} | N/A | 失败 |")
            else:
                lines.append(f"| {name} | N/A | 失败 |")
        lines.append("")

    return lines


def section_layer4(state: dict, base_dir: str) -> list[str]:
    """## Layer 4"""
    lines = ["## Layer 4: 深度优化", ""]
    l4 = state.get("layers", {}).get("layer4", {})
    if l4.get("status") != "completed":
        lines.append("*未完成*\n")
        return lines

    bots = l4.get("top_bottlenecks", [])
    if bots:
        lines.append("### 瓶颈算子")
        lines.append("")
        for b in bots:
            lines.append(f"- {b}")
        lines.append("")

    opt_results = l4.get("opt_results", {})
    tested = l4.get("tested_opts", [])
    kept = set(l4.get("kept_opts", []))
    rejected = set(l4.get("rejected_opts", []))
    if tested or opt_results:
        lines.append("### 优化结果")
        lines.append("")
        lines.append("| 优化项 | avg_tps | 变化 | 结果 |")
        lines.append("|--------|---------|------|------|")
        for opt_id in (tested or list(opt_results.keys())):
            r = opt_results.get(opt_id, {})
            tps = r.get("avg_tps")
            delta = r.get("delta_pct")
            reason = r.get("reason", "")
            tps_s = f"{tps:.2f}" if isinstance(tps, (int, float)) and tps else "N/A"
            delta_s = f"{delta:+.2f}%" if isinstance(delta, (int, float)) and delta is not None else reason or "N/A"
            result = "保留" if opt_id in kept else ("回滚" if opt_id in rejected else "?")
            lines.append(f"| {opt_id} | {tps_s} | {delta_s} | {result} |")
        lines.append("")

        bl_tps = l4.get("baseline_tps")
        best_tps = l4.get("best_tps")
        imp = l4.get("total_improvement_pct")
        if bl_tps and best_tps:
            lines.append(f"Layer 4 基线: {bl_tps:.2f} → 最优: {best_tps:.2f} ({imp:+.2f}%)" if imp else "")
            lines.append("")

    return lines


def section_kept_summary(state: dict) -> list[str]:
    """## 保留的全部优化项汇总"""
    lines = ["## 保留的全部优化项汇总", ""]
    layers = state.get("layers", {})
    all_kept = []
    for layer_name in ["layer1", "layer2", "layer3", "layer4"]:
        layer = layers.get(layer_name, {})
        for opt in layer.get("kept_opts", []):
            all_kept.append((layer_name, opt))
    if all_kept:
        lines.append("| Layer | 优化项 |")
        lines.append("|-------|--------|")
        for ln, opt in all_kept:
            lines.append(f"| {ln} | {opt} |")
    else:
        lines.append("无保留优化项")
    lines.append("")
    return lines


def section_files(state: dict, base_dir: str) -> list[str]:
    """## 文件路径"""
    lines = ["## 文件路径", ""]
    cb = state.get("current_best", {})
    serve = cb.get("serve_script", "N/A")
    lines.append(f"- 最优 serve_script: `{serve}`")
    patch = os.path.join(base_dir, "patches", "final.patch")
    if os.path.isfile(patch):
        lines.append(f"- 最终 patch: `{patch}`")
    lines.append("")
    return lines


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 generate_loop_summary.py <base_dir>", file=sys.stderr)
        sys.exit(1)

    base_dir = os.path.abspath(sys.argv[1])
    state = load_json(os.path.join(base_dir, "state.json"))
    if state is None:
        print(f"Error: {base_dir}/state.json not found", file=sys.stderr)
        sys.exit(1)

    sections = [
        ("基本信息", lambda: section_basic_info(state)),
        ("总览", lambda: section_overview(state)),
        ("Layer 1", lambda: section_layer1(state, base_dir)),
        ("Layer 2", lambda: section_layer2(state, base_dir)),
        ("Layer 3", lambda: section_layer3(state, base_dir)),
        ("Layer 4", lambda: section_layer4(state, base_dir)),
        ("汇总", lambda: section_kept_summary(state)),
        ("文件", lambda: section_files(state, base_dir)),
    ]

    lines = ["# vLLM 推理优化报告", ""]
    for name, fn in sections:
        try:
            lines.extend(fn())
        except Exception as e:
            lines.append(f"## {name}")
            lines.append("")
            lines.append(f"*数据缺失或解析错误: {e}*")
            lines.append("")

    out_path = os.path.join(base_dir, "summary.md")
    with open(out_path, "w") as f:
        f.write("\n".join(lines))
    print(f"Generated: {out_path}")


if __name__ == "__main__":
    main()
