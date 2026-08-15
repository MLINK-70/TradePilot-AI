# -*- coding: utf-8 -*-
"""生成两份报告到桌面：
1. 市场分析：电脑 → 美国
2. 贸易数据：中国出口全球电视（HS 8528）
"""
import os
import sys
import time

sys.path.insert(0, r"D:\毕设一")

t0 = time.time()
from main import _collect_evidence
from llm import analyze_market, analyze_trade_trend
from trade import query_trend, summarize_trend, summarize_stats, get_competitiveness_matrix
from export import build_market_report, build_word_report, finalize_docx
from hs_descriptions import get_hs_description

DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop")

# ── 1. 市场分析：电脑 → 美国 ─────────────────────────────────────
print(f"[{time.time()-t0:.0f}s] 1/2 市场分析：电脑 → 美国")
product, country = "电脑", "美国"
market_ctx, trade_evidence, competitiveness, background, landscape = _collect_evidence(product, country)
print(f"[{time.time()-t0:.0f}s]   证据链完成")
data = analyze_market(product, country, market_ctx, trade_evidence,
                      competitiveness, background, landscape, refresh=True)
print(f"[{time.time()-t0:.0f}s]   AI 分析完成")
buf, fmt = finalize_docx(build_market_report(product, country, data, trade_evidence,
                                             competitiveness, background, landscape, market_ctx),
                         as_pdf=False)
out1 = os.path.join(DESKTOP, f"TradePilot-{product}-{country}-市场分析报告.docx")
with open(out1, "wb") as f:
    f.write(buf.getvalue())
print(f"[{time.time()-t0:.0f}s]   已生成: {out1}")
ms = data.get("market_size") or {}
print(f"   市场规模: {ms.get('value')} | 2024出口: {sum(trade_evidence.get('trend', {}).values() if trade_evidence.get('trend') else [])}")

# ── 2. 贸易数据：中国出口全球电视 ─────────────────────────────────
print(f"[{time.time()-t0:.0f}s] 2/2 贸易数据：中国出口全球电视")
product2, target2, reporter2 = "电视", "全球", "中国"
years = list(range(2020, 2025))
hs, rows, _ = query_trend(product2, target2, years, reporter=reporter2)
print(f"[{time.time()-t0:.0f}s]   HS={hs} 记录={len(rows)}")
trend = summarize_trend(rows)
stats = summarize_stats(trend) if len(trend) >= 3 else {}
print(f"   逐年: { {y: round(v['value']/1e8, 2) for y, v in trend.items()} }")

market_ctx2, trade_evidence2, competitiveness2, background2, landscape2 = _collect_evidence(product2, "美国")
try:
    ai = analyze_market(product2, "美国", market_ctx2, trade_evidence2,
                        competitiveness2, background2, landscape2)
except ValueError:
    ai = {}
analysis = {}
if len(trend) >= 3:
    try:
        analysis = analyze_trade_trend(product2, target2, reporter2, trend, stats)
    except ValueError as e:
        print(f"   解读跳过: {e}")
matrix = []
try:
    matrix = get_competitiveness_matrix(product2, "美国", years, reporter2)
except Exception:
    pass
year_label = f"{years[0]}-{years[-1]}"
buf2, fmt2 = finalize_docx(build_word_report(product2, target2, year_label, hs, rows, ai,
                                             get_hs_description(hs), stats, analysis,
                                             landscape2, market_ctx2, matrix, background2, competitiveness2,
                                             reporter=reporter2),
                           as_pdf=False)
out2 = os.path.join(DESKTOP, f"TradePilot-{product2}-{target2}-{year_label}-贸易数据报告.docx")
with open(out2, "wb") as f:
    f.write(buf2.getvalue())
print(f"[{time.time()-t0:.0f}s]   已生成: {out2}")
print(f"\n总耗时 {time.time()-t0:.0f}s，两份报告完成")
