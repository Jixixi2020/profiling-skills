# M-RoPE 非 Contiguous 2D H2D Copy 修复

## 模型信息
- 模型: Qwen3.5-35B-A3B (使用 M-RoPE 位置编码)
- 硬件: Ascend NPU
- 框架: vLLM + vllm-ascend, async_scheduling + cudagraph 模式

## 性能瓶颈

`step_trace_time.csv` 中 Free 占比 33.8%，decode step latency 15.9ms，其中 NPU 空等 host 约 5.3ms/step。

根因: `mrope_positions` 是 `[3, seq_len]` 的 2D tensor，按列切片 `positions[i]` 产生非 contiguous view，触发 torch_npu copy_ 慢路径。

诊断链路:
1. `step_trace_time.csv` -> Free 33.8%（异常高）
2. `api_statistic.csv` -> `aclrtSynchronizeStreamWithTimeout` 总耗时 437ms，454 次调用
3. `operator_details.csv` -> `aten::copy_` 的 input shape 为 `3,16385`（2D tensor）
4. 源码追踪 -> `vllm-ascend/vllm_ascend/worker/worker.py` 中 `execute_model()` 将 `mrope_positions` 从 CPU copy 到 NPU

torch_npu copy_ 慢路径机制:
- 快路径条件: src 是 contiguous + src 在 pinned memory + dst 在 NPU
- 2D tensor 列切片 -> 非 contiguous src -> `.clone()` 分配普通 host memory -> 非 pinned memory -> `aclnnInplaceCopy` fallback 全流同步 -> 阻塞 host 线程 -> 打断异步流水线 -> 产生 Free

## 优化方案

拆 2D copy 为 N x 1D copy，确保每个 slice 是 contiguous 且在 pinned memory 中:

```python
# 修复前（慢路径）:
positions = mrope_positions.to(device)  # [3, seq_len] 2D copy

# 修复后（快路径）:
# 在 CPU 侧预切片为 contiguous 1D tensors
pos_list = [mrope_positions[i].contiguous().pin_memory() for i in range(3)]
positions = torch.stack([p.to(device, non_blocking=True) for p in pos_list])
```

关键点:
- 每个 1D slice `.contiguous()` 后是 contiguous 的
- `.pin_memory()` 确保在 pinned pool 中
- `non_blocking=True` 保持异步
- 3 次 1D copy（各 0.08ms）替代 1 次 2D copy（5.3ms）

## 实验效果

- Free 占比: 33.8% -> 13.4%
- Step latency: 15.9ms -> 10.6ms (-33%)
- 单项优化收益最大的一项

## 关键词
latency, Free, contiguous, copy_, H2D, mrope, pin_memory, non_blocking, async_scheduling, cudagraph, 2D tensor, 慢路径, aclrtSynchronizeStreamWithTimeout, 非 contiguous
