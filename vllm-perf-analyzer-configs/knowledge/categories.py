"""
瓶颈维度、优化类别、MoE 模型名称列表等基础定义。
"""

# 瓶颈维度（从报告中提取的量化指标）
BOTTLENECK_DIMENSIONS = [
    "free_ratio",           # float, Timeline Free/Idle 占比
    "compute_ratio",        # float, Timeline Computing 占比
    "comm_ratio",           # float, Communication 占比
    "enforce_eager",        # bool
    "tp_size",              # int
    "pp_size",              # int
    "is_moe",               # bool
    "hardware",             # str: "A2"/"A3"
    "has_speculative",      # bool
    "gpu_mem_util",         # float
    "max_num_seqs",         # int
    "max_batched_tokens",   # int
]

# MoE 模型名称模式（小写匹配）
MOE_MODEL_PATTERNS = [
    "mixtral", "deepseek-v2", "deepseek-v3", "deepseek-r1",
    "qwen3-moe", "qwen2-moe", "qwen2.5-moe", "qwen3.5-moe",
    "dbrx", "jamba", "arctic", "grok",
    "deepseek-v2.5", "deepseek-v2-lite",
    "-a3b", "-a14b", "-a22b",  # MoE 模型常见后缀
]

# 优化类别
CATEGORIES = [
    "dispatch",        # 算子下发优化
    "compute",         # 计算优化
    "communication",   # 通信优化
    "memory",          # 显存优化
    "scheduling",      # 调度优化
    "parallel",        # 并行优化
    "compile",         # 编译/图优化
    "speculative",     # 投机解码
    "overlap",         # 异步/overlap 优化
    "bandwidth",       # 带宽优化
    "op_fusion",       # 算子融合
]

# 风险等级乘数
RISK_MULTIPLIER = {
    "低": 1.0,
    "中": 0.85,
    "高": 0.65,
}
