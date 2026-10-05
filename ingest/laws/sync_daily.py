#!/usr/bin/env python3
"""法規每日同步（完整性/正確性優先）。

流程：
  1. 下載官方 ZIP（https://law.moj.gov.tw/api/ch/law/json，約 26MB，每日成本極低）
  2. 解壓 ChLaw.json → 完整 JSON parse＋課程基本檢查（Laws 非空、UpdateDate 齊、條文數>0）
  3. sha256＋UpdateDate 與上次（data/laws/.law_sync.json）比對
     - 相同 → 只記心跳（log 一行），不動任何既有資料
     - 不同 → 舊版工件先備份到 versions/<舊 sha>/，才置換 ChLaw.json，
       依序 normalize(清洗)→eda(duckdb parquet)→pg_load(metadata)→qdrant_load(向量，
       整庫重建所以上游刪除也會正確移除)→驗證，並把新版工件+原始 zip 備份 versions/<新 stamp>/
  4. 防呆：新版條文數若比上次少 20%+（資料縮水）→ 中止並保留舊資料，等人工確認
  5. 成功套用後有界化 versions/ 快照數量（預設留 5 份，RAGDEMO_KEEP_VERSIONS 可調）
     ⚠️ 上游只提供**當前**版本，所以砍掉的快照是無法再取得的歷史 —— 這是
        保留深度的取捨，不是清垃圾。當前版本與前一版永不刪（回退路徑）。

用法：
  sync_daily.py          # 檢查＋回報（預設，不套用）
  sync_daily.py --apply  # 檢查＋有新版才真正跑整條管線（crontab 用這個）

環境變數：
  RAGDEMO_KEEP_VERSIONS  保留幾份 versions/ 快照（預設 5）
"""
import fcntl
import hashlib
import io
import json
import logging
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "laws"
RAW = DATA / "raw"
VERS = DATA / "versions"
STATE = DATA / ".law_sync.json"
LOCK = DATA / ".sync.lock"
LOG = DATA / "sync.log"
URL = "https://law.moj.gov.tw/api/ch/law/json"
VENV_PY = ROOT / ".venv" / "bin" / "python"  # uv 統一環境（根 pyproject.toml，uv sync）。勿 .resolve()：會追 symlink 到 uv base python（無套件）
UA = "Mozilla/5.0 (X11; Linux x86_64) ragdemo-law-sync/1.0"
SHRINK_GUARD = 0.80  # 新版條文數 < 上次 80% → 視為縮水，中止

VALID_BACKUP = [
    "ChLaw.json", "laws_flat.parquet", "laws_meta.parquet",
    "laws_flat.jsonl", "laws_meta.jsonl",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.FileHandler(LOG, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("sync")


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def load_state() -> dict:
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}


def save_state(s: dict) -> None:
    tmp = STATE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, STATE)


def download(url: str, dest: Path) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    last_err: Exception | None = None
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                body = r.read()
            if int(r.status) != 200:
                raise RuntimeError(f"HTTP {r.status}")
            return body
        except (urllib.error.URLError, RuntimeError, TimeoutError) as e:
            last_err = e
            log.warning("下載失敗(第%d次)：%s", attempt, e)
    raise RuntimeError(f"下載連續失敗：{last_err}")


def parse_validate(body: bytes) -> tuple[dict, int, int]:
    """回傳 (data, laws 數, 條文數)；任何異常都中止。"""
    if not zipfile.is_zipfile(io.BytesIO(body)):
        raise ValueError("下載內容不是有效 ZIP")
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        names = z.namelist()
        hit = [n for n in names if n.endswith("ChLaw.json")]
        if not hit:
            raise ValueError(f"ZIP 內找不到 ChLaw.json（實際：{names[:5]}…）")
        jbytes = z.read(hit[0])
    data = json.loads(jbytes.decode("utf-8-sig"))
    if not isinstance(data, dict) or "Laws" not in data:
        raise ValueError("JSON 結構不符（缺 Laws）")
    if not isinstance(data.get("UpdateDate"), str) or not data["UpdateDate"]:
        raise ValueError("JSON 缺 UpdateDate")
    laws = data["Laws"]
    if not isinstance(laws, list) or not laws:
        raise ValueError("Laws 為空，疑似空/壞檔")
    articles = sum(len(l.get("LawArticles") or []) for l in laws)
    if articles <= 0:
        raise ValueError("條文總數為 0，疑似空/壞檔")
    return data, len(laws), articles


