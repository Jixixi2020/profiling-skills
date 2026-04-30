#!/usr/bin/env python3
"""latency_judge.py — 比较 decode_step_latency_us，latency 越低越好。
当 latency 提取失败时返回 metric="latency_failed"，由编排层决定是否 fallback 到 throughput 重测。"""
import sys
import json


def judge(prev_path, curr_path):
    with open(prev_path) as f:
        prev = json.load(f)
    with open(curr_path) as f:
        curr = json.load(f)

    prev_lat = prev.get("decode_step_latency_us", 0)
    curr_lat = curr.get("decode_step_latency_us", 0)

    if prev_lat <= 0 or curr_lat <= 0:
        reason = (
            f"latency extraction failed: prev={prev_lat}, curr={curr_lat}"
        )
        result = {
            "keep": False,
            "metric": "latency_failed",
            "reason": reason,
            "prev_latency_us": prev_lat,
            "curr_latency_us": curr_lat,
            "change_pct": 0.0,
        }
        print(json.dumps(result, indent=2))
        return False

    change_pct = round((curr_lat - prev_lat) / prev_lat * 100, 2)
    keep = change_pct <= -1.0  # 至少 1% 改善才保留，<1% 视为噪声
    reason = (
        f"latency {'improved' if keep else 'degraded'}: "
        f"{prev_lat:.1f} -> {curr_lat:.1f} us ({change_pct:+.1f}%)"
    )

    result = {
        "keep": keep,
        "metric": "latency",
        "reason": reason,
        "prev_latency_us": round(prev_lat, 1),
        "curr_latency_us": round(curr_lat, 1),
        "change_pct": change_pct,
    }
    print(json.dumps(result, indent=2))
    return keep


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(f"Usage: python3 {sys.argv[0]} <prev_perf.json> <curr_perf.json>")
        sys.exit(1)
    keep = judge(sys.argv[1], sys.argv[2])
    sys.exit(0 if keep else 1)
