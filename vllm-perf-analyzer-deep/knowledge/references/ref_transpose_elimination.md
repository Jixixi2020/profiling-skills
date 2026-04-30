# 参考案例: Transpose Kernel 冗余执行消除

## 问题模式

- **触发条件**: `op_statistic.csv` 中 Transpose 算子调用次数 = 模型层数 × decode 步数
- **表现**: 30 次 Transpose/step（= 30 层），占 device time 4.6%，input shape `4096,4` 对应 conv1d weight
- **根因**: `causal_conv1d_update_npu()` 中每次 decode 都对静态 weight 执行 `.transpose(-1,-2).contiguous()`

## 诊断链路

1. `op_statistic.csv` → Transpose: AI_VECTOR_CORE, count=3030, ratio=4.6%
2. 计算: 3030 / 101 steps = 30/step = 模型层数 → 每层每步执行一次
3. `kernel_details.csv` → Transpose input shape `4096,4`（对应 conv1d weight `[out_ch, width]`）
4. 源码追踪 → `causal_conv1d.py` 中 `causal_conv1d_update_npu()`:
   ```python
   weight = rearrange(weight, "d 1 w -> d w")  # view, 无 kernel
   weight = weight.transpose(-1, -2).contiguous()  # 产生 Transpose kernel!
   ```
5. weight 是模型参数，推理中不变 → 静态参数在热路径上重复转换

## 关键区分: view vs contiguous

- `tensor.transpose(0, 1)` → 纯 view 操作，只改 stride，不产生 kernel
- `tensor.transpose(0, 1).contiguous()` → 实际内存重排，产生 Transpose kernel
- 只有后者会出现在 op_statistic.csv 中

## 修复模式: lazy init 预转置

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

## cudagraph 约束

- cudagraph capture 录制的是算子序列，capture 后删除源码中的 transpose 无效
- 必须在 capture 前完成 weight layout 变更（lazy init 在第一次 forward 时执行）
- prefill 不走 cudagraph，可以用 view transpose 适配

## 踩坑记录

| 尝试 | 结果 | 原因 |
|------|------|------|
| 只改 conv_state layout | +2.8% 劣化 | conv_state 不是瓶颈，改 layout 引入额外开销 |
| 只删 decode 中的 transpose 代码 | +0.9% 劣化 | cudagraph 已录制 transpose，删代码无效 |
| lazy init 预转置 + 删 decode transpose | -2.6% 提升 | capture 前改 layout，录制时不含 transpose |

## 效果

- Transpose kernel: 30次/step → 0次/step
- Step latency: 11.58ms → 11.28ms（-2.6%）
- 在新版 vllm-ascend 上: Transpose 减少 81.9%，时间减少 30.6%

## 适用条件

- Transpose 调用次数 / decode_steps ≈ 模型层数（整数倍关系）
- Transpose input shape 对应模型静态参数（weight/bias）
- 使用 cudagraph 模式
- 模型包含 conv1d 等需要 weight transpose 的算子
