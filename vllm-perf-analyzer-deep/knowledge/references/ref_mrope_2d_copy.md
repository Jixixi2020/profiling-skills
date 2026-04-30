# 参考案例: M-RoPE 2D Copy 导致 Free 时间异常

## 问题模式

- **触发条件**: async_scheduling + cudagraph 模式下，step_trace_time.csv 中 Free 占比 33.8%
- **表现**: decode step latency 15.9ms，其中 NPU 空等 host 约 5.3ms/step
- **根因**: `mrope_positions` 是 `[3, seq_len]` 的 2D tensor，按列切片 `positions[i]` 产生非 contiguous view，触发 torch_npu copy_ 慢路径

## 诊断链路

1. `step_trace_time.csv` → Free 33.8%（异常高）
2. `api_statistic.csv` → `aclrtSynchronizeStreamWithTimeout` 总耗时 437ms，454 次调用
3. `operator_details.csv` → `aten::copy_` 的 input shape 为 `3,16385`（2D tensor）
4. 源码追踪 → `vllm-ascend/vllm_ascend/worker/worker.py` 中 `execute_model()` 将 `mrope_positions` 从 CPU copy 到 NPU

## torch_npu copy_ 慢路径机制

快路径条件（全部满足才走快路径）:
- src 是 contiguous
- src 在 pinned memory 中
- dst 在 NPU 上

慢路径触发链:
1. 2D tensor 列切片 → 非 contiguous src
2. torch_npu 对非 contiguous src 调用 `.clone()` → 在普通 host memory 分配新 tensor
3. clone 出的 tensor 不在 CachingHostAllocator 管理的 pinned pool 中
4. `aclnnInplaceCopy` 检测到非 pinned memory → fallback 到全流同步模式
5. `aclrtSynchronizeStreamWithTimeout` 阻塞 host 线程 → 打断异步流水线 → 产生 Free

## 修复模式: 拆 2D copy 为 N×1D copy

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

## 效果

- Free 占比: 33.8% → 13.4%
- Step latency: 15.9ms → 10.6ms（-33%）

## 适用条件

- async_scheduling + cudagraph 模式
- 存在 2D+ tensor 的 H2D copy
- copy 的 src 是非 contiguous 的（如列切片、stride 不连续）
