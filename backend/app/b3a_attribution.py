"""B3-A: evidence attribution (deterministic, no LLM).

Assigns every evidence passage one attribution status:

  COURT_VOICE / PARTY_VOICE / QUOTED_OTHER_JUDGMENT / QUOTED_STATUTE /
  PROCEDURAL_DESCRIPTION / UNRESOLVED_ATTRIBUTION

Only COURT_VOICE passages may support material court claims downstream.
Everything else is displayable labeled source material at most; UNRESOLVED
fails closed. Unknown attribution is safer than inferred attribution: no
adoption inference, no similarity-based attribution, no LLM.

Rule order is load-bearing (first match wins, rule_id recorded):
  R-PARTY    party-voice patterns (arguments, not court reasoning)
  R-QUOTE    substantial quotation marks -> sub-classify content
  R-PRIOR    prior/other-court references without quotation
  R-PROC     procedural boilerplate (appeal instructions, service, appendices)
  R-STATUTE  statute-shaped quotation outside the above
  R-COURT    positive court-voice markers (B2-C rule reuse)
  R-UNKNOWN  fallthrough -> UNRESOLVED_ATTRIBUTION (fail closed)
"""
from __future__ import annotations

import re

COURT_VOICE = "COURT_VOICE"
PARTY_VOICE = "PARTY_VOICE"
QUOTED_OTHER_JUDGMENT = "QUOTED_OTHER_JUDGMENT"
QUOTED_STATUTE = "QUOTED_STATUTE"
PROCEDURAL_DESCRIPTION = "PROCEDURAL_DESCRIPTION"
UNRESOLVED_ATTRIBUTION = "UNRESOLVED_ATTRIBUTION"

ELIGIBLE_FOR_COURT_CLAIM = frozenset({COURT_VOICE})

# Party attribution openers and voice (broader than B2-C's sentence filter:
# attribution must also catch section-framed party content). Bare 等語 alone
# is NOT a trigger (prosecutors narrate utterances too); it only confirms
# passages that already carry an attribution verb.
_PARTY = re.compile(
    r"原告主張|被告主張|原告抗辯|被告抗辯|原告辯稱|被告辯稱|"
    r"原告訴稱|被告辯稱|上訴意旨[：:]|答辯意旨[：:]|聲請意旨[：:]|答辯要旨|辯護要旨|"
    r"辯稱|主張|聲明|抗辯|答辯|辯護|陳稱|供稱|聲稱|(?<!意思)表示|自稱|"
    r"(?:原告|被告|告訴人|上訴人|被上訴人|聲請人|相對人|辯護人).{0,6}稱")
# Prior / other-court references (not the current court's reasoning).
_PRIOR = re.compile(
    r"最高法院|高等法院|前案|另案|原審|前審|他案|"
    r"確定終局裁判|確定終局判決|判決意旨|裁定意旨|判決可參|判決理由|"
    r"前揭.{0,6}判決|另案判決")
# Operative prosecutorial voice (passage-level). Bare 起訴書 is deliberately
# NOT a trigger (courts legitimately narrate "引用檢察官起訴書之記載").
_PROSECUTOR = re.compile(
    r"公訴意旨|告訴意旨|報告意旨|"
    r"檢察官.{0,8}(認|以為|主張|聲請)|此致")
# Prosecutorial-document framing: an indictment/report chunk (此致 + named
# prosecutor / 正本證明, without court-judgment framing) caps COURT_VOICE at
# UNRESOLVED — passage rules cannot see document genre, this one can.
_PROS_DOC = re.compile(r"此致|正本證明|告訴暨報告意旨")
_PROS_NAMED = re.compile(r"檢察官\s*\S{2,5}\s*($|\r|\n)")
_COURT_DOC = re.compile(r"主[\s　]{0,4}文|本院(判決|認為|查|核)")


def _prosecutorial_document(context: str) -> bool:
    if not context:
        return False
    return (bool(_PROS_DOC.search(context)) or bool(_PROS_NAMED.search(context))) \
        and _COURT_DOC.search(context) is None
# Procedural boilerplate (instructions, service, appendix labels).
_PROC = re.compile(
    r"如不服.{0,12}(判決|裁定)|提出(上訴|抗告).{0,8}書狀|送達後.{0,6}日內|"
    r"附具繕本|上為正本係照原本作成|書記官|附錄|附件|言詞辯論終結|"
    r"審判長|受命法官|陪席法官|提起公訴|正本證明|此致")
_QUOTE_CHARS = "「『」』"
_LAW_NAME_HINT = re.compile(r"(法|例|則|綱|約|條例|通則|準則|辦法|細則|要點)")


