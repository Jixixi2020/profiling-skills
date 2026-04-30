#!/usr/bin/env bash
# service_manager.sh — vllm 服务生命周期管理
# 用法: PROGRESS_LOG=<log> bash service_manager.sh <action> [参数...]
#   clean  <container>                                — 清理残留进程
#   start  <container> <serve_cmd> <log_file>         — 后台启动服务
#   wait   <container> <port> <timeout_secs> <serve_log> — 等待服务就绪
#   test   <container> <port> <model_name>            — 测试服务响应
#   stop   <container> [timeout_secs]                 — 停止服务

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILLS_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
source "$SCRIPT_DIR/common.sh"

ACTION="${1:?用法: service_manager.sh <clean|start|wait|test|stop> [参数...]}"
shift

# ========== 辅助函数：安全 docker exec（去除 motd/banner 干扰） ==========
_docker_exec() {
    local container="$1"
    shift
    docker exec "$container" bash --norc --noprofile -c "$*"
}

_docker_exec_d() {
    local container="$1"
    shift
    docker exec -d "$container" bash --norc --noprofile -c "$*"
}

# docker exec 带重试（应对 OCI runtime /proc/self/fd bug）
_docker_exec_with_retry() {
    local container="$1"
    local cmd="$2"
    local max_retries="${3:-3}"
    local retry=0
    local output=""
    while [[ $retry -lt $max_retries ]]; do
        output=$(docker exec "$container" bash --norc --noprofile -c "$cmd" 2>&1)
        local rc=$?
        if [[ $rc -eq 0 ]] || ! echo "$output" | grep -q "OCI runtime exec failed"; then
            echo "$output"
            return $rc
        fi
        retry=$((retry + 1))
        [[ $retry -lt $max_retries ]] && sleep 1
    done
    echo "$output"
    return 1
}

# ========== clean: 清理残留进程 ==========
action_clean() {
    local container="${1:?缺少容器名}"
    step_start "清理进程"

    # 先检测是否有残留进程
    local vllm_pids
    vllm_pids=$(_docker_exec "$container" "pgrep -f 'vllm' 2>/dev/null || pgrep VLLM 2>/dev/null || true" | tr -d '\r' | xargs)
    if [[ -z "$vllm_pids" ]]; then
        log_info "无残留 vllm 进程，跳过清理"
        step_end
        return 0
    fi

    log_info "检测到残留进程 ($vllm_pids)，清理中..."
    _docker_exec "$container" "pkill -9 python 2>/dev/null || true"
    _docker_exec "$container" "pkill -9 VLLM 2>/dev/null || true"

    log_info "等待 NPU 显存释放 (10s)..."
    sleep 10

    local remaining
    remaining=$(_docker_exec "$container" "pgrep -f 'vll[m]' 2>/dev/null || true" | tr -d '[:space:]')
    if [[ -n "$remaining" ]]; then
        log_warn "仍有残留进程: $remaining"
    else
        log_info "进程已全部清理"
    fi

    step_end
}

# ========== start: 后台启动服务 ==========
action_start() {
    local container="${1:?缺少容器名}"
    local serve_cmd="${2:?缺少启动命令}"
    local log_file="${3:?缺少日志文件路径}"

    step_start "启动服务"
    log_info "启动命令: $serve_cmd"
    log_info "服务日志: $log_file"

    : > "$log_file" 2>/dev/null || true
    touch "$log_file"

    _docker_exec "$container" "rm -f /tmp/vllm_serve_*.log 2>/dev/null || true" || true

    local container_log="/tmp/vllm_serve_$(date +%s).log"

    local script_dir
    script_dir=$(echo "$serve_cmd" | grep -oP '(?:bash\s+)\K\S+' | xargs dirname 2>/dev/null || echo "/")
    _docker_exec_d "$container" "cd $script_dir && $serve_cmd > $container_log 2>&1"

    (while true; do docker cp "$container:$container_log" "$log_file" 2>/dev/null; sleep 5; done) &
    local sync_pid=$!
    echo "$sync_pid" > "${log_file}.sync_pid"

    sleep 5
    local pid
    pid=$(_docker_exec "$container" "pgrep -f 'vllm serve|vllm[.]entrypoints' 2>/dev/null | head -1 || true" | tr -d '[:space:]')
    if [[ -n "$pid" ]]; then
        log_info "服务进程已启动 (PID: $pid)"
    else
        log_error "服务进程未能启动"
        log_tail_context "$log_file" 30 "服务日志"
        step_end
        return 1
    fi

    step_end
}

