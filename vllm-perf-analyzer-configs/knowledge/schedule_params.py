#!/usr/bin/env python3
"""Layer3 调度参数空间定义 — 用于黑盒网格搜索。"""


def _dcp_values(tp_size):
    """生成 data-context-parallel 候选值。"""
    return list(range(1, max(2, tp_size) + 1))


def generate_cudagraph_sizes(max_num_seqs):
    """生成 cudagraph_capture_sizes: 2 的幂次列表，最大值=max_num_seqs，最多 10 个。"""
    sizes = []
    v = 1
    while v <= max_num_seqs and len(sizes) < 10:
        sizes.append(v)
        v *= 2
    return sizes


PARAM_SPACE = [
    {
        "id": "max_num_seqs",
        "name": "最大并发序列数",
        "arg": "--max-num-seqs",
        "values": [64, 128, 256, 512],
        "description": "控制单次调度的最大序列数",
    },
    {
        "id": "gpu_memory_utilization",
        "name": "GPU 显存利用率",
        "arg": "--gpu-memory-utilization",
        "values": [0.4, 0.6, 0.8, 0.9, 0.95],
        "description": "KV cache 可用显存比例",
    },
    {
        "id": "dcp",
        "name": "Data Context Parallel",
        "arg": "--data-parallel-size",
        "values_fn": _dcp_values,
        "description": "数据并行度，与 TP 配合",
    },
]