def _court_marker_local(text: str) -> str | None:
    """Court-voice markers for attribution: B2-C's list minus bare 爰/爰以."""
    words = ("本院認為", "本院查", "本院核", "審酌", "斟酌", "綜上", "從而", "足見",
             "足認", "堪認", "難認", "難謂", "顯見", "顯係", "應認", "應屬",
             "參以", "復參", "佐以", "核閱", "經查", "經核")
    stripped = text.lstrip(" \t\r\n　")
    m = re.match(r"^(?:查|按|核)(?![照理])", stripped)
    if m:
        return m.group(0)
    for w in words:
        if w in text:
            return w
    if re.search(r"[。；，、：\s　](?:查|按|核)(?![照理])", text):
        return "查/按/核(句中)"
    return None


def _quoted_spans(text: str) -> list[tuple[int, int]]:
    """Balanced 「」/『』 spans (naive stack; nested quotes kept outermost)."""
    opens = {"「": "」", "『": "』"}
    stack: list[tuple[str, int]] = []
    spans: list[tuple[int, int]] = []
    for i, ch in enumerate(text):
        if ch in opens:
            stack.append((ch, i))
        elif ch in "」』" and stack and opens[stack[-1][0]] == ch:
            _, start = stack.pop()
            if not stack:
                spans.append((start, i + 1))
    return spans


def _statute_shaped(text: str) -> bool:
    num = r"[0-9一二三四五六七八九十百千零○兩\-]+"
    return re.search(rf"[法例則綱約]第{num}條", text) is not None


def _opens_with_statute_text(text: str) -> bool:
    # Appendix-style reproduction: the passage opens with a law-article header
    # (an 附錄/附件 label line may precede it) AND carries no court-operative
    # markers of its own. Mere in-text citations (even early ones) in
    # court-voiced sentences never qualify.
    num = r"[0-9一二三四五六七八九十百千零○兩\-]+"
    s = text.lstrip(" \t\r\n　")
    s = re.sub(r"^(附錄|附件)[^\n]{0,60}\n", "", s, count=1)
    if re.match(rf".{{0,12}}[法例則綱約]第{num}條", s) is None:
        return False
    return _court_marker_local(text) is None


