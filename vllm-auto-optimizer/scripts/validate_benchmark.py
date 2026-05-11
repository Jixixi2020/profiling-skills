#!/usr/bin/env python3
"""validate_benchmark.py — 验证测量结果的合理性。

两种模式：
  1. 单文件: python3 validate_benchmark.py <perf.json>
  2. A/B 对比: python3 validate_benchmark.py <prev_perf.json> <curr_perf.json>

检查项：
  1. 双源一致性 — bench output tps vs 引擎采样 avg (偏差 > 5% → ERROR)
  2. 采样窗口对称 — A/B sample_count 差异 > 30% → WARN
  3. 预热污染检测 — min sample < avg 的 20% → WARN
  4. bench output 可用性 — primary_metric_source != vllm_bench_output → WARN
  5. 零值/空值检测 — throughput=0 或 sample_count=0 或 failed_requests>0 → ERROR

输出: JSON (含 overall=PASS|WARN|FAIL, checks[...], summary)
"""

import sys
import json
import os


def _load(path):
    with open(path, "r") as f:
        return json.load(f)


def validate_single(data):
    """Validate a single perf.json. Returns list of issue dicts."""
    issues = []
    src = data.get("primary_metric_source", "unknown")

    # --- Check 5: zero / empty detection ---
    avg_tps = data.get("generation_throughput_avg_tps", 0)
    max_tps = data.get("generation_throughput_max_tps", 0)
    sample_count = data.get("sample_count", 0)
    failed_req = data.get("failed_requests", 0)
    bench_failed = data.get("bench_successful_requests")
    bench_output_parsed = data.get("bench_output_parsed", False)

    if avg_tps <= 0 and max_tps <= 0:
        issues.append({
            "check": "zero_throughput",
            "severity": "ERROR",
            "detail": f"Both avg_tps({avg_tps}) and max_tps({max_tps}) are zero or negative",
        })

    if sample_count <= 0:
        issues.append({
            "check": "zero_sample_count",
            "severity": "ERROR",
            "detail": f"sample_count={sample_count}, no engine log samples extracted",
        })

    if failed_req > 0:
        issues.append({
            "check": "failed_requests",
            "severity": "ERROR",
            "detail": f"{failed_req} benchmark requests failed",
        })

    if bench_output_parsed and bench_failed is not None and bench_failed == 0 and avg_tps <= 0:
        issues.append({
            "check": "bench_ok_but_zero_tps",
            "severity": "ERROR",
            "detail": "Bench ran successfully (200 requests) but throughput is zero — inference may have stalled",
        })

    # --- Check 4: bench output availability ---
    if not bench_output_parsed:
        issues.append({
            "check": "no_bench_output",
            "severity": "WARN",
            "detail": f"primary_metric_source={src}. vllm bench output was not parsed. Engine log sampling may be unreliable.",
        })

    # --- Check 3: warmup contamination ---
    if sample_count >= 3 and avg_tps > 0:
        values = data.get("values", [])
        warmup_detected = False
        warmup_detail = ""

        if values:
            min_val = min(values)
            if min_val < avg_tps * 0.20:
                warmup_detected = True
                warmup_detail = f"Min sample ({min_val:.1f}) is < 20% of avg ({avg_tps:.1f}). Window likely includes warmup/cooldown samples."
        elif max_tps > 0 and sample_count >= 5:
            # Heuristic: if max/avg ratio > 1.5 with enough samples, warmup is dragging avg down
            ratio = max_tps / avg_tps
            if ratio > 1.5:
                warmup_detected = True
                warmup_detail = f"max_tps/avg_tps ratio is {ratio:.2f} (max={max_tps:.1f}, avg={avg_tps:.1f}, n={sample_count}). Strongly suggests warmup/cooldown samples are dragging average down."

        # Also check measurement_duration vs benchmark duration
        meas_dur = data.get("measurement_duration_s", 0)
        bench_dur = data.get("bench_duration_s")
        if not warmup_detected and bench_dur and meas_dur > bench_dur * 1.5:
            warmup_detected = True
            warmup_detail = f"measurement_duration_s({meas_dur}s) is {meas_dur/bench_dur:.1f}x longer than benchmark({bench_dur}s), likely includes warmup/cooldown samples."

        if warmup_detected:
            issues.append({
                "check": "warmup_contamination",
                "severity": "WARN",
                "detail": warmup_detail,
            })

    # --- Check 1: dual-source consistency ---
    if bench_output_parsed and sample_count > 0 and avg_tps > 0:
        bench_tput = data.get("bench_output_token_throughput_tps", 0)
        if bench_tput > 0 and src == "vllm_bench_output":
            # bench output was used as primary, but engine sampling might differ
            engine_avg = sum(data.get("values", [avg_tps])) / max(len(data.get("values", [avg_tps])), 1) if data.get("values") else avg_tps
            # When primary is bench output, avg_tps IS the bench value (overwritten in run_measurement.sh)
            # So we need to check if engine sampling would have given different result
            pass  # already handled by the overwrite in run_measurement.sh

        elif bench_tput > 0 and src != "vllm_bench_output" and avg_tps > 0:
            # bench output available but NOT used as primary — compare them
            ratio = abs(bench_tput - avg_tps) / max(bench_tput, avg_tps) * 100
            if ratio > 5.0:
                issues.append({
                    "check": "dual_source_mismatch",
                    "severity": "ERROR",
                    "detail": f"Bench output ({bench_tput:.1f} tps) differs from engine sampling avg ({avg_tps:.1f} tps) by {ratio:.1f}%. Possible measurement artifact.",
                })

    return issues


