#!/usr/bin/env python3
"""duckdb 分析法規扁平資料 + 產出 parquet 備份。

輸入：data/laws/laws_flat.jsonl、laws_meta.jsonl（normalize.py 產出）
輸出：data/laws/laws_flat.parquet、laws_meta.parquet（備份，清理後結構化）
    + 終端印出分析報告
"""
import duckdb
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.join(ROOT, "data", "laws")

con = duckdb.connect()
con.execute(f"""CREATE TABLE meta AS SELECT * FROM read_json_auto('{DATA}/laws_meta.jsonl');""")
con.execute(f"""CREATE TABLE art AS SELECT * FROM read_json_auto('{DATA}/laws_flat.jsonl');""")

P = lambda *x: print(*x, sep="\n")


def section(t):
    print("\n" + "=" * 8, t, "=" * 8)


section("0. 基本量")
P(con.execute("SELECT 'laws' k, count(*) v FROM meta UNION ALL SELECT 'articles', count(*) FROM art").df().to_string(index=False))

section("1. 條文長度分布（char_len）")
P(con.execute("""
SELECT approx_quantile(char_len, .1) p10, approx_quantile(char_len,.5) p50,
       approx_quantile(char_len,.9) p90, approx_quantile(char_len,.99) p99,
       max(char_len) max_len,
       count(*) FILTER (WHERE char_len=0) empty_len0,
       count(*) FILTER (WHERE char_len>2000) gt2k,
       count(*) FILTER (WHERE char_len>3000) gt3k
FROM art""").df().to_string(index=False))

section("2. 已廢止法規 vs 現行（條文數、佔比）")
P(con.execute("""
SELECT meta.is_abandoned, count(DISTINCT meta.pcode) laws, count(*) arts
FROM meta JOIN art USING(pcode) GROUP BY 1""").df().to_string(index=False))

section("3. 每部法規條文數分布")
P(con.execute("""
SELECT min(c) mn, approx_quantile(c,.5) mid, max(c) mx
FROM (SELECT pcode, count(*) c FROM art GROUP BY 1)""").df().to_string(index=False))
P("條文最多 10 部：")
P(con.execute("""
SELECT law_name, article_count FROM meta ORDER BY article_count DESC LIMIT 10""").df().to_string(index=False))

section("4. 章節覆蓋：沒有章節標題的條文佔比")
P(con.execute("""
SELECT count(*) no_chapter, round(100.0*count(*)/ (SELECT count(*) FROM art),1) pct
FROM art WHERE chapter=''""").df().to_string(index=False))

section("5. 資料品質：條號缺失 / 內容為刪除註記")
P(con.execute("""
SELECT
 count(*) FILTER (WHERE article_no='') no_no,
 count(*) total_rows,
 count(*) FILTER (WHERE is_repealed) repealed,
 count(*) FILTER (WHERE is_abandoned) abandoned_law,
 count(*) FILTER (WHERE NOT is_repealed AND NOT is_abandoned) usable_for_rag
FROM art""").df().to_string(index=False))

section("6. 重複 pcode 檢查")
P(con.execute("SELECT count(*) - count(DISTINCT pcode) dup_pcode FROM meta").df().to_string(index=False))

section("7. 現行法規類別 top 15（供檢索過濾）")
P(con.execute("""
SELECT law_category, count(*) laws FROM meta WHERE NOT is_abandoned
GROUP BY 1 ORDER BY 2 DESC LIMIT 15""").df().to_string(index=False))

section("8. 同時含中英名且有前言/沿革的法規（metadata 價值）")
P(con.execute("""
SELECT count(*) FILTER (WHERE has_eng) with_eng,
       count(*) FILTER (WHERE law_foreword!='') with_foreword,
       count(*) FILTER (WHERE law_histories!='') with_history
FROM meta WHERE NOT is_abandoned""").df().to_string(index=False))

section("9. 產出 parquet 備份")
con.execute(f"COPY meta TO '{DATA}/laws_meta.parquet' (FORMAT PARQUET);")
con.execute(f"COPY art  TO '{DATA}/laws_flat.parquet'  (FORMAT PARQUET);")
con.close()
P("寫出 laws_meta.parquet / laws_flat.parquet", os.path.getsize(f"{DATA}/laws_flat.parquet"), "bytes")