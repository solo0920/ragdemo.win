"""rules_store 純函式測試：驗證／匹配／增刪停（全離線，tmp 檔案）。"""
from app import rules_store as rs


def _setup(tmp_path, monkeypatch):
    f = tmp_path / "rules.json"
    f.write_text('{"version": 2, "rules": []}', encoding="utf-8")
    monkeypatch.setattr(rs, "PATHS", [f, f])


def test_validate_rule(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    assert rs.validate_rule({"match": "欠薪", "answer": "依勞基法…", "kind": "contains"}) is None
    assert rs.validate_rule({"match": "", "answer": "x"}) is not None
    assert rs.validate_rule({"match": "a", "answer": ""}) is not None
    assert rs.validate_rule({"match": "a", "answer": "x", "kind": "wrong"}) is not None
    assert rs.validate_rule({"match": "(壞", "answer": "x", "kind": "regex"}) is not None
    assert rs.validate_rule({"match": "(好)?法", "answer": "x", "kind": "regex"}) is None


def test_add_toggle_delete_roundtrip(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    r = rs.add({"kind": "contains", "match": "欠薪", "answer": "答A", "note": "n1"})
    assert "rule" in r and r["rule"]["id"]
    assert rs.counts() == {"total": 1, "enabled": 1}

    rid = r["rule"]["id"]
    t = rs.toggle(rid)
    assert t["rule"]["enabled"] is False
    assert rs.counts() == {"total": 1, "enabled": 0}

    d = rs.delete(rid)
    assert d == {"deleted": rid}
    assert rs.counts() == {"total": 0, "enabled": 0}

    assert "error" in rs.toggle("不存在")
    assert "error" in rs.delete("不存在")


def test_match_rule_kinds_and_law_scope(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    rs.add({"kind": "exact", "match": "證交法有多少條", "answer": "209 條"})
    rs.add({"kind": "contains", "match": "欠薪", "answer": "勞基法答", "law": "勞動基準法"})
    rules = rs.load()
    assert len(rules) == 2

    # exact：空白歸一後相等
    m = rs.match_rule("證交法  有多少條", None, rules)
    assert m and m["answer"] == "209 條"
    # contains + law 限定：法名不符不命中
    assert rs.match_rule("公司欠薪怎麼辦", "證券交易法", rules) is None
    # contains + law 限定：法名相符命中
    m = rs.match_rule("公司欠薪怎麼辦", "勞動基準法", rules)
    assert m and m["answer"] == "勞基法答"
    # 停用規則不命中
    rid = next(r["id"] for r in rules if r["match"] == "欠薪")
    rs.toggle(rid)
    rules = rs.load()
    assert rs.match_rule("公司欠薪怎麼辦", "勞動基準法", rules) is None


def test_regex_kind_matching(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    rs.add({"kind": "regex", "match": r"(多少條|幾條)$", "answer": "條數規則"})
    rules = rs.load()
    assert rs.match_rule("勞基法共有幾條", None, rules) is not None
    assert rs.match_rule("幾條法條被引用", None, rules) is None


def test_load_missing_or_broken_file(tmp_path, monkeypatch):
    f = tmp_path / "rules.json"
    monkeypatch.setattr(rs, "PATHS", [f, f])
    assert rs.load() == []          # 檔案不存在 → 空（fail-safe）
    f.write_text("不是json{", encoding="utf-8")
    assert rs.load() == []          # 壞格式 → 空（fail-safe）


def test_load_caches_by_mtime_and_refreshes(tmp_path, monkeypatch):
    f = tmp_path / "rules.json"
    f.write_text('{"version": 2, "rules": [{"id": "a", "kind": "contains", "match": "欠薪", "answer": "A"}]}',
                 encoding="utf-8")
    monkeypatch.setattr(rs, "PATHS", [f, f])
    assert len(rs.load()) == 1
    rs.load()
    assert rs._cache["path"] == f and rs._cache["rules"][0]["id"] == "a"   # 快取命中
    # 他台 git pull 換檔（mtime 改變）→ 下次讀取自動同步
    f.write_text('{"version": 2, "rules": [{"id": "b", "kind": "contains", "match": "資遣", "answer": "B"}]}',
                 encoding="utf-8")
    assert rs.load()[0]["id"] == "b"


def test_probe_identity_exact_and_contains_equality(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    rs.add({"kind": "exact", "match": "證交法有多少條", "answer": "209 條"})
    rs.add({"kind": "contains", "match": "欠薪的救濟管道", "answer": "勞基法答"})
    p = rs.probe("證交法   有多少條", None)
    assert p["identity"] is True and p["rule"]["answer"] == "209 條"
    p = rs.probe("欠薪的救濟管道", None)   # 白話字串歸一相等 → identity
    assert p["identity"] is True and p["rule"]["match"] == "欠薪的救濟管道"


def test_probe_approximate_contains_is_not_identity(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    rs.add({"kind": "contains", "match": "欠薪", "answer": "勞基法答"})
    p = rs.probe("公司欠薪怎麼辦", None)
    assert p["identity"] is False and p["rule"]["match"] == "欠薪"


def test_probe_fuzzy_overlap_candidate(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    rs.add({"kind": "contains", "match": "證交法施行日期是", "answer": "民國 113 年 8 月 7 日"})
    p = rs.probe("證交法施行日期為何時？", None)
    assert p is not None and p["identity"] is False and p["rule"]["match"] == "證交法施行日期是"


def test_probe_exact_kind_no_fuzzy_fallback(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    rs.add({"kind": "exact", "match": "證交法施行日期", "answer": "民國 113 年"})
    assert rs.probe("證交法從何時開始施行？", None) is None   # exact 故意不命中 → 不做模糊
    assert rs.probe("證交法施行日期", None)["identity"] is True


def test_probe_respects_law_scope_and_disabled(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    rs.add({"kind": "contains", "match": "欠薪", "answer": "勞基法答", "law": "勞動基準法"})
    assert rs.probe("欠薪", "證券交易法") is None      # 法名不符
    rid = rs.load()[0]["id"]
    rs.toggle(rid)
    assert rs.probe("欠薪", "勞動基準法") is None      # 停用不命中


def test_probe_priority_identity_over_approximate(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    rs.add({"kind": "contains", "match": "欠薪", "answer": "近似"})
    rs.add({"kind": "exact", "match": "證交法有多少條", "answer": "identity"})
    p = rs.probe("證交法有多少條", None)   # identity 分數較高，勝過順序在前者
    assert p["identity"] is True and p["rule"]["answer"] == "identity"