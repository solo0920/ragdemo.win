"""`extract.py` 的契約測試（spec 004 T005）。

## 這組測試的三條紅線

1. **不解壓整個 archive。** 全部測試只針對單一 entry。整批解壓是後續 batch 的事，
   而那需要 108,409 次 CRC 驗證 —— 不是這裡該驗的。
2. **不解讀 JSON。** 回傳的 bytes 必須與 archive 記錄的長度/CRC 完全一致，
   但**不得**被 `json.loads` 過。JSON schema 是 T008；這裡一旦解讀，就等於
   對 0.46% 抽樣建立了一個未經驗證的形狀假設。
3. **不解壓時修改 raw artifact。** 每一條測試都驗 `raw/` 沒被動過。

## fixture 與 decoder

fixture 全部是 store method，所以**不需要任何 decoder binary** —— 這是 CI 能跑
這組測試的前提。真 artifact 的 108,409 筆全是 `0x33`，必須有 decoder；那條路徑
由 `decoder=` 參數注入，並且用 caller-supplied path，不搜尋 PATH。

decoder 相關的測試需要一支 binary。`UNRAR_BIN` 環境變數可指定；沒有就 skip ——
絕不假設它存在（三台機器都沒有，plan.md §0.2）。
"""
from __future__ import annotations

import json
import os
import sys
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "judgements" / "fixture.rar"
if str(ROOT / "ingest" / "judgements") not in sys.path:
    sys.path.insert(0, str(ROOT / "ingest" / "judgements"))

import artifact  # noqa: E402
import extract  # noqa: E402
import inventory  # noqa: E402

HAS_REAL = artifact.ARTIFACT_PATH.is_file()
DECODER = os.environ.get("UNRAR_BIN")
needs_decoder = pytest.mark.skipif(
    not (DECODER and Path(DECODER).is_file()),
    reason="需要 UNRAR_BIN 指向的 decoder binary（這三台機器預設都沒有）",
)

# raw 守衛（`artifact.assert_outside_raw`）丟的是 ArtifactIntegrityError，不是
# ExtractionError —— 它比 extraction 更底層，且 T002 的測試直接斷言這個型別。
# 在這裡重新取用，而不是寫 `extract.ArtifactIntegrityError`：那個屬性只是因為
# extract 剛好 import 了 artifact 才偶然存在，不是 extract 的公開介面。
RAW_GUARD_ERROR = artifact.ArtifactIntegrityError

# fixture 裡的可解壓 entry（store method，內容已知）
OK_ENTRY = "資料\\最高法院\\刑事\\test,115,刑訴,1,20260101,1.json"
OK_PAYLOAD = '{"JID":"test,115,刑訴,1,20260101,1"}'.encode("utf-8")
SIZE_DIFFERS = "資料\\size-differs.json"       # packed 512 / unpacked 1024
BAD_SIZE = "資料\\bad-unpackedsize.json"       # 宣告 1024，實際 512
BAD_CRC = "資料\\bad-crc.json"                 # 長度對，CRC 錯


def _raw_digest() -> str:
    """真 artifact 的 digest；沒有 artifact 時回傳空字串。"""
    if not HAS_REAL:
        return ""
    return artifact.sha256_file(artifact.ARTIFACT_PATH)


# ── 正向：解壓 + 驗證 ─────────────────────────────────────────────────────────

def test_extract_store_entry_returns_verified_bytes(tmp_path):
    """store entry 解出來的 bytes 必須與 archive 記錄一致。"""
    r = extract.extract_one(OK_ENTRY, archive=FIXTURE, dest=tmp_path)
    assert r.data == OK_PAYLOAD
    assert r.size == len(OK_PAYLOAD)
    assert r.crc32 == zlib.crc32(OK_PAYLOAD) & 0xFFFFFFFF
    assert r.crc32 == r.declared_crc32
    assert r.decoder_used == "store"


def test_extracted_file_is_written_to_destination(tmp_path):
    """bytes 必須真的落在 destination 底下，不只在記憶體裡。"""
    r = extract.extract_one(OK_ENTRY, archive=FIXTURE, dest=tmp_path)
    assert r.destination.is_file()
    assert r.destination.read_bytes() == OK_PAYLOAD
    assert tmp_path in r.destination.parents


