# Conv1d Weight 预转置消除 Transpose Kernel

## 模型信息
- 模型: Qwen3.5-35B-A3B (MoE, 含 conv1d 层)
- 硬件: Ascend NPU
- 框架: vLLM + vllm-ascend, cudagraph 模式

## 性能瓶颈

`op_statistic.csv` 中 Transpose 算子调用次数 = 模型层数 x decode 步数（30 次/step = 30 层），占 device time 4.6%。

根因: `causal_conv1d_update_npu()` 中每次 decode 都对静态 weight 执行 `.transpose(-1,-2).contiguous()`，产生 Transpose kernel。weight 是模型参数，推理中不变，属于静态参数在热路径上的重复转换。

诊断链路:
1. `op_statistic.csv` -> Transpose: AI_VECTOR_CORE, count=3030, ratio=4.6%
2. 计算: 3030 / 101 steps = 30/step = 模型层数
3. `kernel_details.csv` -> Transpose input shape `4096,4` 对应 conv1d weight `[out_ch, width]`
4. 源码追踪 -> `causal_conv1d.py` 中 `weight.transpose(-1, -2).contiguous()`

关键区分:
- `tensor.transpose(0, 1)` -> 纯 view 操作，不产生 kernel
- `tensor.transpose(0, 1).contiguous()` -> 实际内存重排，产生 Transpose kernel

## 优化方案

采用 lazy init 预转置模式: 在模型第一次 forward（cudagraph capture 前）做一次性 weight layout 变更，后续 decode 路径直接使用预转置的 weight。

```python
# 在模型 forward 开头（cudagraph capture 前）做一次性 layout 变更
if not hasattr(self.conv1d, '_weight_transposed'):
    w = self.conv1d.weight.data  # [out_ch, 1, width]
    self.conv1d.weight.data = w.squeeze(1).transpose(-1, -2).contiguous()
    self.conv1d._weight_transposed = True

# decode 路径: 直接使用预转置的 weight，跳过运行时 transpose
weight = self.conv1d.weight  # 已经是 [width, out_ch] layout

# prefill 路径: 不走 cudagraph，用 view transpose 适配原始 API
weight = self.conv1d.weight.transpose(-1, -2)  # view only, 无 kernel
```

cudagraph 约束: 必须在 capture 前完成 weight layout 变更（lazy init 在第一次 forward 时执行），capture 后删除源码中的 transpose 无效。

踩坑记录:
- 只改 conv_state layout -> +2.8% 劣化（conv_state 不是瓶颈）
- 只删 decode 中的 transpose 代码 -> +0.9% 劣化（cudagraph 已录制 transpose）
- lazy init 预转置 + 删 decode transpose -> -2.6% 提升（正确方案）

## 实验效果

- Transpose kernel: 30 次/step -> 0 次/step
- Step latency: 11.58ms -> 11.28ms (-2.6%)
- 在新版 vllm-ascend 上: Transpose 减少 81.9%，时间减少 30.6%

## 关键词
latency, Transpose, conv1d, contiguous, causal_conv1d, weight, cudagraph, lazy init, 预转置, AI_VECTOR_CORE, 静态参数, 冗余操作
