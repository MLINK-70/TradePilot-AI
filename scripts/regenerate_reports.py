# -*- coding: utf-8 -*-
"""用干净数据重新生成蓝牙耳机→德国 两份报告（替换桌面修复前旧版）"""
import os
import sys
import time

sys.path.insert(0, r"D:\毕设一")

t0 = time.time()
from main import _collect_evidence, markdown_report
from llm import analyze_market, analyze_trade_trend
from trade import query_trend, summarize_trend, summarize_stats, get_competitiveness_matrix
from export import build_market_report, build_word_report, finalize_docx
from hs_descriptions import get_hs_description
from database import save_report_history

DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop")
product, country = "蓝牙耳机", "德国"

# ── 1. 市场分析报告 ─────────────────────────────────────────────
print("1/2 市场分析证据链...")
market_ctx, trade_evidence, competitiveness, background, landscape = _collect_evidence(product, country)
print(f"   证据链完成 ({time.time()-t0:.0f}s)")
data = analyze_market(product, country, market_ctx, trade_evidence,
                      competitiveness, background, landscape, refresh=True)
report_md = markdown_report(product, country, data)
save_report_history("market", product, country, {
    "report": report_md, "trade": trade_evidence,
    "competitiveness": competitiveness,
    "market_context": market_ctx, "background": background or {},
})
buf, fmt = finalize_docx(build_market_report(product, country, data, trade_evidence,
                                             competitiveness, background, landscape, market_ctx),
                         as_pdf=False)
out1 = os.path.join(DESKTOP, f"TradePilot-{product}-{country}-市场分析报告.docx")
with open(out1, "wb") as f:
    f.write(buf.getvalue())
print(f"   市场分析报告已生成: {out1} ({time.time()-t0:.0f}s)")

# ── 2. 贸易数据报告 ─────────────────────────────────────────────
print("2/2 贸易数据报告...")
years = list(range(2020, 2025))
hs, rows, _ = query_trend(product, country, years, reporter="中国")
trend = summarize_trend(rows)
stats = summarize_stats(trend) if len(trend) >= 3 else {}
analysis = {}
if len(trend) >= 3:
    try:
        analysis = analyze_trade_trend(product, country, "中国", trend, stats)
    except ValueError:
        pass
matrix = []
try:
    matrix = get_competitiveness_matrix(product, country, years, "中国")
except Exception:
    pass
year_label = f"{years[0]}-{years[-1]}"
buf2, fmt2 = finalize_docx(build_word_report(product, country, year_label, hs, rows, data,
                                             get_hs_description(hs), stats, analysis,
                                             landscape, market_ctx, matrix, background, competitiveness),
                           as_pdf=False)
out2 = os.path.join(DESKTOP, f"TradePilot-{product}-{country}-{year_label}-贸易数据报告.docx")
with open(out2, "wb") as f:
    f.write(buf2.getvalue())
print(f"   贸易数据报告已生成: {out2} ({time.time()-t0:.0f}s)")

# ── 验证关键数字 ────────────────────────────────────────────────
print("\n=== 关键数字验证 ===")
print("2024 中国对德出口(8518):", round(sum(r.get('primaryValue') or 0 for r in rows if str(r.get('refYear')) == '2024') / 1e8, 2), "亿美元（应约 5.94）")
ms = data.get("market_size") or {}
print("市场规模:", repr(ms.get("value")), "| note:", repr((ms.get("note") or "")[:60]))
print("趋势:", {y: round(v['value']/1e8, 2) for y, v in trend.items()})
