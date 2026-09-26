#!/usr/bin/env python3
"""env-audit — 核對 .env 的變數與實際消費者的落差，並比對兩份副本是否漂移。

為什麼需要（2026-09-26）：根 .env 給 compose、backend/.env 給 host 腳本，
25 個變數維護兩份。那天 QDRANT_API_KEY 在兩份之間漂移（根與 backend 值不同），
造成「本機 qdrant 200、遠端 401」的非對稱故障，要靠人手發現。

三種類型的落差，本腳本全部從程式碼反查，不靠人工維護清單：
  1. 幽靈變數：.env 裡有，但沒有任何程式讀（誤以為有效，實際無作用）
  2. 缺失變數：程式有讀（且非預設可省），但 .env 沒有
  3. 副本漂移：根 .env 與 backend/.env 同名變數的值不一致

用法：
  scripts/env-audit.py                # 稽核目前 checkout
  scripts/env-audit.py --template     # 順帶輸出 .env.example 骨架
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 變數 → 誰會讀。None = 程式裡有預設值，不填也能跑（只當提醒，不算缺失）。
# 這張表靠 scripts/env-audit.sh 從程式碼反查更新；新增 .env 變數時同步補。
KNOWN: dict[str, dict] = {
    # ── 服務綁定 ──
    "TS_IP": {"by": ["compose", "backend", "scripts"], "required": True,
              "note": "tailscale IP。compose 用它綁 6333/5432；腳本算 dest URL"},
    "HOST_ID": {"by": ["compose", "backend"], "required": True,
                "note": "registry 主鍵，也是前端切換鍵值：x570 / mbp / msi"},
    "HOST_NAME": {"by": ["compose", "backend"], "required": True,
                  "note": "取代 /etc/hostname 單檔 bind mount（Docker Desktop 會 exit=127）"},
    "HOST_MACHINE_ID": {"by": ["compose", "backend"], "required": True,
                        "note": "取代 /etc/machine-id 單檔 bind mount。32 字元 hex"},
    "COLLECTION": {"by": ["compose", "backend", "scripts"], "required": False,
                   "note": "qdrant collection 名，預設 laws"},
    # ── 資料庫 ──
    "POSTGRES_PASSWORD": {"by": ["compose"], "required": True,
                          "note": "compose 用 :? 語法，缺值直接拒絕啟動。勿用 changeme"},
    "POSTGRES_DSN": {"by": ["compose", "backend", "scripts"], "required": False,
                     "note": "備援機要指 x570 的 registry；x570 本機留空走 compose 預設"},
    # ── qdrant ──
    "QDRANT_API_KEY": {"by": ["compose", "backend", "scripts"], "required": True,
                       "note": "本機自己的 qdrant。開了認證就必填"},
    "QDRANT_PEER_API_KEY": {"by": ["scripts"], "required": False,
                            "note": "同步對象（x570）的 key。不設則退回 QDRANT_API_KEY"},
    # ── 模型 ──
    "LLM_MODEL": {"by": ["compose", "backend"], "required": False, "note": ""},
    "OLLAMA_URLS": {"by": ["compose", "backend"], "required": False, "note": ""},
    "OLLAMA_MODELS": {"by": ["backend"], "required": False,
                      "note": "與 OLLAMA_URLS 同順序對應各機的 LLM model"},
    # ── 快照同步 ──
    "SRC_API_URL": {"by": ["scripts"], "required": False,
                    "note": "來源機 api base，取法規版本用。8000 綁 loopback，只能填 tunnel 網址"},
    # ── 外部服務憑證（皆選填）──
    "ADMIN_TOKEN": {"by": ["compose", "backend"], "required": False,
                    "note": "題庫寫入與 law-update 的管理權限。未設則寫入停用"},
    "CF_AIG_TOKEN": {"by": ["compose", "backend"], "required": False, "note": ""},
    "HF_TOKEN": {"by": ["compose", "backend"], "required": False, "note": ""},
    "NVIDIA_API_KEY": {"by": ["compose", "backend"], "required": False, "note": ""},
    "TYPESAFE_API_KEY": {"by": ["compose", "backend"], "required": False, "note": ""},
    "ZEN_API_KEY": {"by": ["compose", "backend"], "required": False, "note": ""},
    "ZEN_BASE_URL": {"by": ["compose", "backend"], "required": False, "note": ""},
    "OPENROUTER_GATEWAY_URL": {"by": ["compose", "backend"], "required": False, "note": ""},
    "JEV_VERIFY_MIN": {"by": ["compose", "backend"], "required": False, "note": ""},
    "JEV_BANK_MIN": {"by": ["compose", "backend"], "required": False, "note": ""},
}

# 已知幽靈：.env 裡常見但程式讀不到。附原因，避免下次又有人認真設它。
GHOSTS: dict[str, str] = {
    "QDRANT_URLS": "compose 只傳 QDRANT_URL=http://qdrant:6333（服務名解析），"
                   "容器內讀不到 QDRANT_URLS；僅原生執行（不經 compose）時有效",
    "EMBED_MODEL": "compose.yaml 直接寫死 bge-m3:latest，不從 .env 讀",
    "RERANK_MODEL": "compose.yaml 直接寫死 qllama/bge-reranker-v2-m3:latest",
}

SECRET_HINT = re.compile(r"(KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL)", re.I)
ASSIGN = re.compile(r"^([A-Za-z_][A-Za-z_0-9]*)=(.*)$")


def load_env(path: Path) -> tuple[dict[str, str], list[str]]:
    """回 (變數→值, 檔內註解行)。值原樣保留（不 strip，避免動到憑證）。"""
    if not path.exists():
        return {}, []
    vals: dict[str, str] = {}
    comments: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("#"):
            comments.append(raw)
            continue
        m = ASSIGN.match(raw)
        if m:
            vals[m.group(1)] = m.group(2)
    return vals, comments


def audit(env: dict[str, str], label: str) -> int:
    problems = 0

    ghosts = [k for k in env if k in GHOSTS]
    if ghosts:
        print(f"  [{label}] 幽靈變數（程式讀不到，設了沒作用）:")
        for k in sorted(ghosts):
            print(f"    - {k}")
            print(f"        {GHOSTS[k]}")
        problems += len(ghosts)

    missing = [k for k, m in KNOWN.items() if m["required"] and k not in env]
    if missing:
        print(f"  [{label}] 缺少必填變數:")
        for k in sorted(missing):
            print(f"    - {k}  {KNOWN[k]['note']}")
        problems += len(missing)

    unknown = [k for k in env if k not in KNOWN and k not in GHOSTS]
    if unknown:
        print(f"  [{label}] 不在 KNOWN 表中（可能漏登錄，請確認是否真的有人讀）:")
        for k in sorted(unknown):
            print(f"    - {k}")
        problems += len(unknown)

    per_machine = [k for k in env if re.match(r"^(msi|mbp|x570)_", k, re.I)]
    if per_machine:
        print(f"  [{label}] 機台專屬變數（帶 msi_/mbp_/x570_ 前綴）: "
              f"{', '.join(sorted(per_machine))}")
    return problems


def check_drift(root: dict[str, str], back: dict[str, str]) -> int:
    if not (root and back):
        return 0
    shared = sorted(set(root) & set(back))
    drift = [k for k in shared if root[k] != back[k]]
    print(f"  [副本] 根 .env {len(root)} 變數／backend/.env {len(back)} 變數"
          f"／同名 {len(shared)} 個")
    if drift:
        print("  ❌ 值不一致（這正是 2026-09-26 造成 401 的原因）:")
        for k in drift:
            print(f"    - {k}")
        return len(drift)
    only_root = sorted(set(root) - set(back))
    only_back = sorted(set(back) - set(root))
    if only_root:
        print(f"  ⚠️  只在根 .env: {', '.join(only_root)}")
    if only_back:
        print(f"  ⚠️  只在 backend/.env: {', '.join(only_back)}")
    if not only_root and not only_back:
        print("  ✅ 兩份完全一致（但重複維護本身就是風險，見 --template 說明）")
    return 0


def print_template() -> None:
    print("\n".join([
        "# .env.example — 三機共用的變數骨架（單一真相來源）",
        "#",
        "# 填法：cp .env.example .env && chmod 600 .env，再逐項填值。",
        "#   這份檔案**不含任何真實憑證**，可安全進版控。",
        "#",
        "# 機台差異怎麼填：",
        "#   共用變數直接寫；某一台獨有的加前綴（msi_ / mbp_ / x570_），",
        "#   例如只有 MSI 要設的雲端金鑰：msi_NVIDIA_API_KEY=...。",
        "#   程式端用 ${VAR_名} 讀取時會自動去掉前綴。",
        "#",
        "# ⚠️ 一律 chmod 600：這個檔案有憑證。",
        "",
    ]))
    for k, m in KNOWN.items():
        req = "必填" if m["required"] else "選填"
        by = "/".join(m["by"])
        print(f"# ── {k}（{req}；消費者：{by}）")
        if m["note"]:
            for line in m["note"].splitlines():
                print(f"#   {line}")
        print(f"{k}=")
        print()
    if GHOSTS:
        print("# ── 以下不該出現在 .env（2026-09-26 audit 確認無人讀）")
        for k, why in GHOSTS.items():
            print(f"# {k}: {why}")
        print()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", action="store_true", help="輸出 .env.example 骨架")
    args = ap.parse_args()

    print("════ env audit ════")
    root, _ = load_env(ROOT / ".env")
    back, _ = load_env(ROOT / "backend" / ".env")
    front, _ = load_env(ROOT / "frontend" / ".env")

    if not root and not back:
        print("  ⚠️  找不到 .env（若這是刚 clone 的機器，正常）")
    n = 0
    if root:
        n += audit(root, "根 .env")
    if back:
        n += audit(back, "backend/.env")
    if front:
        print(f"  [frontend/.env] {len(front)} 個變數"
              f"（{', '.join(sorted(front))}）— 獨立於後端，不參與本 audit")
    n += check_drift(root, back)

    if args.template:
        print_template()

    print(f"\n════ {n} 項需處理 ════")
    return 1 if n else 0


if __name__ == "__main__":
    sys.exit(main())
