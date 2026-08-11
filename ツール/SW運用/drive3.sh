#!/bin/bash
# 残り4点(3-13 / 5-22 / 4-01 / 5-17)を1件ずつ流す(v3・ディレクター指示)。
#
# v3 の要点:
#   - **タイムボックス**: OMC_WS_TIMEOUT_SCALE=0.5 で TIMEOUT_MEASURE を 1800→900秒(15分)に。
#     ハングした1件で全滅させない(ディレクター指示4)。
#     他の段も半分になるが、実測は 候補投影20〜40秒 / 生成1〜3秒 なので余裕がある。
#   - **1-106(J20260811-001)はスキップ**。failed のままなので claim されない(指示2)。
#   - 部品と部品のあいだで SolidWorks を落として起こし直す。取り込みハングを kill した後の
#     SW は LoadFile4 が None(error=1) を返す壊れ方をする実測があるため、健康診断ではなく
#     **毎回作り直す**のが確実。
set -u
SP="C:/Users/imaizumi.LINEWORKS-NET/AppData/Local/Temp/claude/C--Users-imaizumi-LINEWORKS-NET-Documents------agent/ce37c11f-9bbf-48d0-9e22-7064c8a5bb40/scratchpad/random5"
REPO="C:/Users/imaizumi.LINEWORKS-NET/Documents/3D CAD Operator Agent"
SWEXE="C:/Program Files/SOLIDWORKS Corp/SOLIDWORKS/SLDWORKS.exe"
export SOLIDIFY_DRAWING_ROOT="C:\\Users\\imaizumi.LINEWORKS-NET\\Documents\\部品図作成agent"
export OMC_WS_TIMEOUT_SCALE=0.5
export PYTHONIOENCODING=utf-8

log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$SP/drive.log"; }
sw_alive() { tasklist //FI "IMAGENAME eq SLDWORKS.exe" 2>/dev/null | grep -qi SLDWORKS; }

fresh_sw() {
  if sw_alive; then
    log "SolidWorks を落とします(取り込みハングの後始末を残さないため)"
    powershell -NoProfile -Command "Stop-Process -Name SLDWORKS -Force -ErrorAction SilentlyContinue" >/dev/null 2>&1
    sleep 12
  fi
  powershell -NoProfile -Command "Start-Process -FilePath '$SWEXE'" >/dev/null 2>&1
  for _ in $(seq 1 30); do
    sleep 5
    sw_alive && break
  done
  sleep 25
  out=$(python "$SP/sw_health.py" 2>&1); rc=$?
  log "SW健康診断: $(echo "$out" | tr '\n' ' / ')"
  return $rc
}

for round in 1 2 3 4 5 6; do
  left=$(python "$SP/queued_count.py")
  log "=== v3 ラウンド $round / 残り queued=$left (計測タイムボックス15分) ==="
  [ "$left" = "0" ] && { log "キューが空になりました"; break; }
  fresh_sw || { log "❗SW を用意できませんでした"; break; }
  log "ワーカーを起こします(--max-jobs 1)"
  ( cd "$REPO" && python -m phase2.worker --real \
      --api http://127.0.0.1:8799 --data-dir "$SP/data" \
      --max-jobs 1 --exit-when-idle 60 -v >> "$SP/worker_run.log" 2>&1 )
  log "ワーカーが終わりました(rc=$?)"
  python "$SP/watch.py" >> "$SP/drive.log" 2>&1
done
log "=== drive3.sh 終了 ==="
