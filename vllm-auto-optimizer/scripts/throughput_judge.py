#!/usr/bin/env python3
"""throughput_judge.py — 比较 throughput，支持 max_tps 或 avg_tps 指标"""
import sys
import json
import argparse

METRIC_KEYS = {
    "max_tps": "generation_throughput_max_tps",
    "avg_tps": "generation_throughput_avg_tps",
}


def judge(prev_path, curr_path, metric="max_tps"):
    with open(prev_path) as f:
        prev = json.load(f)
    with open(curr_path) as f:
        curr = json.load(f)

    key = METRIC_KEYS.get(metric, metric)
    prev_tps = prev.get(key, 0)
    curr_tps = curr.get(key, 0)

    if prev_tps > 0:
        change_pct = round((curr_tps - prev_tps) / prev_tps * 100, 2)
    else:
        change_pct = 100.0 if curr_tps > 0 else 0.0

    keep = change_pct >= 1.0  # 至少 1% 改善才保留，<1% 视为噪声
    reason = (
        f"throughput({metric}) {'improved' if keep else 'degraded'}: "
        f"{prev_tps:.1f} -> {curr_tps:.1f} tps ({change_pct:+.1f}%)"
    )

    result = {
        "keep": keep,
        "reason": reason,
        "metric": metric,
        "prev_tps": round(prev_tps, 2),
        "curr_tps": round(curr_tps, 2),
        "change_pct": change_pct,
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
