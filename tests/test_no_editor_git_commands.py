"""**repo 印給人看的 git 指令，不得是會開啟編輯器的裸指令。**

⚠️ 2026-10-03 實測：有人照著 repo 印出的指令執行，結果**卡在編輯器畫面**。
那條指令來自 `scripts/drop-ollama-urls.sh` 印的

    git add settings/env/hosts.shared.env && git commit        ← 裸 commit

`git commit` 沒有 `-m` 就會開編輯器；`git pull` 在做 merge 時也會（要產生
merge commit 讓你確認訊息）。

**指令被印出來就是會被照著跑** —— 貼進聊天、貼進 issue、被 cron 呼叫、在
沒有人互動的終端裡。所以 repo 印出的每一條都必須假設**沒有人能回答提示**。

判準不看「有沒有真的執行」，只看「這條指令在沒有人互動時會不會卡住」：

* `git commit`（無 `-m`／`-F`／`--amend`）→ 卡
* `git commit --amend`（無 `-m`／`-F`）→ 卡
* `git pull`（無 `--ff-only`／`--rebase`／`--no-edit`／`-m`）→ merge 時卡
* `git merge`（無 `--no-edit`／`-m`／`--ff-only`）→ 卡
* `git rebase` / `git cherry-pick` / `git revert` / `git tag`（無 `-m`）→ 可能卡

`host-sync.sh` 全部用 `--ff-only`，那按定義不會產生 merge commit，所以天然合格
（它的註解裡也寫明了這點）。這條測試是為了**讓後來的人不會用裸指令把它破掉**。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 掃描：會被印給人看的腳本（heredoc / echo）＋ 真正執行 git 的腳本
SCRIPTS = sorted(
    p for p in (ROOT / "scripts").glob("*.sh")
    if p.is_file()
)

# 一行裡「哪些 token 會讓 git 開編輯器」
#   commit/pull/merge/... 與「已經帶了非互動旗標」的比對
COMMIT_LIKE = r"(?:commit|commit-tree)"
SAFE_FLAGS = r"(?:-m\b|-F\b|--message\b|--no-edit\b|--ff-only\b|--rebase\b|--autosquash\b)"


def _strip_comment(line: str) -> str:
    st = line.lstrip()
    if st.startswith("#"):
        return ""
    cut = len(line)
    for m in re.finditer(r"(\s#|\s(?!://)//)", line):
        cut = m.start()
        break
    return line[:cut]


def _violations_in(path: Path):
    out = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        code = _strip_comment(line)
        for m in re.finditer(r"\bgit\s+([a-z-]+)", code):
            verb = m.group(1)
            if verb not in ("commit", "pull", "merge", "rebase",
                            "cherry-pick", "revert", "tag", "am"):
                continue
            # 取這個指令的「其餘參數」＝ 同一行、這個動詞之後、下一個 git 之前
            tail = code[m.end():]
            nxt = re.search(r"\bgit\s+[a-z-]+", tail)
            if nxt:
                tail = tail[:nxt.start()]

            # ⚠️⚠️ **旗標只看第一個引號之前的那一段。**
            #   理由：2026-10-03 第一版是「掃整行找 `-m`」，而
            #   `echo "git commit"   ← 印出來但有 -m 嗎？` 這行的**散文裡有 `-m `**，
            #   於是那條**裸指令被當成已保護而漏掉**。
            #
            #   真的參數列是無引號的 token（`git commit -m 'x'`），引號之後的東西
            #   屬於訊息本文，不是旗標。所以切在第一個引號之前 —— 那是不做引號解析
            #   時能得到的最準確切法。
            # ⚠️⚠️⚠️ **這裡踩過一個字串字面量的坑**：`r"[""']"` 會被 Python 解析成
            #   **兩個相鄰字串**（`r"["` ＋ `"]'"`），拼出 `"[]']"` —— 那是一個
            #   「`]` 與 `'`」的字符類，**不含雙引號**。於是 `echo "git commit"`
            #   的 `"` 沒被切開，後面散文裡的 `-m` 又被當成旗標 → **整條檢查被
            #   靜默削弱**，而且症狀是「測試綠著但抓不到東西」。
            #
            #   那正是「檢查存在、但看不見現實」那一類，而且**發生在我自己身上**。
            #   用三引號寫法，兩種引號都在。
            args = re.split(r"""["']""", tail, maxsplit=1)[0]

            # `--amend` **不**算保護：沒有 `-m` 的 `git commit --amend` 一樣會開編輯器
            if verb == "commit" and not re.search(r"(-m\S|-m\s|--message|-F\b)", args):
                out.append((i, line.strip(), verb))
            elif verb in ("pull", "merge") and not re.search(
                    r"(--ff-only|--rebase|--no-edit|-m\s|--autosquash|--squash)", args):
                out.append((i, line.strip(), verb))
            elif verb in ("rebase", "cherry-pick", "revert", "tag", "am") and not re.search(
                    r"(-m\s|-F\b|--message|--no-edit)", args):
                out.append((i, line.strip(), verb))
    return out


def test_no_script_prints_a_git_command_that_opens_an_editor():
    """**不得印出（或執行）會開啟編輯器的裸 git 指令。**

    症狀不是「壞掉」，而是**卡住**：沒有人能回答提示時，它就停在那裡 ——
    貼進聊天會卡住貼上的人，cron 會讓排程靜靜地不再完成。
    """
    bad = []
    for f in SCRIPTS:
        for ln, text, verb in _violations_in(f):
            rel = f.relative_to(ROOT).as_posix()
            bad.append(f"{rel}:{ln}  [{verb}]  {text[:88]}")
    assert not bad, (
        "這些 git 指令在沒有人互動時會**卡在編輯器**（指令被印出來就是會被照著跑）：\n  "
        + "\n  ".join(bad)
        + "\n\n  修法：`git commit` 加 `-m`／`-F`、`git pull` 加 `--ff-only`"
          "（那按定義不會產生 merge commit）、`git merge` 加 `--no-edit`。\n"
          "  若真的**執行**（不是印出來）且必須互動，請把該指令從這個掃描範圍排除"
          "並在該處寫明理由 —— 空白理由等於沒有依據。")


def test_the_scan_actually_finds_commands_in_this_repo():
    """**掃描器本身要能找到東西** —— 否則上面那條是假綠。

    這一條是本專案反覆在收的形狀：**檢查存在，但看不見現實**。
    先用一個一定會違反的樣本確認掃描器抓得到，再回去斷言 repo 是乾淨的。
    """
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "probe.sh"
        p.write_text(
            "git add -A && git commit\n"
            "git pull\n"
            "git merge origin/main\n"
            "# git commit   ← 註解裡的不算\n"
            'echo "git commit"   ← 印出來但有 -m 嗎？沒有，照樣算\n',
            encoding="utf-8")
        v = _violations_in(p)
    verbs = [x[2] for x in v]
    assert verbs.count("commit") == 2, f"應抓到 2 個 commit，實際 {verbs}"
    assert verbs.count("pull") == 1, f"應抓到 1 個 pull，實際 {verbs}"
    assert verbs.count("merge") == 1, f"應抓到 1 個 merge，實際 {verbs}"
    # 而帶了保護的就不該被抓
    assert not _violations_in_text("git commit -m 'x'")
    assert not _violations_in_text("git pull --ff-only")
    assert not _violations_in_text("git merge --no-edit origin/main")
    assert not _violations_in_text("# git commit")


def _violations_in_text(text: str):
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "probe.sh"
        p.write_text(text + "\n", encoding="utf-8")
        return _violations_in(p)