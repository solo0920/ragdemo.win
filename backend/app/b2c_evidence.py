"""B2-C: holding / disposition / reasoning evidence (extractive, deterministic).

No LLM, no inference of doctrine. Three evidence types with explicit,
reviewable selection rules:

- DISPOSITION: 主文-headed spans (structural markers). Marker presence is
  sufficient; labeled-but-markerless chunks yield flagged full-chunk evidence.
- REASONING: sentences in REASONING-role chunks matching court-voice markers
  and NOT matching party-voice exclusions (party arguments are never court
  reasoning). QUOTED_* roles are never sources (quoted material belongs to
  another judgment).
- HOLDING: conclusory sentences matched by explicit holding patterns.
  A sentence inside a disposition span is DISPOSITION, never HOLDING
  (structure wins over heuristic). All holding selections carry the matched
  pattern and a heuristic flag; the holding/disposition boundary limits are
  recorded, not hidden.

Relations are evidence-weak only: same_chunk, contains_citation, same_document.
No causal or applied/decisive claims are ever emitted here.
"""
from __future__ import annotations

import re

# 主文 marker with any inter-character spacing (U+3000, spaces, line breaks).
DISPOSITION_MARKER = re.compile(r"主[\s\u3000]{0,4}文|主[\s\u3000]{0,4}旨|裁判主文")
SECTION_MARKER = re.compile(
    r"主[\s\u3000]{0,4}文|事實及理由|理由要領|理由|附錄|附件|犯罪事實|證據|"
    r"本院認為|程序事項|實體事項")

# Section headings for continuation detection live in _opens_with_header
# (spacing-tolerant, line-start anchored).


def _opens_with_header(text: str) -> bool:
    # Spacing-tolerant: headings are often spread with full-width spaces.
    compact = re.sub(r"[ \t　]+", "", text[:200])
    m = re.search(r"(?:^|\r|\n)(?:主文|事實及理由|理由要領|理由|附錄|附件|犯罪事實)",
                  compact)
    if not m:
        return False
    return not compact[:m.start()].strip("\r\n")

# Court-voice markers. Single chars (查/按/核) match only at sentence start or
# after a boundary (never inside 調查/按照): see _COURT_START.
_COURT_WORDS = ("本院認為", "本院查", "本院核", "審酌", "綜上", "從而", "足見",
                "足認", "堪認", "難認", "難謂", "顯見", "顯係", "應認", "應屬",
                "爰", "參以", "復參", "佐以", "核閱", "經查", "經核")
_COURT_START = re.compile(r"^(?:查|按|核)(?![照理])")
_COURT_AFTER_PUNCT = re.compile(r"[。；，、：\s　](?:查|按|核)(?![照理])")
# Party-voice exclusions (checked first): these sentences attribute claims to
# parties, they are not the court's reasoning. 等語 marks reported speech
# (a party's content as retold); court holdings never end in 等語.
_PARTY = re.compile(r"辯稱|主張|聲明|抗辯|答辯|辯護|上訴意旨|聲請意旨.{0,6}所陳.{0,6}之前|等語")

# Conclusory holding patterns (explicit list; matched pattern is recorded).
_HOLDING = (
    "難認有據", "為無理由", "應予駁回", "應予准許", "難認有理由", "洵屬無據",
    "於法無據", "應予維持", "應予撤銷", "應予發回", "難認有據以",
)

_SENT_SPLIT = re.compile(r"[。！？；]+")


def _sentences_with_spans(text: str):
    start = 0
    for m in _SENT_SPLIT.finditer(text):
        end = m.end()
        if text[start:end].strip():
            yield text[start:end], start, end
        start = end
    if start < len(text) and text[start:].strip():
        yield text[start:], start, len(text)


def _is_court_voiced(sentence: str) -> str | None:
    """Return the matched marker, or None. Party voice excluded first."""
    if _PARTY.search(sentence):
        return None
    stripped = sentence.lstrip(" \t\r\n　")
    m = _COURT_START.match(stripped)
    if m:
        return m.group(0)
    for w in _COURT_WORDS:
        if w in sentence:
            return w
    if _COURT_AFTER_PUNCT.search(sentence):
        return "查/按/核(句中)"
    return None


def _holding_pattern(sentence: str) -> str | None:
    for p in _HOLDING:
        if p in sentence:
            return p
    return None


def _disposition_span(text: str):
    # A 主文 heading starts a line (modulo indentation, incl. full-width
    # spaces); in-text references (裁定如主文, 如主文所示之刑, 處刑如主文)
    # are NOT headings.
    for m in DISPOSITION_MARKER.finditer(text):
        start = m.start()
        k = start
        while k > 0 and text[k - 1] in " \t　":
            k -= 1
        if k != 0 and text[k - 1] not in "\r\n":
            continue
        if text[m.end():m.end() + 2] == "所示":
            continue
        n = SECTION_MARKER.search(text, m.end())
        end = n.start() if n else len(text)
        return start, end, n is None
    return None