def test_crc_is_formatted_as_8_hex_digits():
    """CRC 對外表示為 `%08X` —— 與 archive 記錄的表示一致。"""
    d = extract.crc32_hex(b"abc")
    assert len(d) == 8
    assert d == d.upper()
    assert d == f"{zlib.crc32(b'abc') & 0xFFFFFFFF:08X}"


def test_sha256_is_exposed_for_provenance(tmp_path):
    """sha256 是 entry identity 的一部分（T024 golden fixture 用同一組值）。"""
    import hashlib

    r = extract.extract_one(OK_ENTRY, archive=FIXTURE, dest=tmp_path)
    assert r.sha256 == hashlib.sha256(OK_PAYLOAD).hexdigest()


def test_as_dict_does_not_include_payload_by_default(tmp_path):
    """provenance 記錄不含內容 —— 一份 manifest 帶 108,409 筆 bytes 沒有意義。"""
    r = extract.extract_one(OK_ENTRY, archive=FIXTURE, dest=tmp_path)
    assert "data" not in r.as_dict()
    assert r.as_dict(include_data=True)["data"] == OK_PAYLOAD


def test_entry_with_size_difference_is_rejected_not_silently_accepted(tmp_path):
    """packed 512 / unpacked 1024 —— 這是一份**壞掉**的 archive，必須失敗。

    ## 這裡正是 fixture 與 production 語意的交界，值得寫清楚

    `size-differs` 這筆存在的目的，是讓 `PACK_SIZE`(+7) 與 `UNP_SIZE`(+11) 兩個
    size 欄位成為可獨立驗證的東西 —— 沒有它們不等，T004 的 offset regression
    就只有 corpus-gated 測試能抓到，而 clean clone 的 CI 會 skip。

    但在 T005 的語意下，store method 的 packed bytes **就是** content bytes。
    content 只有 512 bytes，而 header 宣告 unpacked 1024 —— 那是 header 在說謊。
    正確反應是失敗，不是「信任 unpacked_size 就好」。

    所以這筆是負向案例。`bad-unpackedsize` 是同一件事的另一個實例（兩筆都
    存在，是為了讓 T004 的 `packed != unpacked` 性質有兩個觀察點）。
    """
    with pytest.raises(extract.VerificationFailure) as ei:
        extract.extract_one(SIZE_DIFFERS, archive=FIXTURE, dest=tmp_path)
    assert "1024" in str(ei.value)


# ── 負向：兩項驗證各自有效 ───────────────────────────────────────────────────

def test_bad_unpacked_size_is_rejected(tmp_path):
    """宣告長度與實際不符 → 必須失敗，不得靜默接受。"""
    with pytest.raises(extract.VerificationFailure) as ei:
        extract.extract_one(BAD_SIZE, archive=FIXTURE, dest=tmp_path)
    assert "512" in str(ei.value) and "1024" in str(ei.value)


def test_bad_crc_is_rejected(tmp_path):
    """長度對但 CRC 不符 → 必須失敗。

    這是「內容被換過」的情境。只驗長度會漏掉它。
    """
    with pytest.raises(extract.VerificationFailure) as ei:
        extract.extract_one(BAD_CRC, archive=FIXTURE, dest=tmp_path)
    assert "CRC32" in str(ei.value)


def test_crc_check_is_not_masked_by_a_matching_length(tmp_path):
    """負向案例的長度刻意是**正確**的。

    否則「長度不符」會先觸發，CRC 那條路徑就從沒被執行過 —— 一個永遠綠著
    但從未真正跑到的檢查，等於沒有檢查。
    """
    entries = {e.path: e for e in inventory.inventory(FIXTURE)}
    bad = entries[BAD_CRC]
    raw = FIXTURE.read_bytes()
    import struct

    _, _, flags, head_size = struct.unpack_from("<HBHH", raw, bad.offset)
    payload = raw[bad.offset + head_size : bad.offset + head_size + bad.packed_size]
    assert len(payload) == bad.unpacked_size  # 長度是對的
    assert zlib.crc32(payload) & 0xFFFFFFFF != bad.crc32  # CRC 是錯的


