#!/usr/bin/env python3
"""抽樣清單的釘選與重現（spec 004 T006 / FR-009 / INV-REPRO）。

## 這份模組做什麼

把 `corpus-schema-survey.md` 那次調查用的 **494 筆抽樣**釘成版控中的檔案，
使得「schema 未來若漂移」這件事可以被偵測。

調查當時用的樣本集合 SHA-256 是

    280b95bbdac0bb933568f55297ea352022457600851847b7ca955db3f0c2d8a3

這個值必須能重現。做不到的話，「後人依樣本修資料模型」的依據就消失了 ——
而那 494 筆是整個 8-key schema 契約的**唯一**經驗證依據（0.46% 抽樣，
corpus-wide 形狀仍是 UNKNOWN，見 spec FR-B02）。

## 為什麼清單是「釘選」而不是「每次重算」

`corpus-schema-survey.md` §2 記錄了 strata A–G 的規則，但它們**不是**能從
inventory 重新推導出同樣 494 筆的演算法：規則描述的是「60 個最常見的 JCASE
代碼」這類語意，而 JCASE 存在於 JSON 內容裡 —— 那需要解壓 + 解析（是 T008
之後的事）。重新實作 strata 會是一份**不同的**演算法，且無法保證產出同一組
路徑。

所以清單本身是證據：釘住當時實際抽到的那 494 筆，重現的是**清單的 digest**，
而不是重新跑抽樣。這是誠實的做法 —— 把「重新抽樣」與「驗證抽樣結果」這兩件
不同的事分開。

## 路徑分隔符的差異（容易踩的坑）

- **inventory**（T004）：archive 內的原始路徑，反斜線分隔，108,547/108,547
  全是反斜線。FR-007 要求原樣往返，不得正規化。
- **抽樣清單**：調查當時用 `unrar lt` 的輸出，正斜線分隔。
- **清單 digest**：依調查記錄的定義，對**排序後、正斜線、換行連接**的
  494 筆計算。

兩者之間的轉換只發生在本模組內部，且是單向、明確的。inventory 那一側永遠
維持反斜線 —— 那才是 authoritative。

## 這份不做什麼

不決定要擷取哪些判決進入 corpus（那是 T007，maintainer decision）、不解壓、
不解析 JSON、不對 entry 內容做任何斷言。它只認得「一份釘選的清單」與「一份
authoritative inventory」之間的包含關係。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "tests" / "fixtures" / "judgements" / "sample_manifest.json"

# corpus-schema-survey.md §1 記錄的樣本集合 SHA-256。
SAMPLE_SET_SHA256 = "280b95bbdac0bb933568f55297ea352022457600851847b7ca955db3f0c2d8a3"
SAMPLE_SIZE = 494


class ManifestError(RuntimeError):
    """清單本身有問題：格式錯誤、digest 不符、或與 inventory 不一致。"""


def forward_slash(path: str) -> str:
    """inventory 路徑（反斜線）→ 清單路徑（正斜線）。

    只換分隔符，不做任何其他轉換：不去空白、不改大小寫、不正規化點段。
    一份抽樣清單的價值全在於「它就是當時抽到的那一筆」，任何多餘的整理都會
    讓 digest 對不上。
    """
    return path.replace("\\", "/")


def backslash(path: str) -> str:
    """清單路徑（正斜線）→ inventory 路徑（反斜線）。"""
    return path.replace("/", "\\")


def sample_digest(paths: list[str]) -> str:
    """依調查定義計算樣本集合的 SHA-256。

    定義（可從 `280b95bb…` 反推並驗證）：排序 → 正斜線 → 以 `\\n` 連接
    → 無結尾換行 → UTF-8 → SHA-256。這個定義已對記錄值驗證通過。
    """
    joined = "\n".join(sorted(paths))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Manifest:
    """一份釘選的抽樣清單。frozen。"""

    paths: list[str]          # 正斜線分隔，已排序
    tags: dict[str, str]      # path → stratum 標籤（來源：調查的 strata A–G）
    digest: str               # 樣本集合 SHA-256
    source: dict              # 調查出處

    def __len__(self) -> int:
        return len(self.paths)

    def as_dict(self) -> dict:
        return {
            "paths": list(self.paths),
            "tags": dict(self.tags),
            "digest": self.digest,
            "source": dict(self.source),
        }


def load(path: Path | None = None) -> Manifest:
    """讀取並**驗證**釘選的清單。

    驗證在載入時就做，不是靠呼叫端記得檢查 —— 一份 digest 不符的清單若能被
    靜靜使用，等於 schema drift 偵測機制失效而沒有人知道。
    """
    p = MANIFEST_PATH if path is None else Path(path)
    if not p.is_file():
        raise ManifestError(f"抽樣清單不存在：{p}")
    raw = json.loads(p.read_text(encoding="utf-8"))

    paths = raw.get("paths")
    if not isinstance(paths, list) or not all(isinstance(x, str) for x in paths):
        raise ManifestError("清單的 paths 必須是字串陣列")

    digest = sample_digest(paths)
    declared = raw.get("digest")
    if declared != digest:
        raise ManifestError(
            f"清單 digest 不符：檔案宣告 {declared}，實際計算 {digest}"
        )
    if digest != SAMPLE_SET_SHA256:
        raise ManifestError(
            f"清單 digest 與 corpus-schema-survey.md 記錄值不符："
            f"{digest} != {SAMPLE_SET_SHA256}"
        )
    if len(paths) != SAMPLE_SIZE:
        raise ManifestError(f"清單筆數 {len(paths)} != 記錄的 {SAMPLE_SIZE}")

    tags = raw.get("tags", {})
    if not isinstance(tags, dict):
        raise ManifestError("清單的 tags 必須是物件")

    return Manifest(
        paths=paths, tags=tags, digest=digest, source=raw.get("source", {})
    )


def verify_against_inventory(manifest: Manifest, inventory_paths: list[str]) -> None:
    """確認抽樣清單是 inventory 的**嚴格子集**。

    三件事：
    1. 每一筆抽樣都存在於 inventory（抽樣不能憑空出現 entry）。
    2. 沒有重複（重複會讓覆蓋率統計失真）。
    3. 抽樣不是整份 inventory（那就不是抽樣了）。

    這是 T006 acceptance 的核心。它不需要解壓任何東西 —— inventory 的路徑
    集合就是全部需要的資訊。
    """
    if len(manifest.paths) != len(set(manifest.paths)):
        dupes = {p for p in manifest.paths if manifest.paths.count(p) > 1}
        raise ManifestError(f"清單有重複項目：{sorted(dupes)[:5]}")

    inv = set(inventory_paths)
    missing = [p for p in manifest.paths if backslash(p) not in inv]
    if missing:
        raise ManifestError(
            f"{len(missing)} 筆抽樣不在 inventory 中，例：{missing[:3]}"
        )

    if len(manifest.paths) >= len(inventory_paths):
        raise ManifestError(
            f"抽樣 {len(manifest.paths)} 筆 vs inventory {len(inventory_paths)} 筆 —— "
            "這不是抽樣"
        )


def tags_for(manifest: Manifest) -> dict[str, str]:
    """回傳 path → stratum 標籤（依 inventory 的反斜線形式為 key）。

    轉換只為了在報告裡能直接對照 inventory；清單內部維持正斜線。
    """
    return {backslash(p): t for p, t in manifest.tags.items()}


def strata_counts(manifest: Manifest) -> dict[str, int]:
    """各 stratum 的筆數。

    標籤格式是 `<stratum>:<detail>`（例如 `jcase-common:司促`），前綴才是
    stratum。只取前綴 —— detail 是 analyst 對當時那筆的描述，不是分類依據。
    """
    counts: dict[str, int] = {}
    for tag in manifest.tags.values():
        key = tag.split(":", 1)[0]
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))
