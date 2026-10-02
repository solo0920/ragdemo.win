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
| S1–S3 | **可以** —— V 還在 sops 以外至少一台機器的 `.env` 裡（見 §4） |
| **S4** | **不可以** —— x570 不再接受 V |

**所以 S4 只能在 S3 三項驗收全過之後做。**

---

## 2. 步驟

> ⚠️ **2026-10-03 改寫**：原本的 S2 是「由 wsl 手動把 N 傳給 x570、貼進 `.env`」。
> 那是**多餘的，而且不安全** —— 秘密會經過聊天。查證後確認 `QDRANT_API_KEY`
> **不在版控總表裡**，所以 `pull`／`render` **不會動它** ⟹ **x570 自己 `pull` 就
> 拿到 N**，N 完全不必離開 wsl 的檔案系統。
>
> 而且新版**沒有任何時刻是拉不到的**（舊版 S2 到 S4 之間是一段隱含風險）。
> 兩台的 `QDRANT_API_KEY` 都是 per-host、pull 不動，所以 **pull 只換 peer**。

### S0. 三台基線（**已完成 2026-10-03**）

```
        QDRANT_API_KEY        QDRANT_PEER_API_KEY
wsl     fe4b2d4ba82a         fe4b2d4ba82a
x570    fe4b2d4ba82a         fe4b2d4ba82a     ← 同值（要修的）
mbp     94c1f7a9ef3d         fe4b2d4ba82a     ← 已拆分
```

`mbp` 的 `LAW_SYNC_SOURCE=http://100.119.83.111:6333`（= x570）✓；
`x570` 沒有那行（它是來源機，不拉別人的）✓。

### S1. 在 **wsl** 產生 N 寫進 sops，然後 commit + push

```bash
read -rs -p '新的 peer key（43 字符；產生方式見下）: ' N; echo
printf '%s' "$N" | bash scripts/rotate-secret.sh QDRANT_PEER_API_KEY --from-stdin
unset N
git add settings/env/secrets.common.enc.env
git commit -m 'wsl: 輪換 QDRANT_PEER_API_KEY（撤銷 2026-10-03 外洩的值）'
git push
```

**N 從哪來**：讓 qdrant 自己產生格式正確的值最省事 ——

```bash
N="$(python3 -c "import secrets,string;a=string.ascii_letters+string.digits+'-_';print(''.join(secrets.choice(a) for _ in range(43)))")"
printf '  長度=%s\n' "${#N}"
```

⚠️ **`rotate-secret.sh` 會改版控中的加密檔**，所以**必須 commit + push**，否則另外兩台
pull 不到。**在 push 之前不要在別的機器 pull** —— 那是 S2 的順序。

### S2. 在 **x570** pull（peer 變 N）＋ 重啟 qdrant

```bash
git pull
bash scripts/env-sync.sh pull          # peer=N；QDRANT_API_KEY 不動（per-host）
docker compose up -d qdrant            # ALT 槽要重啟才吃到 N
bash scripts/host-doctor.sh 2>&1 | grep -E 'qdrant|rotate|query'
```

**此時 x570 的槽位 = 槽1 `V` ＋ 槽2 `N`（兩把都收）** —— 這是**刻意**的過渡狀態。

| 檢查 | 期望 |
|---|---|
| `query` | HTTP 200（x570 自己的後端用 `QDRANT_API_KEY`＝V 打槽 1） |
| `rotate-hint` | 轉成 `[ ok ]` peer 與本機不同值 |

⚠️ **此時 mbp／wsl 手上還是 V，而 x570 的槽 1 仍是 V → 它們照常拉得到。** 沒有風險窗口。

### S3. 在 **mbp 與 wsl** pull ＋ 重啟 qdrant

```bash
git pull
bash scripts/env-sync.sh pull
docker compose up -d qdrant
bash scripts/host-doctor.sh 2>&1 | grep -E 'qdrant|rotate|query'
```

