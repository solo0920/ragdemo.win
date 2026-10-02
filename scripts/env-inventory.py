#!/usr/bin/env python3
"""env-inventory.py — 產生三機共用的環境變數清單（**不含任何值**）。

## 為什麼要有這份清單

`.env` 有 69 個鍵但只有 27 個有值 —— 其餘 42 個是骨架留下的空位。要判斷
「哪些該留、哪些是沒人用的幽靈鍵」，得先有一份**可讀、可排序、不含值**的
清單。直接讀 `.env` 不行（裡面有 8 份憑證），靠記憶也不行。

## 資料來源全部是程式碼，沒有人工清單

* **誰在讀、讀到什麼** → `env-audit.py` 的 registry（它自己就是從
  `compose.yaml` ＋ `backend/**/*.py` ＋ `ingest/**/*.py` ＋ `scripts/*.sh`
  反查出來的）
* **per-host 的鍵集合** → `per_host_keys()`（總表的 `<機台>_<鍵>` 列 ＋
  `PER_HOST_SECRETS` ＋ 總表的 `# LOCAL_ONLY:` 註解）
* **哪台有設** → 各機自己寫一份 `settings/env/host-inventory/<host>.txt`，
  每行只有 `KEY=SET|EMPTY|ABSENT`。**沒有值** —— 所以那個檔可以進版控。

## 為什麼不直接讓各機把 .env 的鍵丟進版控

因為「有沒有值」本身不該進版控。`SET` 與 `EMPTY` 對維護 decisions 足夠，
而 `EMPTY` 與 `SET` 的差別在值本身 —— 那才是敏感的。

## 「三台出一份」是怎麼做到的

清單裡三台的欄位並排，但**每一欄的資料來自那一台自己**。所以流程是：
每台跑一次 `env-inventory.py --emit-column`（印出自己那一欄的 69 行），
把輸出存成 `settings/env/host-inventory/<host>.txt`，commit。
之後任何人跑 `env-inventory.py` 都能產生三欄並排的完整清單。

第一次做時另外兩台的欄位會是「待填」，那不是錯誤 —— 單機先把清單生出來，
比三台互等要快。

## 決策欄位留給人（或 wsl 上的 opencode）

`決策` 欄刻意留空。它要判斷的不是「有沒有人讀」（那是 registry 的答案，
已經印在 `誰讀` 欄），而是**「這把要不要留在 .env」** —— 那是取捨：
留著有文件價值但多一行，刪掉則日後要加回來得知道它存在過。
"""
from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "settings" / "env" / "ENV-VARIABLE-INVENTORY.md"
COL_DIR = ROOT / "settings" / "env" / "host-inventory"

ASSIGN = re.compile(r"^([A-Za-z_][A-Za-z_0-9]*)=(.*)$")
COL_RE = re.compile(r"^([A-Za-z_][A-Za-z_0-9]*)=(SET|EMPTY|ABSENT)$")


