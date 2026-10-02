#!/usr/bin/env python3
"""env-diff-hosts.py — 比對三台回報的 `.env` 欄位，擬出標準化提案。

## 這是三機往返的第二半

第一半在 `env-inventory.py --emit-column`：每台跑一次，印出自己的
`KEY=SET|EMPTY|ABSENT` 欄位（71 行、排序過、**不含任何值**），回報上來。
這支是第二半：吃三份欄位，**逐鍵比對**，並對每個不一致提出一個有理由的
標準化提案。

為什麼要拆成兩半而不是一個指令直接跨機比：`.env` 有 8 份憑證，它不能
離開那台機器；而「哪個鍵在該台有沒有值」是**不敏感的**，可以回報。

## 它產出什麼

1. **不一致清單** —— 分成四類，每類有不同的處置方向：

   | 類別 | 形狀 | 為什麼危險 |
   |---|---|---|
   | **憑證分歧** | 共用憑證在某台不是 SET | 那一台的 peer 探測／外部認證會失敗 |
   | **per-host 分歧** | per-host 鍵在某台缺 | `render` 挑不到列 → 吃預設值 |
   | **SET vs ABSENT** | 某台設了、某台沒這行 | 最難查：兩台的檔案長相不同但都不報錯 |
   | **EMPTY vs ABSENT** | 兩種寫法代表同一件事 | 純格式分歧，讓版面指紋對不上 |

2. **標準化提案** —— 每個鍵建議三台統一成什麼狀態，以及理由。

3. **可直接執行的規格** —— 一份 markdown 表格，`wsl`/`x570`/`mbp` 三欄，
   就是要達成「三台同規格」的目標狀態。

## 為什麼提案是「建議」而不是直接改檔

因為這個檔案**不在任何一台機器上執行** —— 它產生的是給人（與 wsl 上的
opencode）看的規格。實際套用是每台各自跑 `env-relayout.py`／手動調整。

## 用法

    python3 scripts/env-diff-hosts.py x570.txt mbp.txt          # 兩份
    cat x570.txt | python3 scripts/env-diff-hosts.py - mbp.txt  # 可用 stdin

本機的欄位若已存在於 `settings/env/host-inventory/<host>.txt` 會自動讀入；
沒有就提示用 `--emit-column` 產生。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COL_DIR = ROOT / "settings" / "env" / "host-inventory"
COL_RE = re.compile(r"^([A-Za-z_][A-Za-z_0-9]*)=(SET|EMPTY|ABSENT)\b")
STATUSES = ("SET", "EMPTY", "ABSENT")


def load_col(path: str) -> tuple[str, dict[str, str]]:
    """讀一份欄位。刻意**寬鬆**：只抓符合格式的行。

    寬鬆的理由：這份檔會經過聊天／issue／手機輸入，難免夾帶引言或格式變化。
    嚴格解析會讓人得先手工整理才敢貼 —— 而那個步驟幾乎沒人會做，結果就是
    這份往返卡住。抓不到就抓不到，比要求對齊格式好。
    """
    p = Path(path)
    if p.exists():
        text = p.read_text(encoding="utf-8")
        name = p.stem
    else:
        text = sys.stdin.read()
        # `-` 的預設名稱是 "stdin"，但那份內容其實是某台機器的欄位，
        # 而報告裡出現 "stdin" 會讓人搞不清哪台是誰。用檔名參數當名字。
        name = "貼上來的"
    col: dict[str, str] = {}
    for line in text.splitlines():
        m = COL_RE.match(line.strip().lstrip("#").strip())
        if m:
            col[m.group(1)] = m.group(2)
    return name, col


def load_registry():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "env_audit", ROOT / "scripts" / "env-audit.py")
    EA = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(EA)
    sync = (ROOT / "scripts" / "env-sync.sh").read_text(encoding="utf-8")

    def var(name: str) -> set[str]:
        m = re.search(rf'^{name}="([^"]+)"', sync, re.M)
        return set(m.group(1).split()) if m else set()

    return {
        "EA": EA,
        "reg": EA.build_registry(),
        "ph": EA.per_host_keys(),
        "shared": var("SHARED_SECRETS"),
        "per_host_secrets": var("PER_HOST_SECRETS"),
    }


def classify(key: str, ctx) -> str:
    reg, ph = ctx["reg"], ctx["ph"]
    if key in ctx["shared"]:
        return "共用憑證"
    if key in ctx["per_host_secrets"]:
        return "per-host 機密"
    if key in ph:
        return "per-host 設定"
    r = reg.get(key)
    if r is None:
        return "幽靈鍵"
    if r.compose_hardcoded and not r.compose_ref:
        return "compose 寫死（設了無效）"
    if not r.compose_ref and (r.python_read or r.shell_read) \
            and not r.host_only_reader():
        return "未傳入容器（設了無效）"
    if r.compose_default:
        return "有預設值"
    return "槽位（無預設值）"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*", help="各台的欄位檔；用 - 表示 stdin")
    ap.add_argument("--include-absent", action="store_true",
                    help="連 ABSENT 的鍵也列進提案（預設只列有分歧的）")
    args = ap.parse_args()

    ctx = load_registry()

    cols: dict[str, dict[str, str]] = {}
    for f in args.files:
        name, col = load_col(f)
        if not col:
            print(f"⚠️  {f}：抓不到任何 `KEY=SET|EMPTY|ABSENT` 行", file=sys.stderr)
            print("   請在那台跑 python3 scripts/env-inventory.py --emit-column",
                  file=sys.stderr)
            return 1
        cols[name] = col

    # 補上已存在於 host-inventory/ 的機台，並標出缺哪幾台
    missing: list[str] = []
    for h in ctx["EA"].declared_hosts():
        if h in cols:
            continue
        stored = COL_DIR / f"{h}.txt"
        if stored.is_file():
            cols[h] = {m.group(1): m.group(2)
                       for m in (COL_RE.match(l.strip()) for l in
                                 stored.read_text(encoding="utf-8").splitlines())
                       if m}
        else:
            missing.append(h)

    if missing:
        print(f"⚠️  還缺 {missing} 的欄位 —— 下面只比對已有的 "
              f"{list(cols)}。", file=sys.stderr)
        print(f"   在那些機器上跑：python3 scripts/env-inventory.py --emit-column "
              f"> {missing[0]}.txt", file=sys.stderr)

    if len(cols) < 2:
        print("至少要有兩台的欄位才能比對。", file=sys.stderr)
        return 1

    hosts = sorted(cols)
    keys = sorted(set().union(*[set(c) for c in cols.values()]))
    print(f"# 三機 .env 欄位比對（{len(hosts)} 台）\n")
    print("| 鍵 | 分類 | " + " | ".join(hosts) + " | 一致? |")
    print("|---|---|" + "---|" * (len(hosts) + 1))

    buckets: dict[str, list[tuple[str, str, list[str]]]] = {
        "憑證分歧": [], "per-host 分歧": [], "SET vs ABSENT": [],
        "EMPTY vs ABSENT": [], "全部一致": [],
    }
    for k in keys:
        cells = [cols[h].get(k, "ABSENT") for h in hosts]
        kind = classify(k, ctx)
        same = len(set(cells)) == 1
        row = (k, kind, cells)
        if same:
            buckets["全部一致"].append(row)
        else:
            s = set(cells)
            if kind == "共用憑證":
                buckets["憑證分歧"].append(row)
            elif kind.startswith("per-host"):
                buckets["per-host 分歧"].append(row)
            elif s <= {"SET", "ABSENT"} and "SET" in s:
                buckets["SET vs ABSENT"].append(row)
            elif s <= {"EMPTY", "ABSENT"}:
                buckets["EMPTY vs ABSENT"].append(row)
            else:
                buckets["SET vs ABSENT"].append(row)

    order = ("憑證分歧", "per-host 分歧", "SET vs ABSENT", "EMPTY vs ABSENT",
             "全部一致")
    labels = {
        "憑證分歧": "🔴 **共用憑證分歧** —— 必須立刻解決",
        "per-host 分歧": "🟠 **per-host 鍵分歧**",
        "SET vs ABSENT": "🟡 **某台有值、某台沒這行**",
        "EMPTY vs ABSENT": "🔵 **空值 vs 缺行**（同一件事的兩種寫法）",
        "全部一致": "✅ 一致",
    }
    for b in order:
        rows = buckets[b]
        if not rows:
            continue
        if b == "全部一致" and not args.include_absent:
            print(f"\n## {labels[b]}：{len(rows)} 鍵（不逐一列出，"
                  f"要全表加 --include-absent）")
            continue
        print(f"\n## {labels[b]}：{len(rows)} 鍵")
        print()
        print("| 鍵 | 分類 | " + " | ".join(hosts) + " |")
        print("|---|---|" + "---|" * len(hosts))
        for k, kind, cells in rows:
            print(f"| `{k}` | {kind} | " + " | ".join(cells) + " |")

    # ── 標準化提案 ──
    print("\n## 標準化提案")
    print()
    print("目標：**三台的 `.env` 有相同的鍵集合與排列**，只有值不同。")
    print("每個鍵的目標狀態由它的**角色**決定，不是由「現在誰有值」決定 ——")
    print("否則規格只是把現況固化，而現況可能就是問題本身。")
    print()
    print("| 鍵 | 分類 | " + " | ".join(hosts) + " | 提案 | 理由 |")
    print("|---|---|" + "---|" * (len(hosts) + 2))

    for k in keys:
        cells = [cols[h].get(k, "ABSENT") for h in hosts]
        kind = classify(k, ctx)
        if kind == "共用憑證":
            tgt, why = "全部 SET", "sops 分發，三台必須同值；缺一台就會有 peer 失敗"
        elif kind == "per-host 機密":
            tgt, why = "全部 SET", "只存在那一台，但每一台都需要自己那把"
        elif kind == "compose 寫死（設了無效）":
            tgt, why = "全部 ABSENT", "設了對容器無效，留著只會誤導「我改了怎麼沒生效」"
        elif kind == "未傳入容器（設了無效）":
            tgt, why = "全部 ABSENT", "compose 沒傳入，設了到不了；要傳得先改 compose"
        elif kind == "幽靈鍵":
            tgt, why = "全部刪除", "程式碼沒讀它 —— 那是殘骸或手加的"
        elif kind == "有預設值":
            tgt, why = "全部 ABSENT", "有預設值，留著的空行是純文件；刪了行為不變"
        elif kind == "per-host 設定":
            tgt, why = "依 render", "由 `env-sync.sh render` 依 HOST_ID 挑列；"
            tgt, why = tgt, why + "但**鍵必須存在**，否則 render 無處可寫"
        else:  # 槽位（無預設值）
            tgt = "視是否要用"
            why = "沒有預設值 —— 留著是給人填的槽位。若這台確定用不到就 ABSENT"
        print(f"| `{k}` | {kind} | " + " | ".join(cells)
              + f" | **{tgt}** | {why} |")

    print()
    print("---")
    print()
    print("### 套用到各台的步驟")
    print()
    print("1. 每台跑 `env-relayout.py`（以新版 `.env.example` 為骨架補齊鍵）")
    print("2. 再跑 `env-prune.py`（刪掉「空值＋有預設值」、註解掉「設了無效」）")
    print("3. 依上表手動調整**提案 ≠ 現況**的那幾列")
    print("4. `bash scripts/env-sync.sh pull && … render`")
    print("5. `bash scripts/env-sync.sh --check | head -1` —— **三台版面指紋必須相同**")
    print()
    print("⚠️ 第 5 步是唯一的驗收。三台的指紋不同 = 規格沒達成，")
    print("而那個數字不曝露任何值，可以直接回報。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
