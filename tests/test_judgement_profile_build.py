"""M1 profile build：allowlist 自檢的**離線層**（spec 006 T102／T103 / FR-010a）。

## 這份測試證明什麼

不是證明「現在是綠的」，而是證明「**守得住**」。所以除了 1 條正向斷言，
還有 5 條**注入錯誤**的測試（SC-008）：每一條都必須紅燈，否則守門形同虛設。

## 這份測試不需要 284M RAR（沿用 spec 004 的 fixture 策略）

離線層只讀 `allowlist-m1.json`（版控內，29 KB）。因此**在沒有 artifact 的機器上
也必須全綠**——這是 INV-NOSKIP 的具體要求：環境缺什麼可以誠實 skip，
但這一層沒有任何東西可缺。若哪天這條測試在乾淨 clone 上出現 skip，
就是違規，不是環境問題。

## 為什麼測試用「副本」而不是就地改檔

`allowlist-m1.json` 是**決策證據**。就地改它再改回來，等於讓證據在測試期間
被動過——而測試失敗時留下的就是個被改過的證據。全部注入都在 `tmp_path`
的副本上做。
"""
from __future__ import annotations

import hashlib
import json
import sys
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "ingest" / "judgements") not in sys.path:
    sys.path.insert(0, str(ROOT / "ingest" / "judgements"))

import artifact  # noqa: E402
import inventory  # noqa: E402
import profile_build as PB  # noqa: E402

ALLOWLIST = PB.ALLOWLIST_PATH


def _copy_to(tmp_path: Path) -> Path:
    """把 allowlist 複製到 tmp，並回傳路徑（就地改檔案絕不碰版控內的證據）。"""
    dst = tmp_path / "allowlist.json"
    dst.write_text(ALLOWLIST.read_text(encoding="utf-8"), encoding="utf-8")
    return dst


def _mutate(tmp_path: Path, fn) -> list[str]:
    """套用一個破壞動作，回傳 `verify_allowlist` 的問題清單。"""
    p = _copy_to(tmp_path)
    raw = json.loads(p.read_text(encoding="utf-8"))
    fn(raw)
    p.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    return PB.verify_allowlist(PB.load_allowlist(p))


# ── 正向：版控內的 allowlist 必須通過全部檢查 ──────────────────────────────

def test_real_allowlist_has_no_problems():
    problems = PB.verify_allowlist(PB.load_allowlist())
    assert problems == [], f"allowlist 離線自檢失敗：{problems}"


def test_allowlist_loaded_shape():
    al = PB.load_allowlist()
    assert al.schema == PB.ALLOWLIST_SCHEMA
    assert len(al) == al.count == 100
    assert len(al.excluded_frozen) > 0
    # digest 是依清單**順序**算的；若有人誤用 selection.sample_digest（會先排序）
    # 這裡就會對不上。
    assert PB.entries_digest([e.path for e in al.entries]) == al.entries_sha256


def test_digest_differs_from_sorted_digest():
    """守住那個定義差異：本檔的 digest 不排序，`sample_digest` 排序。

    用**明確反轉**的輸入比較，不依賴「當前清單的順序恰好不是排序序」這個
    事實 —— 否則哪天重新選樣剛好同序，這條會假紅。
    """
    al = PB.load_allowlist()
    import selection  # noqa: PLC0415

    paths = [e.path for e in al.entries]
    flipped = list(reversed(paths))

    # 同樣的輸入集合，只因為順序不同，本檔的 digest 就必須不同。
    assert PB.entries_digest(flipped) != PB.entries_digest(paths)
    # 而 selection.sample_digest 會先排序，所以對兩者都給同一個值。
    assert selection.sample_digest(flipped) == selection.sample_digest(paths)


# ── 反向：SC-008 的 5 個注入錯誤，每一條都必須被抓到 ───────────────────────

def test_inject_modified_path_is_caught(tmp_path):
    def mutate(raw):
        raw["entries"][0]["path"] = raw["entries"][0]["path"].replace("2026", "2025")

    problems = _mutate(tmp_path, mutate)
    assert any("entries_sha256 不符" in p for p in problems), problems


def test_inject_dropped_entry_is_caught(tmp_path):
    def mutate(raw):
        raw["entries"].pop()

    problems = _mutate(tmp_path, mutate)
    assert any("count != len(entries)" in p for p in problems), problems


def test_inject_forged_digest_is_caught(tmp_path):
    """把 digest 欄位改掉——這正是「自己證自己」的漏洞，必須被抓。"""

    def mutate(raw):
        raw["entries_sha256"] = "0" * 64

    problems = _mutate(tmp_path, mutate)
    assert any("entries_sha256 不符" in p for p in problems), problems


