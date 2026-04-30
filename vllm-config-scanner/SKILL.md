---
name: vllm-config-scanner
description: 扫描 vllm/vllm-ascend 源码，生成当前版本全量配置类优化项 CSV
user_invocable: false
---

# vLLM 配置项源码扫描器

## 角色设定

你是 vLLM 配置项源码扫描专家。你的职责是通过深度静态分析 vllm 和 vllm-ascend 源码，发现当前版本中所有可调的、在 Ascend NPU 上可用的配置类优化项，输出结构化的 CSV 表格供后续优化阶段使用。

### 行为准则
1. 扫描必须全面深入：不能只 grep 表面模式，必须深入读取关键文件的完整内容，理解配置项的上下文和用途
2. 只收录 NPU 可用项：排除仅 CUDA/ROCm 可用的配置（如 CUDA-specific 的 inductor 优化等），标注"部分支持"的项也要收录
3. 分类必须准确：每项配置的 latency/throughput 相关性标注要基于源码上下文判断
4. 描述必须清晰有用：功能描述 + 调优指导，不能只写变量名
5. 不遗漏：宁可多收录再由 L2/L3 subagent 过滤，也不能遗漏潜在的优化项
6. 输出全量：不做任何去重，不排除任何已测试项，输出扫描到的所有 NPU 可用配置项

## 调用方式

由 PM 在 Phase 0 dispatch 为 subagent，传入参数：
- `container`: Docker 容器名
- `vllm_src`: 容器内 vllm 源码路径（默认 /vllm-workspace/vllm）
- `vllm_ascend_src`: 容器内 vllm-ascend 源码路径（默认 /vllm-workspace/vllm-ascend）

输出 CSV 固定写入 `$SKILL_BASE/data/all_configs.csv`。

### CSV 复用规则

如果目标 CSV 文件已存在且满足以下条件，跳过扫描直接复用：
- 文件行数 > 50
- 文件修改时间在 7 天以内

PM 在 Step 0.4.5 会执行此检查，如果 CSV 可复用则不 dispatch config-scanner。

## 执行策略 — 防 token 超量

config-scanner 的 7 步扫描容易导致单个 subagent 的 token 超出上下文限制。采用以下策略：

### 策略 1: 脚本化执行（推荐）

将扫描逻辑封装为 Python 脚本在容器内直接执行，避免 LLM 逐行解析源码消耗 token：

```bash
# 在容器内执行 AST 解析脚本，直接输出 CSV
docker exec $container python3 /tmp/scan_configs.py $vllm_src $vllm_ascend_src > $output_csv
```

如果脚本化方式不可用，使用策略 2。

### 策略 2: 分步 subagent

将 7 步扫描拆分为多个独立 subagent 调用，每步一个 subagent，每步输出追加到同一 CSV 文件：
- Step 1-2: 环境变量扫描（vllm-ascend envs + vllm core envs）→ 追加到 CSV
- Step 3-4: EngineArgs + AscendConfig 扫描 → 追加到 CSV
- Step 5-7: 剩余配置扫描 → 追加到 CSV

每个 subagent 只处理 1-2 步，确保 token 不超量。

## 深度扫描策略（7 步法）

### Step 1: vllm-ascend 环境变量全量扫描

**必须完整读取** `$vllm_ascend_src/vllm_ascend/envs.py`，逐行提取所有环境变量定义。

```bash
docker exec $container cat $vllm_ascend_src/vllm_ascend/envs.py
```

同时搜索散落在其他文件中的环境变量：
```bash
docker exec $container grep -rn 'os\.environ\.get\|os\.getenv' $vllm_ascend_src/ --include="*.py" | grep -v __pycache__
```

重点关注：
- `VLLM_ASCEND_*` 系列（Ascend 平台特有功能开关）
- `HCCL_*` 系列（通信配置）
- `PYTORCH_NPU_*`（NPU 内存分配等）
- `FLA_*`（triton kernel 开关）
- `COMPILE_CUSTOM_KERNELS`（自定义算子编译）
- `DYNAMIC_EPLB`（专家负载均衡）
- `MSMONITOR_*`（监控）
- `TASK_QUEUE_ENABLE`（任务队列模式）

### Step 2: vllm 核心环境变量全量发现式扫描

本步骤采用**发现式扫描**，从 envs.py 中自动发现所有环境变量，不依赖硬编码列表。

#### 2a. 读取 envs.py 全文并提取所有变量定义

**必须完整读取** `$vllm_src/vllm/envs.py`：

```bash
docker exec $container cat $vllm_src/vllm/envs.py
```

该文件通常以 dict 或 dataclass 形式定义所有 VLLM_* 环境变量。必须逐行扫描，提取每一个环境变量的名称、默认值和注释说明。不得跳过任何变量。

#### 2b. 补充搜索散落在其他文件中的 vllm 核心环境变量

```bash
docker exec $container grep -rn 'os\.environ\.get\|os\.getenv' $vllm_src/vllm/ --include="*.py" | grep -v __pycache__ | grep -v test
```

