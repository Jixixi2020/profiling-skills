---
name: vllm-optimize-l5
description: L5 汇总验证与可视化 subagent — 汇总 L1-L4 优化项，补测缺失指标，绘制逐项叠加折线图，撰写总结报告
---

# L5 汇总验证与可视化

## 角色定位

L5 是优化流程的最后一个执行 subagent，在 L1-L4 全部完成后执行。职责：
1. 汇总所有 KEEP 优化项，检查数据完整性
2. 补测缺失的 latency/throughput 数据
3. 补充端到端完成时间实验
4. 绘制 3 组逐项叠加折线图
5. 撰写总结报告

## 参数

继承 PM 传入的所有参数，额外需要：
- 各 layer 的 results.json 路径
- 各 layer 的 best_serve.sh 路径
- baseline latency (decode_step_latency_us) 和 throughput (generation_throughput_avg_tps)
- 用户 benchmark 脚本路径

## 执行流程

### Step 1: 汇总 KEEP 优化项

读取 L1~L4 的 results.json，提取所有 KEEP 优化项，按 Layer 顺序排列：
- L1: forced 项 + tested KEEP 项
- L2: KEEP 项
- L3: KEEP 项
- L4: KEEP 项

对每项检查是否同时具有：
- decode_step_latency_us（单请求 latency）
- generation_throughput_avg_tps（多请求 throughput）

记录缺失项列表。

### Step 2: 补测缺失指标

对于缺失 latency 数据的优化项：
- 按叠加顺序，逐项应用优化，运行单请求 profiling 提取 decode_step_latency_us
- 所有 run_measurement.sh 调用必须使用 run_in_background=true

对于缺失 throughput 数据的优化项：
- 按叠加顺序，逐项应用优化，运行多请求 benchmark 提取 generation_throughput_avg_tps

### Step 3: 端到端完成时间实验

这是一组独立的补充实验，测量运行用户 benchmark 脚本的端到端完成时间（total_elapsed_s）：

1. 基线：用原始 serve 配置运行 benchmark，记录 total_elapsed_s
2. 逐项叠加：按优化项顺序，每叠加一项后运行 benchmark，记录 total_elapsed_s
3. 最终：所有优化项叠加后的 total_elapsed_s

### Step 4: 绘制 3 组折线图

使用 matplotlib 绘制 3 张折线图，X 轴为优化项叠加顺序（baseline → +opt1 → +opt2 → ...）：

1. **单请求 decode latency 折线图** (`latency_progression.png`)
   - Y 轴: decode_step_latency_us
   - 趋势应为下降（latency 降低）

2. **多请求 throughput 折线图** (`throughput_progression.png`)
   - Y 轴: generation_throughput_avg_tps
   - 趋势应为上升（throughput 提升）

3. **端到端完成时间折线图** (`e2e_time_progression.png`)
   - Y 轴: total_elapsed_s
   - 趋势应为下降（完成时间缩短）

每张图要求：
- 每个数据点标注具体数值
- 标注每步的 delta%
- 图例清晰，标题包含模型名和硬件信息

### Step 5: 撰写总结报告

输出 `$base_dir/final_deliverables/layer5/layer5_report.md`，包含：
1. 优化项汇总表（名称、Layer、类型、latency delta%、throughput delta%）
2. 三组折线图引用
3. 每项优化的效果分析（为什么 latency 有/无改善，为什么 throughput 有/无改善）
4. 评价规则合规性验证：每项 KEEP 满足 "latency 改善>1%" 或 "latency 持平±1% 且 throughput 改善>1%"
5. 总体效果总结

### Step 6: 更新 state.json

python3 $SKILL_BASE/../vllm-optimize-pm/scripts/layer_state.py set_layer_status $base_dir layer5 completed

## 交付件

| 文件 | 说明 |
|------|------|
| $base_dir/layer5/results.json | 汇总数据 |
| $base_dir/final_deliverables/layer5/layer5_report.md | 总结报告 |
| $base_dir/final_deliverables/layer5/charts/latency_progression.png | latency 折线图 |
| $base_dir/final_deliverables/layer5/charts/throughput_progression.png | throughput 折线图 |
| $base_dir/final_deliverables/layer5/charts/e2e_time_progression.png | 端到端时间折线图 |

## results.json schema

```json
{
  "layer": "L5",
  "status": "completed",
  "total_keep_items": "N",
  "items": [
    {
      "name": "opt_name",
      "layer": "L1",
      "type": "forced|config|code",
      "latency_us": 31000,
      "latency_delta_pct": -2.3,
      "throughput_avg_tps": 534.5,
      "throughput_delta_pct": 50.1,
      "e2e_time_s": 320.0,
      "e2e_delta_pct": -3.5,
      "evaluation_rule": "latency_improved|latency_flat_throughput_improved"
    }
  ],
  "baseline": {
    "latency_us": 32000,
    "throughput_avg_tps": 356.05,
    "e2e_time_s": 350.0
  },
  "final": {
    "latency_us": 28000,
    "throughput_avg_tps": 570.71,
    "e2e_time_s": 290.0
  }
}
```

## 报告要求

报告必须包含以下内容，缺少任何一项将被质量总监判定为不合格：
1. 优化项汇总表（含所有 KEEP 项的双指标数据）
2. 三组折线图（latency/throughput/e2e_time），保存到 charts/ 目录并在报告中引用
3. 评价规则合规性验证（逐项检查）
4. 每项优化的效果分析
5. 总体效果总结（baseline vs final 的三个指标对比）
