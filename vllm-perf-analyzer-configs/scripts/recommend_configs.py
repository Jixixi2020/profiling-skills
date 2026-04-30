#!/usr/bin/env python3
"""L2 Config 类优化推荐桥接脚本。

从 Step 2a 的 analyze_timeline 输出 + state.json + serve 脚本 + L1 check_result，
调用 opt-recommender 的 recommend() 生成 config 类优化建议，剔除 L1 已测项。

用法:
  python3 l2_recommend.py <timeline_json> <state_json> <best_serve_sh> <l1_check_result_json> [--container NAME]

输出: JSON 到 stdout
"""
import argparse
import json
import os
import re
import sys

# 导入 opt-recommender 的核心模块
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIGS_SKILL = os.path.join(SCRIPT_DIR, '..')
sys.path.insert(0, os.path.join(CONFIGS_SKILL, 'knowledge'))
sys.path.insert(0, os.path.join(CONFIGS_SKILL, 'scripts'))

from optimization_items import OPTIMIZATION_ITEMS
from recommend_optimizations import recommend, is_item_enabled, check_applicable
from extract_enabled_configs import (
    read_script_content, extract_serve_args, extract_env_vars,
    extract_nested_json, flatten_dict
)


def build_profile(timeline_json_path, state_json_path, serve_content):
    """从 analyze_timeline 输出 + state.json 构建 profile dict"""
    with open(timeline_json_path) as f:
        timeline = json.load(f)
    with open(state_json_path) as f:
        state = json.load(f)

    model_info = state.get('model_info', {})

    # timeline 输出的是 pct (0-100)，recommend 需要 ratio (0-1)
    profile = {
        'free_ratio': timeline.get('free_ratio_pct', 0) / 100.0,
        'compute_ratio': timeline.get('computing_ratio_pct', 0) / 100.0,
        'comm_ratio': timeline.get('comm_ratio_pct', 0) / 100.0,
        'enforce_eager': '--enforce-eager' in serve_content,
        'tp_size': model_info.get('tp_size', 1),
        'pp_size': 1,
        'model_name': model_info.get('name', ''),
        'is_moe': model_info.get('is_moe', False),
        'has_speculative': '--speculative' in serve_content,
        'gpu_mem_util': 0.8,
        'max_num_seqs': 64,
        'max_batched_tokens': 16384,
        'bottleneck_items': [],
        'optimization_hints': [],
        'report_text': '',  # 无 markdown 报告，keyword 评分会为 0
        'goal': None,
    }
    return profile


def build_enabled_configs(serve_content):
    """从 serve 脚本内容构建 enabled_configs dict"""
    additional_config = extract_nested_json(serve_content, '--additional-config')
    # 也尝试 --additional_config (下划线变体)
    if not additional_config:
        additional_config = extract_nested_json(serve_content, '--additional_config')
    compile_config = extract_nested_json(serve_content, '--compilation-config')
    if not compile_config:
        compile_config = extract_nested_json(serve_content, '--compilation_config')
    if not compile_config:
        compile_config = extract_nested_json(serve_content, '-cc')

    return {
        'serve_args': extract_serve_args(serve_content),
        'env_vars': extract_env_vars(serve_content),
        'additional_config': additional_config,
        'additional_config_flat': flatten_dict(additional_config),
        'compile_config': compile_config,
    }


def collect_l1_tested_ids(check_result_path):
    """从 L1 check_result.json 收集所有已测试/已启用的 ID，
    并提取 env var 名和 additional_config key 用于交叉去重。"""
    ids = set()
    env_vars_tested = set()
    additional_keys_tested = set()
    try:
        with open(check_result_path) as f:
            cr = json.load(f)
        for section in ('forced', 'to_test', 'already_enabled'):
            for item in cr.get(section, []):
                ids.add(item['id'])
                # 从 config 字符串提取 env var 名
                cfg = item.get('config', '') or item.get('test_config', '')
                for line in cfg.split('\n'):
                    line = line.strip()
                    m = re.match(r'^export\s+(\w+)=', line)
                    if m:
                        env_vars_tested.add(m.group(1))
                    # 提取 additional-config 中的 key
                    m2 = re.match(r"^--additional-config\s+'(.+)'$", line)
                    if m2:
                        try:
                            d = json.loads(m2.group(1))
                            additional_keys_tested.update(d.keys())
                        except json.JSONDecodeError:
                            pass
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        pass
    return ids, env_vars_tested, additional_keys_tested


