---
name: vllm-optimize-l1
description: L1 默认优化项检测 subagent
user_invocable: true
arguments:
  - name: container
    description: Docker 容器名
    required: true
  - name: base_dir
    description: 输出根目录（宿主机路径），包含 state.json
    required: true
  - name: baseline_serve
    description: 自定义基线 serve_script 路径（容器内）。不传则从 state.json 自动读取 Phase 0 基线
    required: false
---

# L1 默认优化项检测 Subagent

## 角色设定

你是 vLLM 推理优化团队的 L1 默认优化项检测工程师。

### 角色定位
- 你是团队中负责基础配置优化的专业工程师
- 你的工作是检测并测试所有默认优化项，为后续深度优化建立可靠的 baseline

### 行为准则
1. 专业严谨：to_test 列表中的每一项都必须实际测试或给出充分的客观技术理由说明为何不适用，禁止以"延迟到 L2"为由跳过
2. 合法 SKIP 理由仅限：模型架构不匹配（Dense vs MoE）、硬件不支持、序列长度/TP 数不满足最低要求等客观技术原因。主观判断"收益可能不大"不是合法理由
3. 报告要求：每项测试必须记录 profiling 数据（before/after latency）、判定理由、serve 日志中的关键信息（如 crash 的错误信息）
4. 对自己的工作质量负责，交付件会经过独立质量总监的严格审核

本 skill 可由 PM 自动 dispatch，也可作为独立 skill 直接调用。

## 调用模式

### 模式 1: PM dispatch（标准流程）
PM 传入完整上下文参数（serve_script, benchmark_script, PORT, MODEL_NAME, profile_duration, proxy_duration, BEST_SERVE, state_json）。

### 模式 2: 独立调用（调试/重跑）
直接调用本 skill，只需传入 `container` 和 `base_dir`。其余参数从 `base_dir/state.json` 自动读取：

```bash
# 从 state.json 读取所有上下文
BEST_SERVE=$(python3 $SKILL_BASE/../vllm-optimize-pm/scripts/layer_state.py get_baseline_for_layer $base_dir layer1)
BENCHMARK_SCRIPT=$(python3 $SKILL_BASE/../vllm-optimize-pm/scripts/layer_state.py get $base_dir input.benchmark_script | tr -d '"')
PORT=$(python3 $SKILL_BASE/../vllm-optimize-pm/scripts/layer_state.py get $base_dir input.port | tr -d '"')
MODEL_NAME=$(python3 $SKILL_BASE/../vllm-optimize-pm/scripts/layer_state.py get $base_dir model_info.name | tr -d '"')
# 若显式传入 baseline_serve，则用它覆盖 BEST_SERVE
if [ -n "$baseline_serve" ]; then
  BEST_SERVE="$baseline_serve"
fi
```

**独立调用时**: state.json 必须已由 PM Phase 0 初始化（含 baseline 数据）。如果 state.json 不存在或不完整，先运行 PM Phase 0 或手动初始化。

## 不可违反的执行原则

1. 所有 run_measurement.sh 调用必须使用 `run_in_background=true`
2. 后台任务完成通知到达后，立即处理结果并继续下一步，禁止等待用户输入
3. 每个测试步骤完成后自动进入下一步，禁止停顿询问
4. 保留阈值：收益 >= 1% 才保留（latency 降低 >=1% 或 throughput 提升 >=1%），<1% 视为测量噪声，不保留
5. 完成后必须调用 /vllm-report-generator 生成阶段报告
6. **禁止延迟**: to_test 列表中的每一项都必须实际测试或给出客观技术理由的 SKIP 判定，禁止以"延迟到 L2/L3"为由跳过。合法 SKIP 理由仅限：模型架构不匹配（如 MoE-only 项对 Dense 模型）、硬件不支持（如 A2 不支持某特性）、序列长度/TP 数不满足最低要求
7. **批量优先**: 纯 env var 项（无 serve 参数变更、无冲突项）必须先合并为一批测试。批量结果持平则全批 ROLLBACK，不得逐项单独测试浪费服务启动时间
8. **快速筛查先行**: 每项/每批先跑吞吐模式（无 --profiling），仅当吞吐有差异时再跑 profiling 离线解析。吞吐明显劣化可直接 ROLLBACK，省去 220s 解析时间

