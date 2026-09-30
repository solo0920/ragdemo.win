"""`ci.yml` 的 compose job 必須給齊 `compose.yaml` 的所有必填變數。

**這個缺口讓 CI 從 2026-09-27 起連續紅了 10 次 push**（`d02ba7e` 把 `HOST_ID`
改成 `${HOST_ID:?…}` 必填，但 CI 那個 job 的 `env:` 沒跟著加）。

為什麼拖了三天才被回報：CI 的存在是「三機紀律的公開證據」，而**一直紅的 CI
等於沒有 CI** —— 大家都習慣看本機 pre-push 綠就把信寄出去。
本來 `pre-push` 有跑 `docker compose config` 嗎？沒有，所以那 10 次 push
在按鈕那一側全是綠的。

這個測試把「compose 的必填清單」與「CI 給的清單」釘在一起。日後在
`compose.yaml` 加一個 `${NEW:?…}` 而忘了在 ci.yml 給值，這裡會紅。
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "compose.yaml"
CI = ROOT / ".github" / "workflows" / "ci.yml"

REQUIRED_RE = re.compile(r"\$\{([A-Z_][A-Z0-9_]*):\?")


def _required_vars() -> set:
    """compose.yaml 裡所有 `${VAR:?…}`（必填，缺了 `compose config` 直接報錯）。"""
    return set(REQUIRED_RE.findall(COMPOSE.read_text(encoding="utf-8")))


def _compose_job_env() -> set:
    """ci.yml 裡 compose job 的 `env:` 區塊宣告了哪些變數。

    用區塊邊界找而不是全文抓：`env:` 在 workflow 裡到處都是（backend job、
    guards job 都各有），抓全文會把不該算的算進來。
    """
    text = CI.read_text(encoding="utf-8")
    start = text.find("- name: docker compose config")
    assert start != -1, "ci.yml 找不到 compose job"
    # 該 step 的 env 區塊結束於 `run:`
    end = text.find("run: docker compose config", start)
    assert end != -1, "compose job 的 run 行不見了（step 結構變了？）"
    block = text[start:end]
    return set(re.findall(r"^\s+([A-Z_][A-Z0-9_]*):", block, re.M))


def test_compose_has_required_vars_at_all():
    """先確認我們真的在解析出必填變數 —— 否則下面兩條會「因為抓不到東西」而假綠。"""
    required = _required_vars()
    assert required, (
        "compose.yaml 一個 ${VAR:?} 都沒有 —— 那這個測試的前提不成立，"
        "請確認 regex 還跟得上 compose.yaml 的語法"
    )


def test_ci_provides_every_required_compose_var():
    """**CI 給的必須涵蓋 compose 的所有必填項。**"""
    required = _required_vars()
    given = _compose_job_env()
    missing = sorted(required - given)
    assert not missing, (
        f"ci.yml 的 compose job 沒給這些必填變數: {missing} —— "
        f"`docker compose config` 會在 CI 上失敗，"
        f"而本機 pre-push 沒有這道檢查（所以按 push 的人是看不到的）"
    )


def test_ci_does_not_provide_bogus_vars():
    """反向：CI 給的變數必須真的被 compose 用到，否則是維護負擔。

    `TS_IP` 是有在用的（`ports: ["${TS_IP:-127.0.0.1}"]`），但它**不是必填** ——
    給它只是為了讓 CI 的插值路徑涵蓋到「有值」的情況。這個測試只擋那種
    「名字拼錯、compose 根本沒讀」的多餘項。
    """
    text = COMPOSE.read_text(encoding="utf-8")
    bogus = sorted(
        v for v in _compose_job_env()
        if f"${{{v}" not in text
    )
    assert not bogus, (
        f"ci.yml 給了 compose.yaml 沒讀的變數: {bogus} —— "
        f"拼錯字會讓這道 CI 檢查看起來有覆蓋、實際沒覆蓋到"
    )
