#!/usr/bin/env python3
"""extract_serve_throughput.py — 从 serve 日志提取 generation throughput max/avg"""
import re
import sys
import json
import argparse

PATTERN = re.compile(r"Avg generation throughput:\s+([\d.]+)\s+tokens/s")


def extract(log_path, after_line=0):
    values = []
    with open(log_path, "r", errors="replace") as f:
        for i, line in enumerate(f, 1):
            if i <= after_line:
                continue
            m = PATTERN.search(line)
            if m:
                values.append(float(m.group(1)))

    if not values:
        return {"max_tps": 0, "avg_tps": 0, "sample_count": 0, "values": []}

    return {
        "max_tps": round(max(values), 2),
        "avg_tps": round(sum(values) / len(values), 2),
        "sample_count": len(values),
        "values": [round(v, 2) for v in values],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("serve_log", help="serve 日志文件路径")
    parser.add_argument("--after-line", type=int, default=0, help="只处理此行号之后的内容")
    args = parser.parse_args()

    result = extract(args.serve_log, args.after_line)
    print(json.dumps(result, indent=2))
    sys.exit(0)
