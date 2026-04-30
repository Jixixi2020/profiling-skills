#!/usr/bin/env bash
# run_measurement.sh — 统一测量脚本（含服务启停）
# 替代 run_benchmark.sh + run_benchmark_proxy.sh
# 用法: bash run_measurement.sh <container> <serve_script> <port> <benchmark_cmd> \
#         <duration> <perf_json> <serve_log> [--profiling <profiling_output_dir>]
#
# benchmark_cmd 格式:
#   "single <model_name>"          — 发送单请求（用于 decode profiling）
#   "script <benchmark_script>"    — 执行用户 benchmark 脚本（容器内路径）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILLS_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
source "$SCRIPT_DIR/common.sh"

SERVICE_MGR="$SCRIPT_DIR/service_manager.sh"
EXTRACT_LATENCY="$SCRIPT_DIR/extract_decode_step_latency.py"
EXTRACT_THROUGHPUT="$SCRIPT_DIR/extract_serve_throughput.py"

# ========== 参数解析 ==========
CONTAINER="${1:?缺少容器名}"
SERVE_SCRIPT="${2:?缺少 serve_script 路径}"
PORT="${3:?缺少端口}"
BENCHMARK_CMD="${4:?缺少 benchmark_cmd}"
DURATION="${5:?缺少 duration（秒）}"
PERF_JSON="${6:?缺少 perf_json 输出路径}"
SERVE_LOG="${7:?缺少 serve_log 路径}"
shift 7

PROFILING_MODE=0
PROFILING_DIR=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --profiling) PROFILING_MODE=1; PROFILING_DIR="${2:?--profiling 需要输出目录}"; shift 2 ;;
        *) log_error "未知参数: $1"; exit 1 ;;
    esac
done

_docker_exec() {
    docker exec "$CONTAINER" bash --norc --noprofile -c "$*"
}

ensure_dir "$(dirname "$PERF_JSON")"
ensure_dir "$(dirname "$SERVE_LOG")"

TOTAL_START=$(date +%s.%N)

# ========== Step 1: 清理残留 ==========
step_start "清理残留"
PROGRESS_LOG="${SERVE_LOG}.progress" bash "$SERVICE_MGR" clean "$CONTAINER"
step_end

# 清空旧 serve log，避免 fatal error 检测误匹配历史错误
: > "$SERVE_LOG" 2>/dev/null || true

# ========== Step 2: Profiling 模式 — 注入 profiler-config ==========
ACTUAL_SERVE_SCRIPT="$SERVE_SCRIPT"
if [[ "$PROFILING_MODE" -eq 1 ]]; then
    step_start "注入 profiler-config"
    PROFILER_CONTAINER_DIR="/tmp/profiling_$(date +%s)"
    _docker_exec "mkdir -p $PROFILER_CONTAINER_DIR && chmod 755 $PROFILER_CONTAINER_DIR"

    SERVE_CONTENT=$(_docker_exec "cat $SERVE_SCRIPT")
    PROFILER_CONFIG="{\"profiler\":\"torch\",\"torch_profiler_dir\":\"$PROFILER_CONTAINER_DIR\",\"torch_profiler_with_stack\":false}"
    MODIFIED_SERVE="/tmp/_measurement_serve_$$.sh"

    export _PROFILER_CONFIG="$PROFILER_CONFIG"
    export _MODIFIED_SERVE="$MODIFIED_SERVE"
    python3 -c '
import re, sys, os
content = sys.stdin.read()
profiler_config = os.environ["_PROFILER_CONFIG"]
modified_serve = os.environ["_MODIFIED_SERVE"]
if "--profiler-config" not in content:
    # 只匹配非注释行的 vllm serve 命令（行首非 # 开头）
    content = re.sub(
        r"^([^#\n]*vllm\s+serve\s+.*?)(\s*$)",
        r"\1 --profiler-config '"'"'" + profiler_config + "'"'"'" + r"\2",
        content, count=1, flags=re.MULTILINE
    )
if "DATA_SIMPLIFICATION" not in content:
    content = "export VLLM_PROFILER_DATA_SIMPLIFICATION=false\n" + content
with open(modified_serve, "w") as f:
    f.write(content)
' <<< "$SERVE_CONTENT"
    unset _PROFILER_CONFIG _MODIFIED_SERVE

    docker cp "$MODIFIED_SERVE" "$CONTAINER:$MODIFIED_SERVE"
    _docker_exec "chmod +x $MODIFIED_SERVE"
    ACTUAL_SERVE_SCRIPT="$MODIFIED_SERVE"
    log_info "profiler-config 已注入, profiling_dir=$PROFILER_CONTAINER_DIR"
    step_end