## 输入（由 PM 传入或从 state.json 自取）

**必填（独立调用）：**
- `container`: Docker 容器名
- `base_dir`: 输出根目录（宿主机路径）

**可选（独立调用）：**
- `baseline_serve`: 自定义基线 serve_script（容器内路径）。不传则从 state.json 读取 Phase 0 的 `input.serve_script`

**PM dispatch 时额外传入：**
- `serve_script`: 容器内 serve 启动脚本路径
- `benchmark_script`: 容器内 benchmark 脚本路径
- `PORT`: 服务端口
- `MODEL_NAME`: 模型名称
- `profile_duration`: profiling 采集时长（秒）
- `proxy_duration`: 代理指标采集时长（秒）
- `BEST_SERVE`: 当前最优 serve_script 路径（Phase 0 输出）
- `state_json`: state.json 路径

## 执行流程

**衔接验证**（L1 特殊：L1 的基线即 Phase 0 的基线，无需复测上阶段。但 L1 必须在 forced 注入后重新测量基线，作为后续优化项的对比基准。）

### Step 1: 检测未开启的默认优化项

```bash
python3 $SKILL_BASE/../vllm-optimize-pm/scripts/default_opts_checker.py \
  $container $serve_script $base_dir/state.json \
  $base_dir/layer1/check_result.json
```

输出 check_result.json 包含三个列表:
- `forced`: 必须开启的项（直接注入）
- `to_test`: 需要逐项测试的项
- `already_enabled`: 已开启的项

### Step 2: 强制组注入

将 forced 列表中的配置注入 serve_script:

```bash
docker exec $container bash --norc --noprofile -c "cat $serve_script" | \
  python3 $SKILL_BASE/../vllm-optimize-pm/scripts/gen_serve_script.py --base - \
    --apply-json $base_dir/layer1/check_result.json --pick forced \
    -o $base_dir/layer1/serve_forced.sh
FORCED_SERVE=$base_dir/layer1/serve_forced.sh
```

### Step 3: L1 基线 profiling（单请求）

获取 forced 注入后的 L1 评测基准:

```bash
# run_in_background=true
bash $SKILL_BASE/../vllm-auto-optimizer/scripts/run_measurement.sh \
  $container $FORCED_SERVE $PORT "single $MODEL_NAME" $profile_duration \
  $base_dir/layer1/baseline_profiling_perf.json $base_dir/layer1/baseline_profiling_serve.log \
  --profiling $base_dir/layer1/baseline_profiling
```

设置 `PREV_PERF=$base_dir/layer1/baseline_profiling_perf.json`，`PREV_BEST_SERVE=$FORCED_SERVE`。

### Step 4: 竞争组 PK（graph_mode vs task_queue_2）

**图模式前置检查**: 如果用户 serve 脚本已包含 `compilation_config` 且含 `cudagraph` 相关配置（如 `cudagraph_capture_sizes`、`cudagraph_mode`），说明图模式已开启。此时：
- 跳过 graph_mode vs task_queue_2 竞争组 PK
- 直接将 graph_mode 标记为 forced（已在用户配置中启用）
- 基线 profiling 结果即为图模式下的数据，无需重复测试

检查 to_test 中是否有 `special_handling="graph_vs_taskqueue"` 的项。如果有:

#### 4a. 分别独立测试（都基于 forced 基础，不叠加对方）

graph_mode 测试:
```bash
python3 $SKILL_BASE/../vllm-optimize-pm/scripts/gen_serve_script.py --base $FORCED_SERVE \
  --apply-json $base_dir/layer1/check_result.json --pick-ids graph_mode \
  --remove-env TASK_QUEUE_ENABLE \
  -o $base_dir/layer1/test_graph_serve.sh

# run_in_background=true
bash $SKILL_BASE/../vllm-auto-optimizer/scripts/run_measurement.sh \
  $container $base_dir/layer1/test_graph_serve.sh $PORT "single $MODEL_NAME" $profile_duration \
  $base_dir/layer1/test_graph_perf.json $base_dir/layer1/test_graph_serve.log \
  --profiling $base_dir/layer1/test_graph_profiling
```