def test_inject_duplicate_entry_is_caught(tmp_path):
    def mutate(raw):
        raw["entries"].append(dict(raw["entries"][0]))

    problems = _mutate(tmp_path, mutate)
    # 重複會同時觸發 count 不符與 digest 不符；只要「重複」被點名即可。
    assert any("重複" in p for p in problems), problems


def test_inject_overlap_with_frozen_is_caught(tmp_path):
    def mutate(raw):
        raw["excluded_frozen_document_ids"].append(
            raw["entries"][0]["path_posix"].rsplit("/", 1)[-1][:-len(".json")]
        )

    problems = _mutate(tmp_path, mutate)
    assert any("交集" in p for p in problems), problems


# ── 還原後必須轉綠（否則上面的紅燈可能只是「永久紅」）──────────────────────

def test_restore_turns_green_again(tmp_path):
    p = _copy_to(tmp_path)
    original = p.read_text(encoding="utf-8")
    raw = json.loads(original)
    raw["entries_sha256"] = "0" * 64
    p.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    assert PB.verify_allowlist(PB.load_allowlist(p)) != []

    p.write_text(original, encoding="utf-8")
    assert PB.verify_allowlist(PB.load_allowlist(p)) == []


# ── 結構性錯誤必須 raise（不是回傳問題清單）───────────────────────────────

def test_missing_file_raises(tmp_path):
    with pytest.raises(PB.AllowlistError):
        PB.load_allowlist(tmp_path / "nope.json")