fi

# ========== Step 3: 启动服务 ==========
step_start "启动服务"
PROGRESS_LOG="${SERVE_LOG}.progress" bash "$SERVICE_MGR" start "$CONTAINER" "bash $ACTUAL_SERVE_SCRIPT" "$SERVE_LOG"
step_end

# ========== Step 4: 等待服务就绪 ==========
step_start "等待服务就绪"
PROGRESS_LOG="${SERVE_LOG}.progress" bash "$SERVICE_MGR" wait "$CONTAINER" "$PORT" 600 "$SERVE_LOG"
step_end

# ========== Step 5: 启动 benchmark ==========
step_start "启动 benchmark"
BENCH_TYPE=$(echo "$BENCHMARK_CMD" | awk '{print $1}')
BENCH_ARG=$(echo "$BENCHMARK_CMD" | cut -d' ' -f2-)

_docker_exec "rm -f /tmp/_bench_done /tmp/_bench_output.log 2>/dev/null || true"

if [[ "$BENCH_TYPE" == "single" ]]; then
    MODEL_NAME="$BENCH_ARG"
    log_info "发送单请求: model=$MODEL_NAME, max_tokens=2048"
    # 直接内联 curl 后台调用（不通过 single_request.sh，避免子 shell 阻塞）
    curl -s -X POST "http://localhost:${PORT}/v1/completions" \
      -H "Content-Type: application/json" \
      -d "{
        \"model\": \"${MODEL_NAME}\",
        \"prompt\": \"Write a very long and detailed essay about the history of artificial intelligence, covering all major milestones from the 1950s to 2025. Include technical details about each breakthrough.\",
        \"max_tokens\": 2048,
        \"temperature\": 0.7,
        \"stream\": false,
        \"ignore_eos\": true
      }" > /dev/null 2>&1 &
    BENCH_PID=$!
elif [[ "$BENCH_TYPE" == "script" ]]; then
    BENCH_SCRIPT="$BENCH_ARG"
    BENCH_DIR=$(dirname "$BENCH_SCRIPT")
    log_info "启动 benchmark 脚本: $BENCH_SCRIPT"
    docker exec -d "$CONTAINER" bash --norc --noprofile -c \
        "cd $BENCH_DIR && bash $BENCH_SCRIPT > /tmp/_bench_output.log 2>&1; echo DONE > /tmp/_bench_done"
    BENCH_PID=""
else
    log_error "未知 benchmark_cmd 类型: $BENCH_TYPE"
    exit 1
fi
step_end

# ========== Step 6: 等待 decoding 开始（检测 serve 日志 "Avg generation throughput" 行） ==========
step_start "等待 decoding 开始"
_sync_serve_log() {
    local clog
    clog=$(docker exec "$CONTAINER" bash --norc --noprofile -c 'ls -t /tmp/vllm_serve_*.log 2>/dev/null | head -1' 2>/dev/null | tr -d '\r')
    [[ -n "$clog" ]] && docker cp "$CONTAINER:$clog" "$SERVE_LOG" 2>/dev/null || true
}
DECODE_STARTED=0
for i in $(seq 1 60); do
    _sync_serve_log
    if grep -q "Avg generation throughput" "$SERVE_LOG" 2>/dev/null; then
        DECODE_STARTED=1
        log_info "检测到 decoding 已开始 (serve 日志出现 Avg generation throughput)"
        break
    fi
    sleep 5
done
if [[ "$DECODE_STARTED" -ne 1 ]]; then
    log_warn "等待 300s 仍未检测到 decoding，继续采集"
fi
# 记录当前 serve 日志行数，后续只提取此行之后的 throughput
SERVE_LOG_LINE_BEFORE=$(wc -l < "$SERVE_LOG" 2>/dev/null || echo "0")
MEASURE_START=$(date +%s.%N)
step_end