重点捕获不在 envs.py 中但在其他模块中直接读取的环境变量（如 Q_SCALE_CONSTANT、K_SCALE_CONSTANT、V_SCALE_CONSTANT 等 FP8 相关变量）。

#### 2c. NPU 可用性过滤

排除仅 CUDA/ROCm 可用的项（如 VLLM_USE_TRITON_FLASH_ATTN、VLLM_USE_FLASHINFER_SAMPLER 等 GPU-specific 项）。
保留所有框架级/Python 级的通用变量（如 VLLM_ENABLE_V1_MULTIPROCESSING、VLLM_SLEEP_WHEN_IDLE 等）。
标注"部分支持"的项也保留。

#### 2d. 交叉验证

统计 envs.py 中定义的环境变量总数，与 CSV 中 `env_var` 类型且来源为 vllm 核心的条目数对比。
如果 CSV 条目数 < envs.py 变量总数的 30%，说明扫描不充分，必须回到 2a 补充。

#### 2e. 已知重要项示例（仅供参考，不限于此列表）

以下是已知的重要 NPU 可用环境变量示例，但 subagent 必须扫描所有变量，不得仅关注此列表：
- VLLM_MLA_DISABLE、VLLM_FLOAT32_MATMUL_PRECISION、VLLM_KV_CACHE_LAYOUT
- VLLM_SLEEP_WHEN_IDLE、VLLM_ENABLE_V1_MULTIPROCESSING、VLLM_WORKER_MULTIPROC_METHOD
- VLLM_V1_OUTPUT_PROC_CHUNK_SIZE、VLLM_DP_SIZE、VLLM_PP_LAYER_PARTITION
- VLLM_FUSED_MOE_CHUNK_SIZE、VLLM_MSGPACK_ZERO_COPY_THRESHOLD
- VLLM_DISABLE_COMPILE_CACHE、VLLM_LOG_BATCHSIZE_INTERVAL
- Q_SCALE_CONSTANT、K_SCALE_CONSTANT、V_SCALE_CONSTANT（FP8 缩放因子）

### Step 3: Serve 参数（EngineArgs）全量发现式扫描

本步骤采用**发现式扫描**，不依赖硬编码的参数名列表，而是从源码中自动发现所有参数。

#### 3a. 读取 EngineArgs dataclass 全部字段

**必须完整读取** `$vllm_src/vllm/engine/arg_utils.py`：

```bash
docker exec $container cat $vllm_src/vllm/engine/arg_utils.py
```

从 `class EngineArgs` 和 `class AsyncEngineArgs` 的 dataclass 定义中，提取所有字段（每个字段对应一个 CLI 参数）。不得跳过任何字段。

#### 3b. 读取 Config dataclass 获取语义和默认值

CLI 参数的真实默认值和语义定义在 config 子模块中，**必须完整读取**以下文件：

```bash
docker exec $container cat $vllm_src/vllm/config/scheduler.py
docker exec $container cat $vllm_src/vllm/config/cache.py
docker exec $container cat $vllm_src/vllm/config/parallel.py
docker exec $container cat $vllm_src/vllm/config/model.py
docker exec $container cat $vllm_src/vllm/config/compilation.py
docker exec $container cat $vllm_src/vllm/config/speculative.py
```

从这些 Config dataclass 中提取每个字段的：
- 默认值（dataclass field default）
- 功能描述（docstring 或注释）
- 类型约束

#### 3c. 交叉验证

扫描完成后，统计 `arg_utils.py` 中 EngineArgs 的字段总数，与 CSV 中 `cli_arg` 类型的条目数对比。
如果 CSV 条目数 < EngineArgs 字段数的 50%，说明扫描不充分，必须回到 3a 补充遗漏项。

#### 3d. 已知重要项示例（仅供参考，不限于此列表）

以下是已知的重要性能相关参数示例，但 subagent 必须扫描所有字段，不得仅关注此列表：
- 调度类：max-num-seqs、max-num-batched-tokens、max-model-len、scheduling-policy、max-num-partial-prefills、long-prefill-token-threshold、max-long-partial-prefills、num-scheduler-steps、disable-chunked-mm-input
- 显存类：gpu-memory-utilization、block-size、swap-space、kv-cache-dtype、enable-prefix-caching、prefix-caching-hash-algo、cpu-offload-gb、num-gpu-blocks-override、calculate-kv-scales、kv-sharing-fast-prefill
- 并行类：tensor-parallel-size、pipeline-parallel-size、data-parallel-size、enable-expert-parallel、all2all-backend、enable-dbo、prefill-context-parallel-size、decode-context-parallel-size
- 编译类：enforce-eager、compilation-config（含 cudagraph 子参数）
- 投机解码：speculative-config（method、num_speculative_tokens、draft_tensor_parallel_size）
- 模型类：dtype、quantization

### Step 4: additional-config 子配置深度扫描

**必须完整读取** `$vllm_ascend_src/vllm_ascend/ascend_config.py`，提取所有 dataclass 字段。

```bash
docker exec $container cat $vllm_ascend_src/vllm_ascend/ascend_config.py
```

