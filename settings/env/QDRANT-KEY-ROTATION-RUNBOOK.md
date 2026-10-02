# `QDRANT_*_API_KEY` 輪換 runbook（2026-10-03）

**為什麼需要這份**：2026-10-03 `wsl` 的 `QDRANT_API_KEY` 因為一次遮蔽失效而被印進
對話（見該次 commit 訊息）。要吊銷它，但 `settings/env/README.md §7` 已經記錄了
一個**同值是結構性必然**的耦合 —— 而且記錄裡明確寫著「順序錯了就是兩台備援機
永久 401」。

**所以這份 runbook 的重點不是「怎麼換」，是「換的順序，以及哪一步之後不能回頭」。**

> ⚠️ **執行者必須逐項查證後回報，不要先假設原因。** 每一步都有驗收點；
> **驗收不過就停在那一節**，不要往下走。

---

## 0. 現況（2026-10-03 實測，值一律不顯示）

`compose.yaml` **已經**接好第二個槽（這是 M2 的工作，已完成）：

```
compose.yaml:15  QDRANT__SERVICE__API_KEY:     ${QDRANT_API_KEY:-}          # 自己那台
compose.yaml:30  QDRANT__SERVICE__ALT_API_KEY: ${QDRANT_PEER_API_KEY:-}     # 跨機
```

wsl 實測指紋：

```
QDRANT_API_KEY       len=43  sha12=fe4b2d4ba82a
QDRANT_PEER_API_KEY  len=43  sha12=fe4b2d4ba82a      ← 同值
```

`QDRANT_PEER_API_KEY` 在 **`secrets.common.enc.env`**（sops 共用層，`SHARED_SECRETS`
第一名）；`QDRANT_API_KEY` **不在** sops、也不在版控總表 → **per-host**，各自 `.env`。

### 值對照表

| 記號 | 意義 |
|---|---|
| **V** | **外洩的值**（= 目前的共用 peer key ＝ wsl 自己的 ＝ x570 自己的） |
| **N** | 新的共用 peer key（輪換後） |
| **X** | x570 新的自己的 key |
| **Y** | wsl 新的自己的 key |
| **M** | mbp 自己的 key（**不動** —— 它已經是獨立的） |

### 為什麼外洩的是**兩台**

`QDRANT_PEER_API_KEY` 的定義是「能認證到**來源機** qdrant 的那把 key」。wsl 的
peer 拿來打的是 `100.119.83.111`（x570），而它與 wsl 自己的 key 同值 ⟹
**x570 的 `QDRANT_API_KEY` == wsl 的 == V**。

mbp 的 peer 與自己**不同值**，正好交叉印證：mbp 的 peer 才是 x570 的 key。

### 現在的槽位

| 機器 | 槽 1 `QDRANT_API_KEY` | 槽 2 ALT `QDRANT_PEER_API_KEY` | 連 x570 用哪把 |
|---|---|---|---|
| x570（來源） | **V** | **V** | — |
| mbp | M | **V** | V ✓ |
| wsl | **V** | **V** | V ✓ |

### 目標

| 機器 | 槽 1 | 槽 2 ALT | 連 x570 用哪把 |
|---|---|---|---|
| x570 | **X** | **N** | — |
| mbp | M | **N** | N ✓ |
| wsl | **Y** | **N** | N ✓ |

完成後 **V 在三台都不再被接受**。

---

## 1. 順序約束（**先讀這節再動手**）

qdrant 的 `can_write = read_write || alt_read_write`（上游 `src/common/auth/mod.rs`）。
所以：

* **x570 重啟後會同時接受槽 1 與槽 2** → 這就是為什麼要**先**讓槽 2 拿到 N，
  **再**抽掉槽 1 的 V。這段期間 x570 兩把都收，是**刻意**的過渡狀態。
* **反過來做就是永久 401**：先抽掉 V、再給 N，peers 手上是 N 而 x570 兩槽都不認。

### 🚨 不可回頭點

