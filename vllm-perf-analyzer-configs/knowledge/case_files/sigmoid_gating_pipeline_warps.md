# Sigmoid Gating Pipeline Depth and Warp Parallelism Joint Tuning

## Model Info
- Model: Qwen3.5-27B (dense hybrid, GatedDeltaNet + Attention)
- Hardware: Ascend NPU A2 (AI_VECTOR_CORE)

## Relation to Existing Case
| 维度 | sigmoid_gating_tuning (已有) | 本案例 |
|------|-----|------|
| 模型 | Qwen3.5-35B-A3B (MoE) | Qwen3.5-27B (Dense hybrid) |
| 调优参数 | num_warps 1→4 | num_stages 3→2 + num_warps 1→2 |
| 新增维度 | 无 | num_stages (pipeline depth) |
| 效果 | decode latency -3.0% | throughput +2.69% |

选择指南：MoE 模型优先参考 sigmoid_gating_tuning（num_warps=4）；Dense hybrid 模型优先参考本案例（num_stages=2 + num_warps=2 联合调优）。

## Bottleneck
fused_sigmoid_gating 40.86% in multi-request mode (14.9x amplification from single-request 2.74%).
Source: sigmoid_gating.py:347-348, num_stages=3/num_warps=1 hardcoded.
Pipeline depth=3 causes excessive register pressure on AI_VECTOR_CORE; single warp underutilizes parallelism.

## Fix
num_stages 3->2, num_warps 1->2. Pipeline depth reduction + warp parallelism increase.

## Result
Throughput +2.69% (555.75 -> 570.71 avg_tps). Low risk.

## Keywords
throughput, triton, num_stages, num_warps, sigmoid_gating, AI_VECTOR_CORE, pipeline_depth, GatedDeltaNet, dense_hybrid, joint_tuning
