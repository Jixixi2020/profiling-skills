#!/usr/bin/env python3
"""Free 时间异常分析 — 流同步凶手定位。

用法: python3 analyze_free_bottleneck.py <profiling_dir> <state_json>

从 profiling 数据中检测 Free 时间异常，定位流同步凶手，输出 JSON 诊断报告。
"""
import csv
import json
import os
import sys
from collections import defaultdict

FREE_THRESHOLD_PCT = 10.0

SYNC_API_NAMES = [
    'aclrtSynchronizeEvent',
    'aclrtSynchronizeStreamWithTimeout',
    'aclrtSynchronizeStream',
    'aclrtSynchronizeDevice',
    'aclrtSynchronizeDeviceWithTimeout',
]

def find_csv(profiling_dir, filename):
    """在 profiling_dir 下递归查找 CSV 文件。"""
    for root, _, files in os.walk(profiling_dir):
        if filename in files:
            return os.path.join(root, filename)
    return None


def read_state(state_json_path):
    """读取 state.json，从 serve_script 文件内容判断 async_scheduling / cudagraph 配置。"""
    with open(state_json_path, 'r') as f:
        state = json.load(f)
    serve_script_path = state.get('current_best', {}).get('serve_script', '')
    serve_content = ''
    if serve_script_path and os.path.exists(serve_script_path):
        with open(serve_script_path, 'r') as f:
            serve_content = f.read()
    # 从 serve_script 实际内容判断配置状态
    cudagraph = ('cudagraph_capture_sizes' in serve_content or
                 'cudagraph_mode' in serve_content) and '--enforce-eager' not in serve_content
    async_scheduling = '--async-scheduling' in serve_content
    return {'async_scheduling': async_scheduling, 'cudagraph': cudagraph, 'state': state}


def parse_step_trace(csv_path):
    """解析 step_trace_time.csv，返回 Free 占比。"""
    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    if not rows:
        return None
    row = rows[0]
    computing = float(row.get('Computing', 0))
    comm = float(row.get('Communication(Not Overlapped)', 0))
    free = float(row.get('Free', 0))
    bubble = float(row.get('Bubble', 0))
    preparing = float(row.get('Preparing', 0))
    total = computing + comm + free + bubble + preparing
    if total == 0:
        return None
    return {
        'free_us': free, 'total_us': total,
        'free_ratio_pct': round(free / total * 100, 2),
        'computing_us': computing,
    }


def parse_sync_apis(csv_path):
    """解析 api_statistic.csv，提取同步 API 统计。"""
    sync_apis = []
    if not csv_path or not os.path.exists(csv_path):
        return sync_apis
    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            api_name = row.get('API Name', '').strip()
            if any(s in api_name for s in SYNC_API_NAMES):
                sync_apis.append({
                    'api': api_name,
                    'count': int(row.get('Count', 0)),
                    'total_us': float(row.get('Time(us)', 0)),
                    'avg_us': float(row.get('Avg(us)', 0)),
                })
    sync_apis.sort(key=lambda x: x['total_us'], reverse=True)
    return sync_apis


def parse_copy_suspects(csv_path):
    """从 operator_details.csv 提取 copy_ 操作的嫌疑信息。"""
    suspects = []
    if not csv_path or not os.path.exists(csv_path):
        return suspects
    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row.get('Name', '').strip()
            if 'copy_' not in name and 'memcpy' not in name.lower():
                continue
            host_dur = float(row.get('Host Total Duration(us)', 0) or 0)
            device_dur = float(row.get('Device Total Duration(us)', 0) or 0)
            shapes = row.get('Input Shapes', '').strip()
            call_stack = row.get('Call Stack', '').strip()
            # 判断是否为 2D+ tensor copy（shape 中有逗号分隔的多维）
            is_2d = False
            shape_dims = []
            if shapes:
                first_shape = shapes.split(';')[0].strip().strip('"')
                dims = [d.strip() for d in first_shape.split(',') if d.strip()]
                shape_dims = dims
                is_2d = len(dims) >= 2 and all(d.isdigit() for d in dims)
            # H2D 判断: 有 device duration 或名称含 host_to_device
            is_h2d = 'host_to_device' in name.lower() or (device_dur > 0 and host_dur > 100)
            is_d2h = 'device_to_host' in name.lower()
            if host_dur < 100 and not is_h2d and not is_d2h:
                continue
            category = 'unknown'
            if is_h2d and is_2d:
                category = 'h2d_copy_slow_path'
            elif is_h2d:
                category = 'h2d_copy_slow_path'
            elif is_d2h:
                category = 'd2h_sync_read'
            elif 'item' in name.lower() or 'to_cpu' in call_stack.lower():
                category = 'd2h_sync_read'
            suspects.append({
                'operation': name,
                'category': category,
                'evidence': {
                    'input_shape': ','.join(shape_dims) if shape_dims else shapes,
                    'is_2d': is_2d,
                    'host_duration_us': host_dur,
                    'device_duration_us': device_dur,
                },
                'call_stack_snippet': call_stack[:200] if call_stack else '',
            })
    suspects.sort(key=lambda x: x['evidence']['host_duration_us'], reverse=True)
    return suspects[:10]


