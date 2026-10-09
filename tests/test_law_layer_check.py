"""FR-007/FR-020：三層比對只測純函式（fake layers，不碰外部服務）。

沿用 test_three_layer_consistency 的 convention：連線測試不進 tests/，
由 scripts/check-law-layers.py 在有服務的機器上跑。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ingest" / "laws"))

import layer_check as LC  # noqa: E402

T = "第一項內容。\n第二項內容。"


def test_all_present_and_equal_is_ok():
    r = LC.compare("民法", "第 1 條", {"parquet": T, "postgres": T, "qdrant": T})
    assert r["verdict"] == "ok"
    assert "2 段" in r["detail"]


def test_whitespace_only_differences_still_ok():
    other = "第一項內容。\n  第二項內容。　\n"
    r = LC.compare("民法", "第 1 條", {"parquet": T, "postgres": other, "qdrant": T})
    assert r["verdict"] == "ok"


def test_text_difference_points_at_layer_and_segments():
    other = "第一項內容。\n被改寫的第二項。"
    r = LC.compare("民法", "第 1 條", {"parquet": T, "postgres": other, "qdrant": T})
    assert r["verdict"] == "mismatch"
    assert "postgres" in r["detail"] or "不一致" in r["detail"]
    assert "段數" in r["detail"]


def test_missing_layer_names_which_layer():
    r = LC.compare("民法", "第 1 條", {"parquet": T, "postgres": None, "qdrant": T})
    assert r["verdict"] == "missing"
    assert "postgres" in r["detail"]
    r = LC.compare("民法", "第 1 條", {"parquet": None, "postgres": None, "qdrant": None})
    assert r["verdict"] == "missing"


def test_empty_string_is_not_missing_but_zero_segments():
    assert LC.paras("") == 0
    assert LC.paras(None) == 0
    assert LC.paras("單段") == 1
    r = LC.compare("民法", "第 1 條", {"parquet": "", "postgres": "", "qdrant": ""})
    assert r["verdict"] == "ok"


def test_injected_error_is_caught_by_single_run():
    # SC-006 的形狀：一次執行即指到條／層／差異，不需全庫掃描。
    bad_pg = "第一項內容。"
    r = LC.compare("刑法", "第 271 條", {"parquet": T, "postgres": bad_pg, "qdrant": T})
    assert r["verdict"] == "mismatch"
    assert "刑法 第 271 條" in f"{r['law']} {r['article']}"
