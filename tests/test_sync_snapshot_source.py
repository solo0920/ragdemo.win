"""`sync-snapshot.sh` 的 `LAW_SYNC_SOURCE` 求值順序（**靜態**驗證）。

**這是 2026-09-30 mbp 回報「快照同步從沒運作過」的其中一個根因**，而它是
靜默的：`sync.log` 是 **0 bytes** —— 連「source offline, skip」都沒印，
因為腳本在讀到 `SOURCE` 之前就 `exit 2` 了。

## 病根：求值順序

```bash
# 檔頭第 23 行（原本）
SOURCE="${1:-${LAW_SYNC_SOURCE:-}}"

# 第 114 行
_load_env          # .env 這時候才被讀進環境
```

`.env` 是在 `SOURCE` 求值**之後**才載入的 → `.env` 裡的 `LAW_SYNC_SOURCE`
永遠是空字串。而用法訊息卻寫著「也可用環境變數 `LAW_SYNC_SOURCE` 指定
（cron 用這個比較順）」—— **那句話是假的**，而照著做的人（mbp）會照著設定
`LAW_SYNC_SOURCE` 然後發現排程從沒成功過，且沒有任何錯誤提示。

這是「位置參數優先、env 次之」這個常見寫法的陷阱：它假設 `env` 這個來源
已經在環境裡了，但 `.env` **本身就是**要靠程式去讀的東西。

## 為什麼是靜態驗證而不是真的執行

**第一版是執行腳本，結果它真的對著 x570 拉了整份 laws 快照回來**
（`SYNC OK: … 39879 points`），而且 125 秒。兩個原因，都值得記：

1. **`HOME` 無法隔離 `.env`** —— `_load_env` 讀的是 `$ROOT/.env`，而 `ROOT`
   是從腳本自身位置推導的（第 80 行），**沒有任何環境變數能改它**。所以
   fixture 設的 `HOME` 完全沒作用，腳本讀到的是真實的 `.env`
   （`TS_IP` 空 → `DEST=127.0.0.1` → 對本機 qdrant 動手）
2. 保留位址（`192.0.2.99`）在這個環境裡**解析得到**、連過去是逾時而不是
   refused，所以 `-m 5` 會等滿

那次沒有造成損失（拉到的是同資料的冪等重建，事後驗證 points 仍是 39879、
`status: green`、查詢正常），但**測試不該有動真實 qdrant 的能力**。
所以改成靜態驗證：這裡要守的是「求值順序」與「文件不說謊」，兩者都不需要
真的跑。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SYNC = ROOT / "scripts" / "sync-snapshot.sh"


def _text() -> str:
    return SYNC.read_text(encoding="utf-8")


def test_source_is_evaluated_after_load_env():
    """`SOURCE` 的求值必須在 `_load_env` **之後**。

    這是根因的釘子。執行驗證做不到（見模組 docstring），所以靜態比對位置。
    """
    text = _text()
    i_load = text.index("\n_load_env\n")
    i_src = text.index('SOURCE="${1:-${LAW_SYNC_SOURCE:-}}"')
    assert i_src > i_load, (
        "SOURCE 的求值又在 _load_env 之前了 —— 那 .env 裡的 "
        "LAW_SYNC_SOURCE 永遠讀不到（2026-09-30 的 bug）"
    )


def test_dest_is_also_evaluated_after_load_env():
    """`DEST` 同樣要在 `_load_env` 之後 —— 它依賴 `.env` 的 `TS_IP`。

    第一版只修了 `SOURCE`（因為它是症狀所在），但 `DEST` 用
    `${TS_IP:-127.0.0.1}`，而 `TS_IP` 只在 `.env` 裡。留著就會是同類的
    靜默 bug：備援機的 `TS_IP` 有值時，dest 會安靜地退回 `127.0.0.1`。
    """
    text = _text()
    i_load = text.index("\n_load_env\n")
    i_dest = text.index('DEST="${2:-http://${TS_IP:-127.0.0.1}:6333}"')
    assert i_dest > i_load, (
        "DEST 的求值在 _load_env 之前 —— .env 的 TS_IP 不會生效，"
        "備援機會安靜地把目的地位址退回 127.0.0.1"
    )


def test_positional_arg_still_wins_over_env_file():
    """位置參數仍優先於 `.env`。

    這是刻意的：cron／launchd 想臨時換來源機時，命令列比改 `.env` 直觀。
    寫法是 `${1:-${LAW_SYNC_SOURCE:-}}` —— `:-` 讓位置參數（空字串時也取
    後者）優先。
    """
    text = _text()
    m = re.search(r'SOURCE="\$\{1:-\$\{LAW_SYNC_SOURCE:-\}\}"', text)
    assert m, (
        "SOURCE 的寫法必須是 `${1:-${LAW_SYNC_SOURCE:-}}` —— "
        "位置參數優先、.env 次之"
    )


def test_usage_mentions_the_env_var_and_that_line_says_it_works():
    """用法訊息提到 `LAW_SYNC_SOURCE` —— 而它現在真的生效（不再說謊）。

    原先那句是假的：訊息說「也可用環境變數指定（cron 用這個比較順）」，
    但求值在 `_load_env` 之前，照著做的人會得到一個從不生效的設定 +
    一個 0 bytes 的 log。**沒有任何錯誤提示**。
    """
    text = _text()
    m = re.search(r"也可用環境變數 (\w+)", text)
    assert m, "用法訊息沒提環境變數 —— 那 mbp 就無從知道該設哪個"
    assert m.group(1) == "LAW_SYNC_SOURCE", (
        f"用法提到的變數名不對: {m.group(1)}"
    )


def test_missing_source_still_exits_2():
    """兩者都空時仍要 `exit 2` 並印用法。

    別因為修了「.env 讀不到」而放寬到「沒來源也靜默成功」—— 那會讓排程
    從「每次報錯」變成「從不動作」，更難察覺。
    """
    text = _text()
    i = text.index('SOURCE="${1:-${LAW_SYNC_SOURCE:-}}"')
    seg = text[i:i + 500]
    assert "exit 2" in seg, "SOURCE 為空時要 exit 2"
    assert "用法" in seg, "要印用法訊息"


def test_load_env_reads_root_env_not_home():
    """**記住這個約束**：`ROOT` 從腳本自身推導，沒有環境變數能改它。

    這條不是為了測 `_load_env`，而是為了讓未來想寫「執行式」測試的人
    先看到：`_load_env` 讀的是 `$ROOT/.env`，設 `HOME` 隔離不了。
    第一版就是因為沒看到這條而對真實 qdrant 動手（見模組 docstring）。
    """
    text = _text()
    m = re.search(r'^ROOT="\$\(cd .*dirname.*\$0.*pwd\)"$', text, re.M)
    assert m, "ROOT 的推導方式變了 —— 若它開始讀環境變數，測試就能隔離 .env 了"
    # 關鍵：ROOT **不**含任何環境變數（那才叫無法隔離）
    assert "${" not in m.group(0) and "$ENV" not in m.group(0), (
        f"ROOT 現在依賴環境變數: {m.group(0)} —— "
        f"那測試就能用環境變數隔離 .env 了（這條的用意是記錄現況）"
    )
    assert '$ROOT/.env' in text, "_load_env 應該讀 $ROOT/.env"