def classify_attribution(passage: str, *, context_before: str = "",
                         context_after: str = "") -> dict:
    """Classify one passage. context_* are adjacent chunk text (for section
    framing); passage carries the candidate span. Returns attribution record
    with rule_id + evidence spans, never an inference."""
    text = passage or ""
    if not text.strip():
        return _out(UNRESOLVED_ATTRIBUTION, "R-UNKNOWN", text, "empty passage")
    # R-DISP: a 主文-headed span is the court's formal disposition by structure
    # (same heading rule as B2-C; references like 裁定如主文 never match it).
    stripped = text.lstrip(" \t\r\n　")
    if re.match(r"主[\s　]{0,4}文(?!所示)", stripped):
        return _out(COURT_VOICE, "R-DISP", text, "disposition heading structure")
    # R-PARTY: party voice anywhere in the passage.
    m = _PARTY.search(text)
    if m:
        return _out(PARTY_VOICE, "R-PARTY", text,
                    f"party-voice marker {m.group(0)!r}")
    # R-PARTY via section framing: an unclosed party-attribution opener in the
    # preceding context (e.g. 原告主張： three sentences ago) frames this one.
    fm = re.search(r"(原告主張|被告抗辯|被告辯稱|原告聲明|上訴意旨|答辯意旨)[：:]",
                   context_before[-120:])
    if fm and not re.search(r"(本院認為|本院查|本院核|查|按|核).{0,20}$",
                            context_before[-120:]):
        return _out(PARTY_VOICE, "R-PARTY", text,
                    f"party-framed section opener {fm.group(1)!r}")
    # R-QUOTE: substantial quotation.
    spans = _quoted_spans(text)
    quoted_len = sum(e - s for s, e in spans)
    if spans and quoted_len >= max(20, len(text) // 2):
        inner = "".join(text[s:e] for s, e in spans)
        if _statute_shaped(inner):
            return _out(QUOTED_STATUTE, "R-QUOTE", text, "statute-shaped quotation")
        if _PRIOR.search(inner) or _PRIOR.search(text[:spans[0][0]][-40:]):
            return _out(QUOTED_OTHER_JUDGMENT, "R-QUOTE",
                        text, "prior-judgment quotation")
        return _out(UNRESOLVED_ATTRIBUTION, "R-QUOTE", text,
                    "quotation of undetermined voice")
    # R-PROSECUTOR: prosecutorial voice is non-court (fail closed as
    # UNRESOLVED, never PARTY_VOICE mislabel, never COURT_VOICE).
    m = _PROSECUTOR.search(text)
    if m:
        return _out(UNRESOLVED_ATTRIBUTION, "R-PROSECUTOR", text,
                    f"prosecutorial voice {m.group(0)!r}")
    # R-PRIOR: prior/other-court reference without quotation marks.
    # Fires only when the passage carries NO court-operative markers of its
    # own: a court conclusion merely accompanied by a parenthetical cite
    # (自應認為…（最高法院…可參）) stays COURT_VOICE; a bare prior-court
    # proposition does not.
    m = _PRIOR.search(text)
    if m:
        from . import b2c_evidence as _b2c
        if _b2c._is_court_voiced(text) is None:
            return _out(QUOTED_OTHER_JUDGMENT, "R-PRIOR", text,
                        f"other-court reference {m.group(0)!r} without court markers")
    # R-PROC: procedural boilerplate.
    m = _PROC.search(text)
    if m:
        return _out(PROCEDURAL_DESCRIPTION, "R-PROC", text,
                    f"procedural marker {m.group(0)!r}")
    # R-STATUTE: statute-shaped quotation outside the above. Fires only when
    # the passage OPENS with article text (appendix-style reproduction: an
    # early law-article header), never for a mere in-text citation — otherwise
    # any order body mentioning a statute would misclassify.
    # NOTE: bare 爰/爰以 are NOT court markers here (applicants use 爰依/爰以);
    # genuine court sentences carry 核/審酌 etc. alongside.
    if _opens_with_statute_text(text):
        return _out(QUOTED_STATUTE, "R-STATUTE", text,
                    "passage opens with reproduced statute text")
    # R-COURT: positive court-voice markers. Uses a local marker test that
    # EXCLUDES bare 爰/爰以 (applicants write 爰依/爰以; genuine court
    # sentences carry 核/審酌/查/按 alongside). The shared B2-C rule is left
    # untouched for B2-C's own extraction behavior.
    marker = _court_marker_local(text)
    if marker is not None:
        if _prosecutorial_document(context_before + "\n" + context_after):
            return _out(UNRESOLVED_ATTRIBUTION, "R-DOC", text,
                        "prosecutorial-document framing caps court markers")
        return _out(COURT_VOICE, "R-COURT", text, f"court marker {marker!r}")
    return _out(UNRESOLVED_ATTRIBUTION, "R-UNKNOWN", text,
                "no attribution rule fires")


def _out(status: str, rule_id: str, text: str, reason: str) -> dict:
    return {"attribution": status, "rule_id": rule_id,
            "eligible_for_court_claim": status in ELIGIBLE_FOR_COURT_CLAIM,
            "reason": reason, "passage": text}


def attribution_gate(items: list[dict]) -> tuple[bool, list[str]]:
    """Q1-Q8 over attribution records. Each item: {evidence_id, chunk_id,
    source_span, attribution, rule_id, ...} plus usage flags:
    used_for_court_claim (bool)."""
    failures: list[str] = []
    for it in items:
        eid = it.get("evidence_id", "?")
        att = it.get("attribution", "")
        # Q1: every material reasoning claim has attribution metadata.
        if it.get("used_for_court_claim") and not att:
            failures.append(f"Q1: {eid} used without attribution")
        # Q6: provenance present.
        sp = it.get("source_span", {})
        if (not it.get("chunk_id") or not isinstance(sp.get("start"), int)
                or not isinstance(sp.get("end"), int) or sp["start"] >= sp["end"]):
            failures.append(f"Q6: {eid} lacks source provenance")
        if not it.get("rule_id"):
            failures.append(f"Q6: {eid} lacks rule_id")
        # Q7: type/attribution distinct namespace (no conflated label).
        if att and att not in (COURT_VOICE, PARTY_VOICE, QUOTED_OTHER_JUDGMENT,
                               QUOTED_STATUTE, PROCEDURAL_DESCRIPTION,
                               UNRESOLVED_ATTRIBUTION):
            failures.append(f"Q7: {eid} bad attribution label")
        if not it.get("used_for_court_claim"):
            continue
        # Q2: party voice never a court claim.
        if att == PARTY_VOICE:
            failures.append(f"Q2: party voice as court claim: {eid}")
        # Q3: quoted prior judgment needs authorized adoption evidence.
        if att == QUOTED_OTHER_JUDGMENT and not it.get("adoption_evidence"):
            failures.append(f"Q3: quoted judgment as court claim: {eid}")
        # Q4: quoted statute text is not court reasoning.
        if att == QUOTED_STATUTE:
            failures.append(f"Q4: quoted statute as court reasoning: {eid}")
        # Q5: unresolved never supports a material court claim.
        if att in (UNRESOLVED_ATTRIBUTION, "", None):
            failures.append(f"Q5: unresolved attribution as court claim: {eid}")
        if att == PROCEDURAL_DESCRIPTION:
            failures.append(f"Q5: procedural text as court claim: {eid}")
    # Q8 is enforced at the serving boundary (test-pinned), not in this pure gate.
    return (len(failures) == 0), failures
