---
name: vllm-auto-optimizer
description: vLLM 优化执行引擎 — 自动化执行优化措施并验证效果
user_invocable: false
---

# vLLM 优化执行引擎

## 路径约定

所有 shell 脚本内部自动推导 SKILLS_DIR（从脚本自身位置向上两级），调用方无需设置任何环境变量。

Agent 调用脚本时，从系统注入的 skill base directory 推导路径：
- 本 skill 脚本: $SKILL_BASE/scripts/xxx.sh
- 跨 skill 脚本: $SKILL_BASE/../vllm-xxx/scripts/xxx.sh

其中 $SKILL_BASE 为系统提示中的 "Base directory for this skill" 值。

## 调用方式

由 vllm-optimize-pm 的 L1-L4 subagent 各层调用，不直接面向用户。

## 核心脚本

### 统一测量（含服务启停）
```bash
bash $SKILL_BASE/scripts/run_measurement.sh \
  <container> <serve_script> <port> <benchmark_cmd> <duration> <perf_json> <serve_log> \
  [--profiling <profiling_output_dir>]
```
- `benchmark_cmd`: `"single <model_name>"` 或 `"script <benchmark_script>"`
- throughput 模式输出: `{mode:"throughput", generation_throughput_max_tps, generation_throughput_avg_tps, sample_count, measurement_duration_s, total_elapsed_s}`
- profiling 模式额外输出: `{mode:"profiling", decode_step_latency_us, decode_step_throughput_tps, profiling_dir, argmax_count, valid_steps}`
- 内部自动调用 service_manager.sh 完成 clean → start → wait → 采集 → stop 全流程

### 效果判断（throughput 比较）
```bash
python3 $SKILL_BASE/scripts/throughput_judge.py <prev_perf.json> <curr_perf.json>
```
- 判断逻辑: `keep = curr_max_tps > prev_max_tps`
- 输出: `{keep, reason, prev_tps, curr_tps, change_pct}`

### 效果判断（latency 比较）
```bash
python3 $SKILL_BASE/scripts/latency_judge.py <prev_perf.json> <curr_perf.json>
```
- 判断逻辑: `keep = curr_latency < prev_latency`（latency 越低越好）
- 当 latency 提取失败（任一为 0）时返回 `metric="latency_failed"`，不做 fallback
- 编排层（vllm-optimize-pm 的 L1-L4 subagent）收到 `latency_failed` 后负责用多请求 throughput 模式重测
- 输出: `{keep, metric("latency"|"latency_failed"), reason, change_pct}`

### 辅助提取脚本
```bash
python3 $SKILL_BASE/scripts/extract_serve_throughput.py <serve_log> [--after-line N]
python3 $SKILL_BASE/scripts/extract_decode_step_latency.py <profiling_dir> [--trim 5] [--min-interval-us 1000]
```

### 内部脚本（由 run_measurement.sh 调用，调用方不直接使用）
- `service_manager.sh` — vllm 服务生命周期管理（clean/start/wait/stop）
- `common.sh` — 公共日志和工具函数

## 优化执行流程（每个优化项）

1. 生成新 serve_script（由编排层直接生成）
2. `run_measurement.sh`（throughput 或 profiling 模式）完成 服务启停 + 测量
3. `throughput_judge.py` 或 `latency_judge.py` 判断效果
4. 劣化则回滚（恢复上一版 serve_script），优化则保留叠加
