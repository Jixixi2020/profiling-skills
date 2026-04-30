#!/usr/bin/env python3
"""Layer3 调度参数计算 — 确定性参数 + gpu_memory_utilization 候选列表。

用法:
  python3 schedule_grid.py <state_json> <benchmark_info_json> <output_json>

benchmark_info_json: {"num_requests": 128, "input_len": 1024, "output_len": 65536}
输出 JSON: {"max_num_seqs": 128, "max_num_batched_tokens": 8519680, "gpu_memory_utilization_candidates": [0.98, ...]}
"""
import json
import math
import sys


GPU_MEM_CANDIDATES = [0.98, 0.95, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4]


def next_power_of_2(n):
    """向上取到 2 的幂次"""
    if n <= 0:
        return 1
    return 1 << (n - 1).bit_length()


def compute_schedule_params(benchmark_info, state):
    num_requests = benchmark_info.get("num_requests", 128)
    input_len = benchmark_info.get("input_len", 2048)
    output_len = benchmark_info.get("output_len", 2048)

    max_num_seqs = next_power_of_2(num_requests)
    max_num_batched_tokens = (input_len + output_len) * max_num_seqs

    return {
        "max_num_seqs": max_num_seqs,
        "max_num_batched_tokens": max_num_batched_tokens,
        "gpu_memory_utilization_candidates": GPU_MEM_CANDIDATES,
        "source": {
            "num_requests": num_requests,
            "input_len": input_len,
            "output_len": output_len,
        },
    }


def main():
    state_json = sys.argv[1]
    benchmark_info_json = sys.argv[2]
    output_json = sys.argv[3]

    with open(state_json) as f:
        state = json.load(f)
    with open(benchmark_info_json) as f:
        benchmark_info = json.load(f)

    result = compute_schedule_params(benchmark_info, state)

    with open(output_json, "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
