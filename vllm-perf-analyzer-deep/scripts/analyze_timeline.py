#!/usr/bin/env python3
"""解析 step_trace_time.csv，输出时间构成和 bubble 检测。"""
import csv
import json
import sys

def analyze_timeline(filepath):
    with open(filepath, 'r') as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    if not rows:
        return {'error': 'empty file'}
    row = rows[0]
    computing = float(row.get('Computing', 0))
    comm_not_overlap = float(row.get('Communication(Not Overlapped)', 0))
    overlapped = float(row.get('Overlapped', 0))
    communication = float(row.get('Communication', 0))
    free = float(row.get('Free', 0))
    bubble = float(row.get('Bubble', 0))
    preparing = float(row.get('Preparing', 0))
    total = computing + comm_not_overlap + free + bubble + preparing
    if total == 0:
        total = 1
    result = {
        'computing_us': computing,
        'communication_not_overlapped_us': comm_not_overlap,
        'overlapped_us': overlapped,
        'communication_us': communication,
        'free_us': free,
        'bubble_us': bubble,
        'preparing_us': preparing,
        'total_us': round(total, 2),
        'computing_ratio_pct': round(computing / total * 100, 2),
        'free_ratio_pct': round(free / total * 100, 2),
        'bubble_ratio_pct': round(bubble / total * 100, 2),
        'comm_ratio_pct': round(comm_not_overlap / total * 100, 2),
    }
    # 异常标记
    anomalies = []
    if result['free_ratio_pct'] > 50:
        anomalies.append({'type': 'high_free_time', 'ratio_pct': result['free_ratio_pct'], 'severity': 'high', 'desc': '设备空闲时间占比过高，可能存在 host 调度瓶颈或算子下发效率低'})
    if result['bubble_ratio_pct'] > 10:
        anomalies.append({'type': 'high_bubble', 'ratio_pct': result['bubble_ratio_pct'], 'severity': 'medium', 'desc': 'Pipeline bubble 占比较高'})
    if result['computing_ratio_pct'] < 20:
        anomalies.append({'type': 'low_compute_util', 'ratio_pct': result['computing_ratio_pct'], 'severity': 'high', 'desc': '计算利用率极低'})
    result['anomalies'] = anomalies
    return result

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: analyze_timeline.py <step_trace_time.csv>", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(analyze_timeline(sys.argv[1]), indent=2, ensure_ascii=False))
