#!/usr/bin/env bash
# emotion-core Wind 额度渐进拉取 —— 一条 cron 跑两条链。
#
# 起因（2026-10-15）：Wind 日额度加不了，用户拍板「慢慢拉，加个 cron 来做」。
# 两条链共用同一个日额度，所以必须**串行 + 定额**，且对账优先：
#   ① 逐日涨跌停对账（ref_limit_state / ref_limit_state_run）—— 健康检查 check_limit_state_audit 消费它
#   ② ST PIT 预算化回补（ref_security_status）—— 修涨停口径缺 ST ±5% 的前置参考数据
# 两条脚本都自带断点续跑：重复跑同一条命令 = 继续拉，已完成的 (日期,方向)/窗口自动跳过。
#
# 装法（详见 README 运维小节）：
#   cp deploy/bin/wind-pull-daily.sh /home/ubuntu/bin/ && chmod 755 /home/ubuntu/bin/wind-pull-daily.sh
#   cp deploy/cron/emotion-core-wind /etc/cron.d/emotion-core-wind   # 需 root
#   本机 /etc/cron.d 归 root，ubuntu 直写被拒；实际是走 docker + chroot 代装（2026-10-15 已装）：
#     docker run --rm -i --privileged --pid=host -v /:/host alpine:3.20 \
#       chroot /host sh -c 'cat > /etc/cron.d/emotion-core-wind && chown root:root \
#       /etc/cron.d/emotion-core-wind && chmod 644 /etc/cron.d/emotion-core-wind' \
#       < deploy/cron/emotion-core-wind
#
# 时间：12:30 UTC = 20:30 北京 —— **兜底**位。主执行体是两个 DSH 自动化（18:30 对账、
# 19:00 ST PIT）；它们先吃当天额度，本 cron 捡剩余（脚本自动跳过已完成部分）。
#
# 防重入：cron / DSH 自动化 / 手工跑撞车时后到者直接退出，不抢额度。
# 退出码恒为 0：撞额度是**预期**结果（当天额度用尽），不该让 cron 报错刷日志。
set -uo pipefail

# Wind 适配器靠 `node` 调 Wind 终端（wind_client.py 的 argv 首项硬编码就是 "node"，
# 没有可配置的路径），而 node 是本机 nvm 装的。**cron 的 PATH 里没有它**：
# 2026-10-16 那次 /etc/cron.d 跑出来的 40 次对账 + 17 个 ST 窗口**全是**
# `无法执行 node: No such file or directory` —— 一整天额度白烧，且脚本按设计恒退 0，
# 外面看不出任何异常（known-traps #51 同款静默）。这里自己把它补回 PATH。
if ! command -v node >/dev/null 2>&1; then
  _nvm_bin="$(ls -d "$HOME"/.nvm/versions/node/*/bin 2>/dev/null | sort -V | tail -1)"
  if [ -n "${_nvm_bin:-}" ]; then
    PATH="$_nvm_bin:$PATH"
    export PATH
  fi
fi

REPO="${EMOTION_REPO:-/home/ubuntu/DSH/emotion-core}"
PY="$REPO/.venv/bin/python"
LOG="${WIND_PULL_LOG:-$HOME/logs/wind-pull-daily.log}"
LOCK="${WIND_PULL_LOCK:-$HOME/logs/wind-pull-daily.lock}"   # 必须放共享目录：/tmp 在本机是分命名空间的（cron 在宿主 /tmp，DSH/手工跑在容器 /tmp），放 /tmp 等于没锁
AUDIT_BUDGET="${WIND_AUDIT_BUDGET:-40}"    # 对账优先，先吃这 40 次
STPIT_BUDGET="${WIND_STPIT_BUDGET:-20}"    # 剩下的给 ST PIT；额度没了它会自己停

mkdir -p "$(dirname "$LOG")"
log() { printf '%s %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*" >> "$LOG"; }

exec 9>"$LOCK"
if ! flock -n 9; then
  log "SKIP 已有实例在跑（$LOCK）"
  exit 0
fi

[ -x "$PY" ] || { log "FAILED 找不到解释器 $PY"; exit 1; }
cd "$REPO" || { log "FAILED 进不去 $REPO"; exit 1; }

log "===== 开始：对账预算 $AUDIT_BUDGET / ST PIT 预算 $STPIT_BUDGET ====="

log "START limit_state_audit"
PYTHONPATH=src DB_CONFIG="$HOME/.dbconfig" EMOTION_WIND_DAILY_CALL_BUDGET="$AUDIT_BUDGET" \
  timeout 2700 "$PY" scripts/backfill_limit_state_wind.py \
  --direction both --max-calls "$AUDIT_BUDGET" >>"$LOG" 2>&1
rc=$?
log "DONE limit_state_audit exit=$rc$([ "$rc" -ne 0 ] && echo '（撞额度/失败＝正常结束，次日续跑）')"

log "START st_pit"
PYTHONPATH=src DB_CONFIG="$HOME/.dbconfig" EMOTION_WIND_DAILY_CALL_BUDGET="$STPIT_BUDGET" \
  timeout 2700 "$PY" scripts/backfill_st_pit_wind.py \
  --max-calls "$STPIT_BUDGET" >>"$LOG" 2>&1
rc=$?
log "DONE st_pit exit=$rc$([ "$rc" -ne 0 ] && echo '（撞额度/失败＝正常结束，次日续跑）')"

log "===== 结束 ====="
exit 0
