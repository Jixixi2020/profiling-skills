---
name: vllm-report-generator
description: vLLM 优化报告与图表生成
user_invocable: false
---

# vLLM 优化报告与图表生成

## 调用方式

由 PM (vllm-optimize-pm) 或各层 subagent 调用，不可由用户直接调用。

调用时传入参数:
- `base_dir`: 优化工作目录根路径（包含 layer1/ ~ layer4/ 子目录）
- `layer`: 当前层级（`l1` / `l2` / `l3` / `l4` / `final`）
- `state_json`: state.json 路径（读取 baseline 和各层结果）

## 阶段报告模板

每个 Layer 完成后生成 `$base_dir/final_deliverables/layerN/layerN_report.md`，必须包含以下 5 个章节:

### 1. Profiling 瓶颈分析

- Timeline 占比表（Computing / Communication / Free / Overlap）
- 热点算子 TOP-10（算子名、调用次数、avg 耗时、占比）
- 瓶颈归因分析（Free 来源、放大倍数异常算子、通信阻塞点）

### 2. 优化思路

- 针对瓶颈的策略选择及理由
- 参考的案例或知识库条目
- 风险评估（低/中/高）

### 3. 实验数据

- 每项优化的测量结果表:

| 优化项 | 操作 | latency (ms) | throughput (tps) | delta% | 判定 |
|--------|------|-------------|-----------------|--------|------|
| baseline | - | 11.58 | 45.2 | - | - |
| opt_1 | APPLY | 11.23 | 46.1 | -3.0% | KEEP |
| opt_2 | APPLY+ROLLBACK | 11.85 | 44.8 | +2.3% | ROLLBACK |

- 累积效果汇总

### 4. 可视化图表

使用 matplotlib 生成图表文件，保存到 `$base_dir/final_deliverables/layerN/charts/` 目录。

具体图表类型见下方"图表生成规范"。

### 5. 下一步建议

- 未尝试的优化方向
- 当前瓶颈的剩余空间估算
- 风险提示和注意事项

## 最终报告模板

全部 Layer 完成后生成 `$base_dir/final_deliverables/FINAL_REPORT.md`，额外包含:

### 总览表

| 指标 | Baseline | Final | 提升 |
|------|----------|-------|------|
| decode_step_latency_us | 11580 | 9230 | -20.3% |
| throughput_tps | 45.2 | 56.8 | +25.7% |

### 四层优化汇总

每层一段摘要: 优化项数、KEEP/ROLLBACK 数、关键收益。

### 保留优化项清单

所有 KEEP 的优化项列表，含类型（配置/代码）、简述、delta%。

### 全局优化进度折线图

横轴为所有优化项（按执行顺序），纵轴为 latency 和 throughput 的变化趋势。

### Patches 目录结构说明

```
patches/
  config/
    01_xxx/README.md + config.sh
  code/
    01_xxx/README.md + fix.patch
  SUMMARY.md
```

## 图表生成规范

所有图表统一使用以下规范:

- 库: matplotlib
- 字体: DejaVu Sans（确保中英文兼容）
- 分辨率: dpi=150
- 每个数据点标注绝对值和 delta%
- 配色: 使用 matplotlib 默认 tab10 色板
- 保存格式: PNG

## matplotlib 代码模板

### 优化进度折线图

```python
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import os

def plot_optimization_progress(data, output_dir, metric='latency'):
    """
    绘制优化进度折线图。

    Args:
        data: list of dict, 每项包含:
            - name: str, 优化项名称
            - value: float, 指标值 (latency_ms 或 throughput_tps)
            - delta_pct: float, 相对 baseline 的变化百分比
            - status: str, KEEP / ROLLBACK
        output_dir: str, 图表输出目录
        metric: str, 'latency' 或 'throughput'
    """
    plt.rcParams['font.family'] = 'DejaVu Sans'
    fig, ax = plt.subplots(figsize=(max(10, len(data) * 1.2), 6))

    names = [d['name'] for d in data]
    values = [d['value'] for d in data]
    deltas = [d['delta_pct'] for d in data]
    statuses = [d['status'] for d in data]

    colors = ['#2ca02c' if s == 'KEEP' else '#d62728' for s in statuses]

    ax.plot(range(len(values)), values, 'o-', color='#1f77b4', linewidth=2, markersize=8)

    for i, (v, d, c) in enumerate(zip(values, deltas, colors)):
        label = f"{v:.2f}\n({d:+.1f}%)"
        ax.annotate(label, (i, v), textcoords="offset points",
                    xytext=(0, 15), ha='center', fontsize=8, color=c)

    ax.set_xticks(range(len(names)))
    ax.set_xticklabels(names, rotation=45, ha='right', fontsize=9)

    ylabel = 'Latency (ms)' if metric == 'latency' else 'Throughput (tps)'
    ax.set_ylabel(ylabel, fontsize=12)
    ax.set_title(f'Optimization Progress - {ylabel}', fontsize=14)
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f'optimization_progress_{metric}.png')
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()
    return output_path
```

### Timeline 占比对比柱状图

```python
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import os

def plot_timeline_comparison(before, after, output_dir):
    """
    绘制优化前后 Timeline 占比对比柱状图。

    Args:
        before: dict, 优化前占比, e.g. {'Computing': 45.2, 'Communication': 12.1, 'Free': 33.8, 'Overlap': 8.9}
        after: dict, 优化后占比
        output_dir: str, 图表输出目录
    """
    plt.rcParams['font.family'] = 'DejaVu Sans'

    categories = list(before.keys())
    before_vals = [before[c] for c in categories]
    after_vals = [after[c] for c in categories]

    x = np.arange(len(categories))
    width = 0.35

    fig, ax = plt.subplots(figsize=(10, 6))
    bars1 = ax.bar(x - width/2, before_vals, width, label='Before', color='#ff7f0e', alpha=0.8)
    bars2 = ax.bar(x + width/2, after_vals, width, label='After', color='#2ca02c', alpha=0.8)

    for bar_group, vals in [(bars1, before_vals), (bars2, after_vals)]:
        for bar, val in zip(bar_group, vals):
            ax.text(bar.get_x() + bar.get_width()/2., bar.get_height() + 0.5,
                    f'{val:.1f}%', ha='center', va='bottom', fontsize=10)

    # 标注 delta%
    for i, (b, a) in enumerate(zip(before_vals, after_vals)):
        if b > 0:
            delta = (a - b) / b * 100
            ax.annotate(f'{delta:+.1f}%',
                        xy=(x[i] + width/2, a),
                        xytext=(15, 10), textcoords='offset points',
                        fontsize=9, color='#d62728', fontweight='bold',
                        arrowprops=dict(arrowstyle='->', color='#d62728', lw=1.2))

    ax.set_ylabel('Percentage (%)', fontsize=12)
    ax.set_title('Timeline Breakdown: Before vs After Optimization', fontsize=14)
    ax.set_xticks(x)
    ax.set_xticklabels(categories, fontsize=11)
    ax.legend(fontsize=11)
    ax.grid(True, axis='y', alpha=0.3)

    plt.tight_layout()
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, 'timeline_comparison.png')
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()
    return output_path
```
