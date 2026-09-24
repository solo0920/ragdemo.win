"""qdrant_load.chunk_text：長條文切塊（純函式，驗證切塊不丟字）。"""
import qdrant_load as Q


def test_short_content_single_chunk():
    assert Q.chunk_text("短內容。") == ["短內容。"]
    assert Q.chunk_text("") == [""]


def test_chunk_all_pieces_within_limit():
    text = "\n".join(f"第{i}條：契約當事人應依誠實信用原則履行義務、行使權利。" for i in range(200))
    pieces = Q.chunk_text(text, maxlen=500)
    assert len(pieces) > 1
    assert all(len(p) <= 500 for p in pieces)
    # 組合回去驗證不丟字（chunk_text 只 strip 空白行與首尾換行）
    assert "".join(pieces).replace("\n", "").replace(" ", "") == text.replace("\n", "").replace(" ", "")


def test_single_long_line_truncated():
    text = "金" * 5000
    pieces = Q.chunk_text(text, maxlen=800)
    assert all(len(p) <= 800 for p in pieces)
    assert "".join(pieces) == text