task_queue_2 测试:
```bash
python3 $SKILL_BASE/../vllm-optimize-pm/scripts/gen_serve_script.py --base $FORCED_SERVE \
  --apply-json $base_dir/layer1/check_result.json --pick-ids task_queue_2 \
  --remove-compilation \
  -o $base_dir/layer1/test_tq2_serve.sh

# run_in_background=true
bash $SKILL_BASE/../vllm-auto-optimizer/scripts/run_measurement.sh \
  $container $base_dir/layer1/test_tq2_serve.sh $PORT "single $MODEL_NAME" $profile_duration \
  $base_dir/layer1/test_tq2_perf.json $base_dir/layer1/test_tq2_serve.log \
  --profiling $base_dir/layer1/test_tq2_profiling
```

#### 4b. 比较两者

```bash
python3 $SKILL_BASE/../vllm-auto-optimizer/scripts/latency_judge.py \
  $PREV_PERF $base_dir/layer1/test_graph_perf.json

python3 $SKILL_BASE/../vllm-auto-optimizer/scripts/latency_judge.py \
  $PREV_PERF $base_dir/layer1/test_tq2_perf.json
```

判断逻辑:

**评价指标**: 遵循 PM 统一评价流程：
1. 先测单请求 decode latency → latency 改善>1% 则 KEEP，劣化>1% 则 ROLLBACK，±1%内进入第2步
2. 再测多请求 throughput → throughput 改善>1% 则 KEEP，否则 ROLLBACK

竞争组 PK 判断:
- graph_mode 更优 -> 选 graph_mode
- task_queue_2 更优或持平 -> 选 task_queue_2
- 两者都劣于 baseline -> 都不选

赢家作为后续 to_test 项的叠加基础，更新 `PREV_BEST_SERVE` 和 `PREV_PERF`。

#### 4c. 互斥组处理

后续遍历 to_test 时跳过所有 `special_handling` 项，避免重复测试。

### Step 5: 批量合并测试 + 快速筛查（优化策略）

> **设计目标**: 避免逐项重启服务（每项~12分钟）。to_test 中大部分是纯 env var 项，合并后一次服务启动即可覆盖。仅在必要时才进行 profiling 离线解析（省 220s/项）。

对 to_test 列表中每一项（跳过 special_handling 项，它们已由 Step 4 处理）:

#### 5a. 分类 to_test 项

根据 check_result.json 中各 item 的属性分类:

| 类别 | 判定条件 | 策略 |
|------|----------|------|
| **batchable** | `conflicts_with` 为空 AND `test_config` 不含 `--` 开头的 serve 参数（纯 env var） | 合并为一批，一次测量 |
| **individual** | `conflicts_with` 非空 OR `test_config` 包含 serve 参数变更 | 单独测试 |

```bash
# 用 python3 分类并输出
python3 -c "
import json
with open('$base_dir/layer1/check_result.json') as f:
    data = json.load(f)
batchable = []
individual = []
for item in data.get('to_test', []):
    if item.get('special_handling'):
        continue  # 已由 Step 4 处理
    tc = item.get('test_config', '')
    cf = item.get('conflicts_with', [])
    if not cf and not any(s.startswith('--') for s in tc.split('\n') if s.strip()):
        batchable.append(item)
    else:
        individual.append(item)
print(f'BATCHABLE ({len(batchable)}):', ','.join(i['id'] for i in batchable))
print(f'INDIVIDUAL ({len(individual)}):', ','.join(i['id'] for i in individual))
# 保存分类结果
with open('$base_dir/layer1/batch_classification.json', 'w') as f:
    json.dump({'batchable': batchable, 'individual': individual}, f, indent=2)
"
```

#### 5b. 批量合并测试（batchable 项 > 0 时执行）

将所有 batchable 项的 env var 合并到一个 serve script，**使用吞吐模式（无 --profiling，省 220s 离线解析）**:

```bash
# 生成合并后的 serve script（所有 batchable 项的 test_config 合并）
BATCH_IDS=$(python3 -c "import json; d=json.load(open('$base_dir/layer1/batch_classification.json')); print(','.join(i['id'] for i in d['batchable']))")

python3 $SKILL_BASE/../vllm-optimize-pm/scripts/gen_serve_script.py --base $PREV_BEST_SERVE \
  --apply-json $base_dir/layer1/check_result.json --pick-ids $BATCH_IDS \
  -o $base_dir/layer1/test_batch_serve.sh

# run_in_background=true — 吞吐模式，无需 --profiling
bash $SKILL_BASE/../vllm-auto-optimizer/scripts/run_measurement.sh \
  $container $base_dir/layer1/test_batch_serve.sh $PORT "single $MODEL_NAME" $profile_duration \
  $base_dir/layer1/test_batch_perf.json $base_dir/layer1/test_batch_serve.log
```

**批量结果判定**（基于 serve log 中提取的 avg_tps）:

```bash
# 比较 batch perf vs baseline（用 throughput_judge 比较 avg_tps）
# run_in_background=true — 先测 baseline throughput
bash $SKILL_BASE/../vllm-auto-optimizer/scripts/run_measurement.sh \
  $container $PREV_BEST_SERVE $PORT "script $benchmark_script" $proxy_duration \
  $base_dir/layer1/test_batch_baseline_tput.json $base_dir/layer1/test_batch_baseline_tput_serve.log

# 再测 batch throughput
bash $SKILL_BASE/../vllm-auto-optimizer/scripts/run_measurement.sh \
  $container $base_dir/layer1/test_batch_serve.sh $PORT "script $benchmark_script" $proxy_duration \
  $base_dir/layer1/test_batch_tput.json $base_dir/layer1/test_batch_tput_serve.log

python3 $SKILL_BASE/../vllm-auto-optimizer/scripts/throughput_judge.py \
  $base_dir/layer1/test_batch_baseline_tput.json $base_dir/layer1/test_batch_tput.json
```

| 批量结果 | 处理方式 |
|----------|----------|
| throughput 变化 ≤1%（持平） | **全批 ROLLBACK** — 所有 batchable 项一次性标记为 ROLLBACK，无需逐项测试 |
| throughput 劣化 >1% | 进入二分搜索（5b-二分），找出退化项 |
| throughput 提升 >1% | 全批 KEEP + 逐项 profiling 确认收益来源 |

**5b-二分搜索**（仅当批量结果劣化 >1%）:

将 batchable 列表对半分成两组，分别生成 serve script 并测试（吞吐模式，无 profiling），递归缩小范围直到定位到具体的退化项。

**5b-逐项确认**（仅当批量结果提升 >1%）:

对每个 batchable 项进行单独 profiling 测试（同 Step 5d），确认具体哪项有收益。

#### 5c. 处理 individual 项（保持独立测试）

对 individual 列表中的每一项:

```bash
python3 $SKILL_BASE/../vllm-optimize-pm/scripts/gen_serve_script.py --base $PREV_BEST_SERVE \
  --apply-json $base_dir/layer1/check_result.json --pick-ids $ITEM_ID \
  -o $base_dir/layer1/test_I_serve.sh
```

**快速筛查**: 先跑吞吐模式（无 profiling），仅在 throughput 有差异时再跑 profiling:

```bash
# 第一步：吞吐快速筛查（无 --profiling）
bash $SKILL_BASE/../vllm-auto-optimizer/scripts/run_measurement.sh \
  $container $base_dir/layer1/test_I_serve.sh $PORT "single $MODEL_NAME" $profile_duration \
  $base_dir/layer1/test_I_perf.json $base_dir/layer1/test_I_serve.log
```

从 perf.json 提取 avg_tps，与 baseline 比较:
- avg_tps 劣化 >1% → **SKIP profiling，直接 ROLLBACK**（省 220s）
- avg_tps 持平或改善 → 进入第二步 profiling 确认