def backup_version(stamp: str, files: list[Path], extra: list[Path] | None = None) -> Path:
    d = VERS / stamp
    d.mkdir(parents=True, exist_ok=True)
    for p in files + (extra or []):
        if p.exists():
            shutil.copy2(p, d / p.name)
    return d


def run_step(name: str, script: str) -> None:
    log.info("── 階段開始：%s", name)
    r = subprocess.run([str(VENV_PY), str(Path(__file__).parent / script)], cwd=ROOT)
    if r.returncode != 0:
        raise RuntimeError(f"{name}（{script}）失敗 rc={r.returncode}")
    log.info("── 階段完成：%s", name)


def version_changed(prev: dict, sha: str) -> bool:
    """依 sha256 判定是否有新版。首次（無 prev）視為有新版。"""
    return prev.get("sha256") != sha


# 保留幾份 versions/ 快照。**這是歷史深度，不是磁碟空間** ——
#   上游 law.moj.gov.tw 只提供**當前**版本，舊的下不回來。所以砍掉的就是
#   唯一一份過去狀態的紀錄（回退、比對條文變動、稽核都靠它）。
#   預設 5 份 ≈ 355MB，是「有界」與「保留夠回退深度」之間的取捨。
#
# ⚠️ **設定位置：只有「行程環境變數」有效，寫進 `.env` 沒用。**
#   本模組不載入 .env（整個 ingest/ 沒有 load_dotenv —— `pg_load.py` 讀
#   `.env` 是走 `_hostenv.py`，那是另一條路），而 cron 那行也不會
#   `source .env`。所以要調必須寫在 crontab 行的前綴：
#
#       30 6 * * * RAGDEMO_KEEP_VERSIONS=8 cd ... && .venv/bin/python ...
#
#   ⚠️ env-audit.py 會把這個變數列進 `.env.example`（標成「消費者：backend」，
#      但它其實只被 ingest/ 讀 —— 那是 env-audit 的分類缺口，不是本檔的問題）。
#      **那個條目放在那裡不會生效**，不要看到它就去改 .env。
KEEP_VERSIONS = int(os.environ.get("RAGDEMO_KEEP_VERSIONS", "5"))


def prune_versions(keep: int, protect: set[str]) -> list[str]:
    """刪掉 versions/ 裡過舊的快照，回傳被刪的名稱（給 log）。

    ⚠️ 為什麼只在**成功套用之後**呼叫：失敗時留下來的目錄是回退材料。
       實測 2026-10-02/03 有 4 次是 pg_load 失敗留下的（那幾次在
       normalize 之後就中止，但備份步驟已經跑完）—— 那些目錄是排查用線索，
       不是垃圾。

    ⚠️ 為什麼用 mtime 排序而不是名稱：目錄名有兩種來源 ——
       `versions/<sha12>`（舊版備份）與 `versions/<YYYYmmdd-HHMMSS>`（新版備份），
       兩種格式無法直接互比。mtime 是唯一可靠的「時間」。

    ⚠️ 為什麼要有 protect：當前版本與它的前一版是回退路徑。
       即使它們因為 mtime 怪（有人手動搬過）而排在最舊，也不能刪 ——
       刪掉當前版本的備份 = 刪掉唯一一份完整的當前工件。
    """
    if not VERS.is_dir():
        return []
    dirs = [d for d in VERS.iterdir() if d.is_dir()]
    # 最新的 keep 份留著；protect 的無論多舊都留著。
    newest = sorted(dirs, key=lambda d: d.stat().st_mtime, reverse=True)[:keep]
    keep_names = {d.name for d in newest} | protect
    removed = []
    for d in sorted(dirs, key=lambda d: d.stat().st_mtime):
        if d.name in keep_names:
            continue
        try:
            shutil.rmtree(d)
            removed.append(d.name)
        except OSError as e:
            # 刪不掉不是這次同步的錯，絕不能讓它把已完成的套用變成失敗。
            log.warning("無法刪除舊版快照 versions/%s/：%s（保留）", d.name, e)
    return removed


def shrink_guard(prev_articles: int, new_articles: int, ratio: float = SHRINK_GUARD) -> bool:
    """新版條文數是否大幅縮水（True=應中止）。prev_articles<=0（首次）不擋。"""
    if prev_articles <= 0:
        return False
    return new_articles < prev_articles * ratio


