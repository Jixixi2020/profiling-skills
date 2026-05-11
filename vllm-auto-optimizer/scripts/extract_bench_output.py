#!/usr/bin/env python3
"""extract_bench_output.py — parse vllm bench serve standard output summary.

Parses the final summary block:

    ============ Serving Benchmark Result ============
    Output token throughput (tok/s):         162.74
    Total token throughput (tok/s):          187.15
    ...

Returns JSON with:
    output_token_throughput_tps
    total_token_throughput_tps
    peak_output_token_throughput_tps
    benchmark_duration_s
    request_throughput_req_per_s
    successful_requests
    total_input_tokens
    total_generated_tokens
    mean_ttft_ms
    mean_tpot_ms
    mean_itl_ms
    parsed: bool  (True if summary was found)
"""
import re
import sys
import json
import argparse

# Fields to extract from the summary block: (canonical_key, regex label)
FIELDS = [
    ("successful_requests", r"Successful requests:\s+(\d+)"),
    ("failed_requests", r"Failed requests:\s+(\d+)"),
    ("max_request_concurrency", r"Maximum request concurrency:\s+(\d+)"),
    ("benchmark_duration_s", r"Benchmark duration \(s\):\s+([\d.]+)"),
    ("total_input_tokens", r"Total input tokens:\s+(\d+)"),
    ("total_generated_tokens", r"Total generated tokens:\s+(\d+)"),
    ("request_throughput_req_per_s", r"Request throughput \(req/s\):\s+([\d.]+)"),
    ("output_token_throughput_tps", r"Output token throughput \(tok/s\):\s+([\d.]+)"),
    ("peak_output_token_throughput_tps", r"Peak output token throughput \(tok/s\):\s+([\d.]+)"),
    ("peak_concurrent_requests", r"Peak concurrent requests:\s+([\d.]+)"),
    ("total_token_throughput_tps", r"Total token throughput \(tok/s\):\s+([\d.]+)"),
    ("mean_ttft_ms", r"Mean TTFT \(ms\):\s+([\d.]+)"),
    ("median_ttft_ms", r"Median TTFT \(ms\):\s+([\d.]+)"),
    ("p99_ttft_ms", r"P99 TTFT \(ms\):\s+([\d.]+)"),
    ("mean_tpot_ms", r"Mean TPOT \(ms\):\s+([\d.]+)"),
    ("median_tpot_ms", r"Median TPOT \(ms\):\s+([\d.]+)"),
    ("p99_tpot_ms", r"P99 TPOT \(ms\):\s+([\d.]+)"),
    ("mean_itl_ms", r"Mean ITL \(ms\):\s+([\d.]+)"),
    ("median_itl_ms", r"Median ITL \(ms\):\s+([\d.]+)"),
    ("p99_itl_ms", r"P99 ITL \(ms\):\s+([\d.]+)"),
]


def extract(bench_output_path):
    """Parse a vllm bench serve output log and return extracted metrics."""
    with open(bench_output_path, "r", errors="replace") as f:
        text = f.read()

    result = {"parsed": False}

    # Find the summary block boundary
    start_marker = "============ Serving Benchmark Result ============"
    idx = text.find(start_marker)
    if idx == -1:
        return result

    # Slice from the marker to end (summary is at the very end)
    summary = text[idx:]

    for key, pattern in FIELDS:
        m = re.search(pattern, summary)
        if m:
            val = m.group(1)
            try:
                fval = float(val)
                if fval == int(fval):
                    result[key] = int(fval)
                else:
                    result[key] = fval
            except ValueError:
                result[key] = val

    result["parsed"] = True
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Parse vllm bench serve standard output"
    )
    parser.add_argument("bench_output", help="Path to vllm bench output log")
    args = parser.parse_args()

    result = extract(args.bench_output)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("parsed") else 1)