def _mark_continuations(nodes: list[dict], chunks: list[dict]) -> None:
    """§12: a disposition span cut at chunk end continues only if the next
    chunk (span order, same document) opens without a section header."""
    bydoc: dict[str, list[dict]] = {}
    for ch in chunks:
        bydoc.setdefault(ch["document_id"], []).append(ch)
    for doc, chs in bydoc.items():
        chs.sort(key=lambda c: (c.get("span") or {}).get("start", 0))
        for i, ch in enumerate(chs):
            if i + 1 >= len(chs):
                continue
            nxt = chs[i + 1]
            # Continuation test: the next chunk must open with continuation
            # text. A chunk opening directly with a section header means the
            # disposition ended cleanly at the boundary.
            if _opens_with_header(nxt["text"]):
                continue
            for n in nodes:
                if (n["chunk_id"] == ch["chunk_id"]
                        and n["evidence_type"] == "DISPOSITION"
                        and n["source_span"]["end"] == len(ch["text"])
                        and n["provenance"].get("open_ended")):
                    n["provenance"]["continues_in_next_chunk"] = True
                    n["provenance"]["continued_chunk_id"] = nxt["chunk_id"]
                    n["provenance"]["incomplete"] = True


def extract_disposition(chunk: dict) -> list[dict]:
    """DISPOSITION evidence from one corpus record (chunk dict with text,
    chunk_id, document_id, structural_role/labels, span, content hash fields).
    """
    text = chunk["text"]
    jid = chunk["document_id"]
    labels = chunk.get("structural_labels", [])
    flagged = "carries_declared_disposition_unit" in chunk and chunk["carries_declared_disposition_unit"]
    span = _disposition_span(text)
    if span is None:
        if not (flagged or "DISPOSITION" in labels or chunk.get("structural_role") == "DISPOSITION"):
            return []
        return [_ev(jid, chunk, "DISPOSITION", text, 0, len(text),
                    method="marker_absent_flagged_chunk",
                    note="labeled disposition unit without in-chunk marker")]
    start, end, open_ended = span
    return [_ev(jid, chunk, "DISPOSITION", text[start:end], start, end,
                method="zhu_wen_marker", open_ended=open_ended)]


_REASONING_ROLES = frozenset({"REASONING", "FACTS"})


def extract_reasoning(chunk: dict) -> list[dict]:
    """REASONING evidence: court-voiced sentences in REASONING/FACTS chunks.

    FACTS chunks are included because frozen roles place court-voiced
    reasoning (查/按/核...) inside 事實及理由 sections; the court-voice
    marker + party-voice exclusion (not the role) is the precision mechanism.
    HEADER/CLOSING/APPENDIX/QUOTED_*/DISPOSITION roles never yield reasoning.
    """
    if chunk.get("structural_role") not in _REASONING_ROLES:
        return []
    jid = chunk["document_id"]
    out = []
    for sent, s, e in _sentences_with_spans(chunk["text"]):
        marker = _is_court_voiced(sent)
        if marker is None:
            continue
        out.append(_ev(jid, chunk, "REASONING", sent, s, e,
                       method="court_voice_marker", marker=marker))
    return out


def extract_holding(chunk: dict, disposition_spans: list[tuple[int, int]] | None = None) -> list[dict]:
    """HOLDING evidence: conclusory sentences. Sentences inside a disposition
    span stay DISPOSITION (structure wins). Everything selected is flagged
    heuristic with its matched pattern."""
    jid = chunk["document_id"]
    disp = disposition_spans if disposition_spans is not None else []
    out = []
    for sent, s, e in _sentences_with_spans(chunk["text"]):
        if any(ds <= s < de for ds, de in disp):
            continue  # disposition territory, never holding
        pat = _holding_pattern(sent)
        if pat is None:
            continue
        out.append(_ev(jid, chunk, "HOLDING", sent, s, e,
                       method="conclusory_pattern", marker=pat, heuristic=True))
    return out


def _ev(jid: str, chunk: dict, etype: str, text: str, start: int, end: int, **extra) -> dict:
    base = chunk["chunk_id"].rsplit("#", 1)
    cidx = base[1] if len(base) > 1 else "C000"
    return {
        "evidence_id": f"{etype.lower()}:{jid}#{cidx}:{start}-{end}",
        "evidence_type": etype,
        "judgement_id": jid,
        "document_id": jid,
        "chunk_id": chunk["chunk_id"],
        "text": text,
        "source_span": {"start": start, "end": end, "unit": "code_point",
                        "basis": "chunk_text"},
        "provenance": {"structural_role": chunk.get("structural_role"),
                       "structural_labels": chunk.get("structural_labels", []),
                       **extra},
    }