def analyze(profiling_dir, state_json_path):
    """主分析函数。"""
    state_info = read_state(state_json_path)
    step_trace_csv = find_csv(profiling_dir, 'step_trace_time.csv')
    if not step_trace_csv:
        return {'error': 'step_trace_time.csv not found', 'has_anomaly': False}
    trace = parse_step_trace(step_trace_csv)
    if not trace:
        return {'error': 'step_trace_time.csv is empty', 'has_anomaly': False}
    free_ratio = trace['free_ratio_pct']
    has_anomaly = free_ratio > FREE_THRESHOLD_PCT
    result = {
        'free_ratio': round(free_ratio / 100, 4),
        'free_ratio_pct': free_ratio,
        'free_us': trace['free_us'],
        'total_us': trace['total_us'],
        'has_anomaly': has_anomaly,
        'async_scheduling': state_info['async_scheduling'],
        'cudagraph': state_info['cudagraph'],
    }
    if not has_anomaly:
        result['message'] = f'Free ratio {free_ratio}% < {FREE_THRESHOLD_PCT}% threshold, no anomaly'
        return result
    # 深度分析: 同步 API
    api_csv = find_csv(profiling_dir, 'api_statistic.csv')
    sync_apis = parse_sync_apis(api_csv)
    result['sync_apis'] = sync_apis
    sync_total_us = sum(a['total_us'] for a in sync_apis)
    result['sync_total_us'] = round(sync_total_us, 2)
    # 交叉验证: sync API total 与 Free total 的匹配度
    if trace['free_us'] > 0:
        result['sync_free_match_ratio'] = round(sync_total_us / trace['free_us'], 3)
    # 深度分析: copy 嫌疑
    op_details_csv = find_csv(profiling_dir, 'operator_details.csv')
    suspects = parse_copy_suspects(op_details_csv)
    # 为嫌疑添加诊断和修复建议
    for s in suspects:
        if s['category'] == 'h2d_copy_slow_path' and s['evidence']['is_2d']:
            s['diagnosis'] = ('2D tensor H2D copy triggers torch_npu slow path: '
                              'non-contiguous src → clone → unpinned memory → '
                              'CachingHostAllocator fallback → full stream sync')
            s['fix_pattern'] = 'split_2d_to_1d_copies'
            s['reference'] = 'ref_mrope_2d_copy.md'
        elif s['category'] == 'h2d_copy_slow_path':
            s['diagnosis'] = ('H2D copy with high host duration, '
                              'possible non-contiguous or unpinned src')
            s['fix_pattern'] = 'ensure_contiguous_pinned_src'
            s['reference'] = 'ref_mrope_2d_copy.md'
        elif s['category'] == 'd2h_sync_read':
            s['diagnosis'] = 'D2H sync read blocks host thread'
            s['fix_pattern'] = 'defer_or_async_read'
    result['suspects'] = suspects
    # 生成推荐
    if suspects:
        top = suspects[0]
        rec = {
            'type': 'code_fix',
            'description': f"Eliminate stream sync caused by {top['operation']} ({top['category']})",
            'expected_improvement': ('Free time reduction, step latency improvement '
                                    f'proportional to current Free ratio ({free_ratio}%)'),
        }
        if top.get('reference'):
            rec['reference'] = top['reference']
        result['recommendation'] = rec
    else:
        result['recommendation'] = {
            'type': 'manual_investigation',
            'description': (f'Free ratio {free_ratio}% is anomalous but no obvious '
                            'copy suspects found. Check trace_view.json for sync gaps.'),
        }
    return result


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print("Usage: analyze_free_bottleneck.py <profiling_dir> <state_json>", file=sys.stderr)
        sys.exit(1)
    result = analyze(sys.argv[1], sys.argv[2])
    print(json.dumps(result, indent=2, ensure_ascii=False))
