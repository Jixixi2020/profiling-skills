#!/usr/bin/env python3
"""从 operator_details.csv 检测异常 H2D/D2H 操作。"""
import csv
import json
import sys

def detect_h2d_d2h(filepath):
    h2d_ops = []
    d2h_ops = []
    host_only_ops = []
    with open(filepath, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row.get('Name', '').strip()
            host_self = float(row.get('Host Self Duration(us)', 0))
            host_total = float(row.get('Host Total Duration(us)', 0))
            dev_self = float(row.get('Device Self Duration(us)', 0))
            dev_total = float(row.get('Device Total Duration(us)', 0))
            call_stack = row.get('Call Stack', '').strip()
            entry = {'name': name, 'host_self_us': host_self, 'host_total_us': host_total,
                     'device_self_us': dev_self, 'device_total_us': dev_total}
            if call_stack:
                entry['call_stack'] = call_stack[:500]
            if 'memcpy_host_to_device' in name.lower() or 'h2d' in name.lower():
                h2d_ops.append(entry)
            elif 'memcpy_device_to_host' in name.lower() or 'd2h' in name.lower():
                d2h_ops.append(entry)
            elif dev_self == 0 and dev_total == 0 and host_self > 100:
                host_only_ops.append(entry)
    h2d_ops.sort(key=lambda x: x['device_self_us'], reverse=True)
    d2h_ops.sort(key=lambda x: x['device_self_us'], reverse=True)
    host_only_ops.sort(key=lambda x: x['host_self_us'], reverse=True)
    total_h2d_us = sum(o['device_self_us'] for o in h2d_ops)
    total_d2h_us = sum(o['device_self_us'] for o in d2h_ops)
    total_host_only_us = sum(o['host_self_us'] for o in host_only_ops)
    return {
        'h2d_count': len(h2d_ops), 'h2d_total_device_us': round(total_h2d_us, 2), 'h2d_top': h2d_ops[:10],
        'd2h_count': len(d2h_ops), 'd2h_total_device_us': round(total_d2h_us, 2), 'd2h_top': d2h_ops[:10],
        'host_only_count': len(host_only_ops), 'host_only_total_us': round(total_host_only_us, 2), 'host_only_top': host_only_ops[:10],
    }

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: detect_h2d_d2h.py <operator_details.csv>", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(detect_h2d_d2h(sys.argv[1]), indent=2, ensure_ascii=False))
