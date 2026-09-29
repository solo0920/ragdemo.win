"""靜態檢查：模組裡「被使用但沒定義」的全域名稱。

為什麼需要（2026-09-29 實測踩到）：切分 rag.py 成四層時，`QDRANT_API_KEY` 的
宣告沒有跟著搬到 gateway.py，但 `_req()` 裡在用它。結果是
`NameError: name 'QDRANT_API_KEY' is not defined`，而且只在**容器啟動時**
（`ensure_collection()` 呼叫 `_req()`）才炸 —— 233 條測試全綠，因為沒有任何
一條測試真的走過「QDRANT_API_KEY 有值時帶 api-key header」的路徑。

這組測試把「漏搬變數」變成 pytest 會紅的事。屬於架構測試，不驗證行為。
"""
import ast
import builtins
import pathlib

import pytest

APP = pathlib.Path(__file__).resolve().parents[1] / "backend" / "app"
# app 底下所有 .py 都掃（含 common/ 子目錄）：漏搬的變數可能落在任何一層。
# 用相對路徑當 id，不能只用 stem —— common/text.py 與 app/text.py 的 stem 會撞。
MODULES = sorted(str(p.relative_to(APP)) for p in APP.rglob("*.py"))


def _module_level_globals(tree: ast.Module) -> set[str]:
    """模組裡所有「可見的名稱」：import、賦值、def/class、except-as。

    刻意**不**做作用域分析（只收模組層級）。理由：Python 裡函式內的區域變數
    會遮蔽模組層級同名者，把它算進「已定義」是對的；反過來模組層級有、
    函式內沒有，才是 NameError 的真正情況 —— 這正是要抓的。
    """
    defined = set(dir(builtins))
    defined.add("__file__")  # 執行時由 import 機制注入，非 AST 節點
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            defined.add(node.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                defined.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.arg):
            defined.add(node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            defined.add(node.name)
        elif isinstance(node, ast.alias):
            defined.add(alias.asname or alias.name.split(".")[0])
    return defined


def _unresolved(tree: ast.Module) -> set[str]:
    defined = _module_level_globals(tree)
    used = {
        n.id for n in ast.walk(tree)
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
    }
    return used - defined


@pytest.mark.parametrize("mod", MODULES)
def test_no_undefined_global_names(mod):
    """每個模組都不該有「用了但沒定義」的全域名稱。"""
    tree = ast.parse((APP / mod).read_text(encoding="utf-8"))
    missing = sorted(_unresolved(tree))
    assert not missing, (
        f"{mod} 用了沒定義的名稱 {missing} —— "
        f"切模組時漏搬宣告？這會在執行期拋 NameError，"
        f"而且常常只有容器啟動才會走到。")


def test_every_module_imports_cleanly():
    """每層都要能獨立 import（抓漏掉的 import 與循環依賴）。"""
    import importlib
    for mod in ("cn_parse", "law_meta", "gateway", "retrieve", "rag"):
        assert importlib.import_module(f"app.{mod}") is not None
