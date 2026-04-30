#!/usr/bin/env python3
"""
基于性能分析报告 + 已启用配置，从 87 项知识库匹配优化项并评分排序。
多维评分 + 过滤流水线，毫秒级完成，无 LLM 调用。

用法:
  python3 recommend_optimizations.py <report.md> <enabled_configs.json> [--excluded-ids "id1,id2"]

输出: JSON 到 stdout
"""
import sys
import os
import json
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'knowledge'))
from optimization_items import OPTIMIZATION_ITEMS
from categories import RISK_MULTIPLIER

# report_parser 仅在独立命令行调用时需要，延迟导入避免阻塞 recommend_configs.py 的导入链


def is_item_enabled(item, enabled_configs):
    """检查优化项是否已启用，支持所有 config_type"""
    check = item.get("enabled_check", {})
    check_type = check.get("type", "")
    serve_args = enabled_configs.get("serve_args", [])
    env_vars = enabled_configs.get("env_vars", {})
    ac_flat = enabled_configs.get("additional_config_flat", {})
    compile_cfg = enabled_configs.get("compile_config", {})
    arg_names = [a["arg"] for a in serve_args]
    arg_map = {a["arg"]: a["value"] for a in serve_args}

    if check_type == "always_false":
        return False
    if check_type == "env_var":
        return env_vars.get(check["var"]) == check.get("value")
    if check_type == "env_var_present":
        return check["var"] in env_vars
    if check_type == "serve_arg_present":
        return check["arg"] in arg_names
    if check_type == "serve_arg_absent":
        return check["arg"] not in arg_names
    if check_type == "serve_arg_value":
        return arg_map.get(check["arg"]) == check.get("value")
    if check_type == "additional_config":
        key = check.get("key", "")
        if "value" in check:
            return ac_flat.get(key) == check["value"]
        return key in ac_flat
    if check_type == "compile_config_present":
        return bool(compile_cfg)
    return False


def check_applicable(item, profile):
    """检查 applicable_when 条件"""
    conditions = item.get("applicable_when", {})
    if not conditions:
        return True
    for key, val in conditions.items():
        if key == "is_moe" and profile.get("is_moe") != val:
            return False
        if key == "has_speculative" and profile.get("has_speculative") != val:
            return False
        if key == "tp_size_gte" and profile.get("tp_size", 1) < val:
            return False
        if key == "pp_size_gte" and profile.get("pp_size", 1) < val:
            return False
    return True


def eval_trigger(trigger, profile):
    """评估单个 bottleneck_trigger 条件"""
    dim = trigger["dim"]
    op = trigger["op"]
    val = trigger["val"]
    actual = profile.get(dim)
    if actual is None:
        return False
    if op == "gte":
        return actual >= val
    if op == "lte":
        return actual <= val
    if op == "eq":
        return actual == val
    if op == "gt":
        return actual > val
    if op == "lt":
        return actual < val
    return False


def compute_trigger_score(item, profile):
    """计算 trigger_score (0-10)"""
    triggers = item.get("bottleneck_triggers", [])
    if not triggers:
        return 0.0
    max_weight = 0.0
    match_count = 0
    for t in triggers:
        if eval_trigger(t, profile):
            max_weight = max(max_weight, t.get("weight", 0.5))
            match_count += 1
    if match_count == 0:
        return 0.0
    return min(max_weight * 10 + (match_count - 1) * 1.0, 10.0)


def compute_keyword_score(item, report_text):
    """计算 keyword_score (0-10)"""
    text_lower = report_text.lower()
    hits = sum(1 for kw in item.get("keywords", []) if kw.lower() in text_lower)
    return min(hits * 2.0, 10.0)


def compute_context_score(item, profile):
    """计算 context_score (0-10)"""
    if not check_applicable(item, profile):
        return 0.0
    return 10.0


def score_item(item, profile, report_text):
    """多维评分公式"""
    trigger = compute_trigger_score(item, profile)
    priority = min(item.get("priority", 5), 10)
    context = compute_context_score(item, profile)
    keyword = compute_keyword_score(item, report_text)

    risk_str = item.get("risk", "低").split("—")[0].split(" ")[0].strip()
    risk_mult = RISK_MULTIPLIER.get(risk_str, 0.85)

    final = (trigger * 0.40 + priority * 0.25 + context * 0.20 + keyword * 0.15) * risk_mult
    return round(final, 2), {
        "trigger": round(trigger, 2),
        "priority": priority,
        "context": round(context, 2),
        "keyword": round(keyword, 2),
        "risk_mult": risk_mult,
    }


