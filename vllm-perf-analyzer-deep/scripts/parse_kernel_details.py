#!/usr/bin/env python3
"""解析 kernel_details.csv，计算利用率统计和 Prefill/Decode 区分。"""
import csv
import json
import sys

def parse_kernel_details(filepath):
    type_stats = {}  # op_type -> {count, total_dur, cube_utils, shapes}
    anomalies = []   # 异常 kernel（高 wait time、低利用率）
    prefill_kernels = []
    decode_kernels = []
    with open(filepath, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row.get('Name', '').strip()
            op_type = row.get('Type', '').strip()
            dur = float(row.get('Duration(us)', 0))
            wait = float(row.get('Wait Time(us)', 0))
            cube_util = row.get('cube_utilization(%)', '')
            try:
                cube_util = float(cube_util) if cube_util.strip() and cube_util.strip() != 'N/A' else 0.0
            except ValueError:
                cube_util = 0.0
            shapes = row.get('Input Shapes', '')
            dtypes = row.get('Input Data Types', '')
            core = row.get('Accelerator Core', '').strip()
            if op_type not in type_stats:
                type_stats[op_type] = {'count': 0, 'total_dur_us': 0, 'cube_utils': [], 'sample_shapes': []}
            type_stats[op_type]['count'] += 1
            type_stats[op_type]['total_dur_us'] += dur
            if cube_util > 0:
                type_stats[op_type]['cube_utils'].append(cube_util)
            if len(type_stats[op_type]['sample_shapes']) < 3:
                type_stats[op_type]['sample_shapes'].append(shapes)
            # 异常检测：wait time > 10x duration 或 cube 利用率 < 30%
            if wait > dur * 10 and dur > 1:
                anomalies.append({'name': name, 'type': op_type, 'dur_us': dur, 'wait_us': wait, 'shapes': shapes})
            if core == 'AI_CORE' and cube_util > 0 and cube_util < 30 and dur > 10:
                anomalies.append({'name': name, 'type': op_type, 'dur_us': dur, 'cube_util': cube_util, 'shapes': shapes, 'issue': 'low_cube_util'})
            # Prefill/Decode 区分（MatMul 的第一维 > 64 视为 prefill）
            if 'MatMul' in op_type and shapes:
                first_shape = shapes.split(';')[0].strip().strip('"')
                dims = first_shape.split(',')
                if dims and dims[0].isdigit():
                    first_dim = int(dims[0])
                    entry = {'name': name, 'dur_us': dur, 'shapes': shapes, 'cube_util': cube_util}
                    if first_dim > 64:
                        prefill_kernels.append(entry)
                    else:
                        decode_kernels.append(entry)
    # 汇总
    summary = {}
    for op_type, s in type_stats.items():
        avg_cube = round(sum(s['cube_utils']) / len(s['cube_utils']), 2) if s['cube_utils'] else 0
        summary[op_type] = {
            'count': s['count'], 'total_dur_us': round(s['total_dur_us'], 2),
            'avg_cube_util_pct': avg_cube, 'sample_shapes': s['sample_shapes']
        }
    return {
        'type_summary': summary,
        'anomalies': anomalies[:30],
        'prefill_matmul_count': len(prefill_kernels),
        'decode_matmul_count': len(decode_kernels),
        'prefill_sample': prefill_kernels[:5],
        'decode_sample': decode_kernels[:5],
    }

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: parse_kernel_details.py <kernel_details.csv>", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(parse_kernel_details(sys.argv[1]), indent=2, ensure_ascii=False))
