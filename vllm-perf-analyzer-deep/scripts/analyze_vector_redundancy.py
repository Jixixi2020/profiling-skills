#!/usr/bin/env python3
"""Vector 算子冗余分析 — 检测 Transpose/Copy 等算子的冗余执行。

用法: python3 analyze_vector_redundancy.py <profiling_dir> <state_json>

从 op_statistic.csv 和 kernel_details.csv 检测 Vector 算子冗余，输出 JSON 诊断报告。
"""
import csv
import json
import os
import sys
from collections import defaultdict

VECTOR_RATIO_THRESHOLD_PCT = 2.0
TARGET_OPS = ['Transpose', 'TransposeD', 'TransData', 'Copy']


def find_csv(profiling_dir, filename):
    """在 profiling_dir 下递归查找 CSV 文件。"""
    for root, _, files in os.walk(profiling_dir):
        if filename in files:
            return os.path.join(root, filename)
    return None


def read_state(state_json_path):
    with open(state_json_path, 'r') as f:
        state = json.load(f)
    return state


def get_model_layers(state):
    """从 state.json 推断模型层数。"""
    name = state.get('model_info', {}).get('name', '')
    # 常见模型层数映射
    layer_map = {
        'qwen3.5-35b': 30, 'qwen3.5-14b': 28, 'qwen3.5-7b': 28,
        'qwen2.5-72b': 80, 'qwen2.5-32b': 64, 'qwen2.5-14b': 48,
        'llama-3-70b': 80, 'llama-3-8b': 32,
        'deepseek-v3': 61, 'deepseek-v2': 60,
    }
    name_lower = name.lower()
    for key, layers in layer_map.items():
        if key in name_lower:
            return layers
    return None


def parse_op_statistic(csv_path):
    """从 op_statistic.csv 提取目标 Vector 算子统计。"""
    targets = []
    total_device_time = 0.0
    if not csv_path or not os.path.exists(csv_path):
        return targets, total_device_time
    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            op_type = row.get('OP Type', '').strip()
            total_time = float(row.get('Total Time(us)', 0))
            total_device_time += total_time
            if any(t.lower() in op_type.lower() for t in TARGET_OPS):
                targets.append({
                    'op_type': op_type,
                    'core_type': row.get('Core Type', '').strip(),
                    'count': int(row.get('Count', 0)),
                    'total_time_us': total_time,
                    'avg_time_us': float(row.get('Avg Time(us)', 0)),
                    'ratio_pct': float(row.get('Ratio(%)', 0)),
                })
    return targets, total_device_time


def count_decode_steps(csv_path):
    """从 kernel_details.csv 统计 decode step 数（ArgMax kernel 出现次数）。"""
    if not csv_path or not os.path.exists(csv_path):
        return None
    count = 0
    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            op_type = row.get('Type', '').strip()
            if 'ArgMax' in op_type or 'argmax' in row.get('Name', '').lower():
                count += 1
    return count if count > 0 else None


def extract_target_shapes(csv_path, target_op):
    """从 kernel_details.csv 提取目标算子的 input shape 分布。"""
    shape_counts = defaultdict(int)
    if not csv_path or not os.path.exists(csv_path):
        return []
    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            op_type = row.get('Type', '').strip()
            if target_op.lower() not in op_type.lower():
                continue
            shapes = row.get('Input Shapes', '').strip().strip('"')
            first_shape = shapes.split(';')[0].strip() if shapes else ''
            if first_shape:
                shape_counts[first_shape] += 1
    result = [{'shape': s, 'count': c} for s, c in shape_counts.items()]
    result.sort(key=lambda x: x['count'], reverse=True)
    return result[:10]


def analyze(profiling_dir, state_json_path):
    """主分析函数。"""
    state = read_state(state_json_path)
    model_layers = get_model_layers(state)
    op_stat_csv = find_csv(profiling_dir, 'op_statistic.csv')
    kernel_csv = find_csv(profiling_dir, 'kernel_details.csv')
    targets, total_device_time = parse_op_statistic(op_stat_csv)
    if not targets:
        return {'has_anomaly': False, 'message': 'No target Vector ops found'}
    targets.sort(key=lambda x: x['total_time_us'], reverse=True)
    top = targets[0]
    ratio_pct = top['ratio_pct']
    if ratio_pct < VECTOR_RATIO_THRESHOLD_PCT:
        return {
            'has_anomaly': False, 'target_op': top['op_type'],
            'ratio_pct': ratio_pct,
            'message': f"{top['op_type']} ratio {ratio_pct}% < {VECTOR_RATIO_THRESHOLD_PCT}% threshold",
        }
    decode_steps = count_decode_steps(kernel_csv)
    per_step_count = None
    count_per_layer = None
    if decode_steps and decode_steps > 0:
        per_step_count = round(top['count'] / decode_steps, 1)
        if model_layers:
            count_per_layer = round(per_step_count / model_layers, 2)
    input_shapes = extract_target_shapes(kernel_csv, top['op_type'])
    result = {
        'has_anomaly': True, 'target_op': top['op_type'],
        'total_count': top['count'], 'total_us': top['total_time_us'],
        'ratio_pct': ratio_pct, 'decode_steps': decode_steps,
        'per_step_count': per_step_count, 'model_layers': model_layers,
        'count_per_layer_per_step': count_per_layer,
        'input_shapes': input_shapes, 'all_targets': targets,
    }
    is_static_redundancy = (count_per_layer is not None and 0.8 <= count_per_layer <= 1.2)
    if is_static_redundancy:
        result['diagnosis'] = 'Static weight transpose repeated every step in every layer'
        result['recommendation'] = {
            'type': 'code_fix',
            'target': f'{top["op_type"]} kernel in model forward path',
            'description': 'Lazy init pre-transpose weight before cudagraph capture',
            'reference': 'ref_transpose_elimination.md',
            'expected_improvement': f'{top["op_type"]} kernel elimination, step latency -3~6%',
        }
    elif per_step_count and per_step_count > 5:
        result['diagnosis'] = (f'{top["op_type"]} executed {per_step_count} times/step, '
                               'likely redundant in hot path')
        result['recommendation'] = {
            'type': 'code_fix',
            'target': f'{top["op_type"]} kernel source',
            'description': 'Investigate source of repeated transpose/copy, consider pre-computation',
            'reference': 'ref_transpose_elimination.md',
            'expected_improvement': f'Potential {ratio_pct}% device time reduction',
        }
    else:
        result['diagnosis'] = f'{top["op_type"]} ratio {ratio_pct}% is notable but pattern unclear'
        result['recommendation'] = {
            'type': 'manual_investigation',
            'description': 'Review input shapes and source code to determine if elimination is possible',
        }
    return result


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print("Usage: analyze_vector_redundancy.py <profiling_dir> <state_json>", file=sys.stderr)
        sys.exit(1)
    result = analyze(sys.argv[1], sys.argv[2])
    print(json.dumps(result, indent=2, ensure_ascii=False))