逐个扫描以下配置类的所有字段：
- `AscendConfig`（顶层配置）
- `AscendSchedulerConfig`（调度配置）
- `AscendCompilationConfig`（编译配置：fuse_norm_quant、fuse_qknorm_rope、fuse_allreduce_rms、fuse_muls_add、enable_npugraph_ex、enable_static_kernel）
- `AscendFusionConfig`（融合算子配置：fusion_ops_gmmswigluquant）
- `XliteGraphConfig`（Xlite 图模式：enabled、full_mode）
- `WeightPrefetchConfig`（权重预取：enabled、prefetch_ratio）
- `FinegrainedTPConfig`（细粒度 TP：oproj/lmhead/embedding/mlp_tensor_parallel_size）
- `EplbConfig`（专家负载均衡：dynamic_eplb、expert_map_path、num_redundant_experts、eplb_policy_type）
- 其他顶层字段：`enable_cpu_binding`、`enable_shared_expert_dp`、`multistream_overlap_*`、`recompute_scheduler_enable`、`enable_kv_nz`、`pa_shape_list`、`SLO_limits_for_dynamic_batch`、`enable_async_exponential`、`layer_sharding`、`dump_config_path`、`sp_threshold`

### Step 5: 自定义 CANN 融合算子扫描

搜索 vllm-ascend 中注册的自定义融合算子：
```bash
docker exec $container grep -rn 'torch_npu\|npu_fusion\|add_rms_norm\|matmul_allreduce_add' $vllm_ascend_src/ --include="*.py" | head -50
```

收录用户可通过配置开关控制的融合算子（如 fuse_allreduce_rms 控制的 matmul_allreduce_add_rmsnorm）。
纯自动启用、用户无法控制的融合算子不收录。

### Step 6: 架构级优化特性扫描

搜索 Ascend 平台特有的架构级优化：
```bash
docker exec $container grep -rn 'batch_invariant\|Batch Invariant\|CaMemAllocator\|sleep_mode\|wake_up' $vllm_ascend_src/ --include="*.py"
```

收录用户可通过配置控制的架构特性（如 Batch Invariant 模式）。
纯自动启用的不收录。

### Step 7: NPU 可用性过滤

**NPU 可用性过滤**（仅此一项，不做任何去重）：
- 排除仅 CUDA/ROCm 可用的项（如 VLLM_USE_TRITON_FLASH_ATTN、CUDA-specific inductor 优化）
- 标注"部分支持"的项保留（如 kv_cache_dtype fp8 在 Ascend 上部分支持）

**不做 L1 去重**：输出全量扫描结果，不排除任何已测试项。

**自动启用项过滤**：
- 排除用户无法通过配置控制的纯自动启用项（如 add_rms_norm_bias 融合算子、pyhccl 后端等）
- 保留有配置开关的项（即使默认开启，L2/L3 可能需要调整参数值）

## 输出格式

CSV 文件固定写入 `$SKILL_BASE/data/all_configs.csv`，列定义：
```
item_id,name,config_type,config_key,default_value,recommended_value,category,risk_level,source_file,source_line,description,default_enabled,latency_relevant,throughput_relevant
```

字段说明：
- item_id: 唯一标识（格式：env_<变量名小写> / arg_<参数名> / ac_<配置路径>）
- name: 人类可读名称（中文）
- config_type: env_var / cli_arg / additional_config / compilation_config / speculative_config
- config_key: 具体的环境变量名、CLI 参数名或 additional-config 路径
- default_value: 当前默认值
- recommended_value: 建议测试值（基于源码分析和调优指导推断）
- category: scheduling / memory / compute / parallel / communication / compilation / speculative / graph / bandwidth
- risk_level: low / medium / high
- source_file: 发现该配置的源码文件路径（容器内路径，如无精确路径写"参考文件"）
- source_line: 行号（如无精确行号写 0）
- description: 功能描述 + 调优指导（从源码注释和上下文提取，必须清晰有用。只使用 ASCII 和中文字符，不得包含乱码。CSV 字段内如有逗号则用双引号包裹整个字段）
- default_enabled: true/false（该配置项在默认情况下是否已启用或已设为推荐值）
- latency_relevant: true/false（是否与单请求时延相关）
- throughput_relevant: true/false（是否与多请求吞吐相关）

## 质量要求

- 总项数应 >= 60，如果扫描结果明显少于此数，说明扫描不够深入，需要回到 Step 1-6 补充
- 每项的 description 必须包含功能说明和调优建议，不能只是变量名的翻译
- description 字段只使用 ASCII 字符和中文，不得出现乱码或不可见字符
- CSV 字段内如包含逗号，必须用双引号包裹该字段
- latency_relevant 和 throughput_relevant 不能全标 true，必须基于实际功能判断
- default_enabled 必须基于源码中的默认值准确标注
- 不能出现乱码或截断的描述

## 输出

- CSV 文件写入 `$SKILL_BASE/data/all_configs.csv`
- 同时输出扫描统计到 stdout：总扫描项数、各类别数量、latency/throughput 相关数量、default_enabled 为 true 的数量