| 步驟 | 之後還能不能回滾到 V |
|---|---|
| S1–S4 | **可以** —— V 還在 sops 以外至少一台機器的 `.env` 裡（見 §4） |
| **S5** | **不可以** —— x570 不再接受 V |

**所以 S5 只能在 S4 驗收全過之後做。**

---

## 2. 步驟

### S0. 三台基線（兩台分開跑）

```bash
git pull
bash scripts/host-doctor.sh 2>&1 | grep -E 'qdrant|rotate|registry'
bash scripts/env-sync.sh --fingerprints | grep -i qdrant
```

**回報**：三台各自的 `qdrant-api-key` / `qdrant-alt-key` 的 `len=` 與 `sha12=`，
以及 `rotate-hint` 那條。

⚠️ **不要**把任何值貼回來。若 `sha12` 在三台之間**不一致**，**停下來回報** ——
那代表實際狀態與本文件的不同，後面的順序推論全部不成立。

### S0b. 確認來源機是 x570（在 **mbp 與 wsl** 上各跑一次）

```bash
grep -E '^LAW_SYNC_SOURCE=' .env | sed -E 's#(https?://)[^:]*:[^@]*@#\1<HOST>:<PORT>@#'
```

**回報**：主機名（不要貼帳密）。若任一台的來源**不是 x570**，**停下來回報** ——
本 runbook 的槽位模型是照「只有 x570 是來源」寫的。

---

### S1. 在 **wsl** 產生 N 並寫進 sops（**先不要 pull 到別台**）

```bash
read -rs -p '新的 peer key（43 字符，勿貼到聊天）: ' N; echo
printf '%s' "$N" | bash scripts/rotate-secret.sh QDRANT_PEER_API_KEY --from-stdin
unset N
```

**在 wsl 這台自己驗收**（不 pull、不重啟）：

```bash
bash scripts/env-sync.sh --fingerprints | grep -i qdrant
```

`sha12` 應該**已經變了**（因為 sops 檔案改了），而 `.env` 裡的**還是舊的 V** ——
**這個不一致是刻意的**，它是 S4 的回滾保險。**不要**去 pull 把它蓋掉。

### S2. 在 **x570** 手動把 N 放進 `.env`，然後重啟 qdrant

⚠️ 這一步**不能**用 `pull`（會同時覆寫 x570 自己的 `QDRANT_API_KEY`，那正是
S5 才該做的事）。所以手動：

```bash
# 用 read -rs 拿到 N（由 wsl 透過安全管道傳給你，別貼進聊天）
read -rs -p 'N（從 wsl 取得）: ' N; echo
python3 - "$N" <<'PY'
import os, pathlib, sys, tempfile, shutil
new = sys.argv[1]
assert len(new) == 43, f"長度不對：{len(new)}（應為 43）"
p = pathlib.Path(".env")
lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
out, hit = [], 0
for ln in lines:
    if ln.startswith("QDRANT_PEER_API_KEY="):
        out.append(f"QDRANT_PEER_API_KEY={new}\n"); hit += 1
    else:
        out.append(ln)
assert hit == 1, f"QDRANT_PEER_API_KEY 出現 {hit} 次（預期 1）"
pathlib.Path(".env.rotbak").write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
p.write_text("".join(out), encoding="utf-8")
print("  已換（備份在 .env.rotbak）")
PY
unset N
docker compose up -d qdrant
```

**驗收（x570）**：

```bash
docker compose ps qdrant
bash scripts/host-doctor.sh 2>&1 | grep -E 'qdrant-api-key|qdrant-alt-key|query'
```

`query` 必須 **HTTP 200** —— x570 自己的後端用 `QDRANT_API_KEY`（仍是 V）打槽 1，
應該照常。**若 200 不見了，停下來回報，不要往下走。**

### S3. 🚧 閘門：在**還沒 pull** 之前，先證明 N 真的能連 x570

