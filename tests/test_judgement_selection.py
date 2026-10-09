"""`selection.py` 的契約測試（spec 004 T006）。

## 這組測試守住什麼

`corpus-schema-survey.md` 的 494 筆抽樣是整個 8-key schema 契約的**唯一**
經驗證依據（0.46% 抽樣；corpus-wide 形狀仍是 UNKNOWN，spec FR-B02）。這個測試
檔案的職責是讓「後人改了 schema 但沒改證據」這件事**立刻失敗**。

## 三個必須同時成立的性質

1. **清單 digest 仍等於記錄值** `280b95bb…` —— 清單被改過（無論內容或順序）。
2. **清單是 inventory 的嚴格子集** —— 抽樣不能憑空出現 entry，也不能有重複。
3. **清單不是整份 inventory** —— 那就不是抽樣。

## 兩種路徑分隔符

- inventory：反斜線（archive 原樣，FR-007）
- 清單：正斜線（調查當時 `unrar lt` 的輸出）

測試會驗證轉換是雙向且不改變集合語意 —— 這是這份清單最容易被未察覺破壞
的地方：分隔符混用會讓 subset 檢查「看起來通過」但實際上比的是錯的東西。

## 這組測試不解壓、不連線

全部只需要 T004 的 inventory（header walk）。符合 repo 慣例：不碰外部服務。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "ingest" / "judgements") not in sys.path:
    sys.path.insert(0, str(ROOT / "ingest" / "judgements"))

import artifact  # noqa: E402
import inventory  # noqa: E402
import selection  # noqa: E402

HAS_REAL = artifact.ARTIFACT_PATH.is_file()
MANIFEST = selection.MANIFEST_PATH


@pytest.fixture(scope="module")
def manifest() -> selection.Manifest:
    return selection.load()


@pytest.fixture(scope="module")
def inventory_paths() -> list[str]:
    """inventory 的路徑集合（反斜線）。

    真 artifact 在時用真 inventory（108,547 筆）；否則用 fixture（12 筆）——
    但那樣 subset 檢查必然失敗，所以 corpus 相關的斷言一律 corpus-gated。
    這裡只在真 artifact 可用時提供真 inventory。
    """
    if not HAS_REAL:
        pytest.skip("283 MB artifact 不在這個 checkout")
    return [e.path for e in inventory.inventory(artifact.ARTIFACT_PATH)]


# ── 清單本身的完整性 ────────────────────────────────────────────────────────

def test_manifest_exists_and_is_committed():
    """清單必須在版控中，不是執行時產生的。"""
    assert MANIFEST.is_file()
    raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    assert {"paths", "tags", "digest", "source"} <= set(raw)


def test_digest_matches_the_recorded_survey_value(manifest):
    """digest 必須仍是 corpus-schema-survey.md §1 記錄的那個值。"""
    assert manifest.digest == (
        "280b95bbdac0bb933568f55297ea352022457600851847b7ca955db3f0c2d8a3"
    )


def test_digest_recomputation_is_order_independent(manifest):
    """digest 對排序不敏感 —— 這是它能當作「同一組內容」的證明的前提。

    定義：`sha256('\n'.join(sorted(paths)))`。若實作改成「不排序就連接」，
    同一組路徑只因檔案內順序不同就會得到不同 digest。
    """
    assert selection.sample_digest(manifest.paths) == manifest.digest
    shuffled = list(reversed(manifest.paths))
    assert selection.sample_digest(shuffled) == manifest.digest


def test_load_rejects_a_tampered_digest(tmp_path, manifest):
    """digest 被改動時 load 必須失敗。

    一份 digest 不符的清單若能被靜靜使用，等於 drift 偵測機制失效而沒有人
    知道 —— 那比沒有這套機制更糟。
    """
    raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
    raw["digest"] = "0" * 64
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(selection.ManifestError, match="digest"):
        selection.load(p)


def test_load_rejects_a_tampered_path_list(tmp_path):
    """路徑被改動（即使 digest 欄位一起被改對）也必須失敗。

    這一條抓的是「只改 paths 而忘了更新 digest」—— 最常見的破壞方式。
    """
    raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
    raw["paths"] = raw["paths"][:-1] + ["202607/不存在/假的.json"]
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(selection.ManifestError, match="digest"):
        selection.load(p)


def test_load_rejects_wrong_size(tmp_path):
    """筆數不等於 494 必須失敗 —— 記錄值就是 494。"""
    raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
    raw["paths"] = raw["paths"][:100]
    raw["digest"] = selection.sample_digest(raw["paths"])
    p = tmp_path / "small.json"
    p.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(selection.ManifestError):
        selection.load(p)


def test_load_rejects_malformed_paths(tmp_path):
    raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
    raw["paths"] = "not a list"
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(selection.ManifestError, match="paths"):
        selection.load(p)


def test_load_rejects_missing_manifest(tmp_path):
    with pytest.raises(selection.ManifestError, match="不存在"):
        selection.load(tmp_path / "absent.json")


# ── 路徑分隔符 ──────────────────────────────────────────────────────────────

def test_manifest_uses_forward_slashes(manifest):
    """清單用正斜線 —— 調查當時 `unrar lt` 的輸出形式。"""
    assert all("/" in p for p in manifest.paths)
    assert not any("\\" in p for p in manifest.paths)


def test_separator_conversion_is_reversible(manifest):
    """正斜線 → 反斜線 → 正斜線 必須回到原值。

    ## 為什麼不是雙向可逆

    `backslash(forward_slash(p)) != p` 是**正確**的：manifest 的 p 是正斜線，
    轉成反斜線後當然不等於原本的正斜線字串。真正要驗的是轉回去等於原值 ——
    那才是「兩種表示描述同一個 entry」的意思。

    反斜線 → 正斜線 → 反斜線 同理，不能拿 manifest 的正斜線字串去測。
    """
    for p in manifest.paths[:50]:
        assert selection.forward_slash(selection.backslash(p)) == p

    # 反斜線那一側：轉過去再轉回來必須不變
    inv_style = [selection.backslash(p) for p in manifest.paths[:50]]
    for p in inv_style:
        assert selection.backslash(selection.forward_slash(p)) == p


def test_forward_slash_does_not_strip_or_normalise():
    """轉換**只**換分隔符。

    不去空白、不改大小寫、不處理 `.`/`..`。任何多餘的整理都會讓 digest 對不上，
    而且會讓 subset 檢查比錯的東西。
    """
    assert selection.forward_slash("a\\b\\c.json") == "a/b/c.json"
    assert selection.forward_slash(" a\\b ") == " a/b "
    assert selection.forward_slash("a\\..\\b") == "a/../b"


# ── 子集關係（需要真 inventory）──────────────────────────────────────────────

@pytest.mark.judgement_corpus
@pytest.mark.skipif(not HAS_REAL, reason="283 MB artifact 不在這個 checkout")
def test_manifest_is_a_strict_subset_of_the_inventory(manifest, inventory_paths):
    """核心斷言：494 筆全部存在於 108,547 筆的 inventory 中。"""
    selection.verify_against_inventory(manifest, inventory_paths)
    assert len(manifest) == 494
    assert len(inventory_paths) == 108547


@pytest.mark.judgement_corpus
@pytest.mark.skipif(not HAS_REAL, reason="283 MB artifact 不在這個 checkout")
def test_no_duplicates_in_the_manifest(manifest):
    assert len(manifest.paths) == len(set(manifest.paths))


@pytest.mark.judgement_corpus
@pytest.mark.skipif(not HAS_REAL, reason="283 MB artifact 不在這個 checkout")
def test_subset_check_fails_when_a_path_is_absent(manifest, inventory_paths):
    """反向測試：塞一筆不存在的路徑，檢查必須拒絕。

    沒有這條，「subset 檢查通過」可能只是因為它永遠返回 None。
    """
    poisoned = list(manifest.paths) + ["202607\\沒有這個法院\\假的.json"]
    bad = selection.Manifest(
        paths=poisoned,
        tags=manifest.tags,
        digest=manifest.digest,
        source=manifest.source,
    )
    with pytest.raises(selection.ManifestError, match="不在 inventory"):
        selection.verify_against_inventory(bad, inventory_paths)


def test_subset_check_fails_on_duplicates(manifest, inventory_paths):
    """重複必須被拒絕 —— 重複會讓覆蓋率統計失真。"""
    dup = list(manifest.paths)
    dup.append(dup[0])
    bad = selection.Manifest(
        paths=dup, tags=manifest.tags, digest=manifest.digest, source=manifest.source
    )
    with pytest.raises(selection.ManifestError, match="重複"):
        selection.verify_against_inventory(bad, inventory_paths)


def test_subset_check_fails_when_manifest_equals_inventory(manifest):
    """清單等於 inventory 時必須失敗 —— 那不是抽樣。

    用一個極小的假 inventory 來驗這個分支，不必依賴真 artifact。
    """
    paths = [selection.backslash(p) for p in manifest.paths[:3]]
    small = selection.Manifest(
        paths=paths, tags=manifest.tags, digest=manifest.digest, source=manifest.source
    )
    with pytest.raises(selection.ManifestError, match="不是抽樣"):
        selection.verify_against_inventory(small, paths)


# ── stratum 標籤 ────────────────────────────────────────────────────────────

def test_every_path_has_a_stratum_tag(manifest):
    """每一筆抽樣都必須有 stratum 標籤 —— 否則覆蓋率報告會有洞。"""
    assert set(manifest.tags) == set(manifest.paths)


def test_strata_cover_the_documented_groups(manifest):
    """七個 strata A–G 都必須出現。

    缺任何一個都代表某個維度的覆蓋沒了，而那正是抽樣存在的原因
    （`corpus-schema-survey.md` §2）。
    """
    counts = selection.strata_counts(manifest)
    for group in (
        "jcase-common",
        "jcase-rare",
        "jcase-mid",
        "branch",
        "court",
        "ANOMALY-5field",
    ):
        assert group in counts, f"缺少 stratum {group}"
    # D：10 個 size decile，每個 2 筆
    deciles = [f"size-dec{i}" for i in range(10)]
    assert all(d in counts for d in deciles)
    assert all(counts[d] == 2 for d in deciles)


def test_anomaly_stratum_covers_all_139_five_field_entries(manifest):
    """G stratum = 全部 139 筆五欄 JID（憲法法庭）。

    那是**已知的結構性類別**，調查時逐筆檢視過。若數量變了，代表 archive
    換版或清單被改 —— 兩者都需要被注意。
    """
    counts = selection.strata_counts(manifest)
    assert counts["ANOMALY-5field"] == 139


def test_strata_counts_sum_to_the_manifest_size(manifest):
    assert sum(selection.strata_counts(manifest).values()) == len(manifest)


def test_tags_for_returns_inventory_style_keys(manifest):
    """`tags_for()` 回傳反斜線形式 —— 可直接與 inventory 對照。"""
    tags = selection.tags_for(manifest)
    assert all("\\" in k for k in tags)
    assert len(tags) == len(manifest)


def test_tags_use_stratum_prefix_not_detail(manifest):
    """標籤是 `<stratum>:<detail>`；分類只看前綴。

    detail（例如 `司促`）是 analyst 對當時那筆的描述，不是分類依據。若實作
    誤用 detail 當 key，統計會被 case type 的細碎取值淹沒。
    """
    counts = selection.strata_counts(manifest)
    assert "jcase-common:司促" not in counts
    assert counts["jcase-common"] >= 100  # A stratum 是 60 codes × 2


# ── 出處記錄 ────────────────────────────────────────────────────────────────

def test_source_records_the_survey_provenance(manifest):
    """清單必須說明自己是從哪來的、怎麼算的 digest。"""
    src = manifest.source
    assert "corpus-schema-survey.md" in src["document"]
    assert src["corpus_population"] == 108409
    assert src["artifact_sha256"] == artifact.ARTIFACT_SHA256
    assert src["path_separator"] == "/"
    assert "digest_definition" in src


def test_source_records_the_coverage_of_the_sample(manifest):
    """抽樣達到的覆蓋範圍必須記錄下來 —— 那是「這個結論能推多遠」的依據。"""
    cov = manifest.source["coverage"]
    assert cov["courts"] == 84
    assert cov["case_types"] == 170
    assert cov["five_field_jid_entries"] == 139
    assert sum(cov["branches"].values()) == 494


def test_regeneration_is_documented_as_pinned_not_recomputed(manifest):
    """清單必須聲明它是**釘選的證據**，不是重算結果。

    strata A–G 依賴 JSON 內容裡的 JCASE（需解壓 + 解析），無法從 inventory
    重新推導出同一組 494 筆。若日後有人照著 strata 規則重寫一份選取程式並
    宣稱「產生了清單裡的 494 筆」，那是另一回事 —— 這個欄位就是為了讓那個
    誤會無法悄悄發生。
    """
    assert "釘選" in manifest.source["regeneration"]


# ── 這份模組不做什麼 ────────────────────────────────────────────────────────

def test_selection_does_not_extract_or_parse_json():
    """selection 不得解壓、不得對 entry 內容做任何事。

    它只認得兩份清單（釘選的 sample + inventory 的路徑集合）。一旦在這裡
    解壓或解析內容，就會對 0.46% 抽樣建立未經驗證的形狀假設。
    """
    import ast

    tree = ast.parse(
        (ROOT / "ingest" / "judgements" / "selection.py").read_text("utf-8")
    )
    imported: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            imported.update(a.name.split(".")[0] for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            imported.add(n.module.split(".")[0])
    assert imported & {"subprocess", "zlib", "gzip", "rarfile"} == set()
    # 只有 json/hashlib/… ；不得 import extract（那是 T005 的責任）
    assert "extract" not in imported


def test_selection_has_no_semantic_inference():
    """不得推論法院 / JCASE / 年份語意。

    抽樣清單只處理「路徑集合」。任何「從檔名推測法院或案件類型」的邏輯都會
    製造一個未經驗證的對應，而 `corpus-schema-survey.md` 已證明 JCASE 與
    法院都不是可從檔名可靠推得的（court inference 在 spec 裡是明確禁止的）。
    """
    src = (ROOT / "ingest" / "judgements" / "selection.py").read_text("utf-8")
    for forbidden in ("infer_court", "parse_jcase", "guess_court", "court_of("):
        assert forbidden not in src


def test_manifest_record_is_frozen(manifest):
    with pytest.raises(Exception):
        manifest.digest = "0" * 64  # type: ignore[misc]