# ========== Step 7: 采集（throughput / profiling） ==========
if [[ "$PROFILING_MODE" -eq 1 ]]; then
    step_start "Profiling 采集 (${DURATION}s)"
    PROFILE_RESP=$(curl -s -w "\nHTTP_CODE:%{http_code}" -X POST "http://localhost:$PORT/start_profile" 2>&1 || true)
    PROFILE_HTTP=$(echo "$PROFILE_RESP" | grep "HTTP_CODE:" | sed 's/HTTP_CODE://')
    log_info "start_profile HTTP=$PROFILE_HTTP"

    sleep "$DURATION"

    STOP_RESP=$(curl -s -w "\nHTTP_CODE:%{http_code}" -X POST "http://localhost:$PORT/stop_profile" 2>&1 || true)
    STOP_HTTP=$(echo "$STOP_RESP" | grep "HTTP_CODE:" | sed 's/HTTP_CODE://')
    log_info "stop_profile HTTP=$STOP_HTTP"

    # stop_profile 是同步的：返回时 _finalize_profiler() 已完成，数据已在 page cache
    # 等 5s 让文件系统元数据同步
    log_info "等待 profiling 数据写入完成 (5s)..."
    sleep 5

    # 所有 docker exec/cp 操作移到服务停止后执行（避免 serve 运行时 docker exec 挂起）
    # 此处仅记录容器内 profiling 目录路径，后续在 Step 11 统一处理
    mkdir -p "$PROFILING_DIR"
    step_end
else
    if [[ "$DURATION" == "-1" ]]; then
        step_start "Throughput 采集 (等待 benchmark 完成)"
        # duration=-1: 等待整个 benchmark 跑完，用全量日志
        SERVE_LOG_LINE_BEFORE=0
        WAIT_COUNT=0
        while true; do
            # 用 test -f 检测文件存在（避免 docker exec 输出 banner 干扰字符串比较）
            if _docker_exec "test -f /tmp/_bench_done" 2>/dev/null; then
                log_info "benchmark 已完成"
                break
            fi
            WAIT_COUNT=$((WAIT_COUNT + 1))
            if [[ "$WAIT_COUNT" -ge 360 ]]; then
                log_warn "等待 1800s benchmark 仍未完成，强制结束"
                break
            fi
            sleep 5
        done
        log_info "采集窗口结束 (全量日志模式)"
    else
        step_start "Throughput 采集 (${DURATION}s)"
        sleep "$DURATION"
        log_info "采集窗口结束"
    fi
    step_end
fi

MEASURE_END=$(date +%s.%N)

# ========== Step 8: 停止 benchmark ==========
step_start "停止 benchmark"
if [[ -n "${BENCH_PID:-}" ]]; then
    kill "$BENCH_PID" 2>/dev/null || true
fi
# 杀容器内 benchmark 相关进程（匹配常见 benchmark 脚本名和 _bench_output.log 关联进程）
docker exec "$CONTAINER" bash --norc --noprofile -c \
    "pkill -9 -f 'benchmark|stress_test|locust|vllm_loop|_bench_output' 2>/dev/null; exit 0" \
    || true
sleep 2
step_end

# ========== Step 9: 提取指标 ==========
step_start "提取指标"
# 杀掉 service_manager 启动的日志同步进程
SYNC_PID=$(cat "${SERVE_LOG}.sync_pid" 2>/dev/null || echo "")
[[ -n "$SYNC_PID" ]] && kill "$SYNC_PID" 2>/dev/null || true

# 最终同步一次 serve 日志，确保采集窗口内的日志完整
_sync_serve_log

