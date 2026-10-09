# B3-D Follow-ups — recorded, NOT done in B3-D

- F1 Mention-only display prose ("本判決全文提及…", "檢察官／起訴所引用的法條包括…",
  "法院在其理由中明確引用…"): relations support all three wordings; fixed
  strings need product wording authorization + allowlist/gate/fixture updates.
- F2 Procedural-explanation sentences (436-18 class): no B3-A/B2-C marker
  fires; recall gap accepted. Any new marker needs reviewed evidence and
  B3-A-owner review — not a B3-D patch.
- F3 Applicant-request coloring (狀請鈞院): no rule captures it (D-08b
  limitation); candidate future rule with reviewed cases.
- F4 Cross-chunk split citations: spans are chunk-local; a mention wrapping a
  boundary yields partial mentions (conservative, documented).
- F5 UNRESOLVED rate (54% corpus): precision-first by design; recall study
  needs reviewed labels, not threshold tuning.
- F6 Quoted-statute B2-B citations: resolution behavior untouched; whether
  appendix reproductions should resolve at all is a joint B2-B policy
  question for later.
