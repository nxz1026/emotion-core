#!/usr/bin/env bash
# emotion-core 数据库全量备份（pg_dump -Fc）。
#
# 起因（2026-10-07）：上线前审计发现**全机零备份**——crontab / cron.d / cron.daily /
# ~/backups 全查过一处都没有，而库已 2326 MB 且日更每天写入。这条是唯一一条
# 「出故障救不回来」的风险。
#
# 装法（见 README 的运维小节）：
#   cp deploy/bin/backup-db.sh /home/ubuntu/bin/ && chmod 700 /home/ubuntu/bin/backup-db.sh
#   crontab 中：0 1 * * 0  /home/ubuntu/bin/backup-db.sh   # 每周日 01:00 UTC = 09:00 北京
#
# 为什么是 01:00 UTC 而不是深夜：机器时区是 Etc/UTC，01:00 UTC = 北京 09:00，
# 与日更链（09:20~10:05 UTC）无重叠；且周日本身是非交易日。
set -euo pipefail

BACKUP_DIR="${DB_BACKUP_DIR:-$HOME/backups}"
KEEP="${DB_BACKUP_KEEP:-4}"          # 保留最近 N 份（4 = 约一个月）
LOG="${DB_BACKUP_LOG:-$HOME/logs/db-backup.log}"
DB_NAME="emotion_core"
DBCONFIG="${DBCONFIG:-$HOME/.dbconfig}"

mkdir -p "$BACKUP_DIR" "$(dirname "$LOG")"
log() { printf '%s %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*" >> "$LOG"; }

trap 'log "FAILED (exit=$?) at line $LINENO"' ERR

# ---- 凭据：~/.dbconfig 是 `$KEY=value` 行，不是 shell 脚本，**不能 source**
# （db.py 的注释写明了：source 会执行文件内容，且 `$RDSHOST=x` 在 shell 里是语法错误）
# 这里用与 db.py 等价的 sed 抽取。
[ -r "$DBCONFIG" ] || { log "读不到 $DBCONFIG"; exit 1; }
RDSHOST=$(sed -n 's/^\$RDSHOST=//p' "$DBCONFIG" | head -1)
DB_PW=$(sed -n 's/^\$DB_PW=//p' "$DBCONFIG" | head -1)
[ -n "$RDSHOST" ] && [ -n "$DB_PW" ] || { log "$DBCONFIG 缺少 \$RDSHOST / \$DB_PW"; exit 1; }

STAMP=$(date -u '+%Y%m%d-%H%M%S')
OUT="$BACKUP_DIR/${DB_NAME}-${STAMP}.dump"

# -Fc 自定义格式：已压缩，且支持 pg_restore --list 先校验再还原
PGPASSWORD="$DB_PW" pg_dump -h "$RDSHOST" -U postgres -d "$DB_NAME" \
            --format=custom --compress=6 --file="$OUT"
chmod 600 "$OUT"

# 立刻校验产物可读（pg_restore --list 失败说明这份 dump 是坏的，留着等于没备份）
pg_restore --list "$OUT" >/dev/null || { log "dump 校验失败: $OUT"; exit 1; }

SIZE=$(du -h "$OUT" | cut -f1)
log "OK $OUT ($SIZE)"

# 轮转：只保留最近 KEEP 份
ls -1t "$BACKUP_DIR"/${DB_NAME}-*.dump 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r old; do
    rm -f "$old" && log "pruned $old"
done

log "done; keeping $(ls -1 "$BACKUP_DIR"/${DB_NAME}-*.dump 2>/dev/null | wc -l) file(s)"