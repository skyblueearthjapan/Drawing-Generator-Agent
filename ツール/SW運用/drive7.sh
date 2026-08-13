#!/bin/bash
# ランダム5点テスト v7(2026-08-12 深夜・22:21 OS再々起動後の再開)。
# v6(接種方式)との差分は1点のみ:
#   既にSWが起動していて接種確認に合格するなら、そのインスタンスを使い回す
#   (本セッションで 1-18 実測11.4sに成功済み=接種済みのはず。良品を捨てない)。
set -u
SP="C:/Users/imaizumi.LINEWORKS-NET/AppData/Local/Temp/claude/C--Users-imaizumi-LINEWORKS-NET-Documents------agent/7750bde9-ca51-4726-a273-81d40001103f/scratchpad/random5"
MY="C:/Users/imaizumi.LINEWORKS-NET/AppData/Local/Temp/claude/C--Users-imaizumi-LINEWORKS-NET-Documents------agent/5cfd678c-8fcf-4b3b-91d6-fa18cf44d7d0/scratchpad"
REPO="C:/Users/imaizumi.LINEWORKS-NET/Documents/3D CAD Operator Agent"
SWEXE="C:/Program Files/SOLIDWORKS Corp/SOLIDWORKS/SLDWORKS.exe"
export SOLIDIFY_DRAWING_ROOT="C:\\Users\\imaizumi.LINEWORKS-NET\\Documents\\部品図作成agent"
export OMC_WS_TIMEOUT_SCALE=0.5
export PYTHONIOENCODING=utf-8

log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$SP/drive.log"; }
sw_alive() { tasklist //FI "IMAGENAME eq SLDWORKS.exe" 2>/dev/null | grep -qi SLDWORKS; }

kill_sw() {
  powershell -NoProfile -Command "Stop-Process -Name SLDWORKS,sldworks_fs,sldBgDwld -Force -ErrorAction SilentlyContinue" >/dev/null 2>&1
  sleep 10
}

vaccinated_sw() {
  # 既存インスタンスがあれば、まず接種確認だけして使い回す(v7の追加点)
  if sw_alive; then
    out=$(timeout 90 python "$MY/warmup_import.py" 2>&1); rc=$?
    log "既存SWの接種確認: rc=$rc / $(echo "$out" | tr '\n' ' ')"
    if [ $rc -eq 0 ]; then
      log "既存インスタンスは接種済み → そのまま実ジョブを流します"
      return 0
    fi
    log "既存インスタンスが接種不合格 → 作り直しへ"
  fi
  for attempt in 1 2 3 4; do
    sw_alive && { log "SW を作り直します"; kill_sw; }
    powershell -NoProfile -Command "Start-Process -FilePath '$SWEXE'" >/dev/null 2>&1
    for _ in $(seq 1 30); do
      sleep 5
      sw_alive && break
    done
    sleep 25
    out=$(timeout 90 python "$MY/warmup_import.py" 2>&1); rc=$?
    log "接種 $attempt 回目: rc=$rc / $(echo "$out" | tr '\n' ' ')"
    if [ $rc -eq 0 ]; then
      log "接種成功 → このインスタンスに実ジョブを流します"
      return 0
    fi
    log "接種失敗(初回インポートのハング)→ SWをヘルパーごと作り直して再接種"
  done
  log "❗接種が4回とも失敗しました"
  return 1
}

for round in 1 2 3 4 5 6; do
  left=$(python "$SP/queued_count.py")
  log "=== v7 ラウンド $round / 残り queued=$left (接種方式・計測タイムボックス15分) ==="
  [ "$left" = "0" ] && [ "$round" != "1" ] && { log "キューが空になりました"; break; }
  vaccinated_sw || { log "❗SW を用意できませんでした"; break; }
  log "ワーカーを起こします(--max-jobs 1)"
  ( cd "$REPO" && python -m phase2.worker --real \
      --api http://127.0.0.1:8799 --data-dir "$SP/data" \
      --max-jobs 1 --exit-when-idle 60 -v >> "$SP/worker_run.log" 2>&1 )
  log "ワーカーが終わりました(rc=$?)"
  python "$SP/watch.py" >> "$SP/drive.log" 2>&1
done
log "=== drive7.sh 終了 ==="