在 **mbp** 上（此時 mbp 的 peer 還是 V，但你要用 N 手動試）：

```bash
read -rs -p 'N: ' N; echo
printf 'header = "api-key: %s"\n' "$N" \
  | curl -s -o /dev/null -w '  x570 /collections → HTTP %{http_code}\n' --config - \
      http://100.119.83.111:6333/collections
unset N
```

**必須是 200。**

* **200** → 往下走 S4
* **401** → **停下來回報。** 常見原因：x570 的 qdrant 沒重啟成功、或 N 沒真的進到
  `.env`。**此時三台都還能用 V -pull，所以是安全的。**

### S4. 兩台備援機 `pull`（現在才散播 N）

在 **mbp** 與 **wsl** 上各跑：

```bash
git pull
bash scripts/env-sync.sh pull
docker compose up -d qdrant        # ALT 槽要重啟才吃到 N
bash scripts/host-doctor.sh 2>&1 | grep -E 'qdrant|rotate|query'
```

**驗收（兩台都要）**：

| 檢查 | 期望 |
|---|---|
| `rotate-hint` | peer 與本機**不同值**（已拆分，正確） |
| `query` | HTTP 200 |
| `env-check` | per-host 值與總表一致 |

⚠️ **`query` 通過還不算完** —— 它打的是**本機** qdrant。還要證明**跨機**那條路
（`sync-snapshot.sh`）也通：

```bash
LAW_SYNC_SOURCE= bash scripts/sync-snapshot.sh --force 2>&1 | tail -6
```

看有沒有 `SYNC OK`。**沒有就是還沒好，別往下走。**

### S5. 🚨 抽掉 x570 的 V（**不可回頭點**）

⚠️ **只有在 S3 與 S4 都驗收全過之後才做這一步。**

在 **x570**：

```bash
read -rs -p 'x570 新的 QDRANT_API_KEY（43 字符）: ' X; echo
python3 - "$X" <<'PY'
import sys, pathlib
new = sys.argv[1]
assert len(new) == 43, f"長度不對：{len(new)}"
p = pathlib.Path(".env")
pathlib.Path(".env.rotbak2").write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
out, hit = [], 0
for ln in p.read_text(encoding="utf-8").splitlines(keepends=True):
    if ln.startswith("QDRANT_API_KEY="):
        out.append(f"QDRANT_API_KEY={new}\n"); hit += 1
    else:
        out.append(ln)
assert hit == 1, f"QDRANT_API_KEY 出現 {hit} 次（預期 1）—— 停下來查，別繼續"
p.write_text("".join(out), encoding="utf-8")
print("  已換（備份在 .env.rotbak2）")
PY
unset X
docker compose up -d qdrant api
bash scripts/host-doctor.sh 2>&1 | grep -E 'qdrant|rotate|query'
```

**驗收**：`query` HTTP 200；`rotate-hint` 顯示 peer 與本機不同值。

**再證明 V 真的失效了**（在 wsl 上）：

```bash
read -rs -p '舊值 V（見 §4 怎麼取）: ' V; echo
printf 'header = "api-key: %s"\n' "$V" \
  | curl -s -o /dev/null -w '  x570 /collections → HTTP %{http_code}（要 401）\n' --config - \
      http://100.119.83.111:6333/collections
unset V
```

**401 才是完成。** 若仍是 200，V 還活著，**停下來回報**。

### S6. 抽掉 wsl 的 V

wsl 沒有任何別的機器把它當來源（§0b 會確認），所以**這一步是本機的**，不影響
任何人。

