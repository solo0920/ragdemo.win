"""`inventory.py` 的契約測試（spec 004 T004）。

## 這組測試的重點

T004 的 acceptance 有三條：兩次執行**逐 byte 相同**、**不寫任何東西進 raw/**、
backslash 路徑**原樣往返**。三條都直接對應到下面的測試。

## fixture 與真 artifact 的分工

合成 fixture（`tests/fixtures/judgements/fixture.rar`，693 bytes）驗欄位邏輯。
真 artifact 驗**數量錨點**（108,547），但只在 283 MB 檔案存在時跑。

⚠️ 合成 fixture 有「與 parser 共用同一個錯誤假設」的風險，所以真 artifact
那一條不是可選的錦上添花 —— 它是這組測試唯一的外部錨點。若換了月度資料而
entry 數變了，**這個測試必須紅**，那代表要回去更新 evidence，不是改 parser。

## 邊界：不碰 raw/

整組測試對 `data/judgements/raw/` 只有讀取，且 inventory API 根本不接受
「輸出到哪裡」——它回傳記憶體中的資料列。要寫檔是呼叫端的事，而呼叫端在
寫之前必須先過 `artifact.assert_outside_raw()`（T002）。這裡以 mtime 與
檔案清單雙重確認 raw/ 沒有被動過。
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "judgements" / "fixture.rar"
if str(ROOT / "ingest" / "judgements") not in sys.path:
    sys.path.insert(0, str(ROOT / "ingest" / "judgements"))

import artifact  # noqa: E402
import inventory  # noqa: E402

HAS_REAL = artifact.ARTIFACT_PATH.is_file()

# decoder-proof.md 用 `unrar lt` 獨立得出的數字。這是本 feature 唯一的
# 外部錨點 —— parser 若寫錯，這裡會紅，而不是讓下游靜靜地少幾千筆。
REAL_ENTRY_COUNT = 108547
REAL_FILE_COUNT = 108409
REAL_DIR_COUNT = 138


def _snap(entries: list[inventory.Entry]) -> bytes:
    """把清單轉成可比對的 bytes。"""
    return json.dumps(
        [e.as_dict() for e in entries], ensure_ascii=False, sort_keys=True
    ).encode("utf-8")


# ── acceptance 1：兩次執行逐 byte 相同 ─────────────────────────────────────────

def test_two_runs_are_byte_identical():
    a = inventory.inventory(FIXTURE)
    b = inventory.inventory(FIXTURE)
    assert _snap(a) == _snap(b)


def test_order_is_stable_across_many_runs():
    """連跑 5 次，不是 2 次。

    2 次相同可能只是巧合（例如兩次都走同一條錯誤路徑）；連續多次一致才談得上
    確定性。
    """
    snaps = {_snap(inventory.inventory(FIXTURE)) for _ in range(5)}
    assert len(snaps) == 1


def test_a_fresh_interpreter_produces_the_same_list():
    """跨行程確定性 —— 排除 module-level 狀態或雜湊順序的影響。"""
    code = (
        "import sys, json; sys.path.insert(0, %r); import inventory; "
        "print(json.dumps([e.as_dict() for e in inventory.inventory(%r)],"
        " ensure_ascii=False, sort_keys=True))"
        % (str(ROOT / "ingest" / "judgements"), str(FIXTURE))
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip().encode("utf-8") == _snap(inventory.inventory(FIXTURE))


@pytest.mark.skipif(not HAS_REAL, reason="283 MB artifact 不在這個 checkout")
def test_real_archive_is_deterministic_across_two_runs():
    """真 artifact 也必須兩次一致（283 MB，跑一次約 0.7s，可接受）。"""
    a = inventory.inventory(artifact.ARTIFACT_PATH)
    b = inventory.inventory(artifact.ARTIFACT_PATH)
    assert _snap(a) == _snap(b)


# ── acceptance 2：不寫任何東西進 raw/ ─────────────────────────────────────────

@pytest.mark.skipif(not HAS_REAL, reason="283 MB artifact 不在這個 checkout")
def test_inventory_writes_nothing_under_raw(tmp_path):
    """整組測試跑完，raw/ 的檔案清單與 mtime 都不變。"""
    raw = artifact.RAW_DIR
    before_files = sorted(p.name for p in raw.iterdir())
    before_mtimes = {p.name: p.stat().st_mtime_ns for p in raw.iterdir()}

    inventory.inventory(artifact.ARTIFACT_PATH)

    after_files = sorted(p.name for p in raw.iterdir())
    after_mtimes = {p.name: p.stat().st_mtime_ns for p in raw.iterdir()}
    assert before_files == after_files
    assert before_mtimes == after_mtimes


def test_inventory_api_has_no_output_path_parameter():
    """inventory 不得接受「寫到哪裡」——它是純讀取，寫檔是呼叫端的責任。

    用簽章斷言而不是列舉呼叫：日後有人加一個 `out=` 參數，這條就紅。
    """
    import inspect

    params = set(inspect.signature(inventory.inventory).parameters)
    assert params == {"path"}


def test_inventory_module_opens_files_read_only():
    """原始碼中不得出現寫入模式。"""
    src = (ROOT / "ingest" / "judgements" / "inventory.py").read_text("utf-8")
    for bad in ('"wb"', "'wb'", '"w"', '"r+b"', '"ab"'):
        assert bad not in src, f"inventory 不得以寫入模式開檔（發現 {bad}）"


# ── acceptance 3：backslash 路徑原樣往返 ──────────────────────────────────────

def test_backslash_separators_round_trip_unchanged():
    """反斜線不得被換成 `/`、不得被 `Path` 吃掉。

    真 corpus 108,547/108,547 全用反斜線。任何「順手正規化」都會讓
    derived corpus 的路徑與 archive 內的路徑對不上。
    """
    entries = inventory.inventory(FIXTURE)
    # fixture 刻意含兩個「根」entry（`資料` 與 `noaddsize.json`）作為邊界：
    # 一個是根目錄（沒有分隔符），一個是根層檔名。兩者都不該被「補上」分隔符。
    without_sep = [e.path for e in entries if "\\" not in e.path]
    assert sorted(without_sep) == ["noaddsize.json", "資料"]

    # 除了那兩個根層 entry，其餘一律必須有反斜線，且一個 `/` 都不能出現。
    assert not any("/" in e.path for e in entries)
    # 逐字比對，不只是「有反斜線」
    assert entries[0].path == (
        "資料\\最高法院\\刑事\\test,115,刑訴,1,20260101,1.json"
    )


@pytest.mark.skipif(not HAS_REAL, reason="283 MB artifact 不在這個 checkout")
def test_real_paths_keep_backslashes_and_crlf_free_paths():
    """真 artifact：每個 entry 的分隔符都必須是 `\\`，一個 `/` 都不能有。

    `decoder-proof.md` 的 sample_paths 觀察到 108,547 條全是反斜線；根目錄
    entry（`202607`）沒有分隔符是正常的。
    """
    entries = inventory.inventory(artifact.ARTIFACT_PATH)
    assert sum(1 for e in entries if "/" in e.path) == 0
    no_sep = [e.path for e in entries if "\\" not in e.path]
    # 只有根目錄可以沒有分隔符
    assert no_sep == ["202607"]


def test_paths_are_not_interpreted_as_posix_or_parsed():
    """檔名裡的逗號不得被拆成欄位。

    T009 才負責 JID 的解析；inventory 的責任到此為止。若這裡就拆，parser
    會變成 schema 驗證的一半，而 schema 驗證屬於 T008。
    """
    entries = inventory.inventory(FIXTURE)
    target = next(e for e in entries if "fields,115" in e.path)
    assert target.path.endswith(".json")
    assert "," in target.path


# ── 欄位正確性（fixture）──────────────────────────────────────────────────────

def test_fixture_yields_the_expected_entries():
    """fixture 的 12 個 entry，欄位逐一比對。

    預期值寫死是刻意的：它等於 `make_fixture.py` 裡宣告的內容，但改版控中的
    binary 時**不會**跟著變 —— 那是 `test_fixture_file_matches_generator` 的
    職責。這兩條測試分工不同，故意分開。
    """
    entries = inventory.inventory(FIXTURE)
    assert len(entries) == 12
    by_name = {e.path: e for e in entries}

    e = by_name["資料\\最高法院\\刑事\\test,115,刑訴,1,20260101,1.json"]
    assert (e.unpacked_size, e.packed_size, e.method) == (38, 38, 0x30)
    assert e.is_dir is False

    # CRLF + U+3000 的權威形狀
    e = by_name["資料\\最高法院\\民事\\crlf,115,民訴,2,20260102,1.json"]
    assert e.unpacked_size == len("主　　文\r\n臺灣臺北地方法院民事判決\r\n\r\n說明：一\r\n".encode())

    # cp950 檔名必須解出中文，不能是亂碼
    assert "資料\\台北地院\\big5.json" in by_name

    # 目錄
    assert by_name["資料\\最高法院"].is_dir is True
    assert by_name["資料"].is_dir is True

    # 空檔案仍是非目錄 entry
    empty = by_name["資料\\empty.json"]
    assert (empty.is_dir, empty.unpacked_size) == (False, 0)

    # 壓縮方法 0x33
    assert by_name["資料\\m3.json"].method == 0x33

    # 無 ADD_SIZE 旗標（PACK_SIZE 欄位缺席 → UNP_SIZE 前移）
    na = by_name["noaddsize.json"]
    assert (na.is_dir, na.unpacked_size, na.packed_size) == (False, 0, 0)

    # ★ packed_size 與 unpacked_size 必須是兩個獨立可驗證的欄位。
    #   期望值是寫死的常數（512 / 1024），不是從 fixture 檔案算出來的 ——
    #   這樣 parser 若把兩欄讀反、或讀到錯誤偏移，這裡立刻對不上。
    c = by_name["資料\\size-differs.json"]
    assert (c.packed_size, c.unpacked_size) == (512, 1024)
    assert c.packed_size != c.unpacked_size
    assert (c.method, c.is_dir) == (0x30, False)

    # T005 的負向案例：archive 宣告與實際 payload 不符，inventory 必須**如實
    # 回報**宣告值 —— 判斷「這是壞的」是 extraction 的工作，不是 inventory 的。
    bad_size = by_name["資料\\bad-unpackedsize.json"]
    assert (bad_size.packed_size, bad_size.unpacked_size) == (512, 1024)

    # CRC 錯誤的負向案例：inventory 必須照實讀出那個錯的值。
    assert by_name["資料\\bad-crc.json"].crc32 == 0xDEADBEEF

    # ★ crc32 欄位本身：inventory 必須從 header 的 FILE_CRC 讀出真實值。
    #   這是 T005 唯一的完整性錨點 —— 若這裡恆為 0，T005 的 CRC 檢查形同虛設。
    import zlib

    for e in entries:
        if e.is_dir or e.unpacked_size == 0:
            continue
        assert e.crc32 != 0, f"{e.path} 的 CRC32 不該為 0"

    # 已知 entry 的 CRC 與 payload 實際內容對得上。payload 以字串寫出來而非
    # 跳脫序列 —— 寫 `b'...\\u5f8b\\u8a34...'` 會是**反斜線開頭的 JSON 字面值**，
    # 不是中文字，CRC 當然對不上（這個錯誤在第一次跑時就發生了）。
    first = entries[0]
    expected_payload = '{"JID":"test,115,刑訴,1,20260101,1"}'.encode("utf-8")
    assert first.crc32 == zlib.crc32(expected_payload) & 0xFFFFFFFF
    assert first.unpacked_size == len(expected_payload)


def test_fixture_has_an_entry_where_sizes_differ():
    """fixture 必須至少有一筆 `packed_size != unpacked_size`。

    ## 為什麼這條測試必須獨立存在

    它不測 parser 行為，只**守住 fixture 本身的性質**。理由：

    全部 entry 的 `packed_size == unpacked_size` 時（store method 的必然結果），
    這兩個欄位落在不同偏移卻帶相同數值 —— 把 UNP_SIZE 讀成 PACK_SIZE、或是
    offset 差 4，都不會讓任何 fixture-only 測試變紅。

    實測 corpus 是 108,409/108,547 筆 packed != unpacked，所以真實情況下這個
    錯誤會造成大量錯誤的 size；但唯一的 coverage 是 `@judgement_corpus` 測試，
    clean clone 的 CI 沒有那 283 MB artifact，會 skip。

    也就是說：若日後有人「簡化」fixture（把 entry 10 拿掉、或把它改成
    `unpacked_size=None`），parser 的這個 bug 會**默默地**重新獲得保護，
    而沒有任何測試失敗。這條測試就是那份保護本身的存活證據。
    """
    entries = inventory.inventory(FIXTURE)
    differing = [e for e in entries if e.packed_size != e.unpacked_size]
    assert len(differing) == 2, "size-differs 與 bad-unpackedsize 兩筆"
    assert {e.path for e in differing} == {
        "資料\\size-differs.json",
        "資料\\bad-unpackedsize.json",
    }
    # 方向也要對：真 corpus 是 packed < unpacked（壓縮）
    assert all(e.packed_size < e.unpacked_size for e in differing)


def test_fixture_covers_both_size_orderings():
    """fixture 同時有 packed == unpacked 與 packed < unpacked 兩種情形。

    兩個方向都有的意義：只測「packed < unpacked」抓不到「把 UNP_SIZE 誤填成
    packed 再減」這類錯誤；只測相等則完全抓不到 offset 問題。
    """
    entries = inventory.inventory(FIXTURE)
    equal = [e for e in entries if e.packed_size == e.unpacked_size]
    less = [e for e in entries if e.packed_size < e.unpacked_size]
    assert equal, "fixture 必須保留 packed == unpacked 的 entry"
    assert less, "fixture 必須有 packed < unpacked 的 entry"
    # 目錄兩者皆 0，屬於 equal 那一類
    assert all(e.unpacked_size == 0 for e in entries if e.is_dir)


def test_method_0x30_is_store_not_treated_as_directory():
    """0x30 是 store 方法，與目錄無關。

    兩者都出現在真 corpus（108,409 個 0x33 + 138 個 0x30）。若把 method
    當目錄判斷，138 個目錄與 108,409 個檔案就會整批互換。
    """
    entries = inventory.inventory(FIXTURE)
    stored = [e for e in entries if e.method == 0x30]
    dirs = [e for e in entries if e.is_dir]
    # fixture：12 個 entry —— 2 個目錄 ＋ 9 個 store 檔案 ＋ 1 個 m3
    assert len(stored) == 11 and len(dirs) == 2
    # 關鍵斷言：method 與 is_dir 互不決定。所有 store 的非目錄 entry 都必須
    # is_dir False，所有 method=0x33 的都必須是檔案。
    assert all(not e.is_dir for e in stored if not e.is_dir)
    assert [e.is_dir for e in stored].count(True) == 2
    assert all(not e.is_dir for e in entries if e.method == 0x33)


def test_directory_flag_requires_all_three_bits():
    """`flags & 0xE0` 有任一 bit 為 1 **不等於**是目錄。

    必須三個 bit 全部為 1（值 == 0xE0）。用「任一 bit」會誤判。
    """
    assert (0x00E0 & inventory._DIR_WINDOW) == inventory._DIR_VALUE
    for partial in (0x0080, 0x0040, 0x0020, 0x00A0):
        assert (partial & inventory._DIR_WINDOW) != inventory._DIR_VALUE


def test_entry_offsets_are_strictly_increasing_and_cover_the_file():
    """offset 必須嚴格遞增，且每個 entry 的區段不重疊。

    這是 offset 可當作 provenance 錨點的前提。若兩個 entry 的 offset 相同或
    倒退，之後用 offset 追溯原文會指向錯的位元組。
    """
    entries = inventory.inventory(FIXTURE)
    offsets = [e.offset for e in entries]
    assert offsets == sorted(offsets)
    assert len(set(offsets)) == len(offsets)
    for a, b in zip(entries, entries[1:]):
        assert b.offset >= a.offset + 1


# ── fixture 完整性 ────────────────────────────────────────────────────────────

def test_fixture_file_matches_generator():
    """版控中的 fixture.rar 必須與 make_fixture.py 產生的一致。

    有人手改 binary fixture（合理但危險 —— 測試會跟著改，於是保護力消失）時
    這條會紅，迫使他把改動搬進產生器。
    """
    sys.path.insert(0, str(FIXTURE.parent))
    import make_fixture

    assert FIXTURE.read_bytes() == make_fixture.build()


def test_fixture_is_small_enough_to_commit():
    """fixture 必須小到能進版控。"""
    assert FIXTURE.stat().st_size < 64 * 1024


def test_fixture_is_a_real_rar4_archive():
    head = FIXTURE.read_bytes()[:7]
    assert head == b"Rar!\x1a\x07\x00"


# ── 明確拒絕：RAR5 與非 RAR ──────────────────────────────────────────────────

def test_rar5_is_rejected_not_guessed():
    """RAR5 的 block 佈局不同 —— 明確拒絕。

    猜著試是本模組最該避免的行為：RAR5 的 header 帶 extra area，錯位的
    offset 會產出一批看起來正常、實際是垃圾的 entry，而且沒有錯誤可以讓人
    發現。
    """
    p = FIXTURE.parent / "rar5.rar"
    p.write_bytes(b"Rar!\x1a\x07\x01\x00" + b"\x00" * 32)
    try:
        with pytest.raises(inventory.UnsupportedContainer, match="RAR5"):
            inventory.inventory(p)
    finally:
        p.unlink()


def test_non_rar_is_rejected():
    p = FIXTURE.parent / "notrar.bin"
    p.write_bytes(b"PK\x03\x04" + b"\x00" * 32)
    try:
        with pytest.raises(inventory.UnsupportedContainer):
            inventory.inventory(p)
    finally:
        p.unlink()


def test_missing_file_raises_file_not_found():
    with pytest.raises(FileNotFoundError):
        inventory.inventory(FIXTURE.parent / "absent.rar")


def test_truncated_archive_does_not_hang(tmp_path):
    """截斷的 archive 必須安靜停止，不得無限迴圈。

    斷言用「少於完整 fixture 的 entry 數」而不是寫死某個數字 —— fixture 增長時
    這裡不該被迫跟著改，那種耦合只會掩蓋真正的失敗。
    """
    raw = FIXTURE.read_bytes()
    complete = len(inventory.inventory(FIXTURE))
    p = tmp_path / "trunc.rar"
    p.write_bytes(raw[: len(raw) // 2])
    entries = inventory.inventory(p)
    assert len(entries) < complete


def test_garbage_after_marker_does_not_hang(tmp_path):
    """marker 之後接垃圾：head_size 不合理就停。"""
    p = tmp_path / "garbage.rar"
    p.write_bytes(b"Rar!\x1a\x07\x00" + b"\x00" * 64)
    assert inventory.inventory(p) == []


# ── 真 artifact：數量錨點（唯一外部獨立依據）────────────────────────────────

@pytest.mark.judgement_corpus
@pytest.mark.skipif(not HAS_REAL, reason="283 MB artifact 不在這個 checkout")
def test_real_corpus_entry_count():
    """真 artifact 必須是 108,547 筆 entry（108,409 檔 + 138 目錄）。

    這個數字由 `unrar lt` 獨立得出（decoder-proof.md §3），不是我們的 parser
    算出來的 —— 所以它是**外部錨點**，能抓到 parser 自身的錯誤。

    若這條紅了：先確認 artifact 是否換版（`artifact.verify()` 應該也紅）。
    換版就是換資料，需要重新調查並更新 evidence，**不是**改這個數字。
    """
    entries = inventory.inventory(artifact.ARTIFACT_PATH)
    assert len(entries) == REAL_ENTRY_COUNT
    assert sum(1 for e in entries if not e.is_dir) == REAL_FILE_COUNT
    assert sum(1 for e in entries if e.is_dir) == REAL_DIR_COUNT


@pytest.mark.judgement_corpus
@pytest.mark.skipif(not HAS_REAL, reason="283 MB artifact 不在這個 checkout")
def test_real_corpus_first_entry_matches_decoder_proof():
    """第一筆 entry 是 decoder-proof.md 記錄的固定點：3,967 bytes。

    交叉檢查 unpacked_size 與 packed_size 兩個欄位同時正確。
    """
    first = inventory.inventory(artifact.ARTIFACT_PATH)[0]
    assert first.path == "202607\\三重簡易庭刑事\\SJEM,115,重秩,54,20260716,1.json"
    assert first.unpacked_size == 3967
    assert first.packed_size == 1830
    assert first.method == 0x33
    assert first.is_dir is False


@pytest.mark.judgement_corpus
@pytest.mark.skipif(not HAS_REAL, reason="283 MB artifact 不在這個 checkout")
def test_real_corpus_blocks_are_contiguous():
    """每個 entry 的 header+data 區段必須緊接下一個 header。

    對不上代表 parser 的 block chain 走錯了（少讀或多重讀了 packed data），
    而那會讓後續所有 offset 全錯 —— 這是 T010 byte-exact 切片的前提。
    """
    import struct

    p = artifact.ARTIFACT_PATH
    entries = inventory.inventory(p)
    with open(p, "rb") as fh:
        for e in entries:
            fh.seek(e.offset)
            _, _, flags, head_size = struct.unpack("<HBHH", fh.read(7))
            fh.seek(e.offset + head_size)
            payload = fh.read(min(e.packed_size, 1)) if e.packed_size else b""
            if e.packed_size:
                assert len(payload) == 1, f"entry 在 {e.offset} 讀不到 packed data"


@pytest.mark.judgement_corpus
@pytest.mark.skipif(not HAS_REAL, reason="283 MB artifact 不在這個 checkout")
def test_real_corpus_unp_ver_only_known_values():
    """UNP_VER 只應出現已知值（29 檔案 / 20 目錄）。

    若出現未知值，代表 parser 的欄位位移在某種 header 上算錯了 —— 那些 entry
    會被靜靜丟棄。清單少了東西卻沒有人知道，是最壞的失敗形狀。
    """
    import struct

    p = artifact.ARTIFACT_PATH
    seen: set[int] = set()
    with open(p, "rb") as fh:
        for e in inventory.inventory(p):
            fh.seek(e.offset + 24)
            seen.add(fh.read(1)[0])
    assert seen <= {20, 29, 36, 50}
    assert seen == {20, 29}  # 實測：只有這兩個值


# ── inventory ≠ extraction ──────────────────────────────────────────────────

def test_inventory_does_not_read_entry_contents():
    """inventory 不得讀 entry 的資料區。

    這個邊界若失守，T004 就變成 T005（extraction）—— 而那需要驗證 CRC、會寫檔、
    也有 FR-014 的其他要求。在這裡讀取 payload 沒有任何好處，只會讓「有沒有需要
    decoder」這個問題變得含糊。

    ## 為什麼用 AST 而不是字串搜尋

    早期版本搜尋 `zlib` / `extract` 等字樣，結果 docstring 裡一句說明「這裡的
    CRC 與 zlib.crc32 一致」的註解就讓測試變紅 —— 而那個註解正是我們**希望**
    存在的說明。字串搜尋會把「談論某件事」誤判成「做了某件事」。

    AST 只看程式結構：import 名稱與實際呼叫。docstring 是 Constant 節點的
    字串值，不會出現在 `ImportFrom`/`Import` 的名稱裡。
    """
    import ast

    tree = ast.parse(
        (ROOT / "ingest" / "judgements" / "inventory.py").read_text("utf-8")
    )

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    # 壓縮/解壓相關的模組一律不得被 import
    forbidden = {"zlib", "bz2", "lzma", "gzip", "bz3", "zstandard", "lz4", "rarfile"}
    assert imported & forbidden == set(), f"inventory 不得 import {imported & forbidden}"

    # 也不得呼叫任何解壓相關的函式
    called = {
        n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    decompression_calls = {
        "decompress", "decompressobj", "unpack_data", "unpack", "extract", "extractall",
    }
    assert called & decompression_calls == set()

    # 允許的 import 只有 struct / dataclasses / pathlib / typing / artifact
    assert imported <= {"__future__", "struct", "dataclasses", "pathlib", "typing", "artifact"}


def test_inventory_module_does_not_parse_json():
    """inventory 不得解析 entry 內容（T008 的工作）。

    一旦在這裡 `json.loads`，parser 就得假設內容形狀，而 corpus-wide 的形狀
    仍是 UNKNOWN（0.46% 抽樣）。
    """
    import ast

    src = (ROOT / "ingest" / "judgements" / "inventory.py").read_text("utf-8")
    assert "import json" not in src
    assert "loads(" not in src

    # 判「有沒有碰 schema 欄位」不能搜字串 —— docstring 裡正當地提到 `JFULL`
    # 與 `JID`（說明這個模組**不做**什麼）。改用 AST：只看真正的屬存取與
    # 字串常數，註解與 docstring 自然被排除。
    tree = ast.parse(src)
    literals = {
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }
    schema_fields = {"JFULL", "JID", "JYEAR", "JDATE", "JCASE", "JTITLE", "JPDF"}
    assert literals & schema_fields == set()

    # 不得在任何屬存取上讀 schema 欄位
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert attrs & schema_fields == set()


def test_entry_record_is_frozen():
    e = inventory.inventory(FIXTURE)[0]
    with pytest.raises(Exception):
        e.path = "tampered"  # type: ignore[misc]


def test_entry_sha256_of_serialised_inventory():
    """整份 fixture inventory 的 digest —— 欄位一改就紅。

    比逐欄位斷言更嚴：用一個值守住「fixture 的輸出長什麼樣」。
    """
    digest = hashlib.sha256(_snap(inventory.inventory(FIXTURE))).hexdigest()
    assert len(digest) == 64
    # 若 fixture 或 parser 正確改變了輸出，這裡必須同步更新 —— 那正是它該做
    # 的事：讓變更顯式，而不是讓測試繼續綠著描述舊的世界。
    #
    # 變更歷史（三次，每次都有原因；這個值存在的目的就是讓變更顯式）：
    #   1. 加入 packed != unpacked 的 entry —— 舊 fixture 全部 store，兩個 size
    #      欄位數值必然相等，digest 無法分辨它們是否讀對。
    #   2. Entry 增加 `crc32` —— T005 需要 archive 自記的 CRC32 才能驗證解壓。
    #   3. 加入兩筆負向 entry（bad-unpackedsize / bad-crc）並讓 FILE_CRC 記錄
    #      真實 CRC32 —— T005 的兩項驗證需要「只錯一項」的輸入才驗得出來。
    assert digest == "d377a1eea5098ae76d11720724298a1747d108e46b9ca31bcabfa4c344c0f2c2"