# 从 serve 日志提取 throughput（只取采集窗口内的行）
eval $(python3 -c "
import subprocess, json, sys
r = subprocess.run([sys.executable, '$EXTRACT_THROUGHPUT', '$SERVE_LOG', '--after-line', '${SERVE_LOG_LINE_BEFORE:-0}'], capture_output=True, text=True)
out = r.stdout
start, end = out.find('{'), out.rfind('}')
d = json.loads(out[start:end+1]) if start >= 0 and end > start else {}
print(f'MAX_TPS={d.get(\"max_tps\",0)}')
print(f'AVG_TPS={d.get(\"avg_tps\",0)}')
print(f'SAMPLE_COUNT={d.get(\"sample_count\",0)}')
" 2>/dev/null || echo "MAX_TPS=0; AVG_TPS=0; SAMPLE_COUNT=0")

TOTAL_END=$(date +%s.%N)
MEAS_DUR=$(python3 -c "print(round($MEASURE_END - $MEASURE_START, 1))")
TOTAL_DUR=$(python3 -c "print(round($TOTAL_END - $TOTAL_START, 1))")
step_end

# perf.json 生成移到 Step 11（离线解析）之后，确保 profiling CSV 已就绪

# ========== Step 10: 停止服务 ==========
step_start "停止服务"
PROGRESS_LOG="${SERVE_LOG}.progress" bash "$SERVICE_MGR" stop "$CONTAINER"
step_end

# ========== Step 11: 拷贝 + 离线解析 profiling（服务停止后执行，避免 OCI 竞态）==========
if [[ "$PROFILING_MODE" -eq 1 ]]; then
    step_start "离线解析 profiling（服务已停止）"

    # 11a. 查找 profiling 子目录（服务已停止，docker exec 安全）
    FIRST_PROF=$(_docker_exec "find $PROFILER_CONTAINER_DIR -maxdepth 1 -name '*_ascend_pt' -type d | head -1" | tr -d '\r')

    if [[ -n "$FIRST_PROF" ]]; then
        # 11b. 离线解析（带重试）
        ANALYSE_OK=0
        for RETRY in 1 2 3; do
            log_info "离线解析 (尝试 $RETRY/3): $FIRST_PROF"
            if _docker_exec "python3 -c \"from torch_npu.profiler.profiler import analyse; analyse(profiler_path='$FIRST_PROF')\" 2>&1"; then
                ANALYSE_OK=1
                break
            else
                log_warn "离线解析尝试 $RETRY 失败，等待 5s 后重试..."
                sleep 5
            fi
        done

        # 11c. 拷贝到宿主机（解析后拷贝，包含 CSV）
        if [[ "$ANALYSE_OK" -eq 1 ]]; then
            _docker_exec "chmod -R 755 $FIRST_PROF 2>/dev/null || true"
        fi
        docker cp "$CONTAINER:$PROFILER_CONTAINER_DIR/." "$PROFILING_DIR/" 2>/dev/null || true
        if [[ "$ANALYSE_OK" -eq 1 ]]; then
            log_info "离线解析成功，数据已拷贝到宿主机"
        else
            log_warn "离线解析 3 次均失败，仅拷贝原始数据"
        fi
    else
        log_warn "未找到 profiling 子目录"
    fi
    step_end
fi

# ========== Step 12: 生成 perf.json ==========
step_start "提取指标"
if [[ "$PROFILING_MODE" -eq 1 ]]; then
    eval $(python3 -c "
import subprocess, json, sys
r = subprocess.run([sys.executable, '$EXTRACT_LATENCY', '$PROFILING_DIR'], capture_output=True, text=True)
out = r.stdout
start, end = out.find('{'), out.rfind('}')
d = json.loads(out[start:end+1]) if start >= 0 and end > start else {}
print(f'LATENCY_US={d.get(\"latency_us\",0)}')
print(f'STEP_TPS={d.get(\"throughput_tps\",0)}')
print(f'ARGMAX_CNT={d.get(\"argmax_count\",0)}')
print(f'VALID_STEPS={d.get(\"valid_steps\",0)}')
" 2>/dev/null || echo "LATENCY_US=0; STEP_TPS=0; ARGMAX_CNT=0; VALID_STEPS=0")

    python3 -c "
import json
data = {
    'mode': 'profiling',
    'generation_throughput_max_tps': $MAX_TPS,
    'generation_throughput_avg_tps': $AVG_TPS,
    'sample_count': $SAMPLE_COUNT,
    'measurement_duration_s': $MEAS_DUR,
    'total_elapsed_s': $TOTAL_DUR,
    'decode_step_latency_us': $LATENCY_US,
    'decode_step_throughput_tps': $STEP_TPS,
    'profiling_dir': '$PROFILING_DIR',
    'argmax_count': $ARGMAX_CNT,
    'valid_steps': $VALID_STEPS,
}
with open('$PERF_JSON', 'w') as f:
    json.dump(data, f, indent=2)
"
else
    python3 -c "
import json
avg_tps = $AVG_TPS
data = {
    'mode': 'throughput',
    'generation_throughput_max_tps': $MAX_TPS,
    'generation_throughput_avg_tps': avg_tps,
    'sample_count': $SAMPLE_COUNT,
    'measurement_duration_s': $MEAS_DUR,
    'total_elapsed_s': $TOTAL_DUR,
}
with open('$PERF_JSON', 'w') as f:
    json.dump(data, f, indent=2)
"
fi
log_info "perf.json 已保存: $PERF_JSON"
step_end

log_info "测量完成: max_tps=$MAX_TPS, avg_tps=$AVG_TPS, total=${TOTAL_DUR}s"
