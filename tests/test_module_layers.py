"""分層約束：切模組（2026-09-29）之後的依賴方向。

這些是**架構**測試，不是功能測試 —— 它們不驗證行為，只驗證「誰可以依賴誰」。
目的是讓下一次有人（人或模型）想「順手在 gateway 裡 import 個 rag 的函式」時，
測試會先擋下來，而不是等到 runtime 才出現循環 import。

分層（由上到下，禁止反向）：
    rag          → gateway / law_meta / retrieve / cn_parse
    retrieve     → gateway / law_meta / cn_parse
    gateway      → （無：最底層，不依賴任何專案模組）
    law_meta     → （無：純字串處理）
    cn_parse     → （無：純字串處理）
"""
import ast
import pathlib

APP = pathlib.Path(__file__).resolve().parents[1] / "backend" / "app"
MODULES = ["rag", "gateway", "law_meta", "retrieve", "cn_parse"]
# 允許的下層依賴
ALLOWED = {
    "rag": {"gateway", "law_meta", "retrieve", "cn_parse"},
    "retrieve": {"gateway", "law_meta", "cn_parse"},
    "gateway": set(),
    "law_meta": set(),
    "cn_parse": set(),
}
# 唯一允許的跨層呼叫：gateway 需要 law_version，經由 import 時注入
# （gateway._LAW_VERSION_FN），不是 import 語句。
INJECTED = {"_LAW_VERSION_FN"}

# 唯一允許的 `from .X import`：相容層轉出。rag.py 轉出下層的少數名稱，
# 讓 main.py 維持 `rag.warmup()`／`rag.upsert()` 的呼叫樣式（36 個名稱，
# 逐一改 main.py 會讓 diff 淹沒真正的分層改動）。這些名稱只被**呼叫**、
# 不被 monkeypatch，所以轉出是安全的；需要 patch 的人請直接 patch 擁有者模組。
# 鍵是 (來源模組, 目標模組)。
REEXPORT_ALLOWED = {("rag", "gateway"), ("rag", "law_meta"), ("rag", "retrieve")}


def _module_attr_deps(name: str) -> set[str]:
    """此模組在**程式碼**裡實際引用的其他專案模組（排除註解與 docstring）。"""
    tree = ast.parse((APP / f"{name}.py").read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id in MODULES and node.value.id != name:
                found.add(node.value.id)
    return found


def test_no_import_cycles_across_layers():
    """任一模組不得 import 上層模組（否則會有循環 import）。"""
    violations = []
    for mod, allowed in ALLOWED.items():
        for dep in sorted(_module_attr_deps(mod) - allowed):
            violations.append(f"{mod}.py 依賴了 {dep}.py，但分層不允許")
    assert not violations, "分層被打破：\n  " + "\n  ".join(violations)


def test_lower_layers_stay_dependency_free():
    """gateway / law_meta / cn_parse 不得依賴任何專案模組。"""
    for mod in ("gateway", "law_meta", "cn_parse"):
        assert not _module_attr_deps(mod), (
            f"{mod}.py 是底層，卻依賴了 {sorted(_module_attr_deps(mod))}")


def test_gateway_reaches_law_version_only_by_injection():
    """gateway 取得 law_version 的唯一途徑是注入，不是 import。"""
    tree = ast.parse((APP / "gateway.py").read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id == "rag":
                names.add(node.attr)
    assert not (names - INJECTED), f"gateway 不該引用 rag.{sorted(names - INJECTED)}"


def _from_imports(src: str) -> set[str]:
    """原始碼中 `from .X import ...` 的 X 集合。

    刻意用 AST 而非字串比對：gateway.py 的 docstring 裡**解釋了**為什麼
    不用 `from .rag import`（寫出那個字串來說明它是錯的），字串搜尋會把
    說明文字本身當成違規。只看 import 語句才不會誤判。
    """
    out = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
            out.add(node.module)
    return out


def test_every_layer_is_importable_standalone():
    """每個模組單獨 import 都不該拉起上層（可測試性）。"""
    import subprocess
    import sys
    for mod in ("gateway", "law_meta", "cn_parse"):
        code = (
            f"import sys; sys.path.insert(0, {str(APP.parent)!r});"
            f"import app.{mod} as m;"
            f"print('app.rag' in sys.modules)"
        )
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        assert r.returncode == 0, f"import app.{mod} 失敗：{r.stderr[-300:]}"
        assert r.stdout.strip() == "False", (
            f"import app.{mod} 竟拉起了 app.rag —— 底層不該依賴上層")


def test_cross_module_calls_use_attribute_access():
    """跨模組呼叫必須寫成 `mod.fn()`，不能 `from .mod import fn`。

    原因：`from X import fn` 會在 import 時把名稱綁進本地命名空間，之後
    monkeypatch X.fn 不會影響到本地副本 —— 測試會「通過但沒測到東西」。
    2026-09-29 切模組時 28 條測試就是這樣壞掉的。
    """
    for mod in MODULES:
        src = (APP / f"{mod}.py").read_text(encoding="utf-8")
        for other in _from_imports(src) & set(MODULES):
            if other == mod or (mod, other) in REEXPORT_ALLOWED:
                continue
            raise AssertionError(
                f"{mod}.py 使用 `from .{other} import ...`；"
                f"跨模組請改用屬性存取 `{other}.名稱`，否則 monkeypatch 無法生效")
