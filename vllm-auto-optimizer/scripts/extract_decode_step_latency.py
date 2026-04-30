#!/usr/bin/env python3
"""extract_decode_step_latency.py — 从 kernel_details.csv 提取 decode step latency，输出 JSON"""
import csv
import sys
import os
import json
import argparse


def find_kernel_details(profiling_dir):
    """查找 kernel_details.csv"""
    candidate = os.path.join(profiling_dir, "ASCEND_PROFILER_OUTPUT", "kernel_details.csv")
    if os.path.exists(candidate):
        return candidate
    for root, _dirs, files in os.walk(profiling_dir):
        if "kernel_details.csv" in files:
            return os.path.join(root, "kernel_details.csv")
    return None


def measure(profiling_dir, trim=5, min_interval_us=1000):
    csv_path = find_kernel_details(profiling_dir)
    if not csv_path:
        return {"error": f"kernel_details.csv not found in {profiling_dir}"}

    starts = []
    with open(csv_path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if "ArgMax" in row.get("Name", ""):
                starts.append(float(row["Start Time(us)"]))
    starts.sort()

    if len(starts) < 2:
        return {"error": f"only {len(starts)} ArgMax kernels found"}

    # 计算间隔，过滤 TP 重复对
    intervals = []
    for i in range(len(starts) - 1):
        dt = starts[i + 1] - starts[i]
        if dt > min_interval_us:
            intervals.append(dt)

    if len(intervals) < trim * 2 + 1:
        return {"error": f"only {len(intervals)} valid intervals, need >= {trim * 2 + 1}"}

    mid = intervals[trim:-trim] if trim > 0 else intervals
    mid_sorted = sorted(mid)
    n = len(mid_sorted)

    avg_us = sum(mid) / n
    median_us = mid_sorted[n // 2] if n % 2 == 1 else (mid_sorted[n // 2 - 1] + mid_sorted[n // 2]) / 2
    p25 = mid_sorted[n // 4]
    p75 = mid_sorted[3 * n // 4]
    p95 = mid_sorted[int(n * 0.95)]
    p5 = mid_sorted[int(n * 0.05)]
    std_us = (sum((x - avg_us) ** 2 for x in mid) / n) ** 0.5

    return {
        "latency_us": round(median_us, 1),
        "latency_mean_us": round(avg_us, 1),
        "latency_median_us": round(median_us, 1),
        "latency_std_us": round(std_us, 1),
        "latency_p5_us": round(p5, 1),
        "latency_p25_us": round(p25, 1),
        "latency_p75_us": round(p75, 1),
        "latency_p95_us": round(p95, 1),
        "throughput_tps": round(1e6 / median_us, 1),
        "argmax_count": len(starts),
        "valid_steps": len(intervals),
        "trimmed_steps": n,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("profiling_dir", help="profiling 数据目录")
    parser.add_argument("--trim", type=int, default=5, help="头尾各去掉的步数")
    parser.add_argument("--min-interval-us", type=int, default=1000, help="TP 重复对过滤阈值(us)")
    args = parser.parse_args()

    result = measure(args.profiling_dir, args.trim, args.min_interval_us)
    print(json.dumps(result, indent=2))
    sys.exit(1 if "error" in result else 0)
