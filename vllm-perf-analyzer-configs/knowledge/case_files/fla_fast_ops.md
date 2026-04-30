# FLA 快速数学函数环境变量开关

## 模型信息
- 模型: Qwen3.5-35B-A3B (使用 FLA triton kernel)
- 硬件: Ascend NPU
- 框架: vLLM + vllm-ascend

## 性能瓶颈

在分析 `fused_sigmoid_gating` kernel 源码时，发现同一文件中存在 `FLA_USE_FAST_OPS` 环境变量开关（第 18 行），默认未启用。

该开关控制是否使用快速数学函数（fast math ops），启用后 triton kernel 内部的数学运算（如 sigmoid、exp 等）会使用精度略低但速度更快的实现。

诊断链路:
1. 在追踪 `fused_sigmoid_gating` kernel 源码时发现
2. 源码第 18 行: `use_fast_ops = os.environ.get('FLA_USE_FAST_OPS', '0') == '1'`
3. 默认值为 `'0'`（未启用）
4. 启用后影响 kernel 内部的数学函数实现路径

## 优化方案

在启动 vLLM 服务前设置环境变量:

```bash
export FLA_USE_FAST_OPS=1
```

或在 serve 脚本中添加:

```python
import os
os.environ['FLA_USE_FAST_OPS'] = '1'
```

注意事项:
- 快速数学函数会牺牲少量精度换取速度
- 对推理场景影响极小（推理本身已有量化等精度损失）
- 建议在启用后验证模型输出质量无明显退化

## 实验效果

- Decode latency: -3.2%
- 风险等级: 低（环境变量开关，可随时关闭）
- 与 sigmoid_gating num_warps 调优可叠加

## 关键词
latency, FLA_USE_FAST_OPS, 环境变量, fast math, triton, sigmoid, 快速数学函数, kernel 优化, 算子调优
