#!/usr/bin/env python3
"""env-relayout.py — 把 `.env` 遷到新版面（共用在前、per-host 在最後）。

## 為什麼需要這支

2026-10-02 之前 `.env.example`（以及由它衍生的 `.env`）依**設定類別**分節：
必填／必填含憑證／設定選填／憑證選填／未傳入容器／host 端／compose 寫死。
那個分法對「這台怎麼跑」有意義（哪些必填），但對**三台的檔案能不能一致**
沒有意義 —— 於是 per-host 的鍵散落全檔：

```
L44   HOST_ID              per-host
L63   POSTGRES_PASSWORD    per-host 機密
L82   HOST_API_URLS        per-host
L294  OLLAMA_URLS          per-host
L260  QDRANT_PEER_API_KEY   共用
```

要人眼掃過才知道哪個鍵該跟著共用值更新，而三台的長相又不一樣。

新版面：兩個大區塊 —— **共用（三台應該相同）** 在前、**HOST: <代號>**
在最後，各區塊內部仍保留原本的子類別（必填／選填…），因為那個資訊有用。

## 為什麼 per-host 用區段標題而不用前綴

`compose.yaml` 只認 `${VAR}` 插值，沒有「依 `HOST_ID` 動態選 `wsl_`／`mbp_`」
的能力。前綴若寫進 `.env`：

* `LLM_MODEL` → compose 插到空值 → 回退 `rag.py` 的原始碼預設，**沒有錯誤**
  （`settings/env/README.md §9` 記錄的實測）
* `TS_IP` → `ports: ["${TS_IP}:6333"]` 綁到錯的位址 → docker 直接啟動失敗

所以 per-host 的識別寫在**區段標題**（`# ══ HOST: wsl ══`），識別力與前綴
相同，而鍵名不變 → compose 的契約不動。三欄並排的 `x570_`／`mbp_`／`wsl_`
視圖在**被追蹤的總表** `settings/env/hosts.shared.env` 裡（那裡本來就有前綴），
另可用 `env-sync.sh --hosts-table` 印成對齊的表格。

## 這支做什麼

1. 備份 `.env`（值不會印）
2. 以新版 `.env.example` 為骨架，把舊 `.env` 的**值**填進對應的鍵
   —— 值的位置由**鍵名**決定，不由行號決定，所以版面改變不會對錯行
3. 清掉骨架裡那些「有預設值的空值」鍵（與 `env-prune.py` 同一目的，
   但這裡只針對搬遷過程中新增的空鍵）
4. 把區段標題的佔位換成本機 HOST_ID
5. 印出**版面指紋**（鍵名序列的 sha12，不含值）供跨機比對

## 安全

* 全程不印任何值，只印鍵名與指紋
* 備份檔 chmod 600，寫在 `.env` 旁邊（`.env.relayout.bak`）——
  ⚠️ 確認遷移成功後**請自行刪除**，它是整份 `.env` 的明文副本
* 帶 `--dry-run` 可先看會發生什麼，不寫檔

用法：
    python3 scripts/env-relayout.py [--dry-run] [--keep-backup]
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOTENV = ROOT / ".env"
EXAMPLE = ROOT / ".env.example"
BACKUP = ROOT / ".env.relayout.bak"

ASSIGN = re.compile(r"^([A-Za-z_][A-Za-z_0-9]*)=(.*)$")
SECRETISH = re.compile(r"(KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL|DSN)", re.I)


def read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        m = ASSIGN.match(line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def is_required(path: Path, key: str) -> bool:
    """骨架裡該鍵的註解塊有沒有「必填：」那行。

    判準是**「必填」而不是「有預設值」** —— 第一版用後者，結果
    `CF_AIG_GATEWAY_ID` 被判成可省略，而它其實是 `${VAR:?}`：留空會讓
    `docker compose config` 直接失敗。那個鍵的註解塊**同時**有
    `#   預設值：cloudflaregateway` 與 `#   必填：…` 兩行，所以「看有沒有
    預設值」會挑到錯的那個訊號。

    讀「該鍵正上方、連續的註解行」而不是掃前 N 行 —— 掃前 N 行會把
    隔壁鍵的說明吃進來。
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if not line.startswith(f"{key}="):
            continue
        block: list[str] = []
        for prev in reversed(lines[:i]):
            if not prev.startswith("#"):
                break
            block.append(prev)
        return any("必填：" in b for b in block)
    return False


def layout_fingerprint(text: str) -> str:
    """版面指紋：鍵名序列 ＋ 區段標題的 sha12。**不含任何值。**

    三台的指紋相同 = 結構一致（鍵集合、順序、區塊劃分都一樣）。
    這是唯一能在不做跨機 diff 的前提下驗證「三台格式一致」的方法 ——
    `.env` 有 8 份憑證，不能拿去做版本控制或貼進聊天。
    """
    seq = []
    for line in text.splitlines():
        m = ASSIGN.match(line)
        if m:
            seq.append(m.group(1))
        elif line.startswith("# ══ 共用") or line.startswith("# ══ HOST"):
            seq.append(line.strip())
    blob = "\n".join(seq)
    return f"keys={len(seq)} sha12={hashlib.sha256(blob.encode()).hexdigest()[:12]}"


