#!/usr/bin/env python3
"""Generate all_configs.csv from scanned vllm/vllm-ascend source data."""
import csv
rows = []
def add(item_id, name, config_type, config_key, default_value, recommended_value,
        category, risk_level, source_file, source_line, description,
        default_enabled, latency_relevant, throughput_relevant):
    rows.append([item_id, name, config_type, config_key, str(default_value),
                 str(recommended_value), category, risk_level, source_file,
                 str(source_line), description, str(default_enabled).lower(),
                 str(latency_relevant).lower(), str(throughput_relevant).lower()])
SRC_A = "/vllm-workspace/vllm-ascend/vllm_ascend"
SRC_V = "/vllm-workspace/vllm/vllm"
SRC_ARG = "/vllm-workspace/vllm/vllm/engine/arg_utils.py"
SRC_AC = "/vllm-workspace/vllm-ascend/vllm_ascend/ascend_config.py"
# === Step 1: vllm-ascend env vars ===
add("env_compile_custom_kernels","编译自定义算子","env_var","COMPILE_CUSTOM_KERNELS","1","1","compilation","low",f"{SRC_A}/envs.py",0,"是否编译自定义NPU算子. 默认开启. 仅在无NPU的UT环境设为0",True,True,True)
add("env_vllm_ascend_enable_matmul_allreduce","MatmulAllReduce融合","env_var","VLLM_ASCEND_ENABLE_MATMUL_ALLREDUCE","0","1","communication","medium",f"{SRC_A}/envs.py",0,"启用MatmulAllReduce融合算子(A2芯片). TP场景下将matmul和allreduce合并执行. eager模式性能更优",False,True,True)
add("env_vllm_ascend_enable_flashcomm1","FlashComm1通信优化","env_var","VLLM_ASCEND_ENABLE_FLASHCOMM1","0","1","communication","medium",f"{SRC_A}/envs.py",0,"启用FlashComm1优化. 大并发场景下通过通算掩盖提升TP通信效率. 需配合prefill_context_parallel_size",False,True,True)
add("env_vllm_ascend_flashcomm2_parallel_size","FlashComm2并行度","env_var","VLLM_ASCEND_FLASHCOMM2_PARALLEL_SIZE","0","2","communication","medium",f"{SRC_A}/envs.py",0,"FlashComm2的O-matrix TP组大小. 0=禁用. 与FC1互斥. 参考文档选择FC1或FC2",False,True,True)
add("env_vllm_ascend_enable_prefetch_mlp","MLP权重预取(旧)","env_var","VLLM_ASCEND_ENABLE_PREFETCH_MLP","0","1","bandwidth","low",f"{SRC_A}/envs.py",0,"已废弃请用weight_prefetch_config. 小并发场景下预取MLP权重掩盖HBM延迟",False,True,False)
add("env_vllm_ascend_mlp_gate_up_prefetch_size","Gate/Up预取缓冲区","env_var","VLLM_ASCEND_MLP_GATE_UP_PREFETCH_SIZE","18874368","18874368","bandwidth","low",f"{SRC_A}/envs.py",0,"gate_up权重预取缓冲区大小(字节). 默认18MB. 配合ENABLE_PREFETCH_MLP使用",False,True,False)
add("env_vllm_ascend_mlp_down_prefetch_size","Down预取缓冲区","env_var","VLLM_ASCEND_MLP_DOWN_PREFETCH_SIZE","18874368","18874368","bandwidth","low",f"{SRC_A}/envs.py",0,"down_proj权重预取缓冲区大小(字节). 默认18MB",False,True,False)
add("env_msmonitor_use_daemon","msMonitor监控","env_var","MSMONITOR_USE_DAEMON","0","0","compute","low",f"{SRC_A}/envs.py",0,"启用msMonitor性能监控. 生产环境建议关闭避免开销",False,False,False)
add("env_vllm_ascend_enable_mlapo","MLAPO优化","env_var","VLLM_ASCEND_ENABLE_MLAPO","1","1","compute","medium",f"{SRC_A}/envs.py",0,"DeepSeek W8A8模型MLAPO优化. 默认开启提升性能但增加显存. 显存紧张时可关闭",True,True,True)
add("env_vllm_ascend_enable_nz","NZ权重格式","env_var","VLLM_ASCEND_ENABLE_NZ","1","2","compute","medium",f"{SRC_A}/envs.py",0,"权重FRACTAL_NZ格式. 0=关闭 1=仅量化 2=尽可能启用. NZ利用Cube单元提升matmul效率",True,True,True)
add("env_vllm_ascend_enable_context_parallel","Context Parallel","env_var","VLLM_ASCEND_ENABLE_CONTEXT_PARALLEL","0","1","parallel","medium",f"{SRC_A}/envs.py",0,"启用CP并行. 长序列场景将context分片到多卡. 需配合prefill/decode_context_parallel_size",False,True,True)
add("env_dynamic_eplb","动态专家负载均衡","env_var","DYNAMIC_EPLB","false","true","scheduling","high",f"{SRC_A}/envs.py",0,"启用动态EPLB. MoE模型运行时动态调整专家分布. 需配合eplb_config",False,False,True)
add("env_vllm_ascend_enable_fused_mc2","融合MC2算子","env_var","VLLM_ASCEND_ENABLE_FUSED_MC2","0","1","compute","medium",f"{SRC_A}/envs.py",0,"融合MC2模式. 0=标准 1=dispatch_ffn_combine(W8A8/EP<=32) 2=dispatch_gmm_combine_decode(decode专用). 减少MoE通信开销",False,True,True)
add("env_vllm_ascend_balance_scheduling","均衡调度","env_var","VLLM_ASCEND_BALANCE_SCHEDULING","0","1","scheduling","low",f"{SRC_A}/envs.py",0,"启用均衡调度策略. 多卡场景平衡各卡计算负载",False,False,True)
add("env_vllm_ascend_fusion_op_transpose_kv","KV Cache转置融合","env_var","VLLM_ASCEND_FUSION_OP_TRANSPOSE_KV_CACHE_BY_BLOCK","1","1","compute","low",f"{SRC_A}/envs.py",0,"融合算子transpose_kv_cache_by_block. 默认开启减少KV cache转置开销",True,True,False)
add("env_fla_use_fast_ops","FLA快速算子","env_var","FLA_USE_FAST_OPS","0","1","compute","low",f"{SRC_A}/ops/triton/fla/sigmoid_gating.py",17,"启用FLA快速算子(sigmoid_gating优化). Triton kernel优化可小幅提升decode性能",False,True,False)
add("env_pytorch_npu_alloc_conf","NPU内存分配配置","env_var","PYTORCH_NPU_ALLOC_CONF","expandable_segments:True","expandable_segments:True","memory","low",f"{SRC_A}/platform.py",427,"NPU内存分配器配置. 默认expandable_segments:True优化碎片",True,False,True)
add("env_task_queue_enable","CANN任务队列","env_var","TASK_QUEUE_ENABLE","0","1","compute","low","CANN环境变量",0,"启用CANN任务队列模式. 异步算子下发减少host-device同步开销. graph模式下建议开启",False,True,True)
add("env_hccl_buffsize","HCCL通信缓冲区","env_var","HCCL_BUFFSIZE","未设置","512","communication","medium","HCCL环境变量",0,"HCCL集合通信缓冲区大小(MB). 长序列或大TP场景需增大(512-2048)",False,True,True)
# === Step 2: vllm core env vars ===
# PLACEHOLDER_STEP2
OUT = "/home/xxx/.agent/skills/vllm-config-scanner/data/all_configs.csv"
with open(OUT, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["item_id","name","config_type","config_key","default_value","recommended_value","category","risk_level","source_file","source_line","description","default_enabled","latency_relevant","throughput_relevant"])
    for r in rows:
        w.writerow(r)
print(f"Total items: {len(rows)}")
from collections import Counter
types = Counter(r[2] for r in rows)
cats = Counter(r[6] for r in rows)
lat = sum(1 for r in rows if r[12] == "true")
thr = sum(1 for r in rows if r[13] == "true")
de = sum(1 for r in rows if r[11] == "true")
print(f"By config_type: {dict(types)}")
print(f"By category: {dict(cats)}")
print(f"latency_relevant=true: {lat}")
print(f"throughput_relevant=true: {thr}")
print(f"default_enabled=true: {de}")
