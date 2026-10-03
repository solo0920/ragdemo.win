"""**API 的埠不许散落成多個真相。**

⚠️ 2026-10-03（mbp 回報的真實事故之後）：埠 `8000` 被寫死在**七處**，而其中
`compose.yaml` 那一處是**最關鍵的** —— 它決定 docker 實際 publish 在哪裡。

| # | 位置 | 原本 | 改完 |
|---|---|---|---|
| 1 | `compose.yaml:64` | `127.0.0.1:8000:8000` | `${API_PORT:-8000}:8000` |
| 2 | `host-doctor.sh` `API_URL` | 有變數（可用 `HOST_API_LOCAL`） | 不變 |
| 3 | `host-sync.sh` `API_URL` | 有變數 | 不變 |
| 4 | `host-doctor.sh` `ch_readiness` | **寫死 `localhost:8000`** | `${API_URL}/ready` |
| 5 | `host-doctor.sh` `ch_query` | **寫死 `localhost:8000`** | `${API_URL}/query` |
| 6 | `wait-stack.sh` | **寫死 `localhost:8000`** | `${HOST_API_LOCAL:-…}` |
| 7 | `ensure-stack.sh` | **寫死 `127.0.0.1:8000`** | `${HOST_API_LOCAL:-…}` |
| 8 | `sync-snapshot.sh` fallback | **寫死 `:8000`** | 取 `HOST_API_LOCAL` 的埠 |
| 9 | `frontend/vite.config.ts` | **寫死 `localhost:8000`** | `process.env.HOST_API_LOCAL` |

**為什麼這件事值得一個測試**：寫死的地方**功能上都正常**（8000 就是現在的埠），
所以沒有任何症狀會指出「這裡寫死了」。而一旦有人改埠，那一處沒跟著改 →
症狀是「設定改了但行為沒變」或「stack 永遠等不到就緒」，**沒有任何錯誤**。

這正是本專案今天記錄了八次的形狀：**變數存在，但某些呼叫端繞過它**。

⚠️ 本檔**不列舉檔名**（那會是又一份手維護清單，會漂移）—— 掃描整個
`scripts/` 與 `frontend/src`／`vite.config.ts`，任何**可執行行**裡出現
`localhost:8000`／`127.0.0.1:8000`／`:8000` 就是違規。註解裡可以提（那是歷史紀錄，
而且註解裡的 8000 是有價值的說明）。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 這些檔案裡的 8000 會被視為違規（可執行行）
#
# ⚠️ 2026-10-03 補 `.githooks/*` 的原因（實測事故）：換埠到 920 之後，
#    `.githooks/pre-push` 裡 4 處 `localhost:8000` **完全沒被這條守衛看到**，
#    症狀是 `RAGDEMO_SMOKE=1` 永遠報「api 不在線」而中止 push —— 等於
#    「驗證答案非空」那道檢查從此沒跑過。另一個症狀更隱晦：非 smoke 分支
#    在 mbp 上會替搶走 8000 的 omlx 印「✓ 本機 api OK」。
#
#    為什麼漏掉：**副檔名**。`.githooks/pre-push` 沒有副檔名，而當時清單是
#    手列的（`scripts/*.sh`、`frontend/vite.config.ts`、`compose.yaml`）。
#    我自己用 `grep --include='*.sh'` 找同一個問題時也漏了它 —— **同一個形狀**：
#    以為自己找全了，其實篩選條件把它排除在外。
SCANNED_GLOBS = (
    "scripts/*.sh",
    "frontend/vite.config.ts",
    "compose.yaml",
    # 沒有副檔名的可執行檔。`Path.glob("*")` 不含隱藏目錄，所以用明確列舉。
    ".githooks/pre-push",
)

# ⚠️ **散文型**的 8000：出現在**給人看的訊息字串**裡，不是請求端點。
#    以「行內子字串」比對（不用行號 —— 行號會隨檔案編輯漂移）。
#    每筆都要有理由；空白理由等於沒有依據，而沒有依據的例外就是這個缺陷的形狀。
# ⚠️ 2026-10-03 起**是空的**，而且應該保持是空的。
#    原本有一筆「沒綁在 127.0.0.1:8000」是 host-sync.sh 的 warn 訊息字串。
#    換埠後它變成**謊報**（人會去查一個早就沒人用的埠），所以把那句話改成
#    不指名埠 —— 一旦不寫數字就不可能過期，例外也就沒有存在的理由了。
#    **空白理由等於沒有依據，而沒有依據的例外就是這個缺陷的形狀。**
PROSE_ALLOWED: dict[str, str] = {}


def _strip_comment(line: str) -> str:
    """去掉註解。**刻意不做引號解析** —— 那個手寫狀態機會出錯。

    ⚠️ 2026-10-03 兩次實測：
      · 第一版把 `//` 一律當註解起點，於是 `http://localhost:8000` 在 `http:` 就被
        截斷 —— **整條守衛被 URL 靜默禁用**，而它全部的工作就是抓 URL 裡的埠。
      · 第二版改成「`//` 前一個字元不是 `:` 才是註解」，**症狀沒消失**，因為真正的
        根因是**引號狀態機**：shell 那行是
        `body="$(curl ... -w '%{http_code}' http://localhost:8000/ready 2>/dev/null)"`，
        單引號被當成巢狀字串打開又關上，於是 `http://` 的 `//` 落在「字串外」→ 截斷。
        `2>/dev/null` 的 `//` 也一樣。而這條守衛抓的就是 shell 的 curl 行。

    所以：**跳過整行註解就好**，不做引號／巢狀字串的推斷。副作用是「行尾註解裡
    提到 8000」會誤報 —— 那種寫法在本 repo 不存在，而且真出現時訊息會指出是哪一行，
    讓人自己看（比一個會靜默失效的狀態機好）。
    """
    st = line.lstrip()
    if st.startswith("#") or st.startswith("//"):
        return ""
    # 行尾註解：空白之後的 `#`，或空白之後且非 `://` 的 `//`
    cut = len(line)
    for m in re.finditer(r"(\s#|\s(?!://)//)", line):
        cut = m.start()
        break
    return line[:cut]


# 可執行行裡的「硬寫 8000」：必須是 port 的位置（前面是 : 或 冒號後單獨）
HARDCODED = re.compile(r"(?:localhost|127\.0\.0\.1|\])\s*:\s*8000\b|:\s*8000\b")


def _violations(path: Path):
    out = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        code = _strip_comment(line)
        if not code.strip():
            continue
        for m in HARDCODED.finditer(code):
            # `${VAR:-8000}` / `:-8000` 是**預設值**，不是寫死
            before = code[max(0, m.start() - 24):m.start()]
            # `:-8000`（shell 預設）、`?? '…8000'`（JS nullish 預設）、
            # `|| 8000` —— 這些是**預設值**，改了變數仍然生效，所以不算違規。
            if any(tok in before for tok in (":-", "??", "||", "PORT", "API")):
                continue
            # 散文型（給人看的訊息字串）
            if any(k in code for k in PROSE_ALLOWED):
                continue
            # YAML 的 `published:` 之類由 compose 斷言處理
            out.append((i, line.strip()))
            break
    return out


def test_no_script_hardcodes_the_api_port():
    """**可執行行裡不得出現寫死的 API 埠。**

    寫死的地方功能上都正常（8000 就是現在的埠），所以**沒有任何症狀會指出它**。
    """
    bad = []
    for pat in SCANNED_GLOBS:
        for f in sorted(ROOT.glob(pat)):
            if f.name == "compose.yaml":
                continue          # 由 compose 專門的斷言處理
            rel = f.relative_to(ROOT).as_posix()
            for ln, text in _violations(f):
                bad.append(f"{rel}:{ln}  {text[:90]}")
    assert not bad, (
        "這些可執行行把 API 的埠寫死了 —— 設定變數對它們**無效**：\n  "
        + "\n  ".join(bad)
        + "\n\n  請改用 `${HOST_API_LOCAL:-http://127.0.0.1:8000}`（主機側 URL）"
          "或 `${API_PORT:-8000}`（主機側埠）。\n"
          "  註解裡可以提 8000（那是歷史紀錄），這條只管可執行行。")


def test_every_prose_exemption_has_a_reason():
    """**散文放行必須有理由。**

    「沒有依據的例外」就是這個缺陷的形狀 —— 而一個**永遠紅**的測試會被改成
    skip，那又回到原點：壞掉的東西看起來像有覆蓋。
    """
    for k, why in PROSE_ALLOWED.items():
        assert why.strip(), f"散文放行 {k!r} 沒寫理由"


def test_compose_publishes_the_host_port_from_a_variable():
    """**compose 的主機側埠必須是變數。**

    這是**最關鍵的一處**：主機側決定 docker 實際 publish 在哪。如果它寫死，
    那麼**即使所有腳本都讀了變數，整個改埠等於沒改** —— 而且沒有任何錯誤。
    """
    y = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    m = re.search(r'^\s*ports:\s*\["([^"]+)"\]\s*$', y, re.M)
    # 找出 api 服務那條（它有 `${API_PORT` 或 8000:8000）
    api = None
    for line in y.splitlines():
        if re.search(r'"[^"]*:?\$\{API_PORT:-(\d+)\}:(\d+)"', line):
            api = line.strip()
            break
    assert api, (
        "compose.yaml 的 api ports 必須是 `\"127.0.0.1:${API_PORT:-<埠>}:<容器埠>\"` ——\n"
        "  主機側寫死的話，改埠會「看起來成功但行為沒變」。\n"
        f"  目前找到的 ports 行：{[l.strip() for l in y.splitlines() if 'ports:' in l]}")
    assert "127.0.0.1" in api, \
        "主機側必須繼續綁 127.0.0.1（公網只經 cloudflared tunnel）—— 別順手改成 0.0.0.0"


def test_host_api_local_default_and_api_port_default_agree():
    """**`HOST_API_LOCAL` 的預設埠與 compose 的 `API_PORT` 預設必須一致。**

    兩個變數表達同一件事（主機側埠），所以它們不一致時**沒有人會發現** ——
    症狀是「改了 A 但 B 沒改，行為只改了一半」。
    """
    y = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    hd = (ROOT / "scripts" / "host-doctor.sh").read_text(encoding="utf-8")
    df = (ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")
    m1 = re.search(r"\$\{API_PORT:-(\d+)\}", y)
    assert m1, "compose 裡找不到 ${API_PORT:-<數字>}"
    m2 = re.search(r'API_URL="\$\{HOST_API_LOCAL:-http://[^"]*?:(\d+)\}"', hd)
    assert m2, "host-doctor.sh 裡找不到 HOST_API_LOCAL 的預設埠"
    assert m1.group(1) == m2.group(1), (
        f"兩個變數的預設埠不一致：compose 的 API_PORT={m1.group(1)}、"
        f"HOST_API_LOCAL 的埠={m2.group(1)} —— 它們表達同一件事，"
        "不一致時症狀是「改了一半而且沒報錯」")

    # ── 容器側埠是**烤進 image** 的，必須與 compose 右側一致 ──────────────
    # 這是 8000→920（2026-10-03）時補上的第三個點：前兩個只保證「主機側」一致，
    # 但 ports 的**右側**是容器內部的 listening port，而那個值來自 Dockerfile 的
    # `CMD --port`（建 image 時烤死，compose 改不到）。
    # 只改 compose 右側而沒 rebuild → 主機 publish 到沒人 listening 的埠，
    # 症狀是「主機 curl 連不上、容器內 curl 正常」—— 兩邊症狀不同源。
    # ⚠️ 只抓**含 `${API_PORT` 的那條**（api 服務的）。用第一條 `ports:` 會拿到
    #    qdrant 的 6333 —— compose 裡 ports 有三條，而 qdrant 那條寫死（`…}:6333`），
    #    所以靠 `${API_PORT` 這個特徵辨識，比靠行號或「第一條」穩。
    ports_line = re.search(r'^\s*ports:\s*\["[^"]*:?\$\{API_PORT[^"]*:(\d+)"\]\s*$', y, re.M)
    assert ports_line, "compose 裡找不到含 ${API_PORT} 的 api ports 行"
    container_port = ports_line.group(1)
    m3 = re.search(r'--port",\s*"(\d+)"', df) or re.search(r"--port[= ]\s*(\d+)", df)
    assert m3, ("backend/Dockerfile 裡找不到 CMD 的 --port —— api 容器實際 listening "
                "的埠就來自那一行，compose 的右側必須與它一致")
    assert container_port == m3.group(1), (
        f"容器埠不一致：compose ports 右側={container_port}、"
        f"Dockerfile CMD --port={m3.group(1)} —— 只改一邊會 publish 到沒人 "
        "listening 的埠（主機 curl 連不上、容器內 curl 正常）")


def test_the_port_is_extracted_the_same_way_everywhere():
    """**取埠碼的方式必須到處一致。**

    ⚠️ 2026-10-03 實測：我在 `sync-snapshot.sh` 第一版寫成
    `printf '%s' "$URL" | tr -dc '0-9'`，那是**錯的** —— 它把整個 URL 的數字
    串起來：`http://127.0.0.1:8000` → **`1270018000`**。（而且我當時還註解說
    「hostname 裡本來就不該有數字」，而 `127.0.0.1` 全是數字。）

    正確做法是 `${VAR##*:}` 取**最後一個冒號之後**那段。兩處寫法不同就會漂移，
    而且症狀是「埠變成一個 10 位數」—— 那種錯誤很明顯，但**只在改埠時才出現**。
    """
    for f in ("scripts/host-doctor.sh", "scripts/sync-snapshot.sh"):
        src = (ROOT / f).read_text(encoding="utf-8")
        for i, line in enumerate(src.splitlines(), 1):
            # ⚠️ 跳過註解 —— 這條測試自己在註解裡引用了錯誤寫法當反面教材，
            #   不跳過的話它會抓自己（第一版就這樣，而且抓到的行是註解，
            #   訊息會指向一個「看起來在抱怨註解」的無效斷言）。
            if _strip_comment(line).strip() == "":
                continue
            if "tr -dc '0-9'" not in line and 'tr -dc "0-9"' not in line:
                continue
            # 必須作用在 ${...##*:} 的結果上，而不是整個 URL
            assert "##*:" in line, (
                f"{f}:{i} 取埠時用了 `tr -dc 0-9`，但沒有先 `##*:` ——\n"
                f"  {line.strip()[:90]}\n"
                "  那會把整個 URL 的數字串起來（127.0.0.1:8000 → 1270018000）。")

# ── 預設值（default）也必須與源頭一致 ─────────────────────────────────────
#
# 為什麼要另外一條：上面的 `HARDCODED` 只抓**寫死**，並且**刻意排除預設值**
# （`${VAR:-N}`／`?? N`／`|| N`），理由寫得很清楚：「改了變數仍然生效，所以
# 不算違規」。
#
# 那個理由**在功能上對、在漂移上錯**：
#
#   · 寫死  → 症狀是「改了變數也沒用」，**會喊**
#   · 預設值過期 → 症狀是「變數有設時正常、沒設時指向舊埠」，**不喊**
#
# 而實際踩到的就是後者（2026-10-03，8000 → 920）：
#   · `frontend/vite.config.ts` 的 `?? 'http://127.0.0.1:8000'` 換埠後指向死埠，
#     症狀是 dev proxy 500／連不上 —— 很容易誤判成前端壞了
#   · `sync-snapshot.sh` 與 `host-doctor.sh` 的 `|| 8000` 是**睡著的地雷**
#     （上游已帶預設所以走不到，但上游一旦被設成沒埠的值就會靜默用舊埠）
#
# 三個都是靠人手發現的。這條就是讓它們不可能再發生。

def _compose_api_port() -> str:
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    m = re.search(r"\$\{API_PORT:-(\d+)\}", compose)
    assert m, (
        "compose.yaml 裡找不到 `${API_PORT:-N}` —— 那是 api **主機埠**的源頭。"
        "這條守衛的所有比對都以它為準；找不到就無法判斷誰過期了。")
    return m.group(1)


def _exec_lines(path: Path) -> list[tuple[int, str]]:
    out = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if _strip_comment(line).strip():
            out.append((i, line))
    return out


def test_fallback_urls_agree_with_compose():
    """`HOST_API_LOCAL` 沒設時的預設 URL 必須與 compose 的源頭一致。

    比對的是**完整 URL**（含主機），不只是埠 —— 因為連主機名都可能不同步
    （`localhost` vs `127.0.0.1` 在 docker 的 port 轉發下不等價）。
    """
    want_port = _compose_api_port()
    want_url = f"http://127.0.0.1:{want_port}"
    bad = []
    for pat in SCANNED_GLOBS:
        for f in sorted(ROOT.glob(pat)):
            for i, line in _exec_lines(f):
                for m in re.finditer(r"\$\{HOST_API_LOCAL:-([^}]*)\}", line):
                    if m.group(1) != want_url:
                        bad.append(f"{f.relative_to(ROOT)}:{i}  {m.group(0)}")
                for m in re.finditer(r"HOST_API_LOCAL\s*\?\?\s*'([^']*)'", line):
                    if m.group(1) != want_url:
                        bad.append(f"{f.relative_to(ROOT)}:{i}  ?? {m.group(1)!r}")
    assert not bad, (
        f"HOST_API_LOCAL 的預設值必須是 {want_url}（與 compose 的 "
        f"`${{API_PORT:-{want_port}}}` 同源）。以下過期了：\n  "
        + "\n  ".join(bad)
        + "\n  ⚠️ 預設值過期**不會喊** —— 變數有設時正常、沒設時指向舊埠，"
          "症狀是『換了埠但那條路徑沒換』。")


# 可執行行裡**允許出現**的埠。每筆都要有理由；空白理由等於沒有依據，
# 而沒有依據的例外就是這個缺陷的形狀（同 PROSE_ALLOWED 的道理）。
# `None` 不是自由值 —— 它代表「必須等於 compose 的 `${API_PORT:-N}`」。
ALLOWED_PORTS: dict[str | None, str] = {
    None: "api 主機埠 —— 必須等於 compose 的 `${API_PORT:-N}`，不是自由值",
    "6333": "qdrant HTTP（compose 有宣告）",
    "6334": "qdrant gRPC（compose 有宣告）",
    "5432": "postgres（compose 有宣告）",
    "11434": "ollama —— **不是** compose 服務（是外部服務），compose 不會宣告它",
}


def test_no_port_literal_outside_the_allowlist():
    """可執行行裡的埠必須在允許清單內，且 api 的那一個必須等於 compose 源頭。

    抓的是**任何**不在清單裡的埠，不是寫死某個數字 —— 所以下次換埠忘了改
    某一處，它會紅；而且不需要為那個數字新增一條規則。
    """
    want_port = _compose_api_port()
    allowed = {p for p in ALLOWED_PORTS if p is not None} | {want_port}
    bad = []
    for pat in SCANNED_GLOBS:
        for f in sorted(ROOT.glob(pat)):
            for i, line in _exec_lines(f):
                # 只抓「像埠」的數字：URL 裡的、或 `|| 8000`／`printf '8000'`
                # 這種明確的預設值位置。抓不到 `head -c 5`、`--max-time 20`。
                for m in re.finditer(r"(?:localhost|127\.0\.0\.1):(\d{2,5})\b", line):
                    if m.group(1) not in allowed:
                        bad.append(f"{f.relative_to(ROOT)}:{i}  埠 {m.group(1)}")
                for m in re.finditer(r"\|\|\s*(\d{2,5})\b", line):
                    if m.group(1) not in allowed:
                        bad.append(f"{f.relative_to(ROOT)}:{i}  `|| {m.group(1)}`")
                # ⚠️ `|| 8000` 那個 pattern 只認「`||` 後面直接是數字」。實際寫法
                #    常是 `|| _api_port=8000`（數字在 `=` 之後）—— 第一版就是
                #    在這裡漏掉 `sync-snapshot.sh` 的真實案例。
                for m in re.finditer(r"\|\|\s*([A-Za-z_][A-Za-z_0-9]*)=(\d{2,5})\b", line):
                    if "port" in m.group(1).lower() and m.group(2) not in allowed:
                        bad.append(f"{f.relative_to(ROOT)}:{i}  `|| …={m.group(2)}`")
                for m in re.finditer(r"printf\s+'(\d{2,5})'", line):
                    if m.group(1) not in allowed:
                        bad.append(f"{f.relative_to(ROOT)}:{i}  `printf '{m.group(1)}'`")
                # 任何 `${VAR:-NNNN}`（NNNN 像个埠）都要在清單內。不只
                # HOST_API_LOCAL —— 那個只是**已知會用到 api 埠**的那一個。
                #
                # ⚠️ **變數名必須看起來像埠。** 第一版寫成 `\$\{VAR:-(\d{2,5})\}`，
                # 結果抓到 `ensure-stack.sh` 的 `${VAR:-60}`（curl timeout）與
                # compose 兩個 healthcheck 的 `${VAR:-30}` —— **timeout 不是埠**。
                # 症狀是「守衛紅，而紅的原因與它要守的東西無關」，那比沒有守衛更糟：
                # 久了就會被人無脑加進允許清單。
                for m in re.finditer(r"\$\{([A-Za-z_][A-Za-z_0-9]*):-(\d{2,5})\}", line):
                    if "port" in m.group(1).lower() and m.group(2) not in allowed:
                        bad.append(f"{f.relative_to(ROOT)}:{i}  `${{{m.group(1)}:-{m.group(2)}}}`")
    assert not bad, (
        f"可執行行裡出現不在允許清單內的埠。允許的：{sorted(allowed)}"
        f"（api 那一個必須等於 compose 的 ${{API_PORT:-{want_port}}}）。\n  "
        + "\n  ".join(bad)
        + "\n  每個允許的埠都要在 ALLOWED_PORTS 裡有理由 —— 空白理由等於"
          "沒有依據，而沒有依據的例外就是這個缺陷的形狀。")