def recommend(profile, enabled_configs, excluded_ids):
    """过滤流水线 + 评分排序"""
    report_text = profile.get("report_text", "")
    results = []

    for item in OPTIMIZATION_ITEMS:
        # 1. 过滤 skip
        if item.get("action_type") == "skip":
            continue
        # 2. 过滤已启用
        if is_item_enabled(item, enabled_configs):
            continue
        # 3. 过滤 applicable_when 不满足
        if not check_applicable(item, profile):
            continue
        # 4. 过滤冲突项（与已启用项冲突）
        skip = False
        for cid in item.get("conflicts_with", []):
            conflict = next((i for i in OPTIMIZATION_ITEMS if i["id"] == cid), None)
            if conflict and is_item_enabled(conflict, enabled_configs):
                skip = True
                break
        if skip:
            continue
        # 5. 过滤 excluded_ids
        if item["id"] in excluded_ids:
            continue

        # 评分
        score, details = score_item(item, profile, report_text)

        # 无遗漏保障：trigger=0 且 keyword=0 才排除
        if details["trigger"] == 0 and details["keyword"] == 0:
            continue

        results.append({
            "item_id": item["id"],
            "seq": item.get("seq", 0),
            "name": item["name"],
            "category": item["category"],
            "type": item["config_type"],
            "operation": item["operation"],
            "expected_effect": item["expected_effect"],
            "risk": item["risk"],
            "dependencies": ", ".join(item.get("conflicts_with", [])) or "无",
            "tuning_guidance": item.get("tuning_guidance", ""),
            "score": score,
            "score_details": details,
            "keyword_hits": int(details["keyword"] / 2),
        })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results


def build_bottleneck_summary(profile):
    """从 profile 构建瓶颈摘要"""
    bottlenecks = []
    fr = profile.get("free_ratio", 0)
    cr = profile.get("compute_ratio", 0)
    comm = profile.get("comm_ratio", 0)

    if fr >= 0.5:
        bottlenecks.append({"type": "free_critical", "severity": "critical", "value": f"{fr*100:.1f}%"})
    elif fr >= 0.3:
        bottlenecks.append({"type": "free_high", "severity": "high", "value": f"{fr*100:.1f}%"})
    elif fr >= 0.1:
        bottlenecks.append({"type": "free_medium", "severity": "medium", "value": f"{fr*100:.1f}%"})

    if comm >= 0.2:
        bottlenecks.append({"type": "comm_critical", "severity": "critical", "value": f"{comm*100:.1f}%"})
    elif comm >= 0.1:
        bottlenecks.append({"type": "comm_high", "severity": "high", "value": f"{comm*100:.1f}%"})

    if cr >= 0.7:
        bottlenecks.append({"type": "compute_bound", "severity": "medium", "value": f"{cr*100:.1f}%"})

    # 从报告瓶颈表补充
    for bi in profile.get("bottleneck_items", []):
        bottlenecks.append({
            "type": bi["id"],
            "severity": bi["severity"].lower(),
            "value": bi["description"],
        })

    if not bottlenecks:
        bottlenecks.append({"type": "general", "severity": "medium", "value": "未检测到明显瓶颈"})

    return bottlenecks


def main():
    # 延迟导入 report_parser，仅独立命令行调用时需要
    sys.path.insert(0, os.path.dirname(__file__))
    from report_parser import parse_report

    parser = argparse.ArgumentParser(description="推荐优化项")
    parser.add_argument("report", help="performance_analysis_report.md 路径")
    parser.add_argument("enabled_configs", help="enabled_configs.json 路径")
    parser.add_argument("--excluded-ids", default="", help="已排除的优化 ID（逗号分隔）")
    args = parser.parse_args()

    profile = parse_report(args.report)

    with open(args.enabled_configs, 'r', encoding='utf-8') as f:
        enabled_configs = json.load(f)

    excluded_ids = set()
    if args.excluded_ids:
        excluded_ids = {x.strip() for x in args.excluded_ids.split(",") if x.strip()}

    recommendations = recommend(profile, enabled_configs, excluded_ids)
    bottlenecks = build_bottleneck_summary(profile)

    output = {
        "bottlenecks": bottlenecks,
        "recommendations": recommendations,
        "profile_summary": {
            "free_ratio": profile["free_ratio"],
            "compute_ratio": profile["compute_ratio"],
            "comm_ratio": profile["comm_ratio"],
            "enforce_eager": profile["enforce_eager"],
            "tp_size": profile["tp_size"],
            "is_moe": profile["is_moe"],
            "model_name": profile["model_name"],
        },
    }
    json.dump(output, sys.stdout, ensure_ascii=False, indent=2)
    print()


if __name__ == '__main__':
    main()