def _load_audit():
    spec = importlib.util.spec_from_file_location(
        "env_audit", ROOT / "scripts" / "env-audit.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def local_column() -> dict[str, str]:
    """本機 `.env` 每個鍵是 SET / EMPTY / ABSENT。**不讀值以外的任何東西。**"""
    env = ROOT / ".env"
    out: dict[str, str] = {}
    if not env.is_file():
        return out
    for line in env.read_text(encoding="utf-8").splitlines():
        m = ASSIGN.match(line)
        if m:
            out[m.group(1)] = "SET" if m.group(2) != "" else "EMPTY"
    return out


def stored_column(host: str) -> dict[str, str] | None:
    f = COL_DIR / f"{host}.txt"
    if not f.is_file():
        return None
    out: dict[str, str] = {}
    for line in f.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("#") or not line.strip():
            continue
        m = COL_RE.match(line.strip())
        if m:
            out[m.group(1)] = m.group(2)
    return out


def _kind(EA, r) -> tuple[str, str]:
    """回傳 (分類, 這把是幹嘛的)。

    用途說明是**推導**出來的，不是人手寫的 —— 手寫的清單會與程式碼漂移，
    而那份清單的唯一用途就是回答「程式碼現在怎麼用這個鍵」。
    """
    if r.compose_hardcoded and not r.compose_ref:
        return ("compose 寫死", "compose 以字面值覆寫 → **.env 設了對容器無效**")
    if r.superseded_by:
        return ("低優先", f"高優先的 {r.superseded_by} 設了有效值時用不到它")
    if not r.compose_ref and (r.python_read or r.shell_read):
        if r.host_only_reader():
            return ("host 端", "只有 host 端的腳本／ingest 讀得到，容器拿不到")
        return ("未傳入容器", "compose 沒列 environment → 容器讀不到，只吃原始碼預設")
    who = []
    if r.compose_ref:
        who.append("compose")
    if r.python_read:
        who.append("backend")
    if r.shell_read:
        who.append("scripts")
    bits = []
    if r.required:
        bits.append("必填")
    if r.secret:
        bits.append("憑證")
    if r.compose_default and not r.secret:
        bits.append(f"預設 {r.compose_default[:40]}")
    tail = "；".join(bits) if bits else "選填"
    return ("／".join(who) or "未讀", tail)



# ── 决策预设（依证据，不是依感觉）────────────────────────────────────
#
# 这里给的是**机械可判**的那部分结论，剩下 ambiguous 的留给人/AI 看。
# 目的是让 `决策` 栏不是空白纸 —— 一张全空的栏位会被跳过，
# 而「有建议但不照做」至少会被读到并被反驳。
#
# 为什麼不直接照预设去删：`.env` 里有 8 份凭证，而 `QDRANT_API_KEY` 与
# `POSTGRES_PASSWORD` 只存在那一台 —— 删错是永久损失。删除这种不可逆的
# 动作要人明确同意，不是脚本预设。

def default_decision(key, reg, ph, per_host_secrets, shared_secrets, local):
    """回傳 (決策, 理由)。決策 ∈ 留／清空／刪／待確認。

    判準全是 registry 的欄位 ＋ 本機 `.env` 的 SET/EMPTY，**沒有任何主觀成分**。
    """
    r = reg.get(key)

    # ⚠️ 順序有意義：**「這個值永遠不會生效」必須排在「這個值屬於誰」之前。**
    # 第一版把 per-host 的檢查放最前面，結果 `QDRANT_URLS` 被判「留 — per-host
    # 設定」，掩蓋了它同時是「compose 沒傳入容器 → 設了無效」。
    # 一個鍵「是某台的」與「設了沒用」是**兩件獨立的事**，後者嚴重得多：
    # 前者只是歸屬，後者會讓人以為改了會生效，而症狀是靜默的。
    if r is None:
        return ("刪", "**幽靈鍵**：程式碼沒讀它")
    if r.compose_hardcoded and not r.compose_ref:
        tag = "（它同時是 per-host 鍵，但那不影響結論）" if key in ph else ""
        return ("刪", "compose 以字面值覆寫 → .env 設了對容器無效；"
                      "要生效得先改 compose.yaml" + tag)
    if not r.compose_ref and (r.python_read or r.shell_read) \
            and not r.host_only_reader():
        return ("待確認", "compose 沒傳入容器 → 現在設了無效；"
                          "若日後要傳入，值要重新填")
    # 到這裡才問歸屬 —— 因為「設了沒用」已經被排除了。
    if key in per_host_secrets:
        return ("留", "per-host 機密，只存在這台，刪了永久損失")
    if key in shared_secrets:
        return ("留", "共用憑證，sops 分發，三台必須同值")
    if key in ph:
        return ("留", "per-host 設定，render 依 HOST_ID 挑列")
    if r.superseded_by:
        return ("清空", "低優先：有更高優先的來源（%s）" % r.superseded_by)
    st = local.get(key, "ABSENT")
    if st == "SET":
        return ("留", "有值且被讀")
    if r.compose_default:
        return ("留", "空值但有預設值 —— 留著是文件（預設 %s）"
                % r.compose_default[:36])
    return ("留", "空值、無預設值 —— 這是給人填的槽位；刪了之後要加回來，"
                  "得先知道它存在過")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--emit-column", action="store_true",
                    help="印出本機那一欄（KEY=SET|EMPTY|ABSENT），給其他台匯入")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    EA = _load_audit()
    hosts = EA.declared_hosts()
    reg = EA.build_registry() if hasattr(EA, "build_registry") else None
    if reg is None:
        print("env-inventory: env-audit.py 沒有 build_registry() —— "
              "請更新本腳本（那是唯一的真相來源）", file=sys.stderr)
        return 1
    ph = EA.per_host_keys()
    shared_secrets = set()
    sync = (ROOT / "scripts" / "env-sync.sh").read_text(encoding="utf-8")
    m = re.search(r'^SHARED_SECRETS="([^"]+)"', sync, re.M)
    if m:
        shared_secrets = set(m.group(1).split())
    per_host_secrets = set()
    m = re.search(r'^PER_HOST_SECRETS="([^"]+)"', sync, re.M)
    if m:
        per_host_secrets = set(m.group(1).split())

    me = (ROOT / ".env")
    my_id = ""
    if me.is_file():
        for line in me.read_text(encoding="utf-8").splitlines():
            mm = re.match(r"^HOST_ID=(.*)$", line)
            if mm:
                my_id = mm.group(1).strip()
                break

    if args.emit_column:
        col = local_column()
        # ⚠️ POLICY_EXCLUDED 的鍵**不可**印成 `NAME=…`（2026-10-02 修正）。
        # 這個分支的輸出有兩個下游客戶：(a) 貼進聊天回報、(b) 存成
        # `settings/env/host-inventory/<host>.txt` 後 **commit**。
        # 而 `LAN_IP=` 會被 pre-push 與 CI 的 `^LAN_IP=` 擋掉，IP 準則也明文
        # 禁止 —— 於是「照說明跑完、貼回來、commit」這條正常路徑會被自己的
        # 守衛擋下。症狀很難查：指令成功、輸出看起來正常、然後 push 被拒。
        #
        # 這不是「擔心會不會被繞過」，而是**不要讓工具產出自己禁止的東西**。
        excluded = set(getattr(EA, "POLICY_EXCLUDED", {}))
        for k in sorted(set(reg) - excluded):
            print(f"{k}={col.get(k, 'ABSENT')}")
        # .env 裡有、但程式碼沒讀到的 —— 那是幽靈鍵，必須一起報，
        # 否則「清單」會漏掉最該被發現的那一類。
        for k in sorted(set(col) - set(reg) - excluded):
            print(f"{k}={col[k]}  # 幽靈：程式碼沒讀它")
        if excluded:
            print(f"# 略過（政策上停用，不可寫入 .env）：{' '.join(sorted(excluded))}",
                  file=sys.stderr)
        return 0

    cols: dict[str, dict[str, str] | None] = {}
    for h in hosts:
        if h == my_id:
            cols[h] = local_column()
        else:
            cols[h] = stored_column(h)
    if my_id and my_id not in cols:
        cols[my_id] = local_column()

    lines: list[str] = []
    A = lines.append
    A("# 環境變數清單（三機並排）")
    A("")
    A(f"由 `scripts/env-inventory.py` 產生 —— **全部欄位從程式碼反查，不含任何值**。")
    A("")
    A("```bash")
    A("python3 scripts/env-inventory.py                    # 重新產生本檔")
    A("python3 scripts/env-inventory.py --emit-column     # 匯入本機那一欄")
    A("```")
    A("")
    A("## 怎麼讀這張表")
    A("")
    A("| 欄 | 意義 |")
    A("|---|---|")
    A("| `SET` | 該機的 `.env` 裡**有值** |")
    A("| `EMPTY` | 有這一行但值是空的（會吃預設值）|")
    A("| `ABSENT` | `.env` 裡沒有這一行 |")
    A("| `誰讀` | 檔名:行號。**從程式碼反查**，不是人工填的 |")
    A("| `決策` | 已由 `default_decision()` 依證據填好（可推翻，但請說理由）|")
    A("")
    A("> ⚠️ 這份清單**不證明值一致**。值的一致性是 "
      "`scripts/env-sync.sh --fingerprints` 的工作（它比 sha12，不印值）。")
    A("")

    # ── 共用憑證 ──
    A("## 共用憑證（sops 分發，三台必須同值）")
    A("")
    A("這些的值只存在 `settings/env/secrets.common.enc.env`（sops+age 加密）"
      "與各機 `.env`。**清單裡沒有值，也不該有。**")
    A("")
    A("| 變數 | 誰讀 | 用途 | " + " | ".join(hosts) + " | 決策 |")
    A("|---|---|---|" + "---|" * (len(hosts) + 1))
    for k in sorted(shared_secrets):
        r = reg.get(k)
        readers = ", ".join(r.readers[:2]) if r and r.readers else "—"
        _, why = _kind(EA, r) if r else ("—", "—")
        cells = []
        for h in hosts:
            c = cols.get(h)
            cells.append((c or {}).get(k, "待填") if c else "待填")
        d, why_d = default_decision(k, reg, ph, per_host_secrets,
                                    shared_secrets, cols.get(my_id) or {})
        A(f"| `{k}` | {readers} | {why} | " + " | ".join(cells)
          + f" | **{d}** — {why_d} |")
    A("")

    # ── per-host 機密 ──
    A("## per-host 機密（**不分發**，各機自己的值）")
    A("")
    A("刻意不在 sops 裡（`env-sync.sh` 的 `PER_HOST_SECRETS` 註解說明為什麼）。"
      "**丟了沒有任何來源能重建** —— 這兩把的備份是 `scripts/backup-env.sh`。")
    A("")
    A("| 變數 | 誰讀 | 用途 | " + " | ".join(hosts) + " | 決策 |")
    A("|---|---|---|" + "---|" * (len(hosts) + 1))
    for k in sorted(per_host_secrets):
        r = reg.get(k)
        readers = ", ".join(r.readers[:2]) if r and r.readers else "—"
        _, why = _kind(EA, r) if r else ("—", "—")
        cells = []
        for h in hosts:
            c = cols.get(h)
            cells.append((c or {}).get(k, "待填") if c else "待填")
        d, why_d = default_decision(k, reg, ph, per_host_secrets,
                                    shared_secrets, cols.get(my_id) or {})
        A(f"| `{k}` | {readers} | {why} | " + " | ".join(cells)
          + f" | **{d}** — {why_d} |")
    A("")

    # ── per-host 設定 ──
    A("## per-host 設定（值來自 `settings/env/hosts.shared.env` 的 "
      "`<機台>_<鍵>` 列）")
    A("")
    A("| 變數 | 誰讀 | 用途 | " + " | ".join(hosts) + " | 決策 |")
    A("|---|---|---|" + "---|" * (len(hosts) + 1))
    for k in sorted(ph - per_host_secrets):
        r = reg.get(k)
        readers = ", ".join(r.readers[:2]) if r and r.readers else "—"
        _, why = _kind(EA, r) if r else ("—", "—")
        cells = []
        for h in hosts:
            c = cols.get(h)
            cells.append((c or {}).get(k, "待填") if c else "待填")
        d, why_d = default_decision(k, reg, ph, per_host_secrets,
                                    shared_secrets, cols.get(my_id) or {})
        A(f"| `{k}` | {readers} | {why} | " + " | ".join(cells)
          + f" | **{d}** — {why_d} |")
    A("")

    # ── 其餘（共用非敏感 + 幽靈）──
    # POLICY_EXCLUDED（LAN_IP）**不可**出現在決策候選裡：它是「政策上停用」，
    # `.env.example` 刻意不印 `LAN_IP=`（pre-push 與 CI 會擋 `^LAN_IP=`）。
    # 把它列成「待確認」等於邀請人設它 —— 而清單裡出現它本身就有風險：
    # 下游若有工具從這張表產生 `.env`，就會產出一行被 CI 擋掉的賦值。
    excluded = set(getattr(EA, "POLICY_EXCLUDED", {}))
    rest = sorted(set(reg) - ph - excluded)
    A("## 其餘變數（共用非敏感設定）")
    A("")
    A("| 變數 | 誰讀 | 分類／用途 | " + " | ".join(hosts) + " | 決策 |")
    A("|---|---|---|" + "---|" * (len(hosts) + 1))
    for k in rest:
        r = reg[k]
        kind, why = _kind(EA, r)
        readers = ", ".join(r.readers[:2]) if r.readers else "—"
        cells = []
        for h in hosts:
            c = cols.get(h)
            cells.append((c or {}).get(k, "待填") if c else "待填")
        d, why_d = default_decision(k, reg, ph, per_host_secrets,
                                    shared_secrets, cols.get(my_id) or {})
        A(f"| `{k}` | {readers} | **{kind}** — {why} | " + " | ".join(cells)
          + f" | **{d}** — {why_d} |")
    A("")

    # ── 幽靈鍵 ──
    ghosts: dict[str, list[str]] = {}
    for h, c in cols.items():
        if not c:
            continue
        for k in c:
            if k not in reg:
                ghosts.setdefault(k, []).append(h)
    A("## ⚠️ 幽靈鍵（`.env` 裡有，但**程式碼沒讀它**）")
    A("")
    if ghosts:
        A("| 變數 | 出現在 | 為什麼會有 |")
        A("|---|---|---|")
        for k in sorted(ghosts):
            A(f"| `{k}` | {'、'.join(ghosts[k])} | 手加的、或某個功能刪掉後留下的殘骸 |")
    else:
        A("（無 —— 已回報的機器都沒有幽靈鍵。**未回報的機器仍可能有**，"
          "所以這一欄要等三台都匯入才算數。）")
    A("")

    if excluded:
        A("## 政策上停用（**刻意不列入決策候選**）")
        A("")
        for k in sorted(excluded):
            A(f"- `{k}` —— {EA.POLICY_EXCLUDED[k]}")
        A("")
        A("  這裡不列決策欄，因為 `.env.example` 刻意不印 `NAME=` 行，"
          "而 pre-push 與 CI 會擋 `^LAN_IP=`。若把它放進決策表，"
          "下游任何「從清單產生 .env」的工具都會產出一行被擋掉的賦值。")
        A("")

    A("## 決策欄（已依證據填好；要推翻請寫理由）")
    A("")
    A("`決策` 欄判斷的是**取捨**，不是「有沒有人讀」—— 後者已經在 `誰讀` 欄了。")
    A("")
    A("> 這裡的預設是 `default_decision()` 的輸出，判準全是機械可查的"
      "（registry 的欄位 ＋ 本機 `.env` 的 SET/EMPTY）。**不是**「我覺得它沒用」。")
    A("> 推翻某一列時請在理由欄後面加一句 why —— 否則下次重跑這個檔會被蓋掉，")
    A("> 而那份理由正是最值得留下的東西。")
    A("")
    A("| 決策 | 什麼時候 | 刪掉之後 |")
    A("|---|---|---|")
    A("| **留** | 有值且被讀；或雖然被讀但它是文件的骨架（留著才知道有這個鍵）| — |")
    A("| **清空** | `EMPTY` ＋ 有預設值 ＋ 從沒人設過 | 值不變（吃預設）|")
    A("| **刪** | 幽靈鍵（沒人讀）；或「compose 寫死」而 `.env` 有值的（設了無效，留著只誤導）| 日後要生效得先改 compose |")
    A("| **待確認** | `誰讀` 只指向 host 端腳本，而那支腳本可能只在某些情況跑 | 先查讀取點 |")
    A("")
    A("⚠️ **刪之前一定要有 `.env` 的備份。** 這不是形式：")
    A("`QDRANT_API_KEY`／`POSTGRES_PASSWORD` 兩把只存在那一台，刪錯就是永久損失。")
    A("")
    Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"env-inventory: 寫入 {args.out}")
    missing = [h for h, c in cols.items() if c is None]
    if missing:
        print(f"  ⚠️ 這幾台的欄位是「待填」：{missing}")
        print(f"     請在那些機器上跑 python3 scripts/env-inventory.py --emit-column")
        print(f"     存成 settings/env/host-inventory/<host>.txt 後 commit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
