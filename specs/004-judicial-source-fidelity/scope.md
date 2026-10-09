# T007 Selection Scope — M1 跨 chunk 正例候選池（maintainer decision, 2026-10-09）

## 決策

為 S2 跨 chunk HOLDING 正例 hunting 增選 **100 卷**，與凍結 74 卷互斥。
本 scope 只決定「選哪 100 個 entry paths」；解壓、chunk、審閱需 M1 後續授權
（UnRAR 安裝另需 6a 授權）。

## 範圍

- 法院集合（4）：臺灣臺北地方法院民事、臺灣桃園地方法院民事、
  臺灣臺中地方法院民事、臺灣新北地方法院民事。
- 日期：202607（archive 全 period 僅此一月，無從分層）。
- 母體：上述 4 院民事共 25,994 件，扣除已凍結 74 卷的 JID。
- 排序：`unpacked_size` 降冪（長卷宗才切得出 FORCED 斷點——正例的必要條件），
  同 size 以 entry path 字典序 tiebreak（確定性）。取前 100。
- 刻意偏差聲明：本選樣是 candidate hunt，不是代表性抽樣（代表性抽樣是 T006
  的 494 筆，兩者目的不同，不可混用）。

## 結果

- 100 paths，size 38,266–156,818 bytes；法院分布：臺北 38／新北 36／臺中 19／桃園 7。
- 清單 manifest sha256（paths 以 `\n` 連接）：`7afade720d74d7cf…`（前 16 hex，
  完整值見 M1 builder 產物；本檔為決策記錄）。

## 重現命令（header-only，零解壓）

```bash
.venv/bin/python -c "
import sys, json; sys.path.insert(0,'ingest/judgements')
import inventory as I
ents = I.inventory()
files = [e for e in ents if not e.is_dir]
frozen = {r['document_id'] for r in json.load(open(
  '/home/solo/artifacts/t007e05cb/out/corpus_snapshot.json'))['records']}
WANT = {'臺灣臺北地方法院民事','臺灣桃園地方法院民事',
        '臺灣臺中地方法院民事','臺灣新北地方法院民事'}
pool = [e for e in files
        if len(p := e.path.replace(chr(92),'/').split('/')) > 1
        and p[1] in WANT and p[-1][:-5] not in frozen]
pool.sort(key=lambda e: (-e.unpacked_size, e.path))
sel = pool[:100]
assert len(sel) == 100
print('OK', len(pool), '->', len(sel))"
```

## 驗收（T007）

- 本檔寫明 court set／date range／expected count（100）✅（本節）。
- 上述命令重跑得 100（2026-10-09 實測通過，母體 25,986）。
- 下游（解壓→chunk→審閱）未授權，未執行。