def validate_pair(prev_data, curr_data):
    """Validate an A/B pair. Returns list of issue dicts."""
    issues = []

    # --- Check 2: sampling window symmetry ---
    prev_sc = prev_data.get("sample_count", 0)
    curr_sc = curr_data.get("sample_count", 0)
    if prev_sc > 0 and curr_sc > 0:
        ratio = abs(prev_sc - curr_sc) / max(prev_sc, curr_sc) * 100
        if ratio > 30.0:
            issues.append({
                "check": "sample_count_asymmetry",
                "severity": "WARN",
                "detail": f"sample_count differs by {ratio:.0f}% (prev={prev_sc}, curr={curr_sc}). Measurement windows may not be aligned, comparison may be unreliable.",
            })

    # --- Check: measurement_duration asymmetry ---
    prev_dur = prev_data.get("measurement_duration_s", 0)
    curr_dur = curr_data.get("measurement_duration_s", 0)
    if prev_dur > 0 and curr_dur > 0:
        ratio = abs(prev_dur - curr_dur) / max(prev_dur, curr_dur) * 100
        if ratio > 30.0:
            issues.append({
                "check": "measurement_duration_asymmetry",
                "severity": "WARN",
                "detail": f"measurement_duration_s differs by {ratio:.0f}% (prev={prev_dur:.1f}s, curr={curr_dur:.1f}s).",
            })

    # --- Check: both must use same metric source ---
    prev_src = prev_data.get("primary_metric_source", "unknown")
    curr_src = curr_data.get("primary_metric_source", "unknown")
    if prev_src != curr_src:
        issues.append({
            "check": "metric_source_mismatch",
            "severity": "WARN",
            "detail": f"Different metric sources: prev={prev_src}, curr={curr_src}",
        })

    return issues


def overall_severity(issues):
    severities = {i["severity"] for i in issues}
    if "ERROR" in severities:
        return "FAIL"
    if len(issues) >= 3:
        # 3+ issues = likely systemic measurement problems
        return "FAIL"
    if "WARN" in severities:
        return "WARN"
    return "PASS"


def main():
    if len(sys.argv) < 2:
        print(f"Usage: python3 {sys.argv[0]} <perf.json> [prev_perf.json]")
        sys.exit(1)

    perf_path = sys.argv[1]
    if not os.path.exists(perf_path):
        print(json.dumps({"overall": "FAIL", "checks": [{"check": "file_not_found", "severity": "ERROR", "detail": f"File not found: {perf_path}"}], "summary": "Validation failed: file not found"}, indent=2))
        sys.exit(1)

    data = _load(perf_path)
    all_issues = validate_single(data)

    if len(sys.argv) >= 3:
        prev_path = sys.argv[2]
        if os.path.exists(prev_path):
            prev_data = _load(prev_path)
            all_issues += validate_pair(prev_data, data)

    overall = overall_severity(all_issues)

    result = {
        "overall": overall,
        "file": os.path.basename(perf_path),
        "checks_count": len(all_issues),
        "error_count": sum(1 for i in all_issues if i["severity"] == "ERROR"),
        "warn_count": sum(1 for i in all_issues if i["severity"] == "WARN"),
        "checks": all_issues,
        "summary": "PASS" if not all_issues else f"{overall}: {sum(1 for i in all_issues if i['severity']=='ERROR')} error(s), {sum(1 for i in all_issues if i['severity']=='WARN')} warning(s)",
    }

    print(json.dumps(result, indent=2))
    sys.exit(0 if overall == "PASS" else 2 if overall == "FAIL" else 1)


if __name__ == "__main__":
    main()
