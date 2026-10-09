"""B3-C unit: disposition normalization (real 主文 shapes, conservative)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app import b3c_contra as K  # noqa: E402


def test_civil_outcomes():
    assert K.normalize_disposition("主文原告之訴駁回。") == "DISMISSED"
    assert K.normalize_disposition("被告應給付原告。准許原告之請求。") == "GRANTED"
    assert K.normalize_disposition("一部准許，一部駁回。") == "PARTIALLY_GRANTED"
    assert K.normalize_disposition("上訴駁回。") == "APPEAL_DISMISSED"
    assert K.normalize_disposition("原判決撤銷，發回更審。") == "REMANDED"


def test_criminal_sentences_are_unknown_not_granted():
    # A criminal sentence is not a grant/denial of relief: UNKNOWN, and it
    # must never manufacture a DIRECT conflict.
    assert K.normalize_disposition("羅仕竤犯傷害罪，處拘役肆拾日。") == "UNKNOWN"
    assert K.normalize_disposition("處有期徒刑一年。") == "UNKNOWN"


def test_split_frame_and_ambiguous():
    assert K.normalize_disposition("反訴原告之訴駁回。本訴原告勝訴。") == "SPLIT_FRAME"
    assert K.normalize_disposition("本件不受理。") == "UNKNOWN"
    assert K.normalize_disposition("應予維持。") == "OTHER"
    assert K.normalize_disposition("") == "UNKNOWN"


def test_opposition_rule():
    assert K._opposed("DISMISSED", "GRANTED")
    assert not K._opposed("DISMISSED", "DISMISSED")
    assert not K._opposed("APPEAL_DISMISSED", "DISMISSED")  # different stage
    assert not K._opposed("DISMISSED", "UNKNOWN")
    assert not K._opposed("PARTIALLY_GRANTED", "DISMISSED")
