# vLLM Ascend NPU 配置优化推荐

## 职责

基于 profiling 分析数据 + 版本特性知识库，推荐配置类优化项。专注于 serve 参数、环境变量、additional-config 等不涉及代码修改的优化。

## 调用方式

由 vllm-optimize-l2/l3 subagent 的 Layer 2 调用。

## 输入

- `timeline_json`: analyze_timeline.py 的输出（Computing/Comm/Free 占比）
- `state_json`: state.json 路径（含 model_info、current_best）
- `serve_script`: 当前 serve 脚本路径
- `check_result_json`: L1 check_result.json 路径（已测试过的优化项）

## 输出

排序后的配置推荐列表 JSON，每项包含:
- `item_id`: 优化项 ID
- `name`: 优化项名称
- `score`: 多维评分
- `config_type`: env_var / additional_config / serve_arg
- `operation`: 具体配置修改方式
- `expected_effect`: 预期效果
- `risk`: 风险等级

## 执行流程

### Step 1: 提取当前配置
```bash
python3 $SKILL_BASE/scripts/extract_enabled_configs.py <serve_script>
```

### Step 2: 知识库检索 + 多维评分
```bash
python3 $SKILL_BASE/scripts/recommend_configs.py \
  <timeline_json> <state_json> <serve_script> <check_result_json>
```

评分维度:
- profiling 瓶颈匹配度（timeline 中 Computing/Comm/Free 占比触发条件）
- 优化项优先级（知识库中的 priority 字段）
- 模型适用性（MoE/Dense、TP size 等条件过滤）
- 与已有配置的冲突检查

### Step 3: L1 交叉去重

过滤掉已在 L1 测试过的优化项（通过 ID 匹配 + ID_ALIASES 映射 + config key 交叉匹配）。

## 知识库

- `knowledge/optimization_items.py`: 90+ 项 Ascend NPU 优化配置知识库
- `knowledge/categories.py`: 优化项分类
- `knowledge/default_opts.py`: L1 默认优化项定义
- `knowledge/schedule_params.py`: 调度参数知识
