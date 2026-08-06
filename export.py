"""export.py — 导出模块：Word 分析报告 + CSV 原始数据

从 gen_export_demo.py 验证过的逻辑抽取，供 API 路由复用。
"""
import csv
import io

import matplotlib
matplotlib.use("Agg")  # 无界面后端，服务器环境必需
import matplotlib.pyplot as plt
from docx import Document
from docx.shared import Cm, Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH

# 中文字体（Windows）
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False


def build_trend_chart(trend: dict) -> io.BytesIO:
    """生成趋势折线图 PNG（内存流），供 Word 报告嵌入"""
    years = list(trend.keys())
    values = [trend[y]["value"] / 1e8 for y in years]  # 亿美元

    fig, ax = plt.subplots(figsize=(8, 3.6))
    ax.plot(years, values, marker="o", linewidth=2.2, color="#2e5bff")
    ax.fill_between(years, values, alpha=0.12, color="#2e5bff")
    ax.set_title("出口贸易金额趋势（亿美元）", fontsize=12)
    ax.set_xlabel("年份")
    ax.set_ylabel("亿美元")
    ax.grid(True, alpha=0.3)
    for x, y in zip(years, values):
        ax.annotate(f"{y:.2f}", (x, y), textcoords="offset points", xytext=(0, 8), fontsize=9)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150)
    plt.close(fig)
    buf.seek(0)
    return buf


def build_word_report(product: str, target: str, year: str, hs_code: str,
                      rows: list, ai: dict, hs_description: str = "") -> io.BytesIO:
    """生成 Word 分析报告（docxtpl 模板渲染 + 图表嵌入 + python-docx 表格）"""
    from docxtpl import DocxTemplate

    total_value = sum(r.get("primaryValue") or 0 for r in rows)
    total_wgt = sum(r.get("netWgt") or 0 for r in rows)
    hs_desc = f"（{hs_description}）" if hs_description else ""

    # 趋势图 PNG（≥2 年才生成）
    chart_buf = None
    trend_map = {r.get("refYear"): {"value": r.get("primaryValue") or 0} for r in rows}
    if len(trend_map) >= 2:
        chart_buf = build_trend_chart(trend_map)

    # 模板数据（封面 + 趋势图；其余章节 python-docx 顺序追加）
    context = {
        "meta_line": f"{product} → HS{hs_code}{hs_desc} → {target} | 年份 {year}",
        "trend_image": None,
    }

    tpl = DocxTemplate("templates/report_template_v2.docx")

    if chart_buf:
        from docxtpl import InlineImage
        from docx.shared import Cm as CmW
        context["trend_image"] = InlineImage(tpl, chart_buf, width=CmW(15))

    # 渲染模板 → 拿到 Document 对象
    tpl.render(context)
    doc = tpl.docx

    # 二、数据总览表
    doc.add_heading("二、数据总览", level=1)
    tbl = doc.add_table(rows=3, cols=3)
    tbl.style = "Light Grid Accent 1"
    hdr = tbl.rows[0].cells
    hdr[0].text, hdr[1].text, hdr[2].text = "指标", "数值", "单位"
    tbl.rows[1].cells[0].text = "贸易金额"
    tbl.rows[1].cells[1].text = f"{total_value:,.0f}"
    tbl.rows[1].cells[2].text = "美元"
    tbl.rows[2].cells[0].text = "净重"
    tbl.rows[2].cells[1].text = f"{total_wgt:,.0f}"
    tbl.rows[2].cells[2].text = "公斤"

    # 三、原始数据表
    doc.add_heading("三、原始数据（UN Comtrade）", level=1)
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

    # 四、AI 市场分析（分小节）
    doc.add_heading("四、AI 市场分析", level=1)
    ms = ai.get("market_size") or {}
    gt = ai.get("growth_trend") or {}
    risks = ai.get("risks") or []
    up = ai.get("user_profile") or {}
    doc.add_heading("市场规模", level=2)
    doc.add_paragraph(f"{ms.get('value', '未知')}（{ms.get('year', '')}年估算）")
    doc.add_heading("增长趋势", level=2)
    doc.add_paragraph(f"CAGR {gt.get('cagr', '未知')}，{gt.get('forecast_years', '')}")
    if gt.get("description"):
        doc.add_paragraph(gt.get("description", ""))
    doc.add_heading("用户画像", level=2)
    doc.add_paragraph(f"年龄区间: {up.get('age_range', '')} | 收入水平: {up.get('income_level', '')}")
    doc.add_heading("风险分析", level=2)
    for r in risks:
        if isinstance(r, dict):
            doc.add_paragraph(f"• {r.get('type')}（{r.get('level')}）: {r.get('description')}", style="List Bullet")
    doc.add_heading("AI 总结", level=2)
    doc.add_paragraph(ai.get("summary", ""))

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf


def build_market_report(product: str, country: str, ai: dict) -> io.BytesIO:
    """生成市场分析 Word 报告（AI 结构化数据 → 文档）"""
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "微软雅黑"
    style.font.size = Pt(10.5)

    h = doc.add_heading(f"{product}市场分析（{country}）", level=0)
    h.alignment = WD_ALIGN_PARAGRAPH.CENTER
    meta = doc.add_paragraph()
    meta.add_run("声明: 本报告由 AI 大模型生成，数据为估算值，仅供参考，非官方统计")

    # 市场规模
    doc.add_heading("市场规模", level=1)
    ms = ai.get("market_size") or {}
    doc.add_paragraph(f"规模: {ms.get('value', '未知')}（{ms.get('year', '')}年估算）")
    if ms.get("note"):
        doc.add_paragraph(f"说明: {ms.get('note')}")

    # 增长趋势
    doc.add_heading("增长趋势", level=1)
    gt = ai.get("growth_trend") or {}
    doc.add_paragraph(f"CAGR: {gt.get('cagr', '未知')} | 预测区间: {gt.get('forecast_years', '')}")
    doc.add_paragraph(gt.get("description", ""))
    if gt.get("key_drivers"):
        doc.add_heading("关键驱动因素", level=2)
        for k in gt["key_drivers"]:
            doc.add_paragraph(f"• {k}", style="List Bullet")

    # 热门品牌
    doc.add_heading("热门品牌", level=1)
    brands = ai.get("top_brands") or []
    if brands:
        tbl = doc.add_table(rows=1 + len(brands), cols=4)
        tbl.style = "Light Grid Accent 1"
        for j, head in enumerate(["品牌", "所属国家", "市场地位", "备注"]):
            tbl.rows[0].cells[j].text = head
        for i, b in enumerate(brands, 1):
            if not isinstance(b, dict):
                continue
            tbl.rows[i].cells[0].text = str(b.get("name", ""))
            tbl.rows[i].cells[1].text = str(b.get("origin", ""))
            tbl.rows[i].cells[2].text = str(b.get("position", ""))
            tbl.rows[i].cells[3].text = str(b.get("note", ""))

    # 用户画像
    doc.add_heading("用户画像", level=1)
    up = ai.get("user_profile") or {}
    doc.add_paragraph(f"年龄区间: {up.get('age_range', '')} | 收入水平: {up.get('income_level', '')}")
    if up.get("key_needs"):
        doc.add_heading("核心需求", level=2)
        for n in up["key_needs"]:
            doc.add_paragraph(f"• {n}", style="List Bullet")
    if up.get("buying_habits"):
        doc.add_heading("购买习惯", level=2)
        for b in up["buying_habits"]:
            doc.add_paragraph(f"• {b}", style="List Bullet")

    # 风险分析
    doc.add_heading("风险分析", level=1)
    risks = ai.get("risks") or []
    if risks:
        tbl = doc.add_table(rows=1 + len(risks), cols=3)
        tbl.style = "Light Grid Accent 1"
        for j, head in enumerate(["风险类型", "等级", "说明"]):
            tbl.rows[0].cells[j].text = head
        for i, r in enumerate(risks, 1):
            if not isinstance(r, dict):
                continue
            tbl.rows[i].cells[0].text = str(r.get("type", ""))
            tbl.rows[i].cells[1].text = str(r.get("level", ""))
            tbl.rows[i].cells[2].text = str(r.get("description", ""))

    # AI 总结
    doc.add_heading("AI 总结", level=1)
    doc.add_paragraph(ai.get("summary", ""))

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf


def build_csv(rows: list) -> io.BytesIO:
    """生成 CSV 原始数据：完整导出 UN Comtrade 返回的每条记录（所有字段）"""
    if not rows:
        return io.BytesIO("暂无数据".encode("utf-8-sig"))

    # 取所有记录并集字段（保持顺序），UN 返回啥导啥
    all_keys: list[str] = []
    seen = set()
    for r in rows:
        if isinstance(r, dict):
            for k in r.keys():
                if k not in seen:
                    seen.add(k)
                    all_keys.append(k)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(all_keys)
    for r in rows:
        if isinstance(r, dict):
            writer.writerow([r.get(k, "") for k in all_keys])
    return io.BytesIO(buf.getvalue().encode("utf-8-sig"))  # BOM 防 Excel 中文乱码
