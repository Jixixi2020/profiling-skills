# Sigmoid Gating Triton Kernel num_warps 调优

## 模型信息
- 模型: Qwen3.5-35B-A3B (MoE, 使用 sigmoid gating)
- 硬件: Ascend NPU (AI_VECTOR_CORE)
- 框架: vLLM + vllm-ascend

## 性能瓶颈

`op_statistic.csv` 显示 `fused_sigmoid_gating` 在多请求模式占 26.2%，是绝对热点算子。

源码追踪到 `sigmoid_gating.py:348`，发现 `num_warps=1` 硬编码，Vector Core 并行度未充分利用。

诊断链路:
1. `op_statistic.csv` -> `fused_sigmoid_gating` 占比 26.2%（多请求模式）
2. 源码追踪 -> `sigmoid_gating.py` 第 348 行
3. 发现 triton kernel 参数 `num_warps=1` 硬编码
4. AI_VECTOR_CORE 支持更高并行度，`num_warps=1` 是保守默认值

## 优化方案

将 triton kernel 的 `num_warps` 参数从 1 调整为 4，提升 Vector Core 并行度:

```python
# 修复前:
@triton.jit
def fused_sigmoid_gating_kernel(...):
    ...

# 调用处 num_warps=1（硬编码）
fused_sigmoid_gating_kernel[grid](... , num_warps=1, num_stages=1)

# 修复后:
fused_sigmoid_gating_kernel[grid](... , num_warps=4, num_stages=1)
```

调优思路:
- `num_warps` 控制 Vector Core 上的并行 warp 数
- 硬编码为 1 时只用 1 个 warp，浪费并行能力
- 增大到 4 可提升并行度，但过大可能导致寄存器压力
- 需要实测验证最优值

## 实验效果

- Decode latency: -3.0%
- 该 kernel 占比从 26.2% 显著下降
- 风险等级: 低（仅参数调优，不改逻辑）

## 关键词
latency, triton, num_warps, sigmoid_gating, AI_VECTOR_CORE, kernel 参数, MoE, 并行度, 算子调优
