#!/usr/bin/env python3
"""解析 op_statistic.csv，输出算子耗时排名和核心类型汇总。"""
import csv
import json
import sys

def parse_op_stats(filepath):
    ops = []
    core_type_summary = {}
    total_time = 0.0
    with open(filepath, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            op = {
                'op_type': row.get('OP Type', '').strip(),
                'core_type': row.get('Core Type', '').strip(),
                'count': int(row.get('Count', 0)),
                'total_time_us': float(row.get('Total Time(us)', 0)),
                'avg_time_us': float(row.get('Avg Time(us)', 0)),
                'max_time_us': float(row.get('Max Time(us)', 0)),
                'min_time_us': float(row.get('Min Time(us)', 0)),
                'ratio_pct': float(row.get('Ratio(%)', 0)),
            }
            ops.append(op)
            total_time += op['total_time_us']
            ct = op['core_type']
            if ct not in core_type_summary:
                core_type_summary[ct] = {'total_time_us': 0, 'op_count': 0, 'op_types': []}
            core_type_summary[ct]['total_time_us'] += op['total_time_us']
            core_type_summary[ct]['op_count'] += 1
            core_type_summary[ct]['op_types'].append(op['op_type'])
    for ct in core_type_summary:
        core_type_summary[ct]['ratio_pct'] = round(core_type_summary[ct]['total_time_us'] / total_time * 100, 2) if total_time > 0 else 0
    ops.sort(key=lambda x: x['total_time_us'], reverse=True)
    return {'total_device_time_us': round(total_time, 2), 'top_ops': ops[:20], 'core_type_summary': core_type_summary}

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: parse_op_stats.py <op_statistic.csv>", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(parse_op_stats(sys.argv[1]), indent=2, ensure_ascii=False))