def build_evidence_graph(judgement_chunks: list[dict], citations: list[dict]) -> dict:
    """Document-level evidence graph with weak, mechanical relations only."""
    nodes: list[dict] = []
    for ch in judgement_chunks:
        disps = extract_disposition(ch)
        dspans = [(d["source_span"]["start"], d["source_span"]["end"]) for d in disps]
        nodes.extend(disps)
        nodes.extend(extract_holding(ch, dspans))
        nodes.extend(extract_reasoning(ch))
    edges: list[dict] = []
    by_chunk: dict[str, list[str]] = {}
    for n in nodes:
        by_chunk.setdefault(n["chunk_id"], []).append(n["evidence_id"])
    for chunk_id, ids in by_chunk.items():
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                edges.append({"from": ids[i], "to": ids[j], "relation": "same_chunk"})
    texts = {ch["chunk_id"]: ch["text"] for ch in judgement_chunks}
    for n in nodes:
        if n["evidence_type"] not in ("REASONING", "HOLDING"):
            continue
        s, e = n["source_span"]["start"], n["source_span"]["end"]
        for c in citations:
            # The citation must come from THIS node's chunk text (sentence
            # substring) with its span inside the node span. chunk_id strings
            # need not match across conventions (corpus #C002 vs citation
            # #chunk0); text containment + span containment is the link.
            if c.get("judgement_id") != n["judgement_id"]:
                continue
            if c.get("evidence_text", "") not in texts.get(n["chunk_id"], ""):
                continue
            cs = c["source_span"]
            if s <= cs["start"] and cs["end"] <= e:
                edges.append({"from": n["evidence_id"], "to": c["citation_id"],
                              "relation": "contains_citation"})
    jids = {n["judgement_id"] for n in nodes}
    for n in nodes:
        edges.append({"from": n["evidence_id"],
                      "to": f"judgement:{n['judgement_id']}",
                      "relation": "same_document"})
    _mark_continuations(nodes, judgement_chunks)
    return {"judgement_ids": sorted(jids), "nodes": nodes, "edges": edges}


def reasoning_gate(graph: dict, chunk_texts: dict[str, str]) -> tuple[bool, list[str]]:
    """Mechanical gate G1-G8 over the evidence graph."""
    failures: list[str] = []
    for n in graph.get("nodes", []):
        eid = n.get("evidence_id", "?")
        # G1/G2/G3: judgement provenance on every node.
        if not n.get("judgement_id") or not n.get("chunk_id"):
            failures.append(f"G123: {eid} lacks judgement provenance")
        sp = n.get("source_span", {})
        if not (isinstance(sp.get("start"), int) and isinstance(sp.get("end"), int)
                and 0 <= sp["start"] < sp["end"]):
            failures.append(f"G123: {eid} bad source span")
            continue
        # G4: verbatim existence in source data.
        src = chunk_texts.get(n["chunk_id"])
        if src is None:
            failures.append(f"G4: {eid} chunk text unavailable")
        elif src[sp["start"]:sp["end"]] != n.get("text"):
            failures.append(f"G4: {eid} text not verbatim in source chunk")
        # G7: known types only, never conflated labels.
        if n.get("evidence_type") not in ("DISPOSITION", "HOLDING", "REASONING"):
            failures.append(f"G7: {eid} bad evidence type")
    # G5: cross-chunk handling flags present where spans hit chunk edges.
    for n in graph.get("nodes", []):
        prov = n.get("provenance", {})
        if prov.get("continues_in_next_chunk") and not prov.get("continued_chunk_id"):
            failures.append(f"G5: {n.get('evidence_id')} continuation unpointed")
    # G6: no generator provenance anywhere.
    for n in graph.get("nodes", []):
        if n.get("provenance", {}).get("generated"):
            failures.append(f"G6: {n.get('evidence_id')} claims generated text")
    # G8: no applied/decisive relation or label.
    for e in graph.get("edges", []):
        if e.get("relation") in ("applied_statute", "decisive_statute",
                                 "applies", "decides"):
            failures.append(f"G8: forbidden relation {e.get('relation')}")
    for n in graph.get("nodes", []):
        if n.get("evidence_type") in ("APPLIED_STATUTE", "DECISIVE_STATUTE"):
            failures.append(f"G8: forbidden type on {n.get('evidence_id')}")
    return (len(failures) == 0), failures
