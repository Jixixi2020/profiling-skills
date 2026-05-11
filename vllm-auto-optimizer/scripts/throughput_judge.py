#!/usr/bin/env python3
"""throughput_judge.py — 比较 throughput，支持 max_tps 或 avg_tps 指标。

优先使用 vllm bench 标准输出指标 (bench_output_token_throughput_tps)，
若不可用则回退到引擎日志采样 (generation_throughput_avg_tps)。

在做出 KEEP/ROLLBACK 决定前，自动对 A/B 配对进行合理性验证：
  - 采样窗口对称性 (sample_count / measurement_duration 差异 > 30%)
  - 度量来源一致性 (primary_metric_source 是否相同)
  - 预热污染检测 (max_tps/avg_tps ratio > 1.5)
  - bench output 可用性
"""
import sys
import json
import argparse

METRIC_KEYS = {
    "max_tps": "generation_throughput_max_tps",
    "avg_tps": "generation_throughput_avg_tps",
}


def _resolve_key(data, key):
    """Return the best available value for a metric key."""
    bench_key_map = {
        "generation_throughput_max_tps": "bench_peak_output_token_throughput_tps",
        "generation_throughput_avg_tps": "bench_output_token_throughput_tps",
    }
    explicit = bench_key_map.get(key, "")
    if explicit and explicit in data and data[explicit] is not None:
        return data[explicit]
    return data.get(key, 0)


def _validate_pair(prev, curr):
    """Run pair validation checks. Returns (overall, issues_list)."""
    issues = []
    prev_sc = prev.get("sample_count", 0)
    curr_sc = curr.get("sample_count", 0)

    # --- sample count asymmetry ---
    if prev_sc > 0 and curr_sc > 0:
        ratio = abs(prev_sc - curr_sc) / max(prev_sc, curr_sc) * 100
        if ratio > 30.0:
            issues.append({
                "check": "sample_count_asymmetry",
                "severity": "WARN",
                "detail": f"sample_count differs by {ratio:.0f}% (prev={prev_sc}, curr={curr_sc}). Measurement windows not aligned.",
            })

    # --- measurement duration asymmetry ---
    prev_dur = prev.get("measurement_duration_s", 0)
    curr_dur = curr.get("measurement_duration_s", 0)
    if prev_dur > 0 and curr_dur > 0:
        ratio = abs(prev_dur - curr_dur) / max(prev_dur, curr_dur) * 100
        if ratio > 30.0:
            issues.append({
                "check": "measurement_duration_asymmetry",
                "severity": "WARN",
                "detail": f"measurement_duration_s differs by {ratio:.0f}% (prev={prev_dur:.1f}s, curr={curr_dur:.1f}s).",
            })

    # --- metric source mismatch ---
    prev_src = prev.get("primary_metric_source", "unknown")
    curr_src = curr.get("primary_metric_source", "unknown")
    if prev_src != curr_src:
        issues.append({
            "check": "metric_source_mismatch",
            "severity": "WARN",
            "detail": f"Different metric sources: prev={prev_src}, curr={curr_src}",
        })

    # --- no bench output ---
    if not curr.get("bench_output_parsed", False) and not prev.get("bench_output_parsed", False):
        issues.append({
            "check": "no_bench_output",
            "severity": "WARN",
            "detail": "Both perf.json files lack vllm bench output. Using engine log sampling, which may be unreliable.",
        })

    # --- warmup contamination (heuristic) ---
    for label, d in [("prev", prev), ("curr", curr)]:
        avg = d.get("generation_throughput_avg_tps", 0)
        mx = d.get("generation_throughput_max_tps", 0)
        n = d.get("sample_count", 0)
        if mx > 0 and avg > 0 and n >= 5:
            ratio = mx / avg
            if ratio > 1.5:
                issues.append({
                    "check": f"warmup_contamination_{label}",
                    "severity": "WARN",
                    "detail": f"{label}: max_tps/avg_tps ratio={ratio:.2f} (max={mx:.1f}, avg={avg:.1f}, n={n}). Suggests warmup samples drag avg down.",
                })

    severities = {i["severity"] for i in issues}
    if "ERROR" in severities:
        return "FAIL", issues
    if len(issues) >= 3:
        # 3+ warnings = likely systemic measurement issues
        return "FAIL", issues
    if "WARN" in severities:
        return "WARN", issues
    return "PASS", issues


def judge(prev_path, curr_path, metric="max_tps"):
    with open(prev_path) as f:
        prev = json.load(f)
    with open(curr_path) as f:
        curr = json.load(f)

    # --- A/B 配对验证 ---
    validation_overall, validation_issues = _validate_pair(prev, curr)

    # --- 原有吞吐量比较 ---
    key = METRIC_KEYS.get(metric, metric)
    prev_tps = _resolve_key(prev, key)
    curr_tps = _resolve_key(curr, key)

    if prev_tps > 0:
        change_pct = round((curr_tps - prev_tps) / prev_tps * 100, 2)
    else:
        change_pct = 100.0 if curr_tps > 0 else 0.0

    keep = change_pct >= 1.0  # 至少 1% 改善才保留，<1% 视为噪声

    # 如果验证发现严重问题，在 reason 中标注
    reliable = validation_overall != "FAIL"
    if not reliable:
        keep = False  # 数据不可靠时强制 ROLLBACK

    reason = (
        f"throughput({metric}) {'improved' if keep else 'degraded'}: "
        f"{prev_tps:.1f} -> {curr_tps:.1f} tps ({change_pct:+.1f}%)"
    )
    if validation_issues:
        reason += f". Validation: {validation_overall} ({len(validation_issues)} issue(s))"

    result = {
        "keep": keep,
        "reliable": reliable,
        "reason": reason,
        "metric": metric,
        "prev_tps": round(prev_tps, 2),
        "curr_tps": round(curr_tps, 2),
        "change_pct": change_pct,
        "validation": {
            "overall": validation_overall,
            "checks": validation_issues,
        },
    }
    print(json.dumps(result, indent=2))
    return keep


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("prev_perf", help="前一次 perf.json")
    parser.add_argument("curr_perf", help="当前 perf.json")
    parser.add_argument("--metric", default="avg_tps", choices=["max_tps", "avg_tps"],
                        help="比较指标: avg_tps (默认) 或 max_tps")
    args = parser.parse_args()
    keep = judge(args.prev_perf, args.curr_perf, args.metric)
    sys.exit(0 if keep else 1)