```bash
read -rs -p 'wsl 新的 QDRANT_API_KEY（43 字符）: ' Y; echo
python3 - "$Y" <<'PY'
import sys, pathlib
new = sys.argv[1]
assert len(new) == 43, f"長度不對：{len(new)}"
p = pathlib.Path(".env")
pathlib.Path(".env.rotbak3").write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
out, hit = [], 0
for ln in p.read_text(encoding="utf-8").splitlines(keepends=True):
    if ln.startswith("QDRANT_API_KEY="):
        out.append(f"QDRANT_API_KEY={new}\n"); hit += 1
    else:
        out.append(ln)
assert hit == 1, f"QDRANT_API_KEY 出現 {hit} 次（預期 1）—— 停下來查，別繼續"
p.write_text("".join(out), encoding="utf-8")
print("  已換（備份在 .env.rotbak3）")
PY
unset Y
docker compose up -d qdrant api
bash scripts/host-doctor.sh 2>&1 | grep -E 'qdrant|rotate|query'
```

### S7. 三台最終驗收

```bash
bash scripts/host-doctor.sh          # 0 fail，且 rotate-hint = 已拆分
bash scripts/env-sync.sh --check     # 0 warn
bash scripts/env-sync.sh --fingerprints | grep -i qdrant
```

**全部通過後**：刪掉三台的 `.env.rotbak*`（裡面有舊的憑證值）。

⚠️ 刪之前先確認 **S5 的 401 驗收已經做過** —— 刪掉備份之後，V 就**再也拿不回來**
了，而它是「V 是否真的失效」的唯一驗證材料。

---

## 3. 三台現在的 `rotate-hint` 不一樣（2026-10-03 實測）

| 機器 | `rotate-hint` | 意義 |
|---|---|---|
| mbp | `[ ok ]` peer 與本機**不同值**（已拆分，正確） | 已經是目標狀態的**形狀**，但 peer 仍是 V |
| x570 | `[warn]` 同值 → 三台鎖步 | S5 處理 |
| wsl | `[warn]` 同值 → 三台鎖步 | S6 處理 |

---

## 4. 回滾

**S1–S4 之間**：V 還在**尚未 `pull` 的那台機器**的 `.env` 裡。任一台沒 pull 的
機器都能取回：

```bash
python3 -c "
import pathlib
for ln in pathlib.Path('.env').read_text().splitlines():
    if ln.startswith('QDRANT_PEER_API_KEY='):
        print(len(ln.split('=',1)[1]))
"
```

回滾就是把 V 重新 `rotate-secret.sh` 回去，然後 `pull` + 重啟。**因為 x570 在 S5
之前槽 1 仍是 V，所以回滾一定有效。**

**S5 之後**：**沒有回滾**。此時唯一能連 x570 的是 N，只能往前修。

---

## 5. 不要做的事

* **不要**直接 `rotate-secret.sh QDRANT_PEER_API_KEY` 然後在三台同時 `pull` ——
  那會讓 mbp／wsl 立刻拿 N 去打還只認 V 的 x570，**而且再 pull 幾次都不會好**
  （值一致了，只是 x570 不認）。
* **不要**把密碼寫進 crontab 或 `LAW_SYNC_SOURCE=`。那是憑證的第二份副本，而且每台
  密碼不同 —— 等於在三機標準化剛做完的地方重新引入 per-machine 差異。
* **不要**用 `--init-secrets`（那是「建立加密檔」，會覆蓋整份，含沒換的那些）。
  2026-09-27 踩過。
* **不要**用唯讀槽（`read_only_api_key`）當 peer —— 對來源機要 `POST` 建快照與
  `DELETE` 清舊快照，唯讀會擋掉。
* **不要**在任何回報裡貼值。要比對三台用
  `bash scripts/env-sync.sh --fingerprints`（鍵名＋長度＋sha12）。

---

## 6. 完成後要更新的文件

* [ ] `settings/env/README.md §7` — 把「peer 與 own 同值」更新成「ALT 槽已拆開，
      peer 與 own 不同值」，並註明 V 已於 2026-10-03 吊銷
* [ ] `host-doctor.sh` 的 `rotate-hint` 文案 — 確認它現在只報「有沒有拆開」，
      不再報「同值 → 三台鎖步」（那個狀態已不存在）
* [ ] 兩份 handoff — 記下三台的新 `sha12`（**只記 sha12**）