def main() -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--dry-run", action="store_true",
                    help="只印會發生什麼，不寫檔")
    ap.add_argument("--keep-backup", action="store_true",
                    help="遷移後保留 .env.relayout.bak（預設保留，但會提醒你刪）")
    args = ap.parse_args()

    for p, what in ((DOTENV, ".env"), (EXAMPLE, ".env.example")):
        if not p.is_file():
            print(f"env-relayout: 找不到 {what}（{p}）", file=sys.stderr)
            print("  先跑 python3 scripts/env-audit.py --template > .env.example", file=sys.stderr)
            return 1

    old = read_env(DOTENV)
    if not old:
        print("env-relayout: .env 讀不到任何鍵 —— 中止（不寫入任何東西）", file=sys.stderr)
        return 1

    host = old.get("HOST_ID", "").strip()
    tpl_text = EXAMPLE.read_text(encoding="utf-8")
    tpl_keys = [m.group(1) for m in
                re.finditer(r"^([A-Za-z_][A-Za-z_0-9]*)=", tpl_text, re.M)]

    lost = [k for k in old if k not in tpl_keys]
    if lost:
        print("env-relayout: 中止 —— 舊 .env 有新版範本沒有的鍵，值會遺失：", file=sys.stderr)
        for k in lost:
            print(f"    {k}", file=sys.stderr)
        print("  這通常代表程式碼新增了讀取點而 .env.example 過期。", file=sys.stderr)
        print("  先跑 python3 scripts/env-audit.py --template > .env.example 再重試。",
              file=sys.stderr)
        return 1
    if not host:
        print("env-relayout: 中止 —— .env 沒有 HOST_ID，無法填區段標題。", file=sys.stderr)
        return 1

    out_lines: list[str] = []
    added, kept_val, kept_empty, omitted = 0, 0, 0, []
    for line in tpl_text.splitlines():
        m = ASSIGN.match(line)
        if not m:
            out_lines.append(line)
            continue
        key = m.group(1)
        if key in old:
            val = old[key]
            out_lines.append(f"{key}={val}" if val != "" else f"{key}=")
            if val != "":
                kept_val += 1
            else:
                kept_empty += 1
            continue
        # 舊 .env 沒有這個鍵。
        if not is_required(EXAMPLE, key):
            # 非必填 → 留空會吃預設值，存活。不新增（否則三台會因各自的
            # 預設而長相不同 —— 那正是本支要消除的形狀）。
            out_lines.append(f"{key}=")
            omitted.append(key)
            continue
        added += 1
        out_lines.append(f"{key}=")
    new_text = "\n".join(out_lines) + "\n"
    new_text = new_text.replace("# ══ HOST: <本機 HOST_ID> ══",
                                f"# ══ HOST: {host} ══")

    old_fp = layout_fingerprint(DOTENV.read_text(encoding="utf-8"))
    new_fp = layout_fingerprint(new_text)

    print("env-relayout 版面遷移")
    print(f"  舊 .env      {len(old)} 鍵   {old_fp}")
    print(f"  新 .env      {len(tpl_keys)} 鍵   {new_fp}")
    # ⚠️ 要分開報「有值」與「本來就空」。第一版只報有值的，於是舊 .env
    #    39 鍵卻印「沿用舊值的鍵 27」—— 看起來像掉了 12 個鍵的值，
    #    而那 12 個本來就是空的。遷移腳本報「看起來像遺失」是最糟的一種 bug。
    print(f"  沿用舊值      {kept_val} 鍵（值原封不動）")
    print(f"  沿用空值      {kept_empty} 鍵（本來就是空的）")
    print(f"  非必填而留空  {len(omitted)}（不新增，避免三台長相不同）")
    print(f"  新增的必填空鍵 {added}（舊 .env 沒有、且非必填以外的）")
    print(f"  合計          {kept_val + kept_empty + len(omitted) + added}"
          f" / 範本 {len(tpl_keys)} 鍵")
    if lost:
        print(f"  ⚠️ 會遺失 {len(lost)} 個鍵的值 —— 中止", file=sys.stderr)
        return 1

    if args.dry_run:
        print("\n  --dry-run：未寫入任何檔案。")
        return 0

    if BACKUP.exists():
        print(f"env-relayout: {BACKUP.name} 已存在 refusing 覆寫", file=sys.stderr)
        print("  先手動刪掉或改名它 —— 那是上一份 .env 的明文副本，不該留著被覆寫。",
              file=sys.stderr)
        return 1
    shutil.copy2(DOTENV, BACKUP)
    os.chmod(BACKUP, 0o600)
    print(f"  已備份 → {BACKUP.name}（600）")

    DOTENV.write_text(new_text, encoding="utf-8")
    os.chmod(DOTENV, 0o600)
    print(f"  已寫入 {DOTENV.name}（600）")

    print("\n  接著跑這兩步讓值回來（順序不能反）：")
    print("    bash scripts/env-sync.sh pull     # 共用層")
    print("    bash scripts/env-sync.sh render   # per-host 層（會填區段標題）")
    print(f"\n  版面指紋（回報這行，三台必須相同）：\n    {new_fp}")
    if not args.keep_backup:
        print(f"\n  ⚠️ 確認上面兩步都成功、且 {DOTENV.name} 的值正確後，"
              f"請手動刪除 {BACKUP.name}")
        print("     （那是整份 .env 的明文副本，留著是額外的曝露面 —— "
              "這條腳本不替你刪，因為刪檔是不可回復的動作）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