def test_verification_failure_is_distinct_from_extraction_failure():
    """兩種失敗必須可分辨：「拿不到」vs「拿到了但不可信」。"""
    assert issubclass(extract.VerificationFailure, extract.ExtractionError)
    assert not issubclass(extract.ExtractionError, extract.VerificationFailure)


# ── 目錄 entry ───────────────────────────────────────────────────────────────

def test_directory_entry_is_rejected(tmp_path):
    """目錄不是檔案。目錄 header 會被誤當成檔案解壓，是 138 個 entry 全錯。"""
    with pytest.raises(extract.ExtractionError) as ei:
        extract.extract_one("資料", archive=FIXTURE, dest=tmp_path)
    assert "目錄" in str(ei.value)


def test_directory_rejection_happens_before_any_write(tmp_path):
    """目錄必須在寫任何東西**之前**被拒絕。"""
    before = sorted(p.name for p in tmp_path.iterdir())
    with pytest.raises(extract.ExtractionError):
        extract.extract_one("資料\\最高法院", archive=FIXTURE, dest=tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == before


# ── 邊界：raw/ 不得被寫入 ────────────────────────────────────────────────────

def _fake_raw(tmp_path: Path, monkeypatch) -> Path:
    """把 `artifact.RAW_DIR` 指到一個暫存位置。

    ## 為什麼要 monkeypatch，而不是真的寫進 repo 的 raw/

    這組測試的前提就是「守衛會擋下」；若守衛哪天失效，測試就會**真的**在
    `data/judgements/raw/` 底下建立檔案 —— 一個驗證保護機制的測試，本身成了
    破壞保護機制的工具。把 RAW_DIR 指向 tmp 讓這個風險消失：最壞的情況是
    在 tmp 裡寫出一個檔案。

    守衛在呼叫時才讀 `artifact.RAW_DIR`（而非在 import 時抓成預設參數），
    所以 monkeypatch 有效 —— 這也是 T002 把它做成查詢函式而非常數的原因。
    """
    raw = tmp_path / "data" / "judgements" / "raw"
    raw.mkdir(parents=True)
    monkeypatch.setattr(artifact, "RAW_DIR", raw)
    return raw


def test_destination_under_raw_is_refused(tmp_path, monkeypatch):
    """目的地在 `raw/` 之下 → T002 擋下。"""
    raw = _fake_raw(tmp_path, monkeypatch)
    with pytest.raises(RAW_GUARD_ERROR):
        extract.extract_one(OK_ENTRY, archive=FIXTURE, dest=raw / "work")


def test_dotdot_traversal_into_raw_is_refused(tmp_path, monkeypatch):
    """`../` 繞過也必須被擋。

    只用字串比對的守衛會漏掉這個：字串不以 raw/ 開頭，但它確實寫進去了。
    """
    raw = _fake_raw(tmp_path, monkeypatch)
    sneaky = raw / ".." / "raw" / "work"
    with pytest.raises(RAW_GUARD_ERROR):
        extract.extract_one(OK_ENTRY, archive=FIXTURE, dest=sneaky)


def test_raw_is_not_created_even_when_the_guard_fires(tmp_path, monkeypatch):
    """守衛觸發時連目錄都不該被建立 —— 檢查在 mkdir 之前。

    若順序反了（先 mkdir 再檢查），一次被擋下的解壓仍會在 raw/ 底下留下一個
    空目錄。那看起來無害，但下一個只看「raw/ 底下有沒有檔案」的稽核會漏掉它。
    """
    raw = _fake_raw(tmp_path, monkeypatch)
    target = raw / "newdir"
    with pytest.raises(RAW_GUARD_ERROR):
        extract.extract_one(OK_ENTRY, archive=FIXTURE, dest=target)
    assert not target.exists()


def test_missing_dest_is_an_error_not_a_guess(tmp_path):
    """沒給 dest 必須報錯 —— 解壓要寫檔，位置不該由本模組猜。"""
    with pytest.raises(extract.ExtractionError, match="dest"):
        extract.extract_one(OK_ENTRY, archive=FIXTURE)


# ── 邊界：entry 路徑不得逃出目的地 ───────────────────────────────────────────

def test_entry_path_traversal_is_neutralised(tmp_path):
    """檔名裡的 `..` 不得讓寫出位置逃出目的地。

    unrar 7.13 自己也會清掉 `../`（實測），但那是 decoder 的行為不是契約 ——
    一旦換了 decoder 或版本就可能失效。我們不把安全性外包給第三方 binary。
    """
    rel = extract._relative_output_path("../../escape.json")
    assert ".." not in rel.parts
    assert rel == Path("escape.json")


def test_entry_path_with_only_dots_is_rejected():
    """沒有有效部分的 entry 名必須報錯，不能變成寫到目的地根目錄。"""
    for bad in ("", "..", "../../", "/"):
        with pytest.raises(extract.ExtractionError):
            extract._relative_output_path(bad)


def test_backslash_path_maps_to_nested_dirs(tmp_path):
    """archive 的反斜線路徑 → 目的地的目錄樹（單一檔案解壓，不解壓整樹）。"""
    rel = extract._relative_output_path("a\\b\\c.json")
    assert rel == Path("a/b/c.json")


def test_sibling_dir_with_raw_prefix_is_allowed(tmp_path, monkeypatch):
    """`raw_backup` 不是 `raw` 之下 —— 常見的暫存資料夾名。

    用 `str.startswith()` 的守衛會把它誤判成 raw 的子目錄，然後擋掉一個
    完全合法的解壓目的地。
    """
    raw = _fake_raw(tmp_path, monkeypatch)
    dest = raw.parent / "raw_backup"
    r = extract.extract_one(OK_ENTRY, archive=FIXTURE, dest=dest)
    assert r.data == OK_PAYLOAD
    assert r.destination.is_file()


# ── 邊界：不可解的 entry 必須明確失敗 ───────────────────────────────────────

def test_compressed_entry_without_decoder_is_an_explicit_error(tmp_path):
    """method 0x33 需要 decoder；沒給就明確報錯，不靜默跳過。"""
    with pytest.raises(extract.ExtractionError) as ei:
        extract.extract_one("資料\\m3.json", archive=FIXTURE, dest=tmp_path)
    assert "decoder" in str(ei.value)


def test_missing_entry_path_is_an_error(tmp_path):
    with pytest.raises(extract.ExtractionError, match="不存在"):
        extract.extract_one("資料\\沒有這個.json", archive=FIXTURE, dest=tmp_path)


def test_missing_archive_is_an_error(tmp_path):
    with pytest.raises(extract.ExtractionError, match="不存在"):
        extract.extract_one(OK_ENTRY, archive=tmp_path / "nope.rar", dest=tmp_path)


def test_extract_many_aborts_on_first_failure(tmp_path):
    """任一筆失敗就中止 —— spec 說 "a mismatch aborts the pipeline"。"""
    with pytest.raises(extract.VerificationFailure):
        extract.extract_many(
            [OK_ENTRY, BAD_SIZE, OK_ENTRY], archive=FIXTURE, dest=tmp_path
        )


# ── 邊界：JSON 不得被解讀 ───────────────────────────────────────────────────

def test_extract_does_not_parse_json(tmp_path):
    """extract 模組不得 `json.loads`。

    JSON schema 是 T008 的工作。在這裡解讀就等於對 0.46% 抽樣建立未經驗證的
    形狀假設 —— 而 spec 明確標記 corpus-wide 形狀為 UNKNOWN。
    """
    import ast

    tree = ast.parse(
        (ROOT / "ingest" / "judgements" / "extract.py").read_text("utf-8")
    )
    imported: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            imported.update(a.name.split(".")[0] for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            imported.add(n.module.split(".")[0])
    assert "json" not in imported

    # 也不得出現任何 J* schema 欄位名（docstring 提及不算）
    literals = {
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }
    assert literals & {"JFULL", "JID", "JYEAR", "JDATE", "JCASE", "JTITLE", "JPDF"} == set()


def test_returned_bytes_are_the_raw_payload_not_parsed_structure(tmp_path):
    """回傳的是 bytes，不是 dict —— 結構化處理留給後面。"""
    r = extract.extract_one(OK_ENTRY, archive=FIXTURE, dest=tmp_path)
    assert isinstance(r.data, bytes)
    # 它**恰好**是合法 JSON，但那是 fixture 的性質，不是 extract 的契約
    assert json.loads(r.data)["JID"] == "test,115,刑訴,1,20260101,1"


def test_crlf_and_fullwidth_space_survive_extraction(tmp_path):
    """CRLF 與 U+3000 必須逐 byte 保留。

    這是權威文字的形狀（FR-010）。任何 strip/normalize 在這一層就會摧毀它，
    而且之後無法從 derived data 復原。
    """
    path = "資料\\最高法院\\民事\\crlf,115,民訴,2,20260102,1.json"
    payload = "主　　文\r\n臺灣臺北地方法院民事判決\r\n\r\n說明：一\r\n".encode("utf-8")
    r = extract.extract_one(path, archive=FIXTURE, dest=tmp_path)
    assert r.data == payload
    assert r.data.count(b"\r\n") == 4
    assert r.data.count(b"\n") == 4  # 沒有裸 LF
    assert "　" in r.data.decode("utf-8")


def test_cp950_filename_is_extracted_under_decoded_name(tmp_path):
    """cp950 檔名的 entry 仍可解壓（inventory 階段已解碼成名稱）。"""
    r = extract.extract_one("資料\\台北地院\\big5.json", archive=FIXTURE, dest=tmp_path)
    assert r.data == b"{}"
    assert r.destination.is_file()


# ── raw artifact 不得被修改 ────────────────────────────────────────────────

@pytest.mark.skipif(not HAS_REAL, reason="283 MB artifact 不在這個 checkout")
def test_real_artifact_digest_unchanged_after_extraction(tmp_path):
    """解壓之後 raw artifact 的 digest 必須**完全**不變。

    這是 T005 acceptance 的一條，也是 INV-SRC 的實際執行。
    """
    before = _raw_digest()
    entry = "202607\\三重簡易庭刑事\\SJEM,115,重秩,54,20260716,1.json"
    with pytest.raises(extract.ExtractionError):
        # 真 archive 的 entry 是 0x33，沒有 decoder 時應該明確失敗 —— 但
        # **無論成功或失敗**，digest 都必須不變。
        extract.extract_one(entry, archive=artifact.ARTIFACT_PATH, dest=tmp_path)
    assert _raw_digest() == before


@pytest.mark.skipif(not HAS_REAL, reason="283 MB artifact 不在這個 checkout")
def test_real_artifact_size_unchanged_after_extraction(tmp_path):
    before = artifact.ARTIFACT_PATH.stat().st_size
    try:
        extract.extract_one(
            "202607\\三重簡易庭刑事\\SJEM,115,重秩,54,20260716,1.json",
            archive=artifact.ARTIFACT_PATH,
            dest=tmp_path,
        )
    except extract.ExtractionError:
        pass
    assert artifact.ARTIFACT_PATH.stat().st_size == before


def test_extraction_never_opens_archive_for_writing():
    """原始碼不得出現寫入模式。"""
    src = (ROOT / "ingest" / "judgements" / "extract.py").read_text("utf-8")
    for bad in ('"wb"', "'wb'", '"r+b"', '"ab"', "unlink", "rmtree"):
        assert bad not in src, f"extract 不得以寫入模式開檔或刪除檔案（發現 {bad}）"


def test_extraction_never_writes_into_raw():
    """extract 不得直接呼叫任何寫入 raw/ 的路徑 —— 一律經過 assert_outside_raw。"""
    src = (ROOT / "ingest" / "judgements" / "extract.py").read_text("utf-8")
    assert "_artifact.assert_outside_raw" in src
    # 且不得繞過它直接 mkdir 在 raw 下
    assert "RAW_DIR" not in src


# ── 需要真 decoder 的路徑 ───────────────────────────────────────────────────

@needs_decoder
def test_fixture_0x33_entry_cannot_be_decoded_and_says_so(tmp_path):
    """fixture 的 0x33 entry **無法**解壓，且必須明確失敗。

    ## 為什麼 fixture 沒有一筆「真的壓縮過」的 entry

    產生 RAR3 壓縮流需要 RAR 的編碼器，而這個 repo 的三台機器都沒有（而且
    `rar` 的授權不允許隨意散布）。fixture 是自己逐 byte 組出來的 store-method
    archive，所以它的 0x33 entry 宣告了 method 0x33，payload 卻不是合法的
    RAR3 壓縮流。

    真 unrar 對它回報 CRC error（exit 3）—— 這是**正確**行為，也正是我們想
    看到的：不合法輸入被明確拒絕，而不是解出垃圾然後看起來成功。

    「0x33 真的能解壓」這條路徑由 `test_real_corpus_known_good_entry` 驗證 ——
    真 artifact 的 108,409 筆全是真正的 -m3 資料。fixture 做不到這件事，
    這一點寫在這裡是為了讓後人不會誤以為缺了測試。
    """
    with pytest.raises(extract.VerificationFailure) as ei:
        extract.extract_one(
            "資料\\m3.json", archive=FIXTURE, dest=tmp_path, decoder=Path(DECODER)
        )
    assert "CRC error" in str(ei.value) or "CRC32" in str(ei.value)


@needs_decoder
def test_decoder_does_not_search_path(tmp_path, monkeypatch):
    """不給 decoder 路徑就明確失敗，不去 PATH 裡找。"""
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(extract.ExtractionError, match="decoder"):
        extract.extract_one("資料\\m3.json", archive=FIXTURE, dest=tmp_path)


def test_absent_decoder_binary_is_an_explicit_error(tmp_path):
    """指向不存在的 binary → 明確錯誤，不是 KeyError/FileNotFound 洩漏。"""
    with pytest.raises(extract.ExtractionError, match="decoder binary"):
        extract.extract_one(
            "資料\\m3.json",
            archive=FIXTURE,
            dest=tmp_path,
            decoder=tmp_path / "not-installed",
        )


@pytest.mark.judgement_corpus
@needs_decoder
@pytest.mark.skipif(not HAS_REAL, reason="283 MB artifact 不在這個 checkout")
def test_real_corpus_known_good_entry(tmp_path):
    """tasks.md 指定的固定點：3,967 bytes，SHA-256 `65cd7d32…`。

    這是 T005 對真 archive 的唯一斷言，也是 corpus-schema-survey.md 記錄的
    已驗證值。若這裡紅了，代表 decoder 或 extraction 路徑出了問題 —— 絕不是
    資料變了（那會先讓 `artifact.verify()` 紅）。
    """
    entry = "202607\\三重簡易庭刑事\\SJEM,115,重秩,54,20260716,1.json"
    r = extract.extract_one(
        entry, archive=artifact.ARTIFACT_PATH, dest=tmp_path, decoder=Path(DECODER)
    )
    assert r.size == 3967
    assert r.sha256 == (
        "65cd7d32c13550adf8a36d7db2885266c295d12d18743f6e2cc8ed170755531a"
    )
    assert r.crc32 == r.declared_crc32


@pytest.mark.judgement_corpus
@pytest.mark.skipif(not HAS_REAL, reason="283 MB artifact 不在這個 checkout")
def test_real_corpus_crc_is_the_archive_recorded_value(tmp_path):
    """第一筆 entry 的 CRC32 必須是 header 記錄的 CD7072B9。

    這個值已獨立驗證過：解壓後的 bytes 重算 CRC32 也是 CD7072B9（decoder-proof
    的調查當時做過同樣的比對）。
    """
    e = inventory.inventory(artifact.ARTIFACT_PATH)[0]
    assert e.crc32 == 0xCD7072B9
    assert e.unpacked_size == 3967
    assert e.packed_size == 1830
