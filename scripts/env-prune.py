#!/usr/bin/env python3
"""清理本機 `.env`：留下在用的，註解掉「設了也不生效」的。

## 為什麼需要這個工具

`.env` 是 `cp .env.example .env` 出來的骨架，所以它天生帶著幾十個**空值賦值**。
問題是空值賦值不等於「沒設」：

    os.getenv("KEY", default)   # 對 `KEY=` 回傳 ""，不是 default

所以那些 `KEY=` 不是雜訊，是**主動把鍵設成空字串**。實例：
`gateway.py` 的 `CF_AIG_TOKEN_FILE` 沒有 `or` 保護，寫成 `CF_AIG_TOKEN_FILE=`
會讓 token 檔 fallback 變成 `Path("")` → 例外 → 回 `""`，等於把它弄壞。

判準只有三條：

1. **空值 + compose 已有自帶預設** → 刪掉整行。預設值的單一真相是
   `compose.yaml`，在 `.env` 留一份空值當備忘，那份副本本身就是漂移的來源。
2. **設了也不生效**（compose 以字面值覆寫／根本沒傳入容器）→ 註解掉，並在旁邊
   寫明「要生效得改哪裡」。理由比設定更值得留，刪掉的話三個月後沒人知道它為什麼不在。
3. **空值本身就是決策**（`TS_IP`、密度門檻…）→ 留空，補一句理由。

## 兩種模式

- **預設（就地轉換）**：只動本檔列出的鍵，**其他鍵逐字保留、不重新排序**。
  這是給每一台機器用的預設行為 —— 因為別台可能有只有它才有的鍵，整份重建會
  靜默把它們丟掉。
- `--reorganize`：整份重建成 §1–§6 的標準版面。只在你想讓本機檔案跟 wsl 那台
  的 canonical 版面一致時用；會**丟掉不在保留清單裡的鍵**，所以先 `--dry-run` 看。

## 用法

    scripts/env-prune.py --dry-run      # 只印會動哪些鍵，不寫檔
    scripts/env-prune.py                # 就地清理（備份寫到 ~/.）
    scripts/env-prune.py --reorganize   # 順便重排成標準版面

備份放在 `~/.ragdemo-env.bak-<日期>`，**不放 repo 內** —— `.gitignore` 的 `.env`
是精確比對，`.env.bak` 不會被擋，`git add -A` 就會把全明文憑證 commit 進去。
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from datetime import date
from pathlib import Path

# ── 刪掉：空值 + compose 已有自帶預設（預設值在 compose.yaml 寫得更好）──────
# 必須是 dict：`key in DELETE` 對 list-of-tuple 永遠是 False，會靜默一個都不刪。
DELETE = {
    "CF_AIG_GATEWAY_ID": "compose.yaml 有預設 cloudflaregateway",
    "COHERE_GATEWAY_URL": "compose.yaml 預設為空＝不啟用 cohere",
    "COHERE_MODELS": "compose.yaml 有預設清單",
    "GEMINI_GATEWAY_URL": "compose.yaml 預設為空＝不啟用 gemini",
    "GEMINI_MODELS": "compose.yaml 有預設清單",
    "GROQ_GATEWAY_URL": "compose.yaml 預設為空＝不啟用 groq",
    "GROQ_MODELS": "compose.yaml 有預設清單",
    "HF_BASE_URL": "compose.yaml 有預設 https://router.huggingface.co/v1",
    "HF_MODELS": "compose.yaml 有預設清單",
    "MISTRAL_GATEWAY_URL": "compose.yaml 預設為空＝不啟用 mistral",
    "MISTRAL_MODELS": "compose.yaml 有預設清單",
    "NVIDIA_BASE_URL": "compose.yaml 有預設 https://integrate.api.nvidia.com/v1",
    "NVIDIA_MODELS": "compose.yaml 有預設清單",
    "OLLAMA_BASE_URL": "OLLAMA_URLS 有有效值時用不到（gateway.py 的低優先 fallback）",
    "OPENROUTER_MODELS": "compose.yaml 有預設清單",
    "TYPESAFE_URL": "compose.yaml 有預設 https://api.typesafe.ai/v1/systemone",
    "ZEN_FREE_MODELS": "compose.yaml 有預設清單",
}

# ── 註解掉：設定了也不生效 / 是幽靈鍵 ──────────────────────────────────
COMMENT = {
    "EMBED_MODEL": "compose 以字面值覆蓋（compose.yaml:87）→ 要改請改 compose，不是這裡。",
    "RERANK_MODEL": "compose 寫死（compose.yaml:88）**且** backend/app 沒有任何消費者\n"
                    "（幽靈分發鍵，見 settings/env/README.md §4）。",
    "POSTGRES_DB": "compose 寫死（compose.yaml:27）。",
    "POSTGRES_USER": "compose 寫死（compose.yaml:25）。",
    "QDRANT_URL": "compose 寫死（compose.yaml:39），固定 http://qdrant:6333。",
    "QDRANT_URLS": "compose 不傳這個變數；容器化後它對容器**完全無作用**\n"
                   "（gateway.py:74 讀不到 → 走 QDRANT_URL）。若本機曾填過 tailscale IP，\n"
                   "那是已過期值 —— 留著只會在原生執行時誤導。",
    "HOST_HOSTNAME_FILE": "compose 沒傳入容器 → 這裡設了到不了。registry.py 的 fallback\n"
                          "/run/secrets/host-hostname 在容器裡也不存在（compose 沒有那個\n"
                          "mount）。主機名請用 HOST_NAME（env 值優先於檔案）。",
    "HOST_MACHINE_ID_FILE": "同上。machine-id 請用 HOST_MACHINE_ID；兩者都拿不到時\n"
                            "registry.py 才退回 /etc/machine-id（那是弱識別，會告警）。",
    "CF_AIG_TOKEN_FILE": "⚠️ **不要寫空值**：gateway.py 沒有 or 保護，`CF_AIG_TOKEN_FILE=`\n"
                         "會讓 _gateway_token() 走 Path(\"\") → 例外 → 回 \"\"，等於把 token\n"
                         "檔 fallback 弄壞。要停用就整行刪掉（让它用\n"
                         "~/.config/opencode/cf-aig-token 的預設）。",
    "ZEN_API_KEY": "幽靈鍵：2026-09-30 移出共用層（enc 檔裡是空值、沒有任何機台設過）。\n"
                   "程式仍 os.getenv 它 → 拿不到就讓 _zen_complete 回 false，前端優雅降級。\n"
                   "要復活就重新申請 key，並同時加回 SHARED_SECRETS 與\n"
                   "secrets.common.env.example（tests 會鎖兩者同步）。",
    "HOST_API_LOCAL": "scripts 有預設（http://127.0.0.1:8000，host-doctor.sh:38／\n"
                      "host-sync.sh:47）。只有要走 tunnel 而不是 127.0.0.1 時才設。",
    "QDRANT": "只 host 端 ingest 讀（qdrant_load.py:30），預設 http://localhost:6333。",
}

# ── 保留空值，但補一句「空是決策」的理由 ─────────────────────────────
KEEP_EMPTY_NOTE = {
    "TS_IP": "空＝只綁 127.0.0.1。單機部署的預設，也是三台的現況；要讓別台機器\n"
             "連你的 qdrant/pg 才填 tailscale IP。絕不填 LAN_IP（IP 準則 2026-09-22，\n"
             "pre-push hook 與 CI 會擋）。",
    "RAG_MIN_DENSE": "空＝用 compose.yaml 校準過的 0.58（本機量測：命中 0.64–0.76／無關 0.43–0.57）。",
    "RAG_MID_DENSE": "空＝用 compose.yaml 校準過的 0.62。",
    "RAG_HIGH_DENSE": "空＝用 compose.yaml 校準過的 0.70。",
    "LIMIT": "空＝不限制。只 host 端 ingest 讀（qdrant_load.py 的\n"
             "`int(os.getenv(\"LIMIT\") or 0)` 容忍空字串）。",
}

KV = re.compile(r"^([A-Za-z_][A-Za-z_0-9]*)=(.*)$")
SECTION = re.compile(r"^#\s*[═━=]{3,}")


def _why(text: str) -> list[str]:
    """理由文字 → 註解行。續行在來源裡已帶 '# '，不要再疊一層。"""
    out = []
    for ln in text.splitlines():
        ln = ln[2:] if ln.startswith("# ") else ln.lstrip("#")
        out.append(f"# {ln}" if ln.strip() else "#")
    return out


def in_place(lines: list[str]) -> tuple[list[str], dict[str, str]]:
    """就地轉換：只動清單裡的鍵，其他鍵逐字保留、不重新排序。"""
    acted: dict[str, str] = {}
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        m = KV.match(line)
        if not m:
            out.append(line)
            i += 1
            continue

        key, val = m.group(1), m.group(2)
        # 這個鍵的說明＝它上方**連續**的註解行（碰到空行／章節標題／另一個鍵就停）
        start = len(out)
        while start > 0:
            prev = out[start - 1]
            if prev.strip() == "" or SECTION.match(prev) or KV.match(prev):
                break
            if not prev.lstrip().startswith("#"):
                break
            start -= 1
        block = out[start:] + [line]
        del out[start:]

        if val.strip() == "" and key in DELETE:
            acted[key] = f"刪（空值 + {DELETE[key]}）"
        elif key in COMMENT:
            reason = COMMENT[key]
            if not val.strip():
                acted[key] = "註解（設定了也不生效）"
            else:
                acted[key] = "註解（有值，但設定了也不生效 → 值一併棄用）"
            out += _why(reason) + [f"# {key}=", ""]
        elif key in KEEP_EMPTY_NOTE and val.strip() == "":
            # ⚠️ 一律**取代**原有的 .env.example 產生式註解，不沿用。那段文字是
            # env-audit.py --template 生成的，其中「必填…沒設會啟動失敗」對
            # `${TS_IP:-127.0.0.1}` 是錯的（ARCHITECTURE.md〈已知缺口〉有記）——
            # 沿用它等於把已知的錯誤敘述保留下來。
            acted[key] = "留空（補上理由，並取代生成的錯誤註解）"
            out += _why(KEEP_EMPTY_NOTE[key]) + [f"{key}="]
        else:
            out += block
        i += 1
    return out, acted


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("env", nargs="?", default=".env")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--reorganize", action="store_true",
                    help="整份重建成 §1–§6 版面（會丟掉不在保留清單裡的鍵）")
    args = ap.parse_args()

    path = Path(args.env)
    if not path.exists():
        print(f"✗ 找不到 {path}", file=sys.stderr)
        return 1

    raw = path.read_text(encoding="utf-8")
    if "# ═══ §1" in raw:
        print("! 這個檔已經是本工具的輸出格式了（§1–§6 版面）。不重複處理。")
        return 0

    lines = raw.splitlines()
    out, acted = in_place(lines)

    if args.reorganize:
        keep = [ln for ln in out if KV.match(ln)]
        print("! --reorganize 會丟掉不在保留清單裡的鍵。先人工確認上面的清單。")
        if args.dry_run:
            print(f"  會保留 {len(keep)} 行 KV")
            return 0
        out = keep

    print(f"{'（dry-run）' if args.dry_run else ''} {path}: {len(lines)} → {len(out)} 行")
    for k, why in acted.items():
        print(f"  {k:<26} {why}")

    if args.dry_run or args.reorganize:
        if args.dry_run:
            return 0

    bak = Path.home() / f".ragdemo-env.bak-{date.today().isoformat()}"
    n = 1
    while bak.exists():
        bak = Path.home() / f".ragdemo-env.bak-{date.today().isoformat()}-{n}"
        n += 1
    shutil.copy2(path, bak)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    print(f"✓ 已寫入。備份：{bak}（repo 外的 ~/. —— 不要放 repo 內，見 .gitignore 的說明）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())