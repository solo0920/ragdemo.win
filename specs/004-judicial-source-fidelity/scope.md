# T007 Selection Scope — M1 跨 chunk 正例候選池（maintainer decision, 2026-10-09）

## 決策

為 S2 跨 chunk HOLDING 正例 hunting 增選 **100 卷**，與凍結 74 卷互斥。
本 scope 只決定「選哪 100 個 entry paths」；解壓、chunk、審閱需 M1 後續授權
（UnRAR 安裝另需 6a 授權）。

## 範圍

- 法院集合（4）：臺灣臺北地方法院民事、臺灣桃園地方法院民事、
  臺灣臺中地方法院民事、臺灣新北地方法院民事。
- 日期：202607（archive 全 period 僅此一月，無從分層）。
- 母體：上述 4 院民事共 **25,994** 件；其中落在本 4 院的**凍結 74 卷有 8 卷**
  （其餘 66 卷屬其他法院，不在本母體內），扣除後 **25,986**。
  ⚠ 原稿寫「扣除已凍結 74 卷的 JID」會讓人以為扣掉 74 —— 實際只扣 8（2026-10-09 修正）。
- 排序：`unpacked_size` 降冪（長卷宗才切得出 FORCED 斷點——正例的必要條件），
  同 size 以 entry path 字典序 tiebreak（確定性）。取前 100。
- 刻意偏差聲明：本選樣是 candidate hunt，不是代表性抽樣（代表性抽樣是 T006
  的 494 筆，兩者目的不同，不可混用）。

## 結果

- 100 paths，size 38,266–156,818 bytes；法院分布：臺北 38／新北 36／台中 19／桃園 7。
- **機器可讀對應物：`allowlist-m1.json`（同目錄，29,638 bytes，進版控）**——
  含 100 筆 `{path, path_posix, unpacked_size, crc32}`，另記 artifact sha256、
  選樣規則、母體三段計數、**被排除的 74 個凍結 JID**（讓重現不依賴 repo 外檔案）。
- `entries_sha256`（`path` 以 `\n` 連接後 sha256）：
  `7afade720d74d7cfce0ec7781e87246bec33dd28688fed2428dd270ec25c1d06`
  （2026-10-09 實測；原先只記 16 hex 前綴，現補全）。
- artifact sha256：`ef35ce4401d7d751bbe7cbeab93cb92949f5971a26947cbde80c64e9722fea4c`。

## 重現命令（header-only，零解壓）

重算並與 `allowlist-m1.json` 記載的 `entries_sha256` 對照：

```bash
.venv/bin/python -c "
import sys, json, hashlib; sys.path.insert(0,'ingest/judgements')
import inventory as I
snap = json.load(open('specs/004-judicial-source-fidelity/allowlist-m1.json',
                      encoding='utf-8'))          # 凍結清單讀回本檔，見下方說明
frozen = set(snap['excluded_frozen_document_ids'])
assert len(frozen) == 74, len(frozen)
ents = I.inventory()
files = [e for e in ents if not e.is_dir]
WANT = set(snap['selection']['courts'])
def parts(p): return p.replace(chr(92),'/').split('/')
def jid(p): return p.replace(chr(92),'/').rsplit('/',1)[-1][:-5]
civil = [e for e in files if parts(e.path)[1] in WANT]
pool = [e for e in civil if jid(e.path) not in frozen]
pool.sort(key=lambda e: (-e.unpacked_size, e.path))
sel = pool[:100]
d = hashlib.sha256(chr(10).join(e.path for e in sel).encode()).hexdigest()
print('pool', len(civil), '->', len(pool), '-> sel', len(sel))
print('entries_sha256 =', d)
print('MATCH' if d == snap['entries_sha256'] else 'MISMATCH ← 清單已漂移')"
```

**為什麼凍結清單讀回本檔而不是外部 manifest**：原指令從
`/home/solo/artifacts/t007e05cb/out/corpus_snapshot.json` 讀凍結 JID，那是 **repo 外**
路徑，新 clone 跑不出同樣結果。現在 74 個 JID 記在 `allowlist-m1.json`，重現只需要
repo 內檔案 + 那個 RAR（`artifact.py` 已釘其 sha256）。

⚠ 這個安排是**自證**不是**獨立佐證**：誰改了 `excluded_frozen_document_ids`，
重現仍會 `MATCH`（分母來源就是它自己）。防線是本檔記載的完整 `entries_sha256`
——改了清單就對不上，那時要嘛連同本檔的記載值一起更新（留下 commit 紀錄），
要嘛回頭核對 T006 的原始 manifest。**T006 的 manifest 至今不在 repo 內**
（`/home/solo/artifacts/…`），而 284M 的 RAR 本身也不可重建：這是既有落差，
已記入 006 的待辦，不是本檔能自行解決的。

## 驗收（T007）

- 本檔寫明 court set／date range／expected count（100）✅（本節）。
- `allowlist-m1.json` 存在且進版控（29,638 bytes、`count=100`、
  `entries` 長度 100、被排除 JID 74 個）✅（2026-10-09 實測）。
- 上方命令重跑：`pool 25994 -> 25986 -> sel 100`、`entries_sha256` 與本檔記載值
  相同 → `MATCH` ✅（2026-10-09 實測，零解壓）。
- 下游（解壓→chunk→審閱）未授權，未執行。