**驗收（兩台都要，三項全過才往下）**：

| 檢查 | 期望 | 為什麼這三項各自獨立 |
|---|---|---|
| `query` | HTTP 200 | 打的是**本機** qdrant（用 `QDRANT_API_KEY`，pull 沒動它） |
| `rotate-hint` | `[ ok ]` 已拆分 | peer 與本機不同值 |
| `sync-snapshot.sh --force` | 有 `SYNC OK` | **唯一證明跨機那條路**的檢查 |

```bash
bash scripts/sync-snapshot.sh --force 2>&1 | tail -6
```

⚠️ **`query` 通過不等於好了** —— 它完全沒碰 x570。只有第三項碰到。

### S4. 🚨 在 **x570** 抽掉 V（**不可回頭點**）

⚠️ **只有 S3 三項全過才做這一步。** 做完之後 V 在 x570 失效，**不能回滾**。

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
PY
unset X
docker compose up -d qdrant api
bash scripts/host-doctor.sh 2>&1 | grep -E 'qdrant|rotate|query'
```

**驗收**：`query` HTTP 200；`rotate-hint` `[ ok ]`。

### S5. 證明 V 真的死了（在 **mbp** 上，用它還留著的 V）

```bash
python3 -c "
import pathlib
for ln in pathlib.Path('.env').read_text().splitlines():
    if ln.startswith('QDRANT_PEER_API_KEY='):
        open('/tmp/v.key','w').write(ln.split('=',1)[1])
" && chmod 600 /tmp/v.key
curl -s -o /dev/null -w '  x570 /collections → HTTP %{http_code}（要 401）\n' \
  --config <(printf 'header = "api-key: %s"\n' "$(cat /tmp/v.key)") \
  http://100.119.83.111:6333/collections
shred -u /tmp/v.key 2>/dev/null || rm -f /tmp/v.key
```

**401 才是完成。** 若 200 → V 還活著，**停下來回報**。

### S6. 在 **wsl** 抽掉 V（本機動作，不影響別人）

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
PY
unset Y
docker compose up -d qdrant api
bash scripts/host-doctor.sh 2>&1 | grep -E 'qdrant|rotate|query'
```

### S7. 三台最終驗收

```bash
bash scripts/host-doctor.sh          # 0 fail，rotate-hint = 已拆分
bash scripts/env-sync.sh --check     # 0 warn
bash scripts/env-sync.sh --fingerprints | grep -i qdrant
```

**S5 的 401 已經看到之後**，才刪掉各台的 `.env.rotbak*`（裡面有舊值）。
刪掉之後 V 就再也拿不回來，而它是「V 是否真失效」的唯一驗證材料。

---

## 3. 三台現在的 `rotate-hint` 不一樣（2026-10-03 實測）

| 機器 | `rotate-hint` | 意義 |
|---|---|---|
| mbp | `[ ok ]` peer 與本機**不同值**（已拆分，正確） | 已經是目標狀態的**形狀**，但 peer 仍是 V |
| x570 | `[warn]` 同值 → 三台鎖步 | **S4** 處理 |
| wsl | `[warn]` 同值 → 三台鎖步 | **S6** 處理 |

---

## 4. 回滾

**S1–S3 之間**：V 還在**尚未 `pull` 的那台機器**的 `.env` 裡。任一台沒 pull 的
機器都能取回：

```bash
python3 -c "
import pathlib
for ln in pathlib.Path('.env').read_text().splitlines():
    if ln.startswith('QDRANT_PEER_API_KEY='):
        print(len(ln.split('=',1)[1]))
"
```

回滾就是把 V 重新 `rotate-secret.sh` 回去，然後 `pull` + 重啟。**因為 x570 在 S4
之前槽 1 仍是 V，所以回滾一定有效。**

**S4 之後**：**沒有回滾**。此時唯一能連 x570 的是 N，只能往前修。

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