#!/usr/bin/env bash
# common.sh — vllm-auto-optimizer 公共函数库
# 提供统一日志、参数解析、通用工具函数
# 使用方式: source "$(dirname "$0")/common.sh"

set -euo pipefail

# ========== 全局变量 ==========
PROGRESS_LOG="${PROGRESS_LOG:-/dev/null}"
STEP_LABEL="${STEP_LABEL:-}"
_STEP_START_TIME=""

# ========== 日志函数 ==========

_timestamp() {
    date '+%Y-%m-%d %H:%M:%S'
}

log_info() {
    local msg="$*"
    local prefix="[$(_timestamp)] [INFO]"
    [[ -n "$STEP_LABEL" ]] && prefix="$prefix [$STEP_LABEL]"
    echo "$prefix $msg" | tee -a "$PROGRESS_LOG"
}

log_warn() {
    local msg="$*"
    local prefix="[$(_timestamp)] [WARN]"
    [[ -n "$STEP_LABEL" ]] && prefix="$prefix [$STEP_LABEL]"
    echo "$prefix $msg" | tee -a "$PROGRESS_LOG"
}

log_error() {
    local msg="$*"
    local prefix="[$(_timestamp)] [ERROR]"
    [[ -n "$STEP_LABEL" ]] && prefix="$prefix [$STEP_LABEL]"
    echo "$prefix $msg" | tee -a "$PROGRESS_LOG" >&2
}

log_section() {
    local title="$*"
    {
        echo ""
        echo "[$(_timestamp)] ========================================"
        echo "[$(_timestamp)] $title"
        echo "[$(_timestamp)] ========================================"
    } | tee -a "$PROGRESS_LOG"
}

log_detail() {
    # 详细级别日志，仅写入日志文件不打印到终端
    local msg="$*"
    local prefix="[$(_timestamp)] [DEBUG]"
    [[ -n "$STEP_LABEL" ]] && prefix="$prefix [$STEP_LABEL]"
    echo "$prefix $msg" >> "$PROGRESS_LOG"
}

# ========== 步骤计时 ==========

step_start() {
    local label="$1"
    STEP_LABEL="$label"
    _STEP_START_TIME=$(date +%s)
    log_info "开始..."
}

step_end() {
    local end_time
    end_time=$(date +%s)
    if [[ -n "$_STEP_START_TIME" ]]; then
        local elapsed=$(( end_time - _STEP_START_TIME ))
        log_info "完成，耗时 ${elapsed} 秒"
    else
        log_info "完成"
    fi
    STEP_LABEL=""
    _STEP_START_TIME=""
}

# ========== 通用工具 ==========

# 检查命令是否存在
check_command() {
    local cmd="$1"
    if ! command -v "$cmd" &>/dev/null; then
        log_error "命令 '$cmd' 不存在"
        return 1
    fi
}

# 检查文件是否存在
check_file() {
    local filepath="$1"
    local desc="${2:-$filepath}"
    if [[ ! -f "$filepath" ]]; then
        log_error "文件不存在: $desc ($filepath)"
        return 1
    fi
    log_detail "文件检查通过: $desc"
}

# 检查目录是否存在，不存在则创建
ensure_dir() {
    local dirpath="$1"
    if [[ ! -d "$dirpath" ]]; then
        mkdir -p "$dirpath"
        log_detail "创建目录: $dirpath"
    fi
}

# 安全读取 JSON 字段（使用 python3）
json_get() {
    local json_file="$1"
    local key="$2"
    python3 -c "
import json, sys
with open('$json_file') as f:
    data = json.load(f)
keys = '$key'.split('.')
val = data
for k in keys:
    val = val[k]
print(val)
" 2>/dev/null
}

# 对比两个浮点数，返回变化百分比
calc_change_pct() {
    local prev="$1"
    local curr="$2"
    python3 -c "
prev, curr = float('$prev'), float('$curr')
if prev == 0:
    print('0.0')
else:
    print(f'{((curr - prev) / prev) * 100:.1f}')
"
}

# 判断是否劣化（当前值大于前一值）
is_degraded() {
    local prev="$1"
    local curr="$2"
    python3 -c "
prev, curr = float('$prev'), float('$curr')
print('true' if curr > prev else 'false')
"
}

# 写入日志文件的最近N行上下文
log_tail_context() {
    local log_file="$1"
    local n="${2:-20}"
    local label="${3:-日志上下文}"
    log_info "$label (最近 ${n} 行):"
    if [[ -f "$log_file" ]]; then
        tail -n "$n" "$log_file" 2>/dev/null | while IFS= read -r line; do
            echo "  | $line" | tee -a "$PROGRESS_LOG"
        done
    else
        log_warn "日志文件不存在: $log_file"
    fi
}
