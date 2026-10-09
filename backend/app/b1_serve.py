"""B1: judgement retrieval + evidence-grounded answer vertical slice.

Scope (RAG-MASTER-ROADMAP B1): judgement retrieval, evidence assembly, statute
linking (Level 1: explicit full-name mention + corpus-verified statute text;
Level 3: abstain otherwise), extractive grounded composition, mechanical
grounding gate, abstention, citation. Level 2 retrieval-based linking,
contradiction/currency/contamination doctrine, and LLM-based generation are
explicitly deferred (see agent/architecture/B1-FOLLOWUPS.md).

Design rules inherited from spec 004 and the frozen layers:
- Reuse, don't duplicate: judgement views/citations come from
  `retrieve.judgment_hit_view` / `rag.judgment_citation`; refusal shape follows
  `rag.answer_judgments`; statutes come from the repo laws corpus file.
- No court inference (spec 004 FR-016): `court` stays None with a reason.
- JID is opaque (FR-028): never decomposed.
- Statute linking never guesses: exact (law_name, article) match in the corpus
  or the mention stays unlinked. Name aliases are NOT resolved here.
- The composed answer contains only allowlisted labels, verbatim quotes, and
  citations — so grounding-gate item 7 is mechanically checkable on this path.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import gateway, retrieve
from .rag import (
    JUDGMENT_NO_SOURCE_ANSWER,
    JudgmentAnswerError,
    answer_judgments,
    judgment_citation,
)

# Laws corpus: the authority for statute text (repo data, not a copy).
# Container layout differs from host: image WORKDIR is /app with code at
# /app/app and data mounted at /app/data, so the host-relative resolution
# below would point at /data (absent) inside the container (2026-10-09:
# live /judgments/query 500 with No such file '/data/laws/laws_flat.jsonl').
# Same /app/app probe as rag._in_container().
LAWS_FLAT = (
    Path("/app/data/laws/laws_flat.jsonl")
    if Path("/app/app").is_dir()
    else Path(__file__).resolve().parent.parent.parent
    / "data" / "laws" / "laws_flat.jsonl"
)

ABSTAIN_REASONS = {
    "invalid_question": "問題為空；無法檢索",
    "retrieval_unavailable": "判決檢索服務不可用；不生成答案",
    "no_judgement_hits": "在目前的判決資料中找不到支持這個問題的原文",
    "no_quotable_evidence": "檢索到判決但無原文可引用；不生成答案",
    "no_statute_evidence": "判決證據存在，但其引用的法規在法規資料庫中無法驗證；不生成答案",
    "grounding_gate_fail": "grounding gate 未通過；不輸出答案",
}

# Fixed answer labels. They carry no legal content; the grounding gate
# allowlists exactly these (plus quotes and citations).
LABEL_JUDGEMENT = "【判決引用】"
LABEL_STATUTE = "【法規引用】"
ALLOWLIST_LABELS = (LABEL_JUDGEMENT, LABEL_STATUTE, "【", "】")

_CJK_DIGITS = {
    "○": 0, "零": 0, "一": 1, "二": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "兩": 2,
}
_CJK_UNITS = {"十": 10, "百": 100, "千": 1000}


def cjk_numeral_to_arabic(s: str) -> str | None:
    """Chinese-numeral article number to arabic (e.g. 四十四 -> 44).

    Returns None for shapes this converter does not understand instead of
    guessing — unconverted mentions simply do not link.
    """
    if re.fullmatch(r"[0-9\-]+", s):
        return s
    if not re.fullmatch(r"[○零一二三四五六七八九兩十百千]+", s):
        return None
    total, current = 0, 0
    prev_small_unit = False  # previous char was 十 (next digit adds, not appends)
    prev_char_unit_ge100 = False
    for ch in s:
        if ch in _CJK_UNITS:
            unit = _CJK_UNITS[ch]
            current = (current if current else 1) * unit
            if unit >= 100:
                total += current
                current = 0
            prev_small_unit = unit == 10
        else:
            d = _CJK_DIGITS[ch]
            if prev_small_unit:
                current += d  # 四十四: 40 + 4; 十五: 10 + 5
            else:
                current = current * 10 + d
            prev_small_unit = False
    total += current
    # Reject degenerate parses (e.g. 十百); recomposition must be stable.
    if total <= 0 or total > 9999:
        return None
    return str(total)


def normalize_article(article: str) -> str:
    """Corpus article_no to lookup form: strip spaces, arabic numerals."""
    compact = article.replace(" ", "").replace("　", "")
    m = re.fullmatch(r"第(.+)條", compact)
    if not m:
        return compact
    num = cjk_numeral_to_arabic(m.group(1))
    return f"第{num}條" if num is not None else compact


@dataclass
class StatuteCorpus:
    """Exact (law_name, article) lookup over the repo laws corpus."""

    rows: dict[tuple[str, str], dict] = field(default_factory=dict)
    source_id: str = "data/laws/laws_flat.jsonl"

    @classmethod
    def from_jsonl(cls, path: Path | str = LAWS_FLAT) -> "StatuteCorpus":
        rows: dict[tuple[str, str], dict] = {}
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                rows.setdefault(
                    (d["law_name"], normalize_article(d["article_no"])), d
                )
        return cls(rows=rows)

    @property
    def law_names(self) -> list[str]:
        return sorted({ln for (ln, _) in self.rows}, key=len, reverse=True)

    def find(self, law_name: str, article: str) -> dict | None:
        key = (law_name, normalize_article(article))
        hit = self.rows.get(key)
        if hit is not None:
            return hit
        # Orthographic variant (explicit, corpus-evidenced): judgements write
        # inserted articles as 之N (第436條之18) while this corpus renders them
        # exclusively as hyphen-form (第436-18條; 3402 rows, 0 之-form rows).
        # Exact match always wins; the fallback never substitutes a different
        # article (a base article without the branch is NOT a candidate).
        m = re.fullmatch(r"第([0-9]+)條之([0-9]+)", key[1])
        if m:
            return self.rows.get((law_name, f"第{m.group(1)}-{m.group(2)}條"))
        return None


_MENTION_RE_CACHE: dict[int, re.Pattern] = {}


def _mention_re(corpus: StatuteCorpus) -> re.Pattern:
    key = id(corpus)
    if key not in _MENTION_RE_CACHE:
        names = "|".join(re.escape(n) for n in corpus.law_names)
        num = r"[0-9一二三四五六七八九十百千零○兩\-]+"
        _MENTION_RE_CACHE[key] = (
            re.compile(
                f"(?P<law>{names})第(?P<art>{num})條(?P<branch>之{num})?"
                f"(?P<detail>第{num}[項款])?"
            ),
            re.compile(f"第(?P<art>{num})條(?P<branch>之{num})?(?P<detail>第{num}[項款])?"),
        )
    return _MENTION_RE_CACHE[key]


# Split on hard sentence boundaries and blank lines only — a single line
# break is layout (judgements wrap mid-mention), not a sentence boundary.
_SENT_SPLIT = re.compile(r"[。；！？]+|\r?\n[ \t\x3000]*\r?\n")


@dataclass
class StatuteMention:
    law_name: str
    article: str  # lookup form, e.g. 第184條 (branch folded in, e.g. 第436條之18)
    detail: str | None  # e.g. 第1項
    surface: str  # mention text (whitespace-collapsed; see below)
    scoped: bool = False  # True when the law name comes from same-sentence scoping
    scoped_from: str | None = None  # the verbatim qualified mention providing the law
    surface_collapsed: bool = False  # True when layout whitespace split the mention


def _build_mention(m: re.Match, law_name: str, *, scoped: bool = False,
                   scoped_from: str | None = None) -> StatuteMention | None:
    num = cjk_numeral_to_arabic(m.group("art"))
    if num is None:
        return None
    article = f"第{num}條"
    branch = m.group("branch")
    if branch:
        bnum = cjk_numeral_to_arabic(branch.removeprefix("之"))
        if bnum is None:
            return None
        article = f"第{num}條之{bnum}"
    return StatuteMention(
        law_name=law_name, article=article, detail=m.group("detail"),
        surface=m.group(0), scoped=scoped, scoped_from=scoped_from,
    )


def _sentences_with_spans(text: str):
    """Split like extract does, but yield (sentence, base_offset) so spans map
    back to original coordinates. Splitter identical to _SENT_SPLIT."""
    start = 0
    for m in _SENT_SPLIT.finditer(text):
        if m.start() > start:
            yield text[start:m.start()], start
        start = m.end()
    if start < len(text):
        yield text[start:], start


def _collapse_with_map(sent: str):
    """Whitespace-collapsed sentence + index map (collapsed pos -> original pos)."""
    chars, amap = [], []
    for i, ch in enumerate(sent):
        if ch.isspace():
            continue
        amap.append(i)
        chars.append(ch)
    return "".join(chars), amap


def extract_statute_mentions_with_spans(text: str, corpus: StatuteCorpus):
    """extract_statute_mentions plus original-coordinate spans.

    Returns [(mention, start, end, sentence)] with start/end as code-point
    offsets into `text` (the un-collapsed input), sentence the original
    (un-collapsed) sentence containing the mention. Order and objects are
    identical to extract_statute_mentions.
    """
    qualified_re, bare_re = _mention_re(corpus)
    out: list[tuple[StatuteMention, int, int, str]] = []
    for sent, base in _sentences_with_spans(text):
        if not sent.strip():
            continue
        flat, amap = _collapse_with_map(sent)
        collapsed = bool(re.search(r"\s", sent))
        quals = list(qualified_re.finditer(flat))
        for m in quals:
            built = _build_mention(m, m.group("law"))
            if built is None:
                continue
            if collapsed:
                built.surface_collapsed = True
            out.append((built, base + amap[m.start()],
                        base + amap[m.end() - 1] + 1, sent))
        spans = [(m.start(), m.end()) for m in quals]
        for m in bare_re.finditer(flat):
            if any(s <= m.start() < e for s, e in spans):
                continue
            prev = [qm for qm in quals if qm.start() < m.start()]
            if not prev:
                continue
            qm = prev[-1]
            built = _build_mention(m, qm.group("law"), scoped=True,
                                   scoped_from=qm.group(0))
            if built is None:
                continue
            if collapsed:
                built.surface_collapsed = True
            out.append((built, base + amap[m.start()],
                        base + amap[m.end() - 1] + 1, sent))
    return out

def extract_statute_mentions(text: str, corpus: StatuteCorpus) -> list[StatuteMention]:
    """Level-1 mention extraction: full law name + article, verbatim.

    Thin wrapper over extract_statute_mentions_with_spans (identical order and
    objects; spans dropped). All semantics documented there.
    """
    return [m for m, _, _, _ in extract_statute_mentions_with_spans(text, corpus)]


def make_judgement_evidence(view: dict, quote: str, citation: str, score) -> dict:
    jid = view["jid"]
    return {
        "evidence_id": f"jdg:{jid}#c{view['chunk_index']}",
        "evidence_type": "judgement",
        "source_id": view["entry_path"],
        "document_id": jid,
        "chunk_id": f"{jid}#chunk{view['chunk_index']}",
        "citation": citation,
        "text": quote,
        # Authority model (spec 004 FR-016/FR-028): no court field exists in the
        # source, and JID is opaque. Recorded as absent, never inferred.
        "court": None,
        "court_unavailable_reason": "source provides no court field (FR-016); not inferred",
        "judgement_number": jid,
        "judgement_date": view["jdate"],
        "provenance": {
            "entry_path": view["entry_path"],
            "start_offset": view["start_offset"],
            "end_offset": view["end_offset"],
            "content_hash": view["content_hash"],
            "jdate": view["jdate"],
            "retrieved_score": score,
        },
    }


def make_statute_evidence(row: dict, mention: StatuteMention) -> dict:
    law, art = row["law_name"], row["article_no"].replace(" ", "")
    return {
        "evidence_id": f"st:{row['pcode']}#{row['article_seq']}",
        "evidence_type": "statute",
        "source_id": "data/laws/laws_flat.jsonl",
        "document_id": row["pcode"],
        "chunk_id": f"{row['pcode']}#{row['article_seq']}",
        "citation": f"{law}{art}",
        "text": row["article_content"],
        "law_name": law,
        "article": art,
        "paragraph": mention.detail,
        "provenance": {
            "pcode": row["pcode"],
            "article_seq": row["article_seq"],
            "mention_surface": mention.surface,
            "surface_collapsed": mention.surface_collapsed,
            "scoped": mention.scoped,
            "scoped_from": mention.scoped_from,
            "orthography_mapped": normalize_article(mention.article) != normalize_article(row["article_no"]),
            "resolved_as": row["article_no"].replace(" ", ""),
        },
    }


def compose_answer(judgement_evs: list[dict], statute_evs: list[dict]) -> tuple[str, list[dict]]:
    """Extractive composition only: labels + verbatim quotes + citations.

    Every quoted span becomes exactly one claim. No free prose with legal
    content exists on this path by construction.
    """
    parts: list[str] = [LABEL_JUDGEMENT]
    claims: list[dict] = []
    for i, ev in enumerate(judgement_evs, 1):
        parts.append(f"[{i}] {ev['text']}\n{ev['citation']}")
        claims.append({
            "claim_id": f"claim-j{i}",
            "kind": "judgement_quote",
            "text": ev["text"],
            "evidence_ids": [ev["evidence_id"]],
        })
    parts.append(LABEL_STATUTE)
    for j, ev in enumerate(statute_evs, 1):
        parts.append(f"[法{j}] {ev['citation']}\n{ev['text']}")
        claims.append({
            "claim_id": f"claim-s{j}",
            "kind": "statute_text",
            "text": ev["text"],
            "evidence_ids": [ev["evidence_id"]],
        })
    return "\n".join(parts), claims


def grounding_gate(response: dict) -> tuple[bool, list[str]]:
    """Mechanical grounding validation (B1 items 1-6 + structural item 7).

    1. every claim has evidence_ids; 2. every id resolves; 3. every evidence
    has source + text; 4. citation metadata present; 5. text non-empty;
    6. evidence_type in {judgement, statute}; 7. answer carries no span
    outside quotes, citations, and allowlisted labels.
    """
    failures: list[str] = []
    if response.get("status") != "grounded":
        return False, ["not a grounded response"]
    by_id = {e["evidence_id"]: e for e in response.get("evidence", [])}
    for claim in response.get("claims", []):
        ids = claim.get("evidence_ids") or []
        if not ids:
            failures.append(f"{claim.get('claim_id')}: no evidence_ids")
            continue
        for eid in ids:
            ev = by_id.get(eid)
            if ev is None:
                failures.append(f"{claim.get('claim_id')}: unknown evidence_id {eid}")
                continue
            if not ev.get("source_id"):
                failures.append(f"{eid}: missing source_id")
            if not ev.get("citation"):
                failures.append(f"{eid}: missing citation")
            if not (ev.get("text") or "").strip():
                failures.append(f"{eid}: empty evidence text")
            if ev.get("evidence_type") not in ("judgement", "statute"):
                failures.append(f"{eid}: bad evidence_type")
    # Item 7: strip every grounded span; nothing legal-bearing may remain.
    remainder = response.get("answer") or ""
    for claim in response.get("claims", []):
        if claim.get("text"):
            remainder = remainder.replace(claim["text"], "", 1)
    for ev in response.get("evidence", []):
        if ev.get("citation"):
            remainder = remainder.replace(ev["citation"], "", 1)
    for label in ALLOWLIST_LABELS:
        remainder = remainder.replace(label, "")
    remainder = re.sub(r"[\s\[\]法0-9]+", "", remainder)
    if remainder:
        failures.append(f"answer carries ungrounded spans: {remainder[:60]!r}")
    return (len(failures) == 0), failures


def _abstain(reason: str, **extra) -> dict:
    return {
        "status": "insufficient_evidence",
        "answer": None,
        "judgements": [],
        "statutes": [],
        "evidence": [],
        "claims": [],
        "abstention": {"reason": reason, "detail": ABSTAIN_REASONS[reason], **extra},
    }


async def serve_question(
    question: str,
    *,
    search_fn=None,
    embed_fn=None,
    jfull_by_entry: dict | None = None,
    statute_corpus: StatuteCorpus | None = None,
    recall: int = 50,
    top_k: int = 5,
) -> dict:
    """Full B1 chain with injectable boundaries (production or test doubles)."""
    question = (question or "").strip()
    if not question:
        return _abstain("invalid_question")
    corpus = statute_corpus or StatuteCorpus.from_jsonl()
    jfull_by_entry = jfull_by_entry if jfull_by_entry is not None else {}

    # 1-2. judgement retrieval (existing validated path; injectable for tests).
    try:
        embed = embed_fn or gateway.embed
        vector = (await embed([question]))[0]
        search = search_fn or (lambda q, v, limit: retrieve.search_judgments(q, v, limit=limit))
        hits = await search(question, vector, recall)
    except Exception as e:  # noqa: BLE001 - retrieval failure is Case A, not a 500
        return _abstain("retrieval_unavailable", error=type(e).__name__)
    if not hits:
        return _abstain("no_judgement_hits")

    # 3. judgement evidence assembly (reuse frozen-tested T019/T020 behavior).
    try:
        jq = answer_judgments(question, hits[: max(top_k, 1)], jfull_by_entry=jfull_by_entry)
    except JudgmentAnswerError as e:
        return _abstain("no_quotable_evidence", error=str(e)[:200])
    if jq.get("no_match"):
        reason = "no_quotable_evidence" if hits else "no_judgement_hits"
        return _abstain(reason, trace=jq.get("trace", ""))
    quoted = jq.get("quoted_blocks", [])
    if not quoted:
        return _abstain("no_quotable_evidence")

    judgement_evs = [
        make_judgement_evidence(q["view"], q["text"], q["citation"], q["view"].get("score"))
        for q in quoted
    ]

    # 4. Level-1 statute linking from the actually-quoted text only.
    statute_evs: list[dict] = []
    seen: set[str] = set()
    unlinked: list[str] = []
    for q in quoted:
        for m in extract_statute_mentions(q["text"], corpus):
            row = corpus.find(m.law_name, m.article)
            if row is None:
                if m.surface not in unlinked:
                    unlinked.append(m.surface)
                continue
            ev = make_statute_evidence(row, m)
            if ev["evidence_id"] not in seen:
                seen.add(ev["evidence_id"])
                statute_evs.append(ev)
    # Strict B1 rule (Case B/C): a judgement answer with zero corpus-verified
    # statute evidence is insufficient — the legal conclusion would otherwise
    # rest on an unverified statute string.
    if not statute_evs:
        return _abstain("no_statute_evidence", unlinked_mentions=unlinked)

    # 5-6. grounded composition + mechanical gate.
    answer, claims = compose_answer(judgement_evs, statute_evs)
    docs: dict[str, dict] = {}
    for ev in judgement_evs:
        docs.setdefault(ev["document_id"], {
            "jid": ev["document_id"],
            "judgement_number": ev["judgement_number"],
            "date": ev["judgement_date"],
            "source": ev["source_id"],
        })
    response = {
        "status": "grounded",
        "answer": answer,
        "judgements": list(docs.values()),
        "statutes": [
            {"law_name": e["law_name"], "article": e["article"], "paragraph": e["paragraph"]}
            for e in statute_evs
        ],
        "evidence": judgement_evs + statute_evs,
        "claims": claims,
        "abstention": None,
        "unlinked_statute_mentions": unlinked,
    }
    ok, failures = grounding_gate(response)
    if not ok:
        return _abstain("grounding_gate_fail", gate_failures=failures)
    return response


async def serve_live(question: str, *, recall: int = 50, top_k: int = 5) -> dict:
    """Production entry: existing runtime only, no new model decisions.

    JFULL comes from the frozen seed store (S1); when the store is empty or
    unreadable this is {}, so a live call honestly abstains instead of
    fabricating quotes.
    """
    try:
        from . import judgement_store as _store
        jfull = _store.jfull_map()
    except Exception:
        jfull = {}
    return await serve_question(
        question, recall=recall, top_k=top_k, jfull_by_entry=jfull
    )