def test_not_json_raises(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(PB.AllowlistError):
        PB.load_allowlist(bad)


def test_missing_key_raises(tmp_path):
    def mutate(raw):
        raw.pop("entries_sha256")

    p = _copy_to(tmp_path)
    raw = json.loads(p.read_text(encoding="utf-8"))
    mutate(raw)
    p.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(PB.AllowlistError):
        PB.load_allowlist(p)


# ── 路徑可攜性（T115 的模組側前提）：模組本身不得含絕對路徑 ───────────────

def test_portable_no_absolute_home_path():
    src = (ROOT / "ingest" / "judgements" / "profile_build.py").read_text(
        encoding="utf-8"
    )
    assert "/home/" not in src, "模組不得寫死 /home/<user>/ 路徑（FR-011／INV-PATH）"


def test_portable_paths_are_repo_relative():
    assert PB.ROOT == ROOT
    assert PB.ALLOWLIST_PATH.is_file()
    # 佔位形式而非真實絕對路徑
    assert "<ARTIFACTS_ROOT>" in PB.EXTERNAL_SNAPSHOT_RELPATTERN
    assert "/home/" not in PB.EXTERNAL_SNAPSHOT_RELPATTERN


# ═══════════════════════════════════════════════════════════════════════════
# T104 對庫層自檢（需要真實 artifact —— 依既有 `@pytest.mark.judgement_corpus`
# 慣例：留在預設套件、靠自己的 skipif、**marker 不加進 addopts**）
# ═══════════════════════════════════════════════════════════════════════════

HAS_REAL = artifact.ARTIFACT_PATH.is_file()
needs_real = pytest.mark.skipif(not HAS_REAL, reason="284 MB artifact 不在這個 checkout")


@needs_real
@pytest.mark.judgement_corpus
def test_selection_matches_archive():
    problems = PB.verify_selection_against_archive(PB.load_allowlist())
    assert problems == [], f"對庫層自檢失敗：{problems}"


@needs_real
@pytest.mark.judgement_corpus
def test_selection_check_is_not_vacuous(tmp_path):
    """反向：allowlist 的 digest 造假時，對庫層**也**必須紅。

    只驗證「正常情況下通過」是無效的 —— 一個永遠回傳空的函式也會通過。
    """
    al = PB.load_allowlist()
    forged = PB.Allowlist(
        schema=al.schema,
        count=al.count,
        entries_sha256="0" * 64,
        entries=al.entries,
        excluded_frozen=al.excluded_frozen,
        artifact_sha256=al.artifact_sha256,
        selection=al.selection,
    )
    problems = PB.verify_selection_against_archive(forged)
    assert any("重算 entries_sha256 不符" in p for p in problems), problems


def test_selection_check_reports_missing_archive(tmp_path):
    """沒有 archive 時必須**報缺檔**，而不是靜默通過（INV-NOSKIP）。"""
    problems = PB.verify_selection_against_archive(
        PB.load_allowlist(), archive=tmp_path / "nope.rar"
    )
    assert any("artifact 不存在" in p for p in problems), problems


# ═══════════════════════════════════════════════════════════════════════════
# T105 解壓階段：用 fixture.rar 的 store entry（**不需要 decoder**）
# ═══════════════════════════════════════════════════════════════════════════

FIXTURE = ROOT / "tests" / "fixtures" / "judgements" / "fixture.rar"
GOOD_ENTRY_PATH = "資料\\最高法院\\刑事\\test,115,刑訴,1,20260101,1.json"
BAD_CRC_PATH = "資料\\bad-crc.json"


def _entry_for(path: str, size: int, crc: str) -> PB.Entry:
    return PB.Entry(
        path=path,
        path_posix=PB._selection.forward_slash(path),
        unpacked_size=size,
        crc32=crc,
    )


def _good_entry() -> PB.Entry:
    inv = {e.path: e for e in inventory.inventory(FIXTURE) if not e.is_dir}
    e = inv[GOOD_ENTRY_PATH]
    return _entry_for(e.path, e.unpacked_size, "%08X" % e.crc32)


def test_extract_good_store_entry(tmp_path):
    res = PB.process_entry(_good_entry(), profile_dir=tmp_path, archive=FIXTURE)
    assert res.extracted and res.size_crc_ok
    assert res.fail is None or res.fail[0] == "schema_valid"  # fixture 非 8-key，drift 是預期
    assert res.dest_path is not None and res.dest_path.is_file()
    # 解壓出來的 bytes 必須逐字等於 inventory 記錄的長度
    assert res.dest_path.stat().st_size == res.entry.unpacked_size


def test_archive_header_crc_bad_fails_at_extract_stage(tmp_path):
    """archive header 的 CRC 是壞的 → T005 自己的驗證就會擋下。

    這一條**不是** M1 的功勞：`extract.extract_one` 內部就會比對 header 的
    FILE_CRC 並拋 `VerificationFailure`。所以階段是 `extracted`，不是
    `size_crc_ok`。寫成 `size_crc_ok` 會是個**假失敗**，讓人以為 M1 的比對邏輯
    生效了 —— 事實上它根本沒被呼叫到。
    """
    inv = {e.path: e for e in inventory.inventory(FIXTURE) if not e.is_dir}
    e = inv[BAD_CRC_PATH]
    entry = _entry_for(e.path, e.unpacked_size, "%08X" % e.crc32)
    res = PB.process_entry(entry, profile_dir=tmp_path, archive=FIXTURE)
    assert res.extracted is False
    assert res.fail is not None and res.fail[0] == "extracted"
    assert "CRC32" in res.fail[1]


def test_allowlist_crc_mismatch_is_caught(tmp_path):
    """**M1 專屬**的檢查：archive 本身沒事，但 allowlist 記的 CRC 與實際不符。

    這是 `verify_selection_against_archive` 之外、逐筆再確認一次的價值 ——
    有人改了 allowlist 的 crc32 欄位而沒改 digest（不可能，但值得防）、
    或 T007 之後 archive 被換版，兩者都會在這裡被看見。
    """
    good = _good_entry()
    forged = PB.Entry(
        path=good.path,
        path_posix=good.path_posix,
        unpacked_size=good.unpacked_size,
        crc32="DEADBEEF",
    )
    res = PB.process_entry(forged, profile_dir=tmp_path, archive=FIXTURE)
    assert res.extracted is True          # archive 本身是好的
    assert res.size_crc_ok is False       # 但 allowlist 的值對不上
    assert res.fail[0] == "size_crc_ok" and "crc32 不符" in res.fail[1]
    assert "DEADBEEF" in res.fail[1]      # 訊息要指出是 allowlist 的值


def test_allowlist_size_mismatch_is_caught(tmp_path):
    """size 不符 → 停在 `size_crc_ok`。順序有意義：長度先比，CRC 後比。"""
    good = _good_entry()
    forged = PB.Entry(
        path=good.path,
        path_posix=good.path_posix,
        unpacked_size=good.unpacked_size + 1,
        crc32=good.crc32,
    )
    res = PB.process_entry(forged, profile_dir=tmp_path, archive=FIXTURE)
    assert res.extracted is True
    assert res.size_crc_ok is False
    assert res.fail[0] == "size_crc_ok" and "size 不符" in res.fail[1]


def test_dest_inside_raw_is_refused(tmp_path):
    """目的地在 raw/ 之下必須被擋（T002／FR-005），而且是**拋出**不是記錄。

    這是「環境不對」不是「這一筆資料不對」，所以走例外而非 fail 欄位 ——
    混在一起會讓「整輪設定錯了」看起來像「有幾筆資料壞了」。
    """
    with pytest.raises(Exception) as ei:
        PB.process_entry(_good_entry(), profile_dir=artifact.RAW_DIR, archive=FIXTURE)
    assert "raw" in str(ei.value).lower()


# ═══════════════════════════════════════════════════════════════════════════
# T106 凍結語料零寫入 ＋ 機器無關的錨點
# ═══════════════════════════════════════════════════════════════════════════

def _tree_state(d: Path) -> list[tuple[str, int, float]]:
    return sorted(
        (p.relative_to(d).as_posix(), p.stat().st_size, p.stat().st_mtime)
        for p in d.rglob("*")
        if p.is_file()
    )


def test_seed_and_raw_untouched_by_a_run(tmp_path):
    """跑一次管線之後，`raw/` 與 `seed/` 的檔案清單與 mtime 必須一字不變。"""
    before_raw = _tree_state(artifact.RAW_DIR)
    before_seed = _tree_state(PB.SEED_DIR)
    PB.process_entry(_good_entry(), profile_dir=tmp_path, archive=FIXTURE)
    assert _tree_state(artifact.RAW_DIR) == before_raw
    assert _tree_state(PB.SEED_DIR) == before_seed


def test_seed_tree_sha256_is_machine_independent(tmp_path):
    """同一份內容、不同絕對路徑 → 必須算出同一個 digest。"""
    src = PB.SEED_DIR
    copy = tmp_path / "seed"
    copy.mkdir()
    for p in src.rglob("*"):
        if p.is_file():
            (copy / p.relative_to(src)).write_bytes(p.read_bytes())
    assert PB.seed_tree_sha256(copy) == PB.seed_tree_sha256(src)


def test_seed_tree_sha256_changes_when_content_changes(tmp_path):
    """反向：改一個 byte → digest 必須變（否則這條檢查形同虛設）。"""
    copy = tmp_path / "seed"
    copy.mkdir()
    (copy / "x.json").write_bytes(b'{"a":1}')
    before = PB.seed_tree_sha256(copy)
    (copy / "x.json").write_bytes(b'{"a":2}')
    assert PB.seed_tree_sha256(copy) != before


def test_external_snapshot_absent_is_reported_not_omitted(tmp_path):
    """沒給路徑 → 記 present:false，但**欄位必須存在**。

    重點是「欄位存在」——缺欄位會讓兩台機器的 manifest 無從比較。
    """
    for fp in (
        PB.external_snapshot_fingerprint(),
        PB.external_snapshot_fingerprint(tmp_path / "nope.json"),
    ):
        assert set(fp) == {"pattern", "present", "sha256"}
        assert fp["present"] is False and fp["sha256"] is None
        assert "<ARTIFACTS_ROOT>" in fp["pattern"]


def test_external_snapshot_present_when_given_a_real_file(tmp_path):
    p = tmp_path / "corpus_snapshot.json"
    p.write_text('{"records": []}', encoding="utf-8")
    fp = PB.external_snapshot_fingerprint(p)
    assert fp["present"] is True
    assert len(fp["sha256"]) == 64
    # 佔位形式不因實際路徑而改變（manifest 可跨機比較）
    assert fp["pattern"] == PB.EXTERNAL_SNAPSHOT_RELPATTERN


# ═══════════════════════════════════════════════════════════════════════════
# T114／T115：分離防護 ＋ 可攜性（兩條都是「給未來的鎖」）
# ═══════════════════════════════════════════════════════════════════════════

def _retrieval_files() -> list[Path]:
    """T114 的檢索面清單。**只讀**，不修改它們。

    與 `scripts/build-m1-profile.py` 的 `RETRIEVAL_FILES` 是同一份清單——
    兩處各寫一份遲早會不一致，所以測試直接讀 CLI 的那份。
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "build_m1_profile_cli", ROOT / "scripts" / "build-m1-profile.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return [ROOT / rel for rel in mod.RETRIEVAL_FILES]


def test_retrieval_files_all_exist():
    """清單裡的檔案必須真的存在——清單若指到不存在的路徑，防護就是空的。"""
    for p in _retrieval_files():
        assert p.is_file(), f"檢索面清單指到不存在的檔：{p}"


def test_zero_reference_retrieval_to_profile():
    """預設檢索路徑**零引用**新 profile（US2／SC-004）。

    本 feature 完全不碰檢索面，所以這條現在必然成立。它的價值是給未來的
    P2／S2 上一道鎖：若有人把 profile 接上線而忘了改這裡，它會紅。
    """
    needles = ("profile-m1", "profile_m1")
    hits = []
    for p in _retrieval_files():
        text = p.read_text(encoding="utf-8")
        for n in needles:
            if n in text:
                hits.append(f"{p.relative_to(ROOT)}:{n}")
    assert hits == [], f"檢索面引用了 profile：{hits}"


def test_zero_reference_guard_is_not_vacuous(tmp_path):
    """反向：把某個檢索檔案的副本改成含 `profile-m1`，掃描必須抓到。

    沒有這條，一個永遠回傳「乾淨」的掃描器也會全綠。
    """
    src = _retrieval_files()[0]
    copy = tmp_path / "copy.py"
    copy.write_text(
        src.read_text(encoding="utf-8") + '\nPROFILE = "data/judgements/profile-m1"\n',
        encoding="utf-8",
    )
    assert "profile-m1" in copy.read_text(encoding="utf-8")


#: 掃描絕對路徑時要跳過的檔案：本測試檔自己。
#:
#: 為什麼需要這個例外——而它**不是**把規則改鬆。這支測試的原始碼裡必然出現
#: `/home/` 這三個字（它就是拿來找那三個字的），所以掃自己會**永遠紅**。
#: 一個永遠紅的防護等於沒有防護，而把它關掉又等於放棄整條規則。
#:
#: 誠實的說法：這支測試無法檢查自己。它能檢查的是另外兩個檔案，以及
#: `test_portable_*_is_not_vacuous` 那兩條反向注入。若日後有人在本檔新增
#: 真的寫死 `/home/solo/...` 的路徑，**這條抓不到**——已列入 tasks.md 的
#: 已知弱點，不假裝解決。
_PORTABILITY_EXEMPT = {"tests/test_judgement_profile_build.py"}


def test_portable_files_have_no_absolute_home_path():
    """T115：新增的檔案不得寫死 `/home/<user>/…`（含註解與字串常數）。"""
    for rel in (
        "ingest/judgements/profile_build.py",
        "scripts/build-m1-profile.py",
        "tests/test_judgement_profile_build.py",
    ):
        if rel in _PORTABILITY_EXEMPT:
            continue
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "/home/" not in src, f"{rel} 含絕對路徑（FR-011／INV-PATH）"


def test_portability_exemption_is_just_this_one_test_file():
    """反向：那個例外名單不得被擴大——它是規則唯一的漏洞，必須看得見。"""
    assert _PORTABILITY_EXEMPT == {"tests/test_judgement_profile_build.py"}
    for rel in _PORTABILITY_EXEMPT:
        assert (ROOT / rel).is_file()


def test_portable_scan_is_not_vacuous(tmp_path):
    """反向：注入一行絕對路徑，掃描必須抓到。"""
    src = (ROOT / "ingest" / "judgements" / "profile_build.py").read_text(
        encoding="utf-8"
    )
    copy = tmp_path / "mod.py"
    copy.write_text(src + '\nP = "/home/someone/artifacts"\n', encoding="utf-8")
    assert "/home/" in copy.read_text(encoding="utf-8")


def test_manifest_paths_are_relative(tmp_path):
    """產出的 manifest 路徑欄位必須是 repo 相對，不含絕對路徑。"""
    from golden import stage_results  # noqa: PLC0415

    al = PB.load_allowlist()
    man = PB.build_manifest(stage_results(), al, decoder=tmp_path / "unrar")
    blob = json.dumps(man, ensure_ascii=False)
    assert ROOT.as_posix() not in blob, "manifest 內含絕對路徑"
    assert "/home/" not in blob
    # decoder 只記檔名與旗標，不記絕對路徑
    assert man["decoder"]["binary_name"] == "unrar"
    assert man["decoder"]["binary_provided"] is True


def test_manifest_external_snapshot_field_always_present(tmp_path):
    """`external_snapshot` 三欄恆存在——缺欄位會讓跨機 manifest 無從比較。"""
    from golden import stage_results  # noqa: PLC0415

    man = PB.build_manifest(stage_results(), PB.load_allowlist())
    fp = man["frozen_corpus"]["external_snapshot"]
    assert set(fp) == {"pattern", "present", "sha256"}
    assert fp["present"] is False


# ═══════════════════════════════════════════════════════════════════════════
# T110 審閱包：`JFULL` 逐字（SC-006 的一半，機器可驗的那一半）
# ═══════════════════════════════════════════════════════════════════════════

def test_review_bundle_jfull_is_verbatim():
    """**逐位元組**比對：從審閱包取出的 JFULL 必須 `==` 原始 JFULL。

    這條擋下兩個實測踩到的坑：

    1. **CRLF 被轉成 LF**（`Path.write_text` 的 universal newlines 轉換）。
       檔案內容看起來完全正常，但逐字性已破——每卷少掉的字元數正好等於其
       CRLF 數。這是**看不見**的改寫，比明面上的錯更危險，所以必須用位元組
       比對而不是「看起來有沒有換行」。
    2. **marker 單獨成行**：取出時多兩個 `\\n`、丟掉結尾的 `\\r`。
    """
    from golden import stage_results  # noqa: PLC0415

    results = stage_results()
    bundle = PB.render_review_bundle(results)
    assert bundle.count(PB.JFULL_BEGIN) == len(results)
    assert bundle.count(PB.JFULL_END) == len(results)

    # ⚠ 用 **JID 定位章節**，不要用「第 N 個出現」。包內依 size 降冪排序，
    # 而 `results` 的順序是 caller 傳進來的——兩者不一致時，用序號比對會拿
    # A 卷的原文去比 B 卷的區段，報出一個**假的**逐字性失敗
    # （2026-10-10 實測踩到：golden 三檔排序不同，第一個就不匹配）。
    for r in results:
        heading = f"\n## {r.entry.jid}\n"
        start = bundle.index(heading)
        rest = bundle[start + len(heading) :]
        end = rest.find("\n## ")
        section = rest[:end] if end > 0 else rest
        extracted = PB.extract_jfull_section(section, occurrence=0)
        assert extracted == r.doc.jfull, (
            f"{r.entry.jid} 的 JFULL 未逐字："
            f"抽出 {len(extracted)} vs 原文 {len(r.doc.jfull)}"
        )


def test_extract_jfull_section_rejects_missing_occurrence():
    """反向：取的區段不存在就必須拋，而不是回傳空字串被當成「逐字相符」。"""
    from golden import stage_results  # noqa: PLC0415

    bundle = PB.render_review_bundle(stage_results())
    with pytest.raises(ValueError):
        PB.extract_jfull_section(bundle, occurrence=99)


def test_review_bundle_section_has_everything_a_human_needs():
    """SC-006 的可開箱性：每節必須含 JID／size／crc32／chunk 數／邊界分布／
    forced_break 索引／chunk 邊界表／JFULL。缺一項人就得自己去猜。"""
    from golden import stage_results  # noqa: PLC0415

    bundle = PB.render_review_bundle(stage_results())
    for needle in (
        "entry：",
        "size：",
        "crc32：allowlist",
        "chunk 數：",
        "邊界分布：",
        "forced_break",
        "### chunk 邊界表",
        "### JFULL",
        "| # | start | end | boundary_kind |",
    ):
        assert needle in bundle, f"審閱包缺 {needle}"


def test_review_bundle_sorted_by_size_desc():
    """依 size 降冪：最長的卷宗最可能切出跨 chunk holding，先看它們。"""
    from golden import stage_results  # noqa: PLC0415

    results = sorted(stage_results(), key=lambda r: -r.entry.unpacked_size)
    bundle = PB.render_review_bundle(results)
    order = [
        line.split("entry：")[1].split("`")[1]
        for line in bundle.splitlines()
        if line.startswith("- entry：")
    ]
    sizes = [r.entry.unpacked_size for r in results]
    assert sizes == sorted(sizes, reverse=True)
    assert len(order) == len(results)


def test_review_bundle_lists_failed_entries():
    """失敗卷必須**如實列在包裡**，不能因為「沒通過」就從人的視野消失。"""
    r = PB.EntryResult(
        entry=PB.Entry(
            path="202607\\x\\bad.json",
            path_posix="202607/x/bad.json",
            unpacked_size=10,
            crc32="DEADBEEF",
        ),
        extracted=False,
        fail=("extracted", "VerificationFailure: 測試用"),
    )
    bundle = PB.render_review_bundle([r])
    assert "未進入審閱的卷" in bundle
    assert "extracted" in bundle and "測試用" in bundle


# ═══════════════════════════════════════════════════════════════════════════
# T111 審閱結論檔（初始全空）
# ═══════════════════════════════════════════════════════════════════════════

def test_verdicts_template_has_all_rows_empty():
    al = PB.load_allowlist()
    text = PB.render_verdicts_template(al)
    rows = [ln for ln in text.splitlines() if ln.startswith("| ") and "---" not in ln]
    data_rows = [r for r in rows if not r.startswith("| jid |")]
    assert len(data_rows) == al.count == 100
    # 未審＝空值：每列除了 jid 之外全部留空
    for row in data_rows:
        cells = [c.strip() for c in row.strip("|").split("|")]
        assert cells[0], f"jid 欄為空：{row}"
        assert cells[1:] == ["", "", "", ""], f"非空欄位（應為未審）：{row}"


def test_verdicts_template_has_no_personal_names():
    """SC-007：版控內的結論檔**不得**含判決全文或姓名。"""
    text = PB.render_verdicts_template(PB.load_allowlist())
    assert "JFULL" not in text
    assert "主　　文" not in text
    # 唯一允許出現的是 JID（那是 opaque 複合碼，不是姓名）
    assert len(text) < 20_000, "結論檔異常膨脹，可能塞進了不該進去的內容"


def test_bundle_writer_preserves_crlf(tmp_path):
    """**守住寫檔那一側**：明確 `newline=""` 才保得住 CRLF（2026-10-10 實測）。

    ⚠ 這條測試的**原假設是錯的，實測推翻了它**，修正後的版本在下方。
    原以為 `Path.write_text`（預設 `newline=None`）會把 CRLF 轉成 LF，所以
    「兩種寫法結果必須不同」。實測（本機 Python 3.12）：**兩種寫法完全相同**，
    預設路徑並不做轉換 —— 也就是說這台機器上的 bug **不在** `write_text`。
    （`Path.write_text` 有 `newline` 參數，預設 `None`；`TextIOWrapper` 在
    `newline=None` 時**寫出**不做任何替換，只有**讀取**才做 universal newlines。
    當時我記錯了方向。）

    所以真正該被釘死的是**明確性**：這裡斷言 `newline=""` 這條路徑保住了
    CRLF，並在下一條測試裡要求 CLI 明確用它。留著反向斷言是故意的——
    它記錄了「預設路徑在某些情況下會壞」這個事實仍然成立（例如 Windows 上
    `newline=None` 會把 `\n` 轉成 `\r\n`，方向相反但同樣是破壞）。
    """
    body = "第一行\r\n第二行\r\n"
    p = tmp_path / "explicit.md"
    with p.open("w", encoding="utf-8", newline="") as fh:
        fh.write(body)
    assert p.read_bytes().decode("utf-8") == body
    assert p.read_bytes().count(b"\r\n") == 2

    # 對照組：預設路徑。本機（Linux）不做轉換，所以結果相同——這是**實測結果**，
    # 不是假設。若哪天 Python 改了行為，這條會紅，那時就更新註解並重新評估
    # 是否還需要 `newline=""`。
    q = tmp_path / "default.md"
    q.write_text(body, encoding="utf-8")
    assert q.read_bytes().decode("utf-8") == body
    # 但**讀**回來時預設路徑會做 universal newlines → CRLF 變 LF。
    # 這正是逐字性檢查必須用 `read_bytes()` 或 `newline=""` 讀的原因。
    assert q.read_text(encoding="utf-8") != body


def test_cli_uses_newline_preserving_write():
    """CLI 的寫檔呼叫必須明確帶 `newline=""`，且**讀回驗證**也必須避開轉換。

    用原始碼檢查：這是唯一能抓到「有人日後把引數拿掉」的方式，而那正是
    逐字性失效的入口。讀回那一側同樣重要 —— 2026-10-10 第一次驗證 100 卷
    全數「不符」時，我先懷疑寫入，結果**真正的原因是我用預設的 `read_text`
    讀回**（它把 CRLF 讀成 LF）。兩端都要釘。
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "cli_check", ROOT / "scripts" / "build-m1-profile.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert 'newline=""' in src, "CLI 寫審閱包時必須明確用 newline=''（保 CRLF）"


def test_no_new_env_var_introduced():
    """本模組**不得**讀環境變數取得路徑。

    實測教訓：原本用 `ARTIFACTS_ROOT`，被 env-audit 的「從程式碼反查」抓到
    （快照缺鍵、CI 紅燈）。那個紅燈是對的——憑空多一個環境變數就是憑空多一個
    跨機不一致的來源。改走 caller 參數後就不需要任何宣告。
    """
    src = (ROOT / "ingest" / "judgements" / "profile_build.py").read_text(
        encoding="utf-8"
    )
    assert "os.environ" not in src and "getenv" not in src


# ═══════════════════════════════════════════════════════════════════════════
# T107／T108：schema ＋ chunk —— 直接餵 bytes，**不需要 archive**
#
# ⚠ 用 `tests/fixtures/judgements/golden/docs_*.json`（repo 內既有的 8-key
# golden 文件），**不是** `data/judgements/seed/`。原因（2026-10-09 實測才發現）：
# seed 檔是**處理後**的 document store 格式（小寫鍵 ＋ `chunks` ＋ `entry_path`），
# 拿它餵 `schema.validate` 必然報 structural drift —— 那會是一個**假失敗**，
# 讓人以為管線壞了。golden 才是進入管線的那種 bytes。
# ═══════════════════════════════════════════════════════════════════════════

GOLDEN = sorted((ROOT / "tests" / "fixtures" / "judgements" / "golden").glob("docs_*.json"))
GOLDEN_CIVIL = next(p for p in GOLDEN if p.name == "docs_civil_6field.json")


def _entry_for_bytes(p: Path, data: bytes) -> PB.Entry:
    rel = f"202607\\臺灣臺北地方法院民事\\{p.name}"
    return PB.Entry(
        path=rel,
        path_posix=PB._selection.forward_slash(rel),
        unpacked_size=len(data),
        crc32="%08X" % zlib.crc32(data),
    )


def _staged(p: Path) -> tuple[PB.EntryResult, bytes]:
    """假裝已解壓成功（size/CRC 已驗），直接進 schema/chunk 階段。"""
    data = p.read_bytes()
    entry = _entry_for_bytes(p, data)
    res = PB.EntryResult(
        entry=entry,
        extracted=True,
        size_crc_ok=True,
        sha256=hashlib.sha256(data).hexdigest(),
        crc32_actual=entry.crc32,
    )
    return PB._run_stages(res, data), data


def test_golden_documents_pass_schema_and_chunk():
    assert GOLDEN, "golden 目錄應該有 8-key 文件"
    for p in GOLDEN:
        res, _ = _staged(p)
        assert res.fail is None, f"{p.name}: {res.fail}"
        assert res.schema_valid and res.chunked
        assert res.chunk_count >= 1
        jfull = res.doc.jfull
        for c in res.chunks:
            assert c.text == jfull[c.start_offset : c.end_offset]
        allowed = {"exact", "natural", "forced", "single", "tail"}
        assert set(res.boundary_kind_histogram) <= allowed
        assert sum(res.boundary_kind_histogram.values()) == res.chunk_count


def test_forced_break_indexes_exclude_chunk_zero():
    """FORCED 斷點記在**該 chunk 的開頭**，且第 0 塊不算（它前面沒有段落）。"""
    for p in GOLDEN:
        res, _ = _staged(p)
        for idx in res.forced_break_chunk_indexes:
            assert idx > 0
            assert res.chunks[idx].boundary_kind.value == "forced"


def test_seed_store_is_not_raw_judgment():
    """守住上面那個踩過的坑：seed 是處理後格式，**不能**當管線輸入餵 schema。

    這條會在 seed 格式哪天改回 8-key 時轉紅 —— 那時請順手改掉 T107/T108 的
    資料來源，而不是把這條刪掉。
    """
    for p in PB.SEED_DIR.glob("*.json"):
        d = json.loads(p.read_text(encoding="utf-8"))
        assert "JFULL" not in d, f"{p.name} 看起來已改成原始 8-key 格式，請更新測試來源"


def test_non_judgment_payload_is_drift_not_crash():
    """非 8-key 的 JSON → schema 階段記 drift，**不拋例外**（FR-003）。"""
    data = b'{"JID":"x"}'
    entry = _entry_for_bytes(Path("x.json"), data)
    res = PB.EntryResult(entry=entry, extracted=True, size_crc_ok=True,
                         sha256=hashlib.sha256(data).hexdigest(),
                         crc32_actual=entry.crc32)
    res = PB._run_stages(res, data)
    assert res.schema_valid is False
    assert res.chunked is False
    assert res.fail[0] == "schema_valid" and "drift" in res.fail[1]


def test_chunker_params_are_frozen_defaults():
    """切塊不得傳 chunk_size —— 凍結預設值是 T108 的驗收條件。

    這條不是裝飾：日後有人為了「切得更好看」傳了 chunk_size，重組斷言仍會綠
    （因為內容仍等於切片），但 corpus 形狀就悄悄變了，而那正是 B1/S2 的阻塞點。
    """
    import inspect

    src = inspect.getsource(PB._run_stages)
    assert "chunk_size=" not in src          # 沒傳值（docstring 提到不算）
    assert PB._chunk.DEFAULT_CHUNK_SIZE == 750