def main() -> int:
    apply = "--apply" in sys.argv

    RAW.mkdir(exist_ok=True)
    VERS.mkdir(exist_ok=True)
    with open(LOCK, "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)  # 防 cron 重疊
        prev = load_state()
        log.info("開始每日檢查（--apply=%s）；上次 sha=%s 更新日=%s",
                 apply, prev.get("sha256", "-")[:12], prev.get("update_date", "-"))

        body = download(URL, RAW / "latest.zip")
        data, laws_n, articles_n = parse_validate(body)
        chlaw = extract_chlaw(body)
        sha = sha256_bytes(chlaw)
        update_date = data["UpdateDate"]

        state = {
            "sha256": sha, "update_date": update_date,
            "laws_count": laws_n, "articles_count": articles_n,
            "last_checked": datetime.now().isoformat(timespec="seconds"),
        }
        if not version_changed(prev, sha):
            state["applied_at"] = prev.get("applied_at")
            save_state(state)
            log.info("無更新：sha=%s… update=%s（法規 %d，條文 %d）",
                     sha[:12], update_date, laws_n, articles_n)
            (RAW / "latest.zip").write_bytes(body)  # 保留本次下載當最新原始檔（審計）
            return 0

        log.info("偵測到新版：上次 sha=%s… → 新 sha=%s… (update=%s, 法規 %d→%d, 條文 %d→%d)",
                 prev.get("sha256", "-")[:12], sha[:12], update_date,
                 prev.get("laws_count", 0), laws_n, prev.get("articles_count", 0), articles_n)

        if shrink_guard(prev.get("articles_count", 0), articles_n):
            log.error("疑似資料縮水：條文 %d < 上次 %d×%.0f%%；中止並保留舊資料，請人工確認。",
                      articles_n, prev["articles_count"], SHRINK_GUARD * 100)
            return 2

        if not apply:
            log.info("有新版但未套用（--apply 才跑管線）；不更動現有資料。")
            return 0

        stamp_new = datetime.now().strftime("%Y%m%d-%H%M%S")
        old_stamp = (prev.get("sha256") or "init")[:12]
        live = [DATA / p for p in VALID_BACKUP]
        zip_live = RAW / "latest.zip"

        # 1) 舊版工件先備份（完整性：新管線破壞前必須有完整舊版可回退）
        backup_version(old_stamp, live, [zip_live] if zip_live.exists() else [])
        log.info("已備份舊版工件 → versions/%s/", old_stamp)

        # 2) 置換 ChLaw.json（先寫 tmp 再 os.replace，原子）
        tmp = DATA / "ChLaw.json.new"
        tmp.write_bytes(chlaw)
        os.replace(tmp, DATA / "ChLaw.json")

        # 3) 清洗 → parquet
        run_step("清洗 normalize", "normalize.py")
        run_step("duckdb parquet eda", "eda.py")
        if not (DATA / "laws_flat.parquet").exists():
            raise RuntimeError("parquet 未產出")

        # 4) 新版工件＋原始 zip 備份
        (RAW / "latest.zip").write_bytes(body)
        backup_version(stamp_new, [DATA / p for p in VALID_BACKUP], [RAW / "latest.zip"])
        log.info("已備份新版工件+原始 ZIP → versions/%s/", stamp_new)

        # 5) metadata → PG、向量 → Qdrant（完整重建：上游刪除的點也會移除）
        run_step("metadata→PG pg_load", "pg_load.py")
        run_step("向量→Qdrant qdrant_load", "qdrant_load.py")

        state["applied_at"] = datetime.now().isoformat(timespec="seconds")
        save_state(state)

        # 6) 有界化快照數量。**必須在成功之後**（見 prune_versions 的說明）。
        # protect = 當前版本 + 前一版：那兩個是回退路徑，永遠不刪。
        # 整段包 try：資料已經套用完成了，為了清目錄而讓這次同步回報失敗是错的。
        try:
            removed = prune_versions(KEEP_VERSIONS, {stamp_new, old_stamp})
            if removed:
                log.info("已清理舊版快照（保留最近 %d 份）→ 刪除 %s",
                         KEEP_VERSIONS, " ".join(removed))
            else:
                log.info("舊版快照無需清理（保留最近 %d 份）", KEEP_VERSIONS)
        except Exception as e:                      # noqa: BLE001 — 見上，不可讓它使同步失敗
            log.warning("清理舊版快照時發生未預期錯誤（不影響本次套用結果）：%s", e)

        log.info("完成：新版已套用（versions/%s/），下次檢查由 crontab 每日執行。", stamp_new)
        return 0


def extract_chlaw(body: bytes) -> bytes:
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        names = z.namelist()
        hit = next(n for n in names if n.endswith("ChLaw.json"))
        return z.read(hit)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # 完整性優先：任何失敗都中止且不更動，留下 log
        log.error("同步中止（未更動資料）：%s", e)
        sys.exit(1)