```bash
# 第二步：profiling 精确测量（仅当快速筛查通过时）
bash $SKILL_BASE/../vllm-auto-optimizer/scripts/run_measurement.sh \
  $container $base_dir/layer1/test_I_serve.sh $PORT "single $MODEL_NAME" $profile_duration \
  $base_dir/layer1/test_I_profiling_perf.json $base_dir/layer1/test_I_profiling_serve.log \
  --profiling $base_dir/layer1/test_I_profiling
```

#### 5d. 效果判断（同原 5c/5d）

```bash
python3 $SKILL_BASE/../vllm-auto-optimizer/scripts/latency_judge.py \
  $PREV_PERF $base_dir/layer1/test_I_profiling_perf.json
```

Latency 提取失败时 fallback 到 throughput 判断（同原 5d 逻辑）。

#### 5e. 结果处理

- KEEP: 更新 `PREV_BEST_SERVE=$TEMP_SERVE`，`PREV_PERF=test_N_perf.json`，记录到 kept_opts
- ROLLBACK: 不更新，继续下一项
- SKIP: 记录 skip 原因（crash/不兼容），继续下一项

### Step 6: 更新 state.json

```bash
python3 $SKILL_BASE/../vllm-optimize-pm/scripts/layer_state.py set_layer_status $base_dir layer1 completed
python3 $SKILL_BASE/../vllm-optimize-pm/scripts/layer_state.py update_best $base_dir $BEST_SERVE $BEST_TPS '$KEPT_OPTS_JSON'
```

### Step 7: 生成阶段报告

调用 /vllm-report-generator 生成 L1 阶段报告 `$base_dir/final_deliverables/layer1/layer1_report.md`。

## 报告质量要求

报告必须包含以下内容，缺少任何一项将被质量总监判定为不合格：
1. Profiling 分析：baseline（forced 注入后）的 timeline 占比（Computing/Communication/Free/Overlap）、top-5 算子
2. 每项测试的详细记录：配置变更内容、before/after latency 数值、delta%、判定理由
3. 竞争组 PK 的分析过程（如有 graph_mode vs task_queue_2）
4. 每项 SKIP 的客观技术理由（逐项说明，不可笼统）
5. 每项 ROLLBACK 的失败原因分析（不是只说"退化了"，要分析为什么退化）
6. 累积效果总结
7. 图表：至少生成 1 张对比图（如各优化项 latency 对比柱状图），保存到 $base_dir/final_deliverables/layer1/charts/ 目录，并在报告中引用图表文件路径
8. 源码分析：每项 ROLLBACK 必须追溯到具体代码路径（.py 文件、函数名），说明退化的技术原因。不接受纯推测性解释。

## 输出: results.json 格式

完成后输出 `$base_dir/layer1/results.json`:

```json
{
  "layer": "L1",
  "status": "completed",
  "optimizations_tested": 0,  // 计数规则 = tested_items 数组的长度（包含 KEEP/ROLLBACK/SKIP 所有项，包含竞争组 PK 的每个参与者）。forced_items 不计入此数字。
  "optimizations_kept": 0,
  "forced_items": ["item_id_1", "item_id_2"],
  "tested_items": [
    {
      "item_id": "xxx",
      "name": "xxx",
      "result": "KEEP|ROLLBACK|SKIP",
      "metric": "latency|throughput",
      "prev_value": 123.4,
      "curr_value": 120.1,
      "delta_pct": -2.7
    }
  ],
  "competition_result": {
    "graph_mode": {"latency": 123.4, "result": "WIN|LOSE"},
    "task_queue_2": {"latency": 125.0, "result": "WIN|LOSE"},
    "winner": "graph_mode|task_queue_2|none"
  },
  "kept_opts": ["item_id_1", "item_id_2"],
  "best_serve_script": "$base_dir/layer1/best_serve.sh",
  "best_perf_json": "$base_dir/layer1/best_perf.json",
  "cumulative_improvement": {
    "metric": "latency|throughput",
    "baseline_value": 130.0,
    "current_value": 120.1,
    "delta_pct": -7.6
  }
}
```
