# 事件記錄：`The request contains invalid parameters`（2026-10-09）

## 觀測到的錯誤

```text
Error: The request contains invalid parameters. Check the request body for any errors or inconsistencies.
```

`repo` 內 grep 無此字串 —— 它不是本專案後端產生的，是上游回的。

## 來源（已確認）

- OpenCode 背後的 LLM provider（`providerID: opencode`／`opencode-go`），model 皆為
  muse-spark 家族＋`variant: xhigh`：
  - `muse-spark-1.3-contributor`（`opencode-go`）：首次失敗 `msg_11c95fe17001Du2UGBLq9drk4k`
  - `muse-spark-1.3-contributor-free`（`opencode`）：`msg_11e785de8001MJG8HvjBGe79hj` 等共 6 次
- Session：`ses_eec0cf3c1ffeCsObljXtQ8TSub`（GitHub Actions Fast/Full CI split），
  2026-10-08 17:36 起至 2026-10-09，assistant `finish=error`、`type=provider.invalid-request`、`status=400`。
- 上游 response body 只有通用字串，`code`／`param` 皆 null —— **沒說是哪個欄位壞**，
  且 opencode log 只存 response、不存 request body，所以確切欄位無法從現有證據唯一確定。

## 關鍵證據（來自 `~/.local/share/opencode/opencode.db`）

1. 同一 model＋variant 在此之前成功上千次 —— 非靜態不相容，是請求體相關的偶發 400。
2. 失敗的都是 agentic loop 中的下一次 LLM call（`content: []`），不是 tool 本身失敗：
   `msg_11c95cb6f001NtI9FmFC7BB3qF` 的兩個 `edit` 皆 `completed`，patch 確實進了
   working tree（`backend/app/b2d_answer.py` 的 `_enforce_continuity` 即為證）。
   「Edit 失敗」是誤判，死的是下一步的模型請求。
3. 失敗前一回合往往是當時段最大的回合（12KB 雙 edit；11KB write＋shell）；
   28304 還是全段唯一零 `reasoning` block 的 xhigh 回合（成功回合都有
   `reasoningEncryptedContent`），缺 thinking 歷史是高度嫌疑之一，但上游不給細節，只能列為嫌疑。
4. Session 已累積 3488 messages／13M input tokens，失敗密度隨歷史變長上升；
   原地重試連敗、換新 prompt 偶爾又能成，符合「請求體隨歷史膨脹偶發踩線」。

## 分類

`EXTERNAL_TOOL_REJECTED`（上游 400）。**不是** `INVALID_TOOL_ARGUMENTS`
（tool 端證據皆為 `completed`，無證據不亂標）。

## 已做的修補（本 repo）

- `backend/app/rag.py`：新增 `log_upstream_failure()`（只記 provider／model、方法、URL、
  狀態、回應前 500 字，**不記 headers**）；`_rstatus()` 非 2xx 先記再 raise，
  覆蓋全部 8 家雲端 provider＋ollama。
- `backend/app/main.py`：`/query` 與兩個 `judgments/*` 的 `HTTPStatusError` 處理共用同一支記錄。
- `tests/test_upstream_error_log.py`：釘住「400 留來源／200 不擾／header 永不進 log／429 限流標記仍在」。

## 未解／後續

- 確切是哪個請求欄位：需開 `OPENCODE_LOG_LEVEL=DEBUG` 重現一次抓 request body 逐欄比對。
- 當下復原：開新 session（或 fork）、拿掉 `xhigh`、避免單回合多 tool／超大 edit。
