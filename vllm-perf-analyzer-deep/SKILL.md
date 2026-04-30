# vLLM Ascend NPU 深度性能分析

## 职责

基于双模式 profiling 数据做深度源码级分析，发现代码修改类优化机会。专注于算子级调优、kernel 参数优化、冗余操作消除等需要阅读源码才能发现的优化点。

## 调用方式

由 vllm-optimize-l4 subagent 的 Layer 4 调用。

## 输入

- `profiling_decode`: 单请求 decode profiling 目录
- `profiling_multi`: 多请求 profiling 目录
- `state_json`: state.json 路径
- `container`: 容器名（用于源码追踪）

## 输出

优化建议列表，每项包含:
- 源码位置（文件、行号）
- 修改方案（代码 diff 或参数调整）
- 风险等级（低/中/高）
- 预期收益

## 方法论（6 步）

以下方法论从 Qwen3.5-35B-A3B 优化实践中总结，适用于 Ascend NPU 上的 vLLM 推理优化。

### Step 1: 双模式 Timeline 对比

对比单请求和多请求的 timeline 占比差异:

```bash
python3 $SKILL_BASE/scripts/analyze_timeline.py <decode_step_trace_time.csv>
python3 $SKILL_BASE/scripts/analyze_timeline.py <multi_step_trace_time.csv>
```

关注点:
- Computing 占比变化（多请求应更高，否则有调度问题）
- Comm overlap 差异（多请求应有更多通算重叠）
- Free 占比（>10% 需深入分析根因）

### Step 2: Top 算子对比

提取两种模式下的 top-10 算子，对比 avg 耗时放大倍数:

```bash
python3 $SKILL_BASE/scripts/parse_op_stats.py <decode_op_statistic.csv>
python3 $SKILL_BASE/scripts/parse_op_stats.py <multi_op_statistic.csv>
```

关注点:
- 放大倍数 >5x 的算子（batch size 敏感，优化收益大）
- 多请求模式下占比 >10% 的算子（绝对热点）
- AI_VECTOR_CORE 上的算子（通常有 triton kernel 参数可调）

### Step 3: 源码追踪

对 Step 2 识别的热点算子，在容器内定位源码:

```bash
# 在容器内搜索算子名
docker exec <container> grep -rn "<kernel_name>" /vllm-workspace/vllm-ascend/ --include="*.py"
```

追踪链路: kernel 名 → triton/torch 函数 → 模型层调用 → forward 方法

### Step 4: 可调参数扫描

对定位到的源码，检查以下可调参数:

**4a. Triton kernel 参数**
- `num_warps`: 是否硬编码为 1？增大可提升 Vector Core 并行度
- `num_stages`: pipeline 深度
- `BLOCK_SIZE` / `BK` / `BV`: tile 大小是否最优

**4b. 环境变量开关**
- 搜索 `os.environ.get` 或 `os.getenv`，查找未启用的快速路径
- 典型案例: `FLA_USE_FAST_OPS=1`（快速数学函数）

**4c. 冗余操作**
- `.contiguous()` 调用: 如果输入已 contiguous，则为冗余
- `.transpose().contiguous()`: 如果可以改变存储 layout 避免每次 transpose
- 条件检查 `if not x.is_contiguous()`: 如果调用方能保证 contiguous，可省略

### Step 5: 通信 Overlap 分析

对比单/多请求的通信重叠:

```
单请求: CUDAGraph 模式下通信无法与计算重叠（overlap ≈ 0）
多请求: 多 batch 可实现通算重叠（overlap > 0）
```

如果多请求的 overlap 仍然很低，检查:
- AllReduce/AllGather 是否在关键路径上
- 是否有 sync 操作阻断了 overlap

### Step 6: 生成优化建议

按风险分级输出:

| 风险 | 类型 | 示例 |
|------|------|------|
| 低 | 参数调优 | num_warps 1→4, 环境变量开关 |
| 中 | 逻辑修改 | 删除冗余 contiguous, 缓存 transpose 结果 |
| 高 | Layout 变更 | 改变 tensor 存储格式, 修改 state buffer 分配 |

每项建议必须包含:
- 源码文件和行号
- 具体修改内容（代码 diff）
- 预期收益估算（基于 op_statistic 中的占比）
- 风险说明

## 参考案例

### 案例 1: sigmoid_gating num_warps 调优
- **发现过程**: op_statistic 显示 `fused_sigmoid_gating` 在多请求模式占 26.2%，源码追踪到 `sigmoid_gating.py:348` 发现 `num_warps=1` 硬编码
- **修复**: `num_warps = 4`
- **效果**: decode latency -3.0%

### 案例 2: FLA 快速数学函数
- **发现过程**: 同一 kernel 源码中发现 `FLA_USE_FAST_OPS` 环境变量开关（第 18 行），默认未启用
- **修复**: `export FLA_USE_FAST_OPS=1`
- **效果**: decode latency -3.2%

### 案例 3: weight transpose 缓存
- **发现过程**: vector_redundancy 分析显示 Transpose 7566 次/3s，源码追踪到 `causal_conv1d.py` 每次 decode 都做 `weight.transpose(0,1).contiguous()`
- **修复**: 用 `_weight_transpose_cache` dict 缓存，按 `data_ptr()` 索引
- **效果**: decode latency -2.3%

### 案例 4: mrope contiguous fix
- **发现过程**: free_bottleneck 分析显示 Free 13%，定位到 `model_runner_v1.py` 的 `mrope_positions.cpu` 是非 contiguous 的 2D slice，H2D copy 走慢路径
- **修复**: `.contiguous()` 确保 contiguous 后再 copy
- **效果**: decode latency -4.9%
