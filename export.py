"""export.py — 导出模块：Word 分析报告 + CSV 原始数据

从 gen_export_demo.py 验证过的逻辑抽取，供 API 路由复用。
"""
import csv
import io

from docx import Document
from docx.shared import Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH


def build_word_report(product: str, target: str, year: str, hs_code: str,
                      rows: list, ai: dict) -> io.BytesIO:
    """生成 Word 分析报告（内存流，供 FastAPI 返回下载）"""
    total_value = sum(r.get("primaryValue") or 0 for r in rows)
    total_wgt = sum(r.get("netWgt") or 0 for r in rows)

    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "微软雅黑"
    style.font.size = Pt(10.5)

    h = doc.add_heading(f"{product} 中国出口 {target} 贸易分析报告", level=0)
    h.alignment = WD_ALIGN_PARAGRAPH.CENTER

    meta = doc.add_paragraph()
    meta.add_run(f"查询: 产品 {product}（HS{hs_code}）→ {target} | 年份 {year}\n")
    meta.add_run("数据来源: UN Comtrade 公共 API\n")
    meta.add_run("声明: 贸易数据为 UN 官方汇总; AI 分析由大模型生成，仅供参考")
    meta.runs[0].bold = True

    # 一、贸易数据总览
    doc.add_heading("一、贸易数据总览", level=1)
    tbl = doc.add_table(rows=3, cols=3)
    tbl.style = "Light Grid Accent 1"
    tbl.rows[0].cells[0].text, tbl.rows[0].cells[1].text, tbl.rows[0].cells[2].text = "指标", "数值", "单位"
    tbl.rows[1].cells[0].text = "贸易金额"
    tbl.rows[1].cells[1].text = f"{total_value:,.0f}"
    tbl.rows[1].cells[2].text = "美元"
    tbl.rows[2].cells[0].text = "净重"
    tbl.rows[2].cells[1].text = f"{total_wgt:,.0f}"
    tbl.rows[2].cells[2].text = "公斤"

    # 二、原始数据表
    doc.add_heading("二、原始数据（UN Comtrade）", level=1)
    raw_tbl = doc.add_table(rows=1 + len(rows), cols=5)
    raw_tbl.style = "Light Grid Accent 1"
    for j, head in enumerate(["年份", "流向", "HS编码", "金额(美元)", "净重(公斤)"]):
        raw_tbl.rows[0].cells[j].text = head
    for i, r in enumerate(rows, 1):
        raw_tbl.rows[i].cells[0].text = str(r.get("refYear"))
        raw_tbl.rows[i].cells[1].text = "出口"
        raw_tbl.rows[i].cells[2].text = str(r.get("cmdCode"))
        raw_tbl.rows[i].cells[3].text = f"{r.get('primaryValue') or 0:,.0f}"
        raw_tbl.rows[i].cells[4].text = f"{r.get('netWgt') or 0:,.0f}"

    # 三、AI 市场分析
    doc.add_heading("三、AI 市场分析", level=1)
    doc.add_heading("市场规模", level=2)
    ms = ai.get("market_size") or {}
    doc.add_paragraph(f"{ms.get('value', '')}（{ms.get('year', '')}年估算）")
    doc.add_heading("增长趋势", level=2)
    gt = ai.get("growth_trend") or {}
    doc.add_paragraph(f"CAGR {gt.get('cagr', '')}，{gt.get('forecast_years', '')}，{gt.get('description', '')}")
    doc.add_heading("风险分析", level=2)
    for r in ai.get("risks") or []:
        doc.add_paragraph(f"• {r.get('type')}（{r.get('level')}）: {r.get('description')}", style="List Bullet")
    doc.add_heading("AI 总结", level=2)
    doc.add_paragraph(ai.get("summary", ""))

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf


def build_csv(rows: list) -> io.BytesIO:
    """生成 CSV 原始数据（内存流）"""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["年份", "流向", "HS编码", "国家代码", "贸易金额(美元)", "净重(公斤)"])
    for r in rows:
        writer.writerow([
            r.get("refYear"), "出口", r.get("cmdCode"), r.get("partnerCode"),
            r.get("primaryValue") or 0, r.get("netWgt") or 0,
        ])
    return io.BytesIO(buf.getvalue().encode("utf-8-sig"))  # BOM 防 Excel 中文乱码
