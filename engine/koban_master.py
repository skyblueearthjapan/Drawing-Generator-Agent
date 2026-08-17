# -*- coding: utf-8 -*-
u"""工番マスター取得(会社GAS doPost API 経由)。

工番 → 納入先(nohinSaki) / 製品名(kishu) を引く。表題欄の自動記入用
(仕様の正: 工番マスター連携_実装手順書.md。実装元は Work Report/src/Master.js)。

- 外部依存なし(urllib のみ)。requirements の変更は不要
- ❗`refreshMaster` は元シートの再取り込みを伴う重い処理(数秒〜十数秒・書き込みあり)。
  **図面1枚ごとに呼んではいけない**。必ずキャッシュ(TTL 12h。元データは日次6:07更新)
- ❗トークンは HTTP ヘッダでなく JSON body に入れる(GAS の doPost はヘッダを読めない)
- ❗失敗時も HTTP 200 で返る。判定は必ず `ok` フィールドで行う
- ❗GAS は稀に警告 HTML を返す(2026-08-12/13 に実測)。リトライ+古いキャッシュで延命する
- 設定は環境変数 GAS_API_URL / GAS_API_TOKEN。無ければリポジトリ直下の `.env` を読む
  (.env はコミット禁止・.gitignore 済み)
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent

CACHE_PATH = _REPO_ROOT / "data" / "koban_master.json"
CACHE_TTL_SEC = 12 * 60 * 60  # 12時間(マスターは日次6:07更新)
TIMEOUT_SEC = 45              # GASは遅い

#: 送信先を /exec に限定(トークンの誤送信防止)
_URL_RE = re.compile(r"^https://script\.google\.com/macros/s/[\w-]+/exec$")


def _load_env_file() -> dict:
    u"""リポジトリ直下の .env を読む(環境変数が未設定のときのフォールバック)。"""
    env_path = _REPO_ROOT / ".env"
    out: dict[str, str] = {}
    try:
        for line in env_path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    except OSError:
        pass
    return out


def _config() -> tuple[str, str]:
    url = os.environ.get("GAS_API_URL", "")
    token = os.environ.get("GAS_API_TOKEN", "")
    if not url or not token:
        env = _load_env_file()
        url = url or env.get("GAS_API_URL", "")
        token = token or env.get("GAS_API_TOKEN", "")
    return url, token


class MasterError(RuntimeError):
    pass


def gas_call(action: str, params: dict | None = None, retries: int = 2) -> Any:
    url, token = _config()
    if not url or not token:
        raise MasterError(u"GAS_API_URL / GAS_API_TOKEN が未設定です(.env も確認)")
    if not _URL_RE.match(url):
        raise MasterError(u"GAS_API_URL が不正です(/exec のみ許可)")

    body = json.dumps(
        {"action": action, "token": token, "params": params or {}}
    ).encode("utf-8")

    last_err: Exception | None = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            # urllib は 302 を自動追従し GET に切り替える(GASの想定どおり)
            with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as res:
                text = res.read().decode("utf-8", errors="replace")
            try:
                j = json.loads(text)
            except json.JSONDecodeError:
                # GASが稀にGoogleの警告HTMLを返す。リトライ対象(手順書§4-3)
                raise MasterError(u"GAS応答が不正(HTMLが返った): %s" % text[:120])
            if not j.get("ok"):
                raise MasterError(j.get("error") or u"GASエラー")
            return j.get("result")
        except Exception as e:  # noqa: BLE001 - リトライ対象を広く取る
            last_err = e
            if attempt >= retries:
                break
            time.sleep(2 * (attempt + 1))  # 2s, 4s
    raise MasterError(u"マスター取得に失敗しました: %s" % last_err)


def load_master(force: bool = False) -> dict:
    u"""マスターを取得(キャッシュ優先)。force=True で強制再取得。

    取得に失敗しても**期限切れキャッシュが残っていればそれで動き続ける**
    (手順書§4-3。バッチ生成を全滅させない)。
    """
    if not force and CACHE_PATH.exists():
        age = time.time() - CACHE_PATH.stat().st_mtime
        if age < CACHE_TTL_SEC:
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))

    try:
        m = gas_call("refreshMaster", {})
    except MasterError:
        if CACHE_PATH.exists():          # 古いキャッシュで延命
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        raise
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
    return m


def build_index(master: dict | None = None) -> dict:
    u"""工番→行 の辞書。**先頭勝ち**(作業報告書アプリ本体 masterKoban() と同じ挙動。
    辞書内包表記の後勝ちにすると本体と食い違う — 手順書§4-4)。"""
    master = master or load_master()
    index: dict[str, dict] = {}
    for k in master.get("kobans", []):
        key = str(k.get("koban", "")).strip().upper()
        if key and key not in index:
            index[key] = k
    return index


def find_koban(koban: str) -> dict | None:
    u"""工番 → {koban, uketsuke, nohinSaki, basho, kishu} / 見つからなければ None。"""
    key = str(koban or "").strip().upper()
    if not key:
        return None
    for k in load_master().get("kobans", []):
        if str(k.get("koban", "")).strip().upper() == key:
            return k  # 先頭一致を採用(手順書§4-4)
    return None


def title_block(koban: str) -> dict:
    u"""表題欄用の3項目。見つからなくても空文字で必ず返す(描画を止めない)。"""
    try:
        hit = find_koban(koban) or {}
    except MasterError:
        hit = {}
    return {
        "koban": hit.get("koban", str(koban or "")),
        "nohinSaki": hit.get("nohinSaki", ""),
        "kishu": hit.get("kishu", ""),
    }