def is_covered_by_l1(item, l1_ids, l1_env_vars, l1_additional_keys):
    """检查 opt-recommender 项是否已被 L1 覆盖（ID 或 config 交叉匹配）"""
    # 1. ID 直接匹配
    if item['id'] in l1_ids:
        return True
    # 2. 显式 ID 映射（L1 default_opts 和 opt-recommender 90项知识库的 ID 不一致）
    ID_ALIASES = {
        'ascend_enable_fused_mc2': 'fused_mc2',
        'ascend_matmul_allreduce': 'matmul_allreduce',
        'ascend_balance_scheduling': 'balance_scheduling',
        'ascend_flashcomm1': 'flashcomm1',
        'enable_shared_expert_dp': 'shared_expert_dp',
        'enable_dbo': 'dbo',
        'eplb_dynamic': 'dynamic_eplb',
        'fuse_allreduce_rms': 'fuse_allreduce_rms',
        'matmul_allreduce_add_rmsnorm': 'fuse_allreduce_rms',
        'enable_mlapo': 'mlapo',
    }
    alias = ID_ALIASES.get(item['id'])
    if alias and alias in l1_ids:
        return True
    # 3. env var 交叉匹配
    check = item.get('enabled_check', {})
    check_type = check.get('type', '')
    if check_type in ('env_var', 'env_var_present') and check.get('var') in l1_env_vars:
        return True
    # 4. additional_config key 交叉匹配
    if check_type == 'additional_config' and check.get('key') in l1_additional_keys:
        return True
    return False


def main():
    parser = argparse.ArgumentParser(description='L2 config 类优化推荐')
    parser.add_argument('timeline_json', help='analyze_timeline.py 输出 JSON')
    parser.add_argument('state_json', help='state.json 路径')
    parser.add_argument('best_serve', help='当前最优 serve 脚本路径（宿主机）')
    parser.add_argument('l1_check_result', help='L1 check_result.json 路径')
    parser.add_argument('--container', default=None, help='容器名（如果 serve 在容器内）')
    parser.add_argument('--goal', choices=['latency', 'throughput'], default=None,
                        help='优化目标: latency 或 throughput，过滤不匹配的优化项')
    args = parser.parse_args()

    # 读取 serve 脚本内容
    serve_content = read_script_content(args.best_serve, args.container)

    # 构建输入
    profile = build_profile(args.timeline_json, args.state_json, serve_content)
    enabled_configs = build_enabled_configs(serve_content)
    l1_ids, l1_env_vars, l1_additional_keys = collect_l1_tested_ids(args.l1_check_result)

    # 额外排除: action_type=skip 的项 ID
    skip_ids = {item['id'] for item in OPTIMIZATION_ITEMS if item.get('action_type') == 'skip'}
    excluded_ids = l1_ids | skip_ids

    # 按优化目标过滤
    if args.goal:
        profile['goal'] = args.goal
        goal_excluded = {item['id'] for item in OPTIMIZATION_ITEMS
                         if args.goal not in item.get('optimization_goal', [])}
        excluded_ids |= goal_excluded

    # 调用推荐引擎
    recommendations = recommend(profile, enabled_configs, excluded_ids)

    # 二次过滤: 用 env_var/additional_config 交叉去重 L1 已测项
    item_map = {item['id']: item for item in OPTIMIZATION_ITEMS}
    config_recs = []
    l1_covered = []
    for r in recommendations:
        if r.get('score', 0) <= 0:
            continue
        item = item_map.get(r['item_id'])
        if item and is_covered_by_l1(item, l1_ids, l1_env_vars, l1_additional_keys):
            l1_covered.append(r['item_id'])
            continue
        config_recs.append(r)

    output = {
        'profile_summary': {
            'free_ratio': profile['free_ratio'],
            'compute_ratio': profile['compute_ratio'],
            'comm_ratio': profile['comm_ratio'],
            'is_moe': profile['is_moe'],
            'tp_size': profile['tp_size'],
        },
        'l1_excluded_count': len(l1_ids),
        'l1_cross_filtered': l1_covered,
        'skip_excluded_count': len(skip_ids),
        'total_recommendations': len(config_recs),
        'recommendations': config_recs,
    }

    json.dump(output, sys.stdout, ensure_ascii=False, indent=2)
    print()


if __name__ == '__main__':
    main()