# ========== wait: 等待服务就绪 ==========
action_wait() {
    local container="${1:?缺少容器名}"
    local port="${2:?缺少端口}"
    local timeout_secs="${3:-600}"
    local serve_log="${4:-}"

    step_start "等待服务就绪"
    log_info "端口: $port, 超时: ${timeout_secs}s"

    local interval=10
    local elapsed=0
    local last_log_lines=0

    while [[ $elapsed -lt $timeout_secs ]]; do
        local pid
        pid=$(_docker_exec_with_retry "$container" "pgrep -f 'vllm serve|vllm[.]entrypoints' 2>/dev/null | head -1 || true" 3 | tail -1 | tr -d '[:space:]')
        if [[ -z "$pid" ]]; then
            log_error "服务进程已退出，启动失败"
            if [[ -n "$serve_log" ]]; then
                log_tail_context "$serve_log" 30 "服务日志(进程退出)"
            fi
            step_end
            return 1
        fi

        if [[ -n "$serve_log" && -f "$serve_log" ]]; then
            local fatal_errors
            fatal_errors=$(grep -ciE "NPU out of memory|RuntimeError.*Engine core initialization failed|CUDA out of memory|TimeoutError|pydantic_core.*ValidationError|ValidationError.*Assertion failed|ERR99999 UNKNOWN" "$serve_log" 2>/dev/null || echo "0")
            fatal_errors=$(echo "$fatal_errors" | tail -1 | tr -d '[:space:]')
            if [[ -z "$fatal_errors" ]]; then fatal_errors=0; fi
            if [[ "$fatal_errors" -gt 0 ]]; then
                log_error "检测到致命错误，服务无法启动"
                log_tail_context "$serve_log" 40 "服务日志(致命错误)"
                step_end
                return 1
            fi
        fi

        local http_code
        http_code=$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:${port}/health" 2>/dev/null || echo '000')
        if [[ "$http_code" == "000" ]]; then
            http_code=$(_docker_exec_with_retry "$container" \
                "curl -s -o /dev/null -w '%{http_code}' http://localhost:${port}/health 2>/dev/null || echo '000'" 3)
        fi
        http_code=$(echo "$http_code" | tail -1 | tr -d '[:space:]')

        if [[ "$http_code" == "200" ]]; then
            log_info "服务就绪 (HTTP 200), 已等待 ${elapsed}s"
            step_end
            return 0
        fi

        local status_msg="HTTP $http_code"
        if [[ -n "$serve_log" && -f "$serve_log" ]]; then
            local current_lines
            current_lines=$(wc -l < "$serve_log" 2>/dev/null || echo "0")
            if [[ $current_lines -gt $last_log_lines ]]; then
                local latest_info
                latest_info=$(tail -n $((current_lines - last_log_lines)) "$serve_log" 2>/dev/null | \
                    grep -iE "loading|initializ|model|engine|worker|ready|error|failed|exception|memory" | \
                    tail -1 || true)
                if [[ -n "$latest_info" ]]; then
                    status_msg="$status_msg | $latest_info"
                fi
                last_log_lines=$current_lines
            fi
        fi

        log_info "已等待 ${elapsed}s/${timeout_secs}s, 状态: $status_msg"

        sleep "$interval"
        elapsed=$((elapsed + interval))
    done

    log_error "等待超时 (${timeout_secs}s)"
    if [[ -n "$serve_log" ]]; then
        log_tail_context "$serve_log" 30 "服务日志(超时)"
    fi
    step_end
    return 1
}

# ========== test: 测试服务响应 ==========
action_test() {
    local container="${1:?缺少容器名}"
    local port="${2:?缺少端口}"
    local model_name="${3:?缺少模型名}"

    step_start "测试服务"
    log_info "发送测试请求: model=$model_name, port=$port"

    local result
    result=$(_docker_exec "$container" "curl -s -X POST http://localhost:${port}/v1/completions \
        -H 'Content-Type: application/json' \
        -d '{\"model\": \"${model_name}\", \"prompt\": \"Hello\", \"max_tokens\": 10}' 2>&1" || true)

    if echo "$result" | grep -q "choices"; then
        log_info "服务响应正常"
        log_detail "响应内容: $result"
        step_end
        return 0
    else
        log_error "服务响应异常"
        log_error "响应内容: $result"
        step_end
        return 1
    fi
}

# ========== stop: 停止服务 ==========
action_stop() {
    local container="${1:?缺少容器名}"
    local timeout_secs="${2:-10}"

    step_start "停止服务"

    log_info "强制停止所有 VLLM 和 python 进程..."
    _docker_exec "$container" "pkill -9 python 2>/dev/null || true"
    _docker_exec "$container" "pkill -9 VLLM 2>/dev/null || true"

    log_info "等待 NPU 显存释放 (10s)..."
    sleep 10

    log_info "服务已停止"
    step_end
}

# ========== 分发 ==========
case "$ACTION" in
    clean) action_clean "$@" ;;
    start) action_start "$@" ;;
    wait)  action_wait "$@" ;;
    test)  action_test "$@" ;;
    stop)  action_stop "$@" ;;
    *)
        echo "未知操作: $ACTION" >&2
        echo "用法: service_manager.sh <clean|start|wait|test|stop> [参数...]" >&2
        exit 1
        ;;
esac