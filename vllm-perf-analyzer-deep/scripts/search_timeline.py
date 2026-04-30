#!/usr/bin/env python3
"""在 trace_view.json 中按算子名、事件类别或时间区间搜索，支持流式读取大文件。
支持按 duration 排序、上下文事件、free 区间搜索。"""
import json
import sys
import argparse
from collections import deque


def iter_events(filepath):
    """流式逐个 yield trace_view.json 中的事件对象。"""
    with open(filepath, 'r') as f:
        # skip to first '['
        while True:
            ch = f.read(1)
            if ch == '[':
                break
            elif ch == '':
                return
        buf = ''
        depth = 0
        in_string = False
        escape = False
        for line in f:
            for ch in line:
                if escape:
                    buf += ch
                    escape = False
                    continue
                if ch == '\\' and in_string:
                    buf += ch
                    escape = True
                    continue
                if ch == '"':
                    in_string = not in_string
                    buf += ch
                    continue
                if in_string:
                    buf += ch
                    continue
                if ch == '{':
                    depth += 1
                    buf += ch
                elif ch == '}':
                    depth -= 1
                    buf += ch
                    if depth == 0:
                        buf = buf.strip()
                        if buf:
                            try:
                                yield json.loads(buf)
                            except json.JSONDecodeError:
                                pass
                        buf = ''
                elif ch == ',' and depth == 0:
                    buf = ''
                elif ch == ']' and depth == 0:
                    return
                else:
                    buf += ch


def matches(event, op_names, ts_start, ts_end, cat_filter):
    name = event.get('name', '')
    ts_raw = event.get('ts', 0)
    dur = event.get('dur', 0)
    cat = event.get('cat', '')

    if cat_filter:
        if cat.lower() != cat_filter.lower():
            return False
    if op_names:
        if not any(n.lower() in name.lower() for n in op_names):
            return False
    try:
        ts = float(ts_raw)
    except (ValueError, TypeError):
        ts = 0
    if ts_start is not None and ts + dur < ts_start:
        return False
    if ts_end is not None and ts > ts_end:
        return False
    return True


def search_events(filepath, op_names=None, ts_start=None, ts_end=None,
                  limit=50, cat_filter=None, sort_by_dur=False, context=0):
    """搜索匹配事件。支持 sort_by_dur 和 context。"""
    effective_limit = 10000 if sort_by_dur else limit

    if context > 0:
        return _search_with_context(filepath, op_names, ts_start, ts_end,
                                    effective_limit, cat_filter, sort_by_dur,
                                    context, limit)

    results = []
    for event in iter_events(filepath):
        if matches(event, op_names, ts_start, ts_end, cat_filter):
            results.append(event)
            if len(results) >= effective_limit:
                break

    if sort_by_dur:
        results.sort(key=lambda e: float(e.get('dur', 0)), reverse=True)
        results = results[:limit]
    return results


def _search_with_context(filepath, op_names, ts_start, ts_end,
                         effective_limit, cat_filter, sort_by_dur,
                         context, final_limit):
    """搜索并返回匹配事件及其前后 context 个事件。"""
    results = []
    before_buf = deque(maxlen=context)
    # pending_matches: list of (match, before, after_remaining)
    pending = []

    for event in iter_events(filepath):
        # fill after context for pending matches
        new_pending = []
        for m, bef, after, remaining in pending:
            after.append(event)
            remaining -= 1
            if remaining > 0:
                new_pending.append((m, bef, after, remaining))
            else:
                results.append({"match": m, "before": bef, "after": after})
        pending = new_pending

        if matches(event, op_names, ts_start, ts_end, cat_filter):
            if len(results) + len(pending) < effective_limit:
                pending.append((event, list(before_buf), [], context))

        before_buf.append(event)

    # flush remaining pending (not enough after events)
    for m, bef, after, remaining in pending:
        results.append({"match": m, "before": bef, "after": after})

    if sort_by_dur:
        results.sort(key=lambda r: float(r["match"].get('dur', 0)),
                     reverse=True)
        results = results[:final_limit]
    else:
        results = results[:final_limit]
    return results


def find_gaps(filepath, min_gap_us=1000, limit=50):
    """按 tid 分组，找相邻事件间的 free 区间。"""
    # collect events by tid (only duration events)
    tid_events = {}
    for event in iter_events(filepath):
        if event.get('ph') not in ('X', 'x') and 'dur' not in event:
            continue
        tid = event.get('tid', 0)
        ts = float(event.get('ts', 0))
        dur = float(event.get('dur', 0))
        if dur <= 0:
            continue
        tid_events.setdefault(tid, []).append((ts, dur, event))

    gaps = []
    for tid, events in tid_events.items():
        events.sort(key=lambda x: x[0])
        for i in range(len(events) - 1):
            prev_ts, prev_dur, prev_ev = events[i]
            next_ts, _, next_ev = events[i + 1]
            gap = next_ts - (prev_ts + prev_dur)
            if gap >= min_gap_us:
                gaps.append({
                    "gap_duration_us": round(gap, 2),
                    "tid": tid,
                    "before_event": prev_ev,
                    "after_event": next_ev
                })

    gaps.sort(key=lambda g: g["gap_duration_us"], reverse=True)
    return gaps[:limit]


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Search trace_view.json')
    parser.add_argument('filepath', help='Path to trace_view.json')
    parser.add_argument('--op', nargs='+',
                        help='Operator/function names to search (substring)')
    parser.add_argument('--cat',
                        help='Filter by event category '
                             '(e.g. python_function, cpu_op)')
    parser.add_argument('--ts-start', type=float,
                        help='Start timestamp (us)')
    parser.add_argument('--ts-end', type=float,
                        help='End timestamp (us)')
    parser.add_argument('--limit', type=int, default=50,
                        help='Max results (default 50)')
    parser.add_argument('--sort-by-dur', action='store_true',
                        help='Sort results by duration descending')
    parser.add_argument('--context', type=int, default=0,
                        help='Return N events before and after each match')
    parser.add_argument('--find-gaps', action='store_true',
                        help='Find free gaps between events on same tid')
    parser.add_argument('--min-gap-us', type=float, default=1000,
                        help='Minimum gap threshold in us (default 1000)')
    args = parser.parse_args()

    if args.find_gaps:
        results = find_gaps(args.filepath, args.min_gap_us, args.limit)
    else:
        results = search_events(args.filepath, args.op, args.ts_start,
                                args.ts_end, args.limit, args.cat,
                                args.sort_by_dur, args.context)
    print(json.dumps(results, indent=2, ensure_ascii=False))
