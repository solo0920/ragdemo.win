"""B2-B: judgment-to-statute provenance (deterministic, no LLM).

Builds on B1's Level-1 linker (StatuteCorpus, mention detection, exact corpus
lookup) and adds the citation evidence contract B2-B requires:

- citation_id, raw_text, normalized{law,article,paragraph,subparagraph},
  source_span (chunk coordinates), evidence_text (containing sentence),
  citation_type (ALWAYS EXPLICIT_CITATION — APPLIED/DECISIVE are never emitted
  here), resolution_status (RESOLVED | UNRESOLVED:reason).
- Document-internal 下稱 definitions as the ONLY alias source: SHORT resolves
  only via an in-document `FULL（下稱SHORT）` definition whose FULL is a corpus
  law name. No global alias map exists; unknown aliases stay UNRESOLVED.
- Corpus version recorded per evidence (sha + update_date from the repo sync
  record); per-article historical text is NOT available — flagged, never faked.

This module takes judgment evidence as input, never the user question: the
same judgment always yields the same statutes (query-independence is structural).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .b1_serve import (
    StatuteCorpus,
    StatuteMention,
    make_statute_evidence,
    normalize_article,
)

CITATION_TYPE = "EXPLICIT_CITATION"  # the only type this module emits
UNRESOLVED_REASONS = ("unknown_alias", "no_corpus_match", "invalid_article")

# FULL（下稱SHORT） / FULL(下稱SHORT): document-internal alias definitions.
_XIACHEN_RE = re.compile(
    r"(?P<full>[\u4e00-\u9fffA-Za-z0-9·・（）()]{2,30})"
    r"[（(]下稱(?P<short>[\u4e00-\u9fffA-Za-z0-9]{2,10})[）)]"
)

# Short forms observed verbatim in real corpus chunks (刑法: 104 chunks,
# 憲法: 4 chunks) that are NOT corpus law names (corpus uses 中華民國刑法 /
# 中華民國憲法). They match here ONLY to be explicitly refused as
# UNRESOLVED:unknown_alias — never resolved, never silently dropped.
# Membership requires real-corpus evidence; unobserved guesses are excluded.
KNOWN_UNRESOLVED_SHORTFORMS = ("刑法", "憲法")

LAWS_SYNC = (
    Path(__file__).resolve().parent.parent.parent
    / "data" / "laws" / ".law_sync.json"
)


def corpus_version() -> dict:
    """Corpus-level version record (file granularity — the finest the repo proves)."""
    try:
        d = json.loads(LAWS_SYNC.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"source": "data/laws/laws_flat.jsonl", "version": "unrecorded"}
    return {"source": "data/laws/laws_flat.jsonl",
            "sha256": d.get("sha256"), "update_date": d.get("update_date")}


def extract_xiachen_definitions(doc_text: str, corpus: StatuteCorpus) -> dict:
    """SHORT -> FULL for definitions whose FULL is verbatim a corpus law name.

    Definitions resolving to non-corpus names are recorded but unusable
    (they can never authorize a resolution). Returns {short: full} usable map
    plus the raw list for provenance."""
    usable: dict[str, str] = {}
    raw: list[dict] = []
    flat = re.sub(r"\s+", "", doc_text)
    for m in _XIACHEN_RE.finditer(flat):
        full, short = m.group("full"), m.group("short")
        usable_flag = full in corpus.law_names
        raw.append({"full": full, "short": short, "usable": usable_flag})
        if usable_flag and short not in usable:
            usable[short] = full
    return {"usable": usable, "raw": raw}


def _split_paragraph(detail: str | None) -> tuple[str | None, str | None]:
    if not detail:
        return None, None
    if detail.endswith("項"):
        return detail, None
    if detail.endswith("款"):
        return None, detail
    return None, None


def extract_citations(chunk_text: str, *, jid: str, chunk_index: int,
                      corpus: StatuteCorpus,
                      xiachen: dict | None = None) -> list[dict]:
    """Explicit statute citations in one judgment chunk → citation objects.

    xiachen: optional output of extract_xiachen_definitions for the enclosing
    document (same-document scoping only). Citations whose law name is neither
    verbatim-corpus nor xiachen-defined are returned UNRESOLVED (unknown_alias)
    — never guessed, never dropped silently.
    """
    usable_aliases = (xiachen or {}).get("usable", {})
    names = "|".join(re.escape(n) for n in
                     sorted(set(corpus.law_names) | set(usable_aliases),
                            key=len, reverse=True))
    num = r"[0-9一二三四五六七八九十百千零○兩\-]+"
    # Branch inserts follow the article's 條 in real text (第339條之4,
    # 第436條之18第1項); a trailing 條 after the branch is accepted but
    # never required. All shapes resolve to one article identity.
    _branch = f"(?P<branch>之{num}(?:條)?)?"
    bareish = re.compile(
        f"(?P<law>{names})"
        f"(?:[（(]下稱[^）)]{{1,10}}[）)])?第(?P<art>{num})條{_branch}"
        f"(?P<detail>第{num}[項款])?")
    refuse = re.compile(
        f"(?P<law>{'|'.join(re.escape(n) for n in KNOWN_UNRESOLVED_SHORTFORMS)})"
        f"第(?P<art>{num})條{_branch}(?P<detail>第{num}[項款])?")
    bare_art = re.compile(
        f"第(?P<art>{num})條{_branch}(?P<detail>第{num}[項款])?")
    para_only = re.compile(f"第(?P<pnum>{num})(?P<pkind>[項款])")
    out: list[dict] = []
    from .b1_serve import _build_mention, StatuteMention
    for sent, base in _sentences(chunk_text):
        if not sent.strip():
            continue
        flat, amap = _collapse(sent)

        def _span(m):
            return base + amap[m.start()], base + amap[m.end() - 1] + 1

        # Gather article-level events with collapsed positions, then walk in
        # position order so every ellipsis sees its NEAREST preceding anchor
        # (not the last qualified mention in the sentence).
        events = []  # (pos, kind, match)
        for m in bareish.finditer(flat):
            events.append((m.start(), "qualified", m))
        for m in refuse.finditer(flat):
            events.append((m.start(), "refused", m))
        for m in bare_art.finditer(flat):
            events.append((m.start(), "bare", m))
        # Refusals inside full corpus names belong to the full-name match.
        # Bare tails inside qualified/refused matches are not separate cites.
        taken_spans = [(m.start(), m.end()) for _, k, m in events
                       if k in ("qualified", "refused")]
        kept = []
        for p, k, m in events:
            if k == "refused" and any(s <= p < e for s, e in
                                      [(m2.start(), m2.end()) for _, k2, m2 in events
                                       if k2 == "qualified"]):
                continue
            if k == "bare" and any(s <= p < e for s, e in taken_spans):
                continue
            kept.append((p, k, m))
        events = kept
        consumed: list[tuple[int, int]] = []
        last_qual = None    # (pos, law, article, surface): nearest preceding qualified
        last_refused = None  # (pos, law, surface): nearest preceding refusal
        last_article = None  # (law, article, surface): nearest preceding article event
        hist: list[tuple[int, object, object]] = []  # (end_pos, last_article, last_refused)
        for _, kind, m in sorted(events, key=lambda e: e[0]):
            if kind == "qualified":
                mention = _build_mention(m, m.group("law"))
                if mention is None:
                    continue
                start, end = _span(m)
                last_qual = (m.start(), m.group("law"), mention.article, m.group(0))
                last_article = last_qual[1:]
                consumed.append((m.start(), m.end()))
                hist.append((m.end(), last_article, last_refused))
                out.append(_resolve_one(mention, m.group("law"), usable_aliases, corpus,
                                        chunk_text, sent, jid, chunk_index,
                                        start, end))
            elif kind == "refused":
                mention = _build_mention(m, m.group("law"))
                if mention is None:
                    continue
                start, end = _span(m)
                last_refused = (m.start(), m.group("law"), m.group(0))
                consumed.append((m.start(), m.end()))
                hist.append((m.end(), last_article, last_refused))
                out.append(_unresolved(mention, m.group("law"), jid, chunk_index,
                                       start, end, chunk_text, sent, "unknown_alias",
                                       False, None))
            else:  # bare article: nearest preceding article-level event wins,
                # whether qualified (resolve scoped) or refused (explicit
                # UNRESOLVED). A refused mention between a qualified law and a
                # bare article blocks qualified inheritance (parallel
                # constructions like 憲法第8條及第16條 stay refused).
                _cand = [(pos, "qual") for pos, _, _, _ in
                           ([last_qual] if last_qual is not None else [])]
                _cand += [(pos, "ref") for pos, _, _ in
                          ([last_refused] if last_refused is not None else [])]
                _cand = [c for c in _cand if c[0] < m.start()]
                if not _cand:
                    continue
                _, _kind = max(_cand)
                if _kind == "ref":
                    law, anchor_surface = last_refused[1], last_refused[2]
                    mention = _build_mention(m, law)
                    if mention is None:
                        continue
                    start, end = _span(m)
                    consumed.append((m.start(), m.end()))
                    hist.append((m.end(), last_article, last_refused))
                    out.append(_unresolved(mention, law, jid, chunk_index,
                                           start, end, chunk_text, sent, "unknown_alias",
                                           True, anchor_surface))
                    continue
                _, law, _, anchor_surface = last_qual
                mention = _build_mention(m, law)
                if mention is None:
                    continue
                start, end = _span(m)
                last_article = (law, mention.article, m.group(0))
                consumed.append((m.start(), m.end()))
                hist.append((m.end(), last_article, last_refused))
                out.append(_resolve_one(mention, law, usable_aliases, corpus,
                                        chunk_text, sent, jid, chunk_index,
                                        start, end, scoped=True,
                                        scoped_from=anchor_surface))
        # Paragraph-only ellipsis: nearest preceding ARTICLE event in sentence.
        for m in para_only.finditer(flat):
            if any(s <= m.start() < e for s, e in consumed):
                continue
            _hist = [(a, r) for (pos, a, r) in hist if pos <= m.start()]
            _la, _lr = _hist[-1] if _hist else (None, None)
            if _la is not None:
                law, article, anchor_surface = _la
                mention = StatuteMention(law_name=law, article=article,
                                         detail=m.group(0), surface=m.group(0),
                                         scoped=True, scoped_from=anchor_surface)
                start, end = _span(m)
                consumed.append((m.start(), m.end()))
                out.append(_resolve_one(mention, law, usable_aliases, corpus,
                                        chunk_text, sent, jid, chunk_index,
                                        start, end, scoped=True,
                                        scoped_from=anchor_surface))
            elif _lr is not None:
                law, anchor_surface = _lr[1], _lr[2]
                mention = StatuteMention(law_name=law, article="",
                                         detail=m.group(0), surface=m.group(0),
                                         scoped=True, scoped_from=anchor_surface)
                start, end = _span(m)
                consumed.append((m.start(), m.end()))
                out.append(_unresolved(mention, law, jid, chunk_index,
                                       start, end, chunk_text, sent, "unknown_alias",
                                       True, anchor_surface))
    out.sort(key=lambda c: (c["source_span"]["start"], c["source_span"]["end"]))
    return out


def _span_split(chunk_text: str, start: int, end: int) -> bool:
    """True iff layout whitespace splits the mention span itself (not merely
    present elsewhere in the sentence). Span-precise, chunk coordinates."""
    return any(ch.isspace() for ch in chunk_text[start:end])


def _resolve_one(mention: StatuteMention, law: str, usable_aliases: dict,
                 corpus: StatuteCorpus, chunk_text: str, sentence: str,
                 jid: str, chunk_index: int, start: int, end: int, *,
                 scoped: bool = False, scoped_from: str | None = None) -> dict:
    from .b1_serve import cjk_numeral_to_arabic
    collapsed = _span_split(chunk_text, start, end)
    paragraph, subparagraph = _split_paragraph(mention.detail)
    law_source = "verbatim"
    lookup_law = law
    if law not in corpus.law_names:
        if law in usable_aliases:
            lookup_law = usable_aliases[law]
            law_source = "xiachen_defined"
        else:
            return _unresolved(mention, law, jid, chunk_index, start, end,
                               chunk_text, sentence, "unknown_alias", scoped, scoped_from)
    row = corpus.find(lookup_law, mention.article)
    if row is None:
        return _unresolved(mention, lookup_law, jid, chunk_index, start, end,
                           chunk_text, sentence, "no_corpus_match", scoped, scoped_from,
                           law_source=law_source)
    ev = make_statute_evidence(row, mention)
    ev["provenance"]["corpus_version"] = corpus_version()
    ev["provenance"]["historical_text_unverified"] = True
    return {
        "citation_id": f"cit:{jid}#c{chunk_index}:{start}-{end}",
        "citation_type": CITATION_TYPE,
        "judgement_id": jid,
        "document_id": jid,
        "chunk_id": f"{jid}#chunk{chunk_index}",
        "raw_text": mention.surface,
        "surface_collapsed": collapsed,
        "normalized": {"law_name": lookup_law, "article": mention.article,
                       "paragraph": paragraph, "subparagraph": subparagraph},
        "source_span": {"start": start, "end": end, "unit": "code_point",
                        "basis": "chunk_text"},
        "evidence_text": sentence,
        "law_source": law_source,
        "scoped": scoped,
        "scoped_from": scoped_from,
        "resolution_status": "RESOLVED",
        "evidence_id": ev["evidence_id"],
        "statute_evidence": ev,
    }


def _unresolved(mention: StatuteMention, law: str, jid: str, chunk_index: int,
                start: int, end: int, chunk_text: str, sentence: str, reason: str,
                scoped: bool, scoped_from: str | None, *,
                law_source: str = "unresolved") -> dict:
    assert reason in UNRESOLVED_REASONS, reason
    collapsed = _span_split(chunk_text, start, end)
    paragraph, subparagraph = _split_paragraph(mention.detail)
    return {
        "citation_id": f"cit:{jid}#c{chunk_index}:{start}-{end}",
        "citation_type": CITATION_TYPE,
        "judgement_id": jid,
        "document_id": jid,
        "chunk_id": f"{jid}#chunk{chunk_index}",
        "raw_text": mention.surface,
        "surface_collapsed": collapsed,
        "normalized": {"law_name": law, "article": mention.article,
                       "paragraph": paragraph, "subparagraph": subparagraph},
        "source_span": {"start": start, "end": end, "unit": "code_point",
                        "basis": "chunk_text"},
        "evidence_text": sentence,
        "law_source": law_source,
        "scoped": scoped,
        "scoped_from": scoped_from,
        "resolution_status": f"UNRESOLVED:{reason}",
        "evidence_id": None,
        "statute_evidence": None,
    }


def _sentences(text: str):
    return _default_sent_iter(text)


def _default_sent_iter(text: str):
    from .b1_serve import _SENT_SPLIT
    start = 0
    for m in _SENT_SPLIT.finditer(text):
        if m.start() > start:
            yield text[start:m.start()], start
        start = m.end()
    if start < len(text):
        yield text[start:], start


def _collapse(sent: str):
    chars, amap = [], []
    for i, ch in enumerate(sent):
        if ch.isspace():
            continue
        amap.append(i)
        chars.append(ch)
    return "".join(chars), amap


def statute_gate(citations: list[dict], evidences: list[dict],
                 corpus: StatuteCorpus) -> tuple[bool, list[str]]:
    """Mechanical statute provenance gate S1–S8 (deterministic, no LLM)."""
    failures: list[str] = []
    by_id = {e["evidence_id"]: e for e in evidences}
    resolved = [c for c in citations if c["resolution_status"] == "RESOLVED"]
    # S1: every returned statute traces to a RESOLVED citation.
    for ev in evidences:
        if not any(c.get("evidence_id") == ev["evidence_id"] for c in resolved):
            failures.append(f"S1: statute {ev['evidence_id']} has no RESOLVED citation")
    # S2: every citation carries judgement provenance.
    for c in citations:
        sp = c.get("source_span", {})
        if not (c.get("judgement_id") and c.get("chunk_id")
                and isinstance(sp.get("start"), int)
                and isinstance(sp.get("end"), int) and sp["start"] < sp["end"]):
            failures.append(f"S2: citation {c.get('citation_id')} lacks provenance")
        if c.get("citation_type") != CITATION_TYPE:
            failures.append(f"S2: citation {c.get('citation_id')} bad type")
    # S3: every resolved statute has exact corpus identity.
    for c in resolved:
        ev = by_id.get(c["evidence_id"] or "")
        if ev is None:
            failures.append(f"S3: citation {c['citation_id']} points at missing evidence")
            continue
        row = corpus.find(ev.get("law_name", ""), ev.get("article", ""))
        if row is None or row.get("pcode") != ev.get("document_id"):
            failures.append(f"S3: {c['citation_id']} corpus identity not exact")
    # S4: no statute without citation surface in judgment text (query-bypass guard).
    for c in resolved:
        flat_ev = re.sub(r"\s+", "", c.get("evidence_text", ""))
        flat_surface = re.sub(r"\s+", "", c.get("raw_text", ""))
        if not flat_surface or flat_surface not in flat_ev:
            failures.append(f"S4: {c['citation_id']} surface not in judgment text")
    # S5: no silently-resolved alias.
    for c in resolved:
        if c.get("law_source") not in ("verbatim", "xiachen_defined"):
            failures.append(f"S5: {c['citation_id']} bad law_source")
    # S6 covered by S2 span checks; S7: unresolved explicitly represented.
    for c in citations:
        if c["resolution_status"].startswith("UNRESOLVED:"):
            reason = c["resolution_status"].split(":", 1)[1]
            if reason not in UNRESOLVED_REASONS:
                failures.append(f"S7: {c['citation_id']} bad unresolved reason")
    # S8: currency/version honesty — version present, no historical exactness claimed.
    for c in resolved:
        ev = by_id.get(c["evidence_id"] or "", {})
        prov = ev.get("provenance", {})
        if not prov.get("corpus_version") or not prov.get("historical_text_unverified"):
            failures.append(f"S8: {c['citation_id']} lacks version/historical flags")
    return (len(failures) == 0), failures
