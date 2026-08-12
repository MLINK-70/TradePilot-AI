"""export.py — 导出模块：Word 分析报告 + CSV 原始数据

从 gen_export_demo.py 验证过的逻辑抽取，供 API 路由复用。
"""
import csv
import datetime
import io
import os
import tempfile
import time

import matplotlib
matplotlib.use("Agg")  # 无界面后端，服务器环境必需
import matplotlib.pyplot as plt
from docx import Document
from docx.shared import Cm, Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn

# 中文字体（Windows）
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

# Word 报告字体（学术论文规范）：
#   正文：宋体 小四（12pt）
#   标题：黑体，一级四号（14pt）> 二级小四（12pt）> 三级五号（10.5pt）——大小递减分级
FONT_BODY = "宋体"      # 正文
FONT_HEADING = "黑体"   # 标题
FONT_SIZES = {
    "Title": (FONT_HEADING, Pt(22)),      # 封面大标题 二号（22pt）
    "Heading 1": (FONT_HEADING, Pt(14)),  # 一级标题 四号（14pt）
    "Heading 2": (FONT_HEADING, Pt(12)),  # 二级标题 小四（12pt）
    "Heading 3": (FONT_HEADING, Pt(10.5)),  # 三级标题 五号（10.5pt）
    "Normal": (FONT_BODY, Pt(12)),        # 正文 小四（12pt）
    "List Bullet": (FONT_BODY, Pt(12)),   # 列表 小四
}


def _set_font_style(style, font_name: str, size: Pt, bold: bool = False) -> None:
    """设置段落样式的字体（含 eastAsia，否则中文字符回退宋体导致大小不一）

    必须清除 w:asciiTheme/w:eastAsiaTheme 等 theme 属性——theme 字体优先于
    直接指定的 rFonts，不清除则设置不生效（Heading 默认用 majorHAnsi 主题字体）。
    """
    style.font.name = font_name
    style.font.size = size
    style.font.bold = bold
    # 关键：中文字体必须写到 rPr/w:eastAsia，python-docx 只设 name 对中文不生效
    rpr = style.element.get_or_add_rPr()
    rfonts = rpr.get_or_add_rFonts()
    # 清除 theme 属性（majorHAnsi/majorEastAsia 等），否则优先于直接指定
    for attr in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme"):
        if rfonts.get(qn(attr)) is not None:
            del rfonts.attrib[qn(attr)]
    rfonts.set(qn("w:eastAsia"), font_name)
    rfonts.set(qn("w:ascii"), font_name)
    rfonts.set(qn("w:hAnsi"), font_name)


def _apply_doc_fonts(doc: Document) -> None:
    """统一整份文档的字体与段间距（学术论文规范）：
    - 正文宋体小四 + 标题黑体四号
    - 章节标题段前 24pt / 段后 12pt（拉开章节间隔）
    - 小节标题段前 15.6pt / 段后 6pt（参考实训报告排版）
    - 正文段后 3pt 微间隔，避免整块挤在一起
    """
    spacing = {
        "Title": (Pt(0), Pt(6)),
        "Heading 1": (Pt(24), Pt(12)),   # 章节：前 2 行 / 后 1 行
        "Heading 2": (Pt(15.6), Pt(6)),  # 小节：前约 1.3 行（实训报告同款）/ 后 0.5 行
        "Heading 3": (Pt(12), Pt(4)),
        "Normal": (Pt(0), Pt(3)),
        "List Bullet": (Pt(0), Pt(2)),
    }
    for name, (font_name, size) in FONT_SIZES.items():
        try:
            _set_font_style(doc.styles[name], font_name, size,
                            bold=name in ("Title", "Heading 1", "Heading 2"))
            sb, sa = spacing.get(name, (Pt(0), Pt(0)))
            doc.styles[name].paragraph_format.space_before = sb
            doc.styles[name].paragraph_format.space_after = sa
        except KeyError:
            continue


def _force_runs_font(doc: Document) -> None:
    """兜底：遍历所有段落 run 强制设置字体（模板渲染/样式继承不完全时仍生效）

    样式表定义对 Word 大多数情况有效，但 docxtpl 渲染的模板段落可能带直接格式
    （direct formatting），此时 run 级强制设置能覆盖，保证全文字体一致。
    """
    for p in doc.paragraphs:
        style_name = p.style.name if p.style else ""
        is_heading = style_name.startswith("Heading") or style_name == "Title"
        font_name = FONT_HEADING if is_heading else FONT_BODY
        for run in p.runs:
            run.font.name = font_name
            # 标题 run 保留样式字号（不覆盖），正文 run 无字号时给默认
            if run.font.size is None and not is_heading:
                run.font.size = Pt(12)
            rpr = run._element.get_or_add_rPr()
            rfonts = rpr.get_or_add_rFonts()
            rfonts.set(qn("w:eastAsia"), font_name)
            rfonts.set(qn("w:ascii"), font_name)
            rfonts.set(qn("w:hAnsi"), font_name)
    # 表格单元格文字也统一（正文宋体）
    for tbl in doc.tables:
        for row in tbl.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    for run in p.runs:
                        run.font.name = FONT_BODY
                        rpr = run._element.get_or_add_rPr()
                        rfonts = rpr.get_or_add_rFonts()
                        rfonts.set(qn("w:eastAsia"), FONT_BODY)
                        rfonts.set(qn("w:ascii"), FONT_BODY)
                        rfonts.set(qn("w:hAnsi"), FONT_BODY)


def _disable_spellcheck(doc: Document) -> None:
    """隐藏拼写/语法检查（文档级设置，随文档走）

    报告含大量英文品牌/术语（AirPods/Huawei/UN Comtrade 等），Word 拼写检查
    会标红波浪线。关键设置（不是 proofState——那只是"已检查"标记，打开时
    Word 仍会重新检查标红）：
    - w:hideSpellingErrors：仅隐藏此文档的拼写错误（谁打开都不显示波浪线）
    - w:hideGrammaticalErrors：同上，语法错误
    - w:proofState clean：辅助标记
    """
    from docx.oxml import OxmlElement

    settings = doc.settings.element
    # 删除已有同名元素再重插（防重复）
    for tag in ("w:hideSpellingErrors", "w:hideGrammaticalErrors", "w:proofState"):
        for el in settings.findall(qn(tag)):
            settings.remove(el)
    # 插到 settings 开头（schema 顺序：proofState 在前，hide 类在后）
    for tag, val in (("w:hideSpellingErrors", "true"), ("w:hideGrammaticalErrors", "true")):
        el = OxmlElement(tag)
        el.set(qn("w:val"), val)
        settings.insert(0, el)
    ps = OxmlElement("w:proofState")
    ps.set(qn("w:spelling"), "clean")
    ps.set(qn("w:grammar"), "clean")
    settings.insert(0, ps)


def _prevent_table_split(doc: Document) -> None:
    """表格行禁止跨页断开（w:cantSplit）

    学术论文规范：表格不能从中间被分页切断。给所有表格行加 cantSplit——
    行放不下时整行移到下一页，而不是被切成两半。
    """
    from docx.oxml import OxmlElement

    for tbl in doc.tables:
        for row in tbl.rows:
            tr_pr = row._tr.get_or_add_trPr()
            if tr_pr.find(qn("w:cantSplit")) is None:
                cant = OxmlElement("w:cantSplit")
                tr_pr.append(cant)


def _add_page_numbers(doc: Document) -> None:
    """页脚加页码（居中：第 X 页 / 共 Y 页，PAGE/NUMPAGES 域）"""
    from docx.oxml import OxmlElement

    section = doc.sections[0]
    footer = section.footer
    p = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER

    def _add_field(text: str) -> None:
        r = p.add_run()
        r.font.size = Pt(9)
        b = OxmlElement("w:fldChar"); b.set(qn("w:fldCharType"), "begin")
        instr = OxmlElement("w:instrText")
        instr.set(qn("xml:space"), "preserve")
        instr.text = text
        sep = OxmlElement("w:fldChar"); sep.set(qn("w:fldCharType"), "separate")
        ph = OxmlElement("w:t"); ph.text = "1"
        end = OxmlElement("w:fldChar"); end.set(qn("w:fldCharType"), "end")
        for el in (b, instr, sep, ph, end):
            r._element.append(el)

    run = p.add_run("第 ")
    run.font.size = Pt(9)
    _add_field(" PAGE ")
    run = p.add_run(" 页 / 共 ")
    run.font.size = Pt(9)
    _add_field(" NUMPAGES ")
    run = p.add_run(" 页")
    run.font.size = Pt(9)


def _add_toc_field(doc: Document, anchor: object = None) -> None:
    """生成学术论文式目录（成品化：点线页码 + 跳转，不依赖 Word 打开时更新）

    必须在文档内容全部生成后调用（目录需要知道每个标题的页码域）：
    - 为每个 Heading 1 标题加书签
    - 在 anchor 段落后生成目录条目：标题文字 + 右对齐点线制表位 + PAGEREF 页码域
    - 点线制表位（dot leader）是学术论文目录标准样式；PAGEREF 支持 Ctrl+点击跳转
    """
    from docx.oxml import OxmlElement

    def _add_bookmark(p, bm_id: int, name: str) -> None:
        bm_start = OxmlElement("w:bookmarkStart")
        bm_start.set(qn("w:id"), str(bm_id))
        bm_start.set(qn("w:name"), name)
        bm_end = OxmlElement("w:bookmarkEnd")
        bm_end.set(qn("w:id"), str(bm_id))
        p._p.insert(0, bm_start)
        p._p.append(bm_end)

    def _add_pageref(entry, bm_name: str) -> None:
        """条目 run 后追加 PAGEREF 页码域（tab 触发点线 + 页码）"""
        r = entry.add_run("\t")._element
        b = OxmlElement("w:fldChar"); b.set(qn("w:fldCharType"), "begin")
        instr = OxmlElement("w:instrText")
        instr.set(qn("xml:space"), "preserve")
        instr.text = f' PAGEREF {bm_name} \\h '
        sep = OxmlElement("w:fldChar"); sep.set(qn("w:fldCharType"), "separate")
        ph = OxmlElement("w:t"); ph.text = "0"
        end = OxmlElement("w:fldChar"); end.set(qn("w:fldCharType"), "end")
        for el in (b, instr, sep, ph, end):
            r.append(el)

    # 1. 给所有 Heading 1 标题加书签（"目录"自身跳过）
    bm_id = 1
    headings = []
    for p in doc.paragraphs:
        if p.style.name != "Heading 1" or p.text.strip() == "目录":
            continue
        name = f"_Toc_{bm_id}"
        _add_bookmark(p, bm_id, name)
        headings.append((p.text.strip(), name))
        bm_id += 1

    # 2. 生成目录条目（正序：lxml addnext 插到上一条之后）
    from docx.text.paragraph import Paragraph as _Paragraph
    # 内部锚点链接（w:anchor）不需要 relationship 声明——只有外部链接（w:r:id）才需要
    last_el = anchor._p  # 从 anchor 段落元素开始
    for title, bm_name in headings:
        new_el = last_el.makeelement(qn("w:p"), {})
        last_el.addnext(new_el)  # 插到 last_el 之后
        entry = _Paragraph(new_el, anchor._parent)
        entry.paragraph_format.tab_stops.add_tab_stop(Cm(15.5), alignment=1, leader=1)
        # 目录文字用 w:hyperlink 包裹（单击直接跳转，无需 Ctrl+点击）
        hl = OxmlElement("w:hyperlink")
        hl.set(qn("w:anchor"), bm_name)  # 锚点 = 标题书签名
        r_el = OxmlElement("w:r")
        rpr = OxmlElement("w:rPr")
        rfonts = OxmlElement("w:rFonts")
        rfonts.set(qn("w:ascii"), "微软雅黑")
        rfonts.set(qn("w:hAnsi"), "微软雅黑")
        rfonts.set(qn("w:eastAsia"), "微软雅黑")
        rpr.append(rfonts)
        color = OxmlElement("w:color")
        color.set(qn("w:val"), "123C5C")  # 深海蓝，区分普通文字
        rpr.append(color)
        r_el.append(rpr)
        t = OxmlElement("w:t")
        t.text = title
        t.set(qn("xml:space"), "preserve")
        r_el.append(t)
        hl.append(r_el)
        entry._p.append(hl)
        # PAGEREF 页码域（点线 + 页码）
        _add_pageref(entry, bm_name)
        last_el = new_el

    # settings.xml 加 updateFields（必须按 w:settings 的 schema 顺序插入，append 到末尾可能被 Word 忽略）
    settings = doc.settings.element
    if settings.find(qn("w:updateFields")) is None:
        upd = OxmlElement("w:updateFields")
        upd.set(qn("w:val"), "true")
        # w:settings 子元素顺序：w:zoom, w:embedSystemFonts, w:defaultTabStop, w:updateFields, ...
        # 插到第一个非 zoom 的元素前，保证在 w:defaultTabStop 之后
        anchor_el = None
        for child in settings:
            tag = child.tag.split('}')[-1]
            if tag not in ("zoom", "embedSystemFonts", "characterSpacingControl", "defaultTabStop"):
                anchor_el = child
                break
        if anchor_el is not None:
            anchor_el.addprevious(upd)
        else:
            settings.append(upd)


def build_trend_chart(trend: dict) -> io.BytesIO:
    """生成趋势折线图 PNG（内存流），供 Word 报告嵌入"""
    years = list(trend.keys())
    values = [trend[y]["value"] / 1e8 for y in years]  # 亿美元

    # 宽幅布局，给标注留足空间（tight_layout 防裁切）
    fig, ax = plt.subplots(figsize=(10, 5))
    fig.subplots_adjust(left=0.1, right=0.95, top=0.88, bottom=0.15)
    ax.plot(years, values, marker="o", linewidth=2.2, color="#2e5bff")
    ax.fill_between(years, values, alpha=0.12, color="#2e5bff")
    ax.set_title("出口贸易金额趋势（亿美元）", fontsize=13)
    ax.set_xlabel("年份", fontsize=11)
    ax.set_ylabel("亿美元", fontsize=11)
    ax.grid(True, alpha=0.3)
    # 顶部留 15% 余量，防止峰值标注文字超出绘图区
    vmin, vmax = ax.get_ylim()
    ax.set_ylim(vmin, vmax * 1.15)
    for x, y in zip(years, values):
        # 标注放数据点下方（避免顶部溢出被裁切；统一向下永不出界）
        ax.annotate(f"{y:.2f}", (x, y), textcoords="offset points",
                    xytext=(0, -14), fontsize=10, ha="center")
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150)
    plt.close(fig)
    buf.seek(0)
    return buf


def build_executive_summary(product: str, target: str, year: str, stats: dict,
                            analysis: dict, total_value: float) -> str:
    """生成执行摘要（报告开头）：关键数字 + AI 一句话总结 + 数据来源标注"""
    lines = []
    if stats:
        # 数据区间（Citation：数字可溯源）；单年显示"X年"，多年显示"X-Y"
        fy, ly = stats.get("first_year"), stats.get("last_year")
        y_range = f"{fy}-{ly}" if fy and ly and fy != ly else (f"{fy}年" if fy else year)
        lines.append(f"• 总出口额: {total_value / 1e8:.2f} 亿美元（{y_range}，UN Comtrade）")
        if stats.get("cagr_pct") is not None:
            lines.append(f"• 年复合增长率: {stats['cagr_pct']}%（{y_range}）")
        if stats.get("peak_year"):
            lines.append(f"• 峰值年份: {stats['peak_year']}（{y_range} 区间内）")
        if stats.get("change_over_period_pct") is not None:
            lines.append(f"• 期末较期初变化: {stats['change_over_period_pct']:.1f}%（{y_range}）")
    if analysis.get("overview"):
        lines.append(f"• AI 总结: {analysis['overview']}")
    lines.append(f"• 数据来源: UN Comtrade 公共 API，报告生成于 {datetime.date.today().isoformat()}")
    return "\n".join(lines) if lines else f"（{product} → {target} {year}，暂无摘要数据）"


def finalize_docx(buf: io.BytesIO, as_pdf: bool = False) -> io.BytesIO:
    """报告收尾（共用）：写临时文件 → COM 更新域/修表格跨页 → 可选转 PDF → 返回

    - docx: COM 更新 PAGEREF 页码、页脚 PAGE、表格防切分，补写拼写检查隐藏
    - pdf: 在上一步基础上 Word 导出 PDF
    - 无 Word/COM 失败时原样返回 docx（域可在用户打开时自动更新）
    """
    import os
    import tempfile
    tmp_path = os.path.join(tempfile.gettempdir(), f"_tp_export_{int(time.time() * 1000)}.docx")
    try:
        with open(tmp_path, "wb") as f:
            f.write(buf.getvalue())
        _refresh_fields_docx(tmp_path)
        read_path = tmp_path
        if as_pdf:
            _convert_to_pdf(tmp_path)
            read_path = tmp_path.replace(".docx", ".pdf")
        with open(read_path, "rb") as f:
            return io.BytesIO(f.read())
    except Exception:
        return buf
    finally:
        for p in (tmp_path, tmp_path.replace(".docx", ".pdf")):
            try:
                os.remove(p)
            except OSError:
                pass


def _convert_to_pdf(docx_path: str) -> None:
    """Word COM: docx → pdf（同目录同名 .pdf），失败静默。串行化（Word 单实例）。"""
    import threading
    _word_lock = getattr(_convert_to_pdf, "_lock", None)
    if _word_lock is None:
        _word_lock = threading.Lock()
        _convert_to_pdf._lock = _word_lock
    with _word_lock:
        try:
            import win32com.client
            word = win32com.client.Dispatch("Word.Application")
            word.Visible = False
            try:
                word.DisplayAlerts = 0
                doc = word.Documents.Open(docx_path)
                doc.SaveAs2(docx_path.replace(".docx", ".pdf"), FileFormat=17)
                doc.Close()
            finally:
                word.Quit()
        except Exception:
            pass


def build_word_report(product: str, target: str, year: str, hs_code: str,
                      rows: list, ai: dict, hs_description: str = "",
                      stats: dict | None = None, analysis: dict | None = None) -> io.BytesIO:
    """生成贸易数据 Word 报告（与市场分析同套规范：封面/目录/字体/页码/表格防切）

    章节：封面 → 目录 → 一、执行摘要 → 二、出口趋势（图）→ 三、数据总览（表）
    → 四、原始数据（表）→ 五、AI 市场分析 → 附录：数据来源。
    """
    from docx.oxml import OxmlElement
    from docx.shared import RGBColor

    total_value = sum(r.get("primaryValue") or 0 for r in rows)
    total_wgt = sum(r.get("netWgt") or 0 for r in rows)
    hs_desc = f"（{hs_description}）" if hs_description else ""

    # 趋势图 PNG（≥2 年才生成）；用 summarize_trend 逐年累加，与执行摘要 stats 同口径
    chart_buf = None
    from trade import summarize_trend
    trend_map = summarize_trend(rows)
    if len(trend_map) >= 2:
        chart_buf = build_trend_chart(trend_map)

    doc = Document()
    _apply_doc_fonts(doc)
    style = doc.styles["Normal"]
    style.font.name = FONT_BODY
    style.font.size = Pt(12)
    NAVY = RGBColor(0x12, 0x3C, 0x5C)
    ACCENT = RGBColor(0xC4, 0x45, 0x2C)

    def _hr(space_before: bool = True):
        p = doc.add_paragraph()
        if space_before:
            p.paragraph_format.space_before = Pt(6)
        ppr = p._p.get_or_add_pPr()
        pbdr = OxmlElement("w:pBdr")
        bottom = OxmlElement("w:bottom")
        bottom.set(qn("w:val"), "single")
        bottom.set(qn("w:sz"), "12")
        bottom.set(qn("w:space"), "1")
        bottom.set(qn("w:color"), "123C5C")
        pbdr.append(bottom)
        ppr.append(pbdr)
        return p

    def _h(text, level=1, blank_before=False):
        if level == 1 and blank_before:
            for _ in range(2):
                blank = doc.add_paragraph()
                blank.paragraph_format.space_before = Pt(0)
                blank.paragraph_format.space_after = Pt(0)
                blank.paragraph_format.keepNext = True
        return doc.add_heading(text, level=level)

    def _p(text="", bold=False, indent=True):
        p = doc.add_paragraph()
        if indent:
            p.paragraph_format.first_line_indent = Pt(24)
        r = p.add_run(text)
        r.bold = bold
        return p

    # ===== 封面 =====
    brand = doc.add_paragraph()
    brand.alignment = WD_ALIGN_PARAGRAPH.CENTER
    brand.paragraph_format.space_before = Pt(12)
    br = brand.add_run("TRADEPILOT AI  ·  EXPORT INTELLIGENCE")
    br.font.size = Pt(11)
    br.font.color.rgb = NAVY
    br.bold = True
    _hr()
    t = doc.add_heading(f"{product}出口贸易分析报告", level=0)
    t.alignment = WD_ALIGN_PARAGRAPH.CENTER
    t.paragraph_format.space_before = Pt(24)
    for run in t.runs:
        run.font.color.rgb = NAVY
    st = doc.add_paragraph()
    st.alignment = WD_ALIGN_PARAGRAPH.CENTER
    st.paragraph_format.space_before = Pt(6)
    sr = st.add_run(f"目标市场：{target}  ·  出口国：中国  ·  HS{hs_code}{hs_desc}")
    sr.font.size = Pt(14)
    sr.font.color.rgb = ACCENT
    sr.bold = True
    doc.add_paragraph()
    info_lines = [
        f"数据来源：UN Comtrade 联合国商品贸易数据库",
        f"生成日期：{datetime.date.today().isoformat()}  ·  报告编号：TP-{datetime.date.today().strftime('%Y%m%d')}-{target}-HS{hs_code}",
        "统计指标由程序精确计算 · AI 仅作解读 · 数据可溯源",
    ]
    for line in info_lines:
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_after = Pt(2)
        r = p.add_run(line)
        r.font.size = Pt(10.5)
        r.font.color.rgb = RGBColor(0x55, 0x55, 0x55)
    _hr(space_before=True)
    # 核心数据速览
    _h("核心数据速览", 1)
    kpi_rows = []
    kpi_rows.append((f"对{target}出口总额（{year}）", f"{total_value / 1e8:.2f} 亿美元"))
    if total_wgt:
        kpi_rows.append(("出口净重", f"{total_wgt / 1e6:.2f} 千吨"))
    if stats:
        if stats.get("cagr_pct") is not None:
            kpi_rows.append(("年复合增长率 CAGR", f"{stats['cagr_pct']}%"))
        if stats.get("peak_year"):
            kpi_rows.append(("峰值年份", f"{stats['peak_year']}"))
    kpi_rows.append(("数据记录数", f"{len(rows)} 条"))
    if kpi_rows:
        kpi_tbl = doc.add_table(rows=len(kpi_rows), cols=2)
        kpi_tbl.style = "Light Grid Accent 1"
        for i, (a, b) in enumerate(kpi_rows):
            kpi_tbl.rows[i].cells[0].text = a
            kpi_tbl.rows[i].cells[1].text = b
            for cell in kpi_tbl.rows[i].cells:
                for cp in cell.paragraphs:
                    cp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _hr()
    foot = doc.add_paragraph()
    foot.alignment = WD_ALIGN_PARAGRAPH.CENTER
    fr = foot.add_run("TradePilot AI · Export Intelligence")
    fr.font.size = Pt(9)
    fr.font.color.rgb = RGBColor(0x99, 0x99, 0x99)

    # ===== 目录（学术论文式：点线页码 + 单击跳转）=====
    doc.add_page_break()
    toc_title = _h("目录", 1)
    doc.add_page_break()

    # ===== 一、执行摘要 =====
    _h("一、执行摘要", 1, blank_before=True)
    summary_text = build_executive_summary(
        product, target, year,
        stats or {}, analysis or {},
        total_value,
    )
    for line in summary_text.split("\n"):
        _p(line)

    # ===== 二、出口趋势（图）=====
    _h("二、出口趋势", 1, blank_before=True)
    if chart_buf:
        _p(f"中国对{target}出口 {product}（HS {hs_code}）出口额变化趋势，单位：亿美元：")
        doc.add_picture(chart_buf, width=Cm(14))
        _p(f"数据来源：UN Comtrade 公共 API（HS {hs_code}）", indent=False)
    else:
        _p("（单年数据，无趋势图）")

    # ===== 三、数据总览表 =====
    _h("三、数据总览", 1, blank_before=True)
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
    if stats:
        _h("统计指标（程序精确计算）", 2)
        if stats.get("change_over_period_pct") is not None:
            _p(f"• 区间 {stats['first_year']}-{stats['last_year']} 期末较期初变化 {stats['change_over_period_pct']:.1f}%", indent=False)
        if stats.get("max_swing_year") is not None:
            _p(f"• 最大单年波动：{stats['max_swing_year']} 年 {stats['max_swing_pct']}%", indent=False)
        prices = stats.get("unit_prices") or []
        if prices:
            _p("• 单价趋势：" + "; ".join(f"{p['year']}年 {p['price']:.2f} 美元/公斤" for p in prices), indent=False)

    # ===== 四、原始数据表 =====
    _h("四、原始数据（UN Comtrade）", 1, blank_before=True)
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

    # ===== 五、AI 市场分析 =====
    _h("五、AI 市场分析", 1, blank_before=True)
    ms = ai.get("market_size") or {}
    gt = ai.get("growth_trend") or {}
    risks = ai.get("risks") or []
    up = ai.get("user_profile") or {}
    _h("市场规模", 2)
    _p(f"{ms.get('value', '未知')}（{ms.get('year', '')}年估算）")
    _h("增长趋势", 2)
    _p(f"CAGR {gt.get('cagr', '未知')}，{gt.get('forecast_years', '')}")
    if gt.get("description"):
        _p(gt["description"])
    _h("用户画像", 2)
    _p(f"年龄区间: {up.get('age_range', '')} | 收入水平: {up.get('income_level', '')}")
    _h("风险分析", 2)
    for r in risks:
        if isinstance(r, dict):
            _p(f"• {r.get('type')}（{r.get('level')}）: {r.get('description')}", indent=False)
    _h("AI 总结", 2)
    _p(ai.get("summary", ""))

    # ===== 附录：数据来源 =====
    _h("附录：数据来源与说明", 1, blank_before=True)
    src_rows = [("数据维度", "来源", "说明")]
    src_rows.append(("出口贸易数据", "UN Comtrade 联合国商品贸易统计数据库", "HS 编码口径，公共 API 实时查询"))
    src_rows.append(("统计指标", "程序精确计算", "CAGR / 区间变化 / 最大波动 / 单价趋势"))
    stbl = doc.add_table(rows=len(src_rows), cols=3)
    stbl.style = "Light Grid Accent 1"
    for i, (a, b, c) in enumerate(src_rows):
        stbl.rows[i].cells[0].text = a
        stbl.rows[i].cells[1].text = b
        stbl.rows[i].cells[2].text = c
        if i == 0:
            for cell in stbl.rows[0].cells:
                if cell.paragraphs[0].runs:
                    cell.paragraphs[0].runs[0].bold = True
    _p()
    _p("免责声明：本报告由 AI 大模型基于真实数据生成，市场估算部分仅供参考，实际决策请以官方统计为准。")

    # 收尾统一：页码 / 拼写检查 / 表格防切 / 目录 / 字体
    _add_page_numbers(doc)
    _disable_spellcheck(doc)
    _prevent_table_split(doc)
    _add_toc_field(doc, toc_title)
    _force_runs_font(doc)

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf


def _refresh_fields_docx(path: str) -> None:
    """用 Word COM 打开 docx 并更新所有域（PAGEREF 页码/页脚 PAGE），保存后返回

    顺带修复表格跨页：检测每个表格首行/末行页码，跨页时表格前插入分页符
    （整表移到下一页，不切断）。仅 Windows + 已装 Word 时可用；失败静默。
    用线程锁串行化：Word COM 单实例，并发调用会冲突。
    """
    import threading
    _word_lock = getattr(_refresh_fields_docx, "_lock", None)
    if _word_lock is None:
        _word_lock = threading.Lock()
        _refresh_fields_docx._lock = _word_lock
    with _word_lock:
        try:
            import win32com.client
            word = win32com.client.Dispatch("Word.Application")
            word.Visible = False
            try:
                word.DisplayAlerts = 0  # 防兼容性/恢复弹窗卡死
                doc = word.Documents.Open(path)
                doc.Repaginate()
                # 检测并修复表格跨页（最多 3 轮，插入分页符后分页变化需重新检查）
                for _ in range(3):
                    doc.Repaginate()
                    fixed = False
                    for tbl in doc.Tables:
                        try:
                            first_pg = tbl.Rows(1).Range.Information(3)  # wdActiveEndPageNumber
                            last_pg = tbl.Rows(tbl.Rows.Count).Range.Information(3)
                        except Exception:
                            continue
                        if first_pg != last_pg:
                            # 表格跨页：在表格第一行前插入分页符（整表下移一页）
                            tbl.Rows(1).Range.InsertBreak(7)  # wdPageBreak
                            fixed = True
                            break  # 分页已变，重新检查
                    if not fixed:
                        break
                doc.Repaginate()
                doc.Fields.Update()
                for section in doc.Sections:
                    for f in section.Footers:
                        if f.Range.Fields.Count:
                            f.Range.Fields.Update()
                # 关闭拼写/语法检查（COM 重写 settings.xml 会清掉，保存后补写）
                try:
                    doc.SpellingChecked = True
                    doc.GrammarChecked = True
                except Exception:
                    pass
                doc.Save()
                doc.Close()
            finally:
                word.Quit()
            # COM 保存后补写文档级"隐藏拼写错误"设置（COM 重写 settings.xml 会清掉）
            from docx import Document as _Doc
            _doc = _Doc(path)
            _disable_spellcheck(_doc)
            _doc.save(path)
        except Exception:
            pass  # 无 Word/COM 失败：交给 updateFields 打开时更新


def build_market_report(product: str, country: str, ai: dict,
                        trade_evidence: dict | None = None,
                        competitiveness: dict | None = None,
                        background: dict | None = None,
                        landscape: dict | None = None,
                        market_context: dict | None = None,
                        trend_series: dict | None = None) -> io.BytesIO:
    """生成市场分析 Word 报告（20 页版：原始数据摘要 + 经济/贸易/竞争力图表 + 竞争格局 + 驱动因素）"""
    from market_data import get_worldbank_series, COUNTRY_ISO3
    from trade import get_latest_year
    from docx.shared import RGBColor

    doc = Document()
    # 统一字体：正文宋体小四 + 标题黑体（学术论文规范）
    _apply_doc_fonts(doc)
    style = doc.styles["Normal"]
    style.font.name = FONT_BODY
    style.font.size = Pt(12)

    iso3 = COUNTRY_ISO3.get(country, "")
    latest = get_latest_year()
    years5 = list(range(latest - 4, latest + 1))

    def _h(text, level=1, blank_before=False):
        """章节标题：level 1 且 blank_before 时前插 2 个空行段落，与上一章节明显隔开

        封面/目录页开头的标题不插（blank_before=False，页首无需空行）。
        空行段落加 keepNext，保证空行+标题不拆分跨页（避免页底留半页空白）。
        """
        if level == 1 and blank_before:
            for _ in range(2):
                blank = doc.add_paragraph()
                blank.paragraph_format.space_before = Pt(0)
                blank.paragraph_format.space_after = Pt(0)
                blank.paragraph_format.keepNext = True  # 与下一段（标题）同页
        return doc.add_heading(text, level=level)

    def _p(text="", bold=False, indent=True):
        """正文段落：中文首行缩进 2 字符（默认），列表项/标题行不缩进"""
        p = doc.add_paragraph()
        if indent:
            p.paragraph_format.first_line_indent = Pt(24)  # 2 字符（12pt 字号 × 2）
        r = p.add_run(text)
        r.bold = bold
        return p

    # ===== 封面（学术论文式：品牌行 + 装饰线 + 大标题 + 信息块 + 核心速览）=====
    from docx.oxml import OxmlElement
    from docx.shared import RGBColor
    NAVY = RGBColor(0x12, 0x3C, 0x5C)
    ACCENT = RGBColor(0xC4, 0x45, 0x2C)

    def _hr(space_before: bool = True):
        """装饰线：段落底边框（skill 建议：横线用段落边框，不用表格）"""
        p = doc.add_paragraph()
        if space_before:
            p.paragraph_format.space_before = Pt(6)
        ppr = p._p.get_or_add_pPr()
        pbdr = OxmlElement("w:pBdr")
        bottom = OxmlElement("w:bottom")
        bottom.set(qn("w:val"), "single")
        bottom.set(qn("w:sz"), "12")      # 1.5pt 线宽
        bottom.set(qn("w:space"), "1")
        bottom.set(qn("w:color"), "123C5C")  # 深海蓝
        pbdr.append(bottom)
        ppr.append(pbdr)
        return p

    # 顶部品牌行
    brand = doc.add_paragraph()
    brand.alignment = WD_ALIGN_PARAGRAPH.CENTER
    brand.paragraph_format.space_before = Pt(12)
    br = brand.add_run("TRADEPILOT AI  ·  EXPORT INTELLIGENCE")
    br.font.size = Pt(11)
    br.font.color.rgb = NAVY
    br.bold = True
    _hr()

    # 大标题（居中）
    t = doc.add_heading(f"{product}市场分析报告", level=0)
    t.alignment = WD_ALIGN_PARAGRAPH.CENTER
    t.paragraph_format.space_before = Pt(24)
    for run in t.runs:
        run.font.color.rgb = NAVY
    st = doc.add_paragraph()
    st.alignment = WD_ALIGN_PARAGRAPH.CENTER
    st.paragraph_format.space_before = Pt(6)
    sr = st.add_run(f"目标市场：{country}  ·  出口国：中国")
    sr.font.size = Pt(14)
    sr.font.color.rgb = ACCENT
    sr.bold = True

    # 信息块（居中段落：数据来源 / 生成日期 / 报告编号）
    doc.add_paragraph()
    info_lines = [
        f"数据来源：UN Comtrade · World Bank · Tavily 行业检索",
        f"生成日期：{datetime.date.today().isoformat()}  ·  报告编号：TP-{datetime.date.today().strftime('%Y%m%d')}-{country}",
        "统计指标由程序精确计算 · AI 仅作解读 · 数据可溯源",
    ]
    for line in info_lines:
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_after = Pt(2)
        r = p.add_run(line)
        r.font.size = Pt(10.5)
        r.font.color.rgb = RGBColor(0x55, 0x55, 0x55)
    _hr(space_before=True)

    # 核心数据速览（封面下半部，KPI 表填充不空）
    _h("核心数据速览", 1)
    kpi_rows = []
    if trade_evidence and trade_evidence.get("trend"):
        trend = trade_evidence["trend"]
        years = sorted(trend.keys())
        last_y, last_v = years[-1], trend[str(years[-1])]
        kpi_rows.append((f"对{country}出口额（{last_y} 年）", f"{last_v} 亿美元"))
    if competitiveness and competitiveness.get("tc") is not None:
        kpi_rows.append(("贸易竞争力指数 TC", f"{competitiveness['tc']}"))
        if competitiveness.get("market_share") is not None:
            kpi_rows.append(("占该国市场进口份额", f"{competitiveness['market_share']}%"))
    if market_context and market_context.get("gdp_per_capita"):
        kpi_rows.append(("人均 GDP", f"{market_context['gdp_per_capita']:,.0f} 美元"))
    if market_context and market_context.get("gdp"):
        kpi_rows.append(("GDP", f"{market_context['gdp'] / 1e12:.2f} 万亿美元"))
    if kpi_rows:
        kpi_tbl = doc.add_table(rows=len(kpi_rows), cols=2)
        kpi_tbl.style = "Light Grid Accent 1"
        for i, (a, b) in enumerate(kpi_rows):
            kpi_tbl.rows[i].cells[0].text = a
            kpi_tbl.rows[i].cells[1].text = b
            for cell in kpi_tbl.rows[i].cells:
                for cp in cell.paragraphs:
                    cp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    # 底部品牌
    _hr()
    foot = doc.add_paragraph()
    foot.alignment = WD_ALIGN_PARAGRAPH.CENTER
    fr = foot.add_run("TradePilot AI · Export Intelligence")
    fr.font.size = Pt(9)
    fr.font.color.rgb = RGBColor(0x99, 0x99, 0x99)

    # 目录（学术论文式：点线页码 + 跳转，独立成页）
    doc.add_page_break()
    toc_title = _h("目录", 1)
    doc.add_page_break()  # 目录后分页（正文从新页开始）
    # 目录条目在文档全部生成后填充（末尾统一生成，见 _build_toc_entries）

    # ===== 一、执行摘要（关键数字速览表）=====
    _h("一、执行摘要", 1, blank_before=True)
    _p("本报告基于多重真实数据证据链生成：联合国商品贸易数据库（UN Comtrade）提供出口贸易数据，"
       "世界银行（World Bank）提供经济环境数据，行业检索提供竞争格局与宏观背景。"
       "所有统计指标（CAGR、贸易竞争力指数、市场出口份额）由程序精确计算，AI 仅作解读，数据可溯源。")
    es = ai.get("executive_summary") or {}
    if es.get("background"):
        _p(f"背景：{es['background']}")
    if es.get("key_findings"):
        _h("核心发现", 2)
        for f in es["key_findings"]:
            _p(f"• {f}", indent=False)
    if es.get("challenges"):
        _h("主要挑战", 2)
        for c in es["challenges"]:
            _p(f"• {c}", indent=False)
    if es.get("recommendation"):
        _p(f"总体建议：{es['recommendation']}")
    if es.get("data_points"):
        _h("关键数据点", 2)
        for d in es["data_points"]:
            _p(f"• {d}", indent=False)

    # ===== 二、市场环境（经济趋势图）=====
    _h("二、市场环境", 1, blank_before=True)
    if iso3:
        gdp_series = get_worldbank_series(iso3, "gdp", years5)
        pc_series = get_worldbank_series(iso3, "gdp_per_capita", years5)
        if gdp_series:
            _p(f"{country} 近 5 年 GDP 变化（World Bank 官方数据，单位：万亿美元）：")
            _add_bar_chart(doc, gdp_series, "GDP 变化趋势", "万亿美元", divisor=1e12)
        if pc_series:
            _p(f"{country} 近 5 年人均 GDP 变化（World Bank 官方数据，单位：美元）：")
            _add_line_chart(doc, pc_series, "人均 GDP 变化趋势", "美元")
        _h("经济环境解读", 2)
        env = []
        if market_context and market_context.get("gdp"):
            env.append(f"GDP {market_context['gdp'] / 1e12:.2f} 万亿美元")
        if market_context and market_context.get("population"):
            env.append(f"人口 {market_context['population'] / 1e8:.2f} 亿")
        if market_context and market_context.get("gdp_per_capita"):
            env.append(f"人均 GDP {market_context['gdp_per_capita']:,.0f} 美元")
        if env:
            _p(f"{country} 最新经济数据：{'，'.join(env)}（World Bank）")
        if background and background.get("summary"):
            _p(f"全球宏观背景：{background['summary']}（{background.get('_source', 'WTO')}）")
    else:
        _p("（该国家不在 World Bank 数据覆盖范围内，市场环境图表跳过）")

    # ===== 三、出口贸易（趋势图 + 汇总表）=====
    _h("三、出口贸易", 1, blank_before=True)
    if trade_evidence and trade_evidence.get("trend"):
        trend = trade_evidence["trend"]
        years = sorted(trend.keys())
        _p(f"中国对{country}出口 {product}（HS {trade_evidence.get('hs_code', '')}）逐年数据，单位：亿美元：")
        _add_line_chart(doc, {int(y): float(v) for y, v in trend.items()}, "出口额趋势", "亿美元")
        # 年度明细表（含净重与单价）
        wt = trade_evidence.get("weight_trend") or {}
        tbl = doc.add_table(rows=1 + len(years), cols=4)
        tbl.style = "Light Grid Accent 1"
        for j, head in enumerate(["年份", "出口额（亿美元）", "净重（千吨）", "同比变化"]):
            tbl.rows[0].cells[j].text = head
        prev = None
        for i, y in enumerate(years, 1):
            v = trend[str(y)]
            tbl.rows[i].cells[0].text = str(y)
            tbl.rows[i].cells[1].text = f"{v}"
            w = wt.get(str(y))
            tbl.rows[i].cells[2].text = f"{w}" if w else "—"
            if prev is not None and prev > 0:
                chg = (v - prev) / prev * 100
                tbl.rows[i].cells[3].text = f"{chg:+.1f}%"
            else:
                tbl.rows[i].cells[3].text = "—"
            prev = v
        # 数据构成解读：单价趋势 + 波动上下文（程序计算，AI 不参与算术）
        _h("数据构成解读", 2)
        unit_prices = []
        for y in years:
            v = trend[str(y)]
            w = wt.get(str(y))
            if v and w and w > 0:
                unit_prices.append((int(y), v * 1e8 / (w * 1e6)))  # 美元/公斤
        if len(unit_prices) >= 2:
            _p("单价（出口额 ÷ 净重）逐年变化，反映产品价值构成：")
            for y, up in unit_prices:
                _p(f"• {y} 年：{up:.2f} 美元/公斤", indent=False)
            first_up = unit_prices[0][1]
            last_up = unit_prices[-1][1]
            if first_up > 0:
                chg = (last_up - first_up) / first_up * 100
                trend_desc = "上升（向高价值产品结构演进）" if chg > 5 else (
                    "下降（低价值产品占比扩大或价格竞争加剧）" if chg < -5 else "平稳")
                _p(f"• 区间单价变化 {chg:+.1f}%：{trend_desc}——"
                   f"单价上升通常意味着产品向中高端升级，单价下降则可能面临价格竞争或低端产品放量。")
        # 波动上下文（峰值/谷值/最大波动）
        if len(years) >= 3:
            vals = [(int(y), trend[str(y)]) for y in years]
            peak_y, peak_v = max(vals, key=lambda x: x[1])
            trough_y, trough_v = min(vals, key=lambda x: x[1])
            swings = []
            for k in range(1, len(vals)):
                a, b = vals[k - 1][1], vals[k][1]
                if a > 0:
                    swings.append((vals[k][0], (b - a) / a * 100))
            max_swing = max(swings, key=lambda x: abs(x[1])) if swings else None
            _p(f"• 区间峰值：{peak_y} 年 {peak_v} 亿美元；谷值：{trough_y} 年 {trough_v} 亿美元。", indent=False)
            if max_swing:
                _p(f"• 最大单年波动：{max_swing[0]} 年 {max_swing[1]:+.1f}%"
                   f"（{'下滑' if max_swing[1] < 0 else '增长'}——"
                   f"需结合当年宏观事件与行业周期解读，如需求波动、供应链调整或贸易政策变化）。")
        _p()
        _p(f"数据来源：UN Comtrade 公共 API（HS {trade_evidence.get('hs_code', '')}），单价与波动为程序计算")
    else:
        _p("（该产品对目标市场暂无贸易数据）")

    # ===== 四、竞争力分析（TC + 份额）=====
    _h("四、竞争力分析", 1, blank_before=True)
    if competitiveness and competitiveness.get("tc") is not None:
        tc = competitiveness["tc"]
        share = competitiveness.get("market_share")
        exp_v = competitiveness.get("export_value", 0) / 1e8
        imp_v = competitiveness.get("import_value", 0) / 1e8
        _p(f"贸易竞争力指数（TC）= {tc}，取值 -1 到 1，越接近 1 表示出口竞争力越强。")
        _p(f"中国对{country}出口 {product}：出口 {exp_v:.2f} 亿美元 vs 进口 {imp_v:.2f} 亿美元。")
        _add_gauge_chart(doc, tc, "TC 贸易竞争力指数")
        _h("进出口结构解读", 2)
        net = exp_v - imp_v
        if exp_v > 0:
            _p(f"• 净出口 {net:+.2f} 亿美元：中国在该品类对{country}呈"
               f"{'贸易顺差' if net > 0 else '贸易逆差'}。")
        if exp_v + imp_v > 0:
            _p(f"• 进出口比 {exp_v / (exp_v + imp_v) * 100:.0f}% 出口占比："
               f"{'以出口为主导' if exp_v > imp_v else '进口依赖明显'}——"
               f"该数据反映中国在该市场的角色是{'供应方' if exp_v > imp_v else '采购方'}。")
        if share is not None:
            _p(f"• 市场出口份额 {share}%：中国产品占{country}该品类进口的比重，"
               f"即该国每进口 100 美元该品类，约 {share:.0f} 美元来自中国。")
            _p(f"• 份额解读：{'渗透率较高，进入成熟竞争期' if share >= 15 else (
                '渗透率中等，仍有扩张空间' if share >= 5 else '渗透率较低，市场拓展空间大')}。")
    else:
        _p("（该品类竞争力数据不足）")

    # ===== 五、竞争格局（龙头品牌 + 份额表）=====
    _h("五、竞争格局", 1, blank_before=True)
    if landscape and landscape.get("top_brands"):
        brands = landscape["top_brands"]
        _p(f"{landscape.get('product_category', product)} 龙头品牌竞争格局"
           f"（来源：{landscape.get('_source', 'Tavily 行业检索')}）：")
        tbl = doc.add_table(rows=1 + len(brands), cols=3)
        tbl.style = "Light Grid Accent 1"
        for j, head in enumerate(["品牌", "市场份额", "地位"]):
            tbl.rows[0].cells[j].text = head
        for i, b in enumerate(brands, 1):
            tbl.rows[i].cells[0].text = str(b.get("name", ""))
            tbl.rows[i].cells[1].text = str(b.get("share", ""))
            tbl.rows[i].cells[2].text = str(b.get("position", ""))
        if landscape.get("shift_reasons"):
            _h("格局变动原因", 2)
            for r in landscape["shift_reasons"]:
                _p(f"• {r}", indent=False)
        if landscape.get("chain_insight"):
            _h("产业链洞察", 2)
            _p(landscape["chain_insight"])
        if landscape.get("key_insight"):
            _h("核心洞察", 2)
            _p(landscape["key_insight"])
        _h("对出口商的启示", 2)
        top_share = None
        for b in brands:
            try:
                s = float(str(b.get("share", "").replace("%", "")))
                top_share = max(top_share or 0, s)
            except (ValueError, TypeError):
                continue
        if top_share is not None:
            if top_share >= 30:
                _p(f"• 龙头品牌份额合计约 {top_share:.0f}%：市场高度集中，新进入者宜避开正面竞争，"
                   f"从细分场景（如通勤降噪、运动佩戴、价格带空档）切入。")
            elif top_share >= 15:
                _p(f"• 龙头品牌份额约 {top_share:.0f}%：市场中度集中，存在差异化空间，"
                   f"可在功能或价格带建立差异化定位。")
            else:
                _p(f"• 龙头品牌份额约 {top_share:.0f}%：市场相对分散，竞争格局未固化，"
                   f"是新进入者布局的窗口期。")
        if landscape.get("shift_reasons"):
            _p(f"• 格局正在变动（{'；'.join(landscape['shift_reasons'][:2])}），"
               f"变动期往往伴随新品牌崛起的机会，建议关注变动的技术/渠道驱动因素。")
        if landscape.get("chain_insight"):
            # 完整引用产业链洞察（不截断拼接模板套话，避免"存储芯片（…）：掌握…"式怪句）
            _p(f"• 产业链洞察：{landscape['chain_insight']}", indent=False)
    else:
        _p("（竞争格局数据不足，此章节跳过）")

    # ===== 六、市场规模与增长 =====
    _h("六、市场规模与增长", 1, blank_before=True)
    ms = ai.get("market_size") or {}
    _p(f"规模：{ms.get('value', '未知')}（{ms.get('year', '')}年估算）")
    if ms.get("note"):
        _p(f"说明：{ms['note']}")
    gt = ai.get("growth_trend") or {}
    if gt.get("cagr") or gt.get("description"):
        _h("增长趋势", 2)
        if gt.get("cagr"):
            _p(f"年复合增长率（CAGR）：{gt['cagr']}（{gt.get('forecast_years', '')}）")
        if gt.get("description"):
            _p(gt["description"])
        if gt.get("key_drivers"):
            _h("关键驱动因素", 3)
            for d in gt["key_drivers"]:
                _p(f"• {d}", indent=False)

    # ===== 七、驱动因素分析（整合数据）=====
    _h("七、驱动因素分析", 1, blank_before=True)
    _p("驱动出口与销量变化的因素可分为三类——需求侧、供给侧、竞争侧，以下结合真实数据逐项说明：")
    # 驱动因素数据表（因素 / 数据 / 影响方向）
    factor_rows = [("驱动因素", "真实数据", "影响")]
    if market_context and market_context.get("gdp_per_capita"):
        factor_rows.append(("人均 GDP（消费力）", f"{market_context['gdp_per_capita']:,.0f} 美元",
                            "高收入市场支撑中高端产品溢价"))
    if market_context and market_context.get("population"):
        factor_rows.append(("人口规模", f"{market_context['population'] / 1e8:.2f} 亿", "决定市场容量上限"))
    if market_context and market_context.get("gdp"):
        factor_rows.append(("经济总量 GDP", f"{market_context['gdp'] / 1e12:.2f} 万亿美元", "整体需求底盘"))
    if background and background.get("global_trade_growth"):
        factor_rows.append(("全球贸易增长预测", background["global_trade_growth"], "宏观景气度影响出口节奏"))
    if competitiveness and competitiveness.get("tc") is not None:
        factor_rows.append(("贸易竞争力 TC", f"{competitiveness['tc']}",
                            f"{'竞争力强，可扩张' if competitiveness['tc'] > 0.5 else '竞争力中等，需提升'}"))
    if competitiveness and competitiveness.get("market_share") is not None:
        factor_rows.append(("占市场进口份额", f"{competitiveness['market_share']}%", "现有渗透率 = 增长基数"))
    if trade_evidence and trade_evidence.get("trend"):
        trend = trade_evidence["trend"]
        years = sorted(trend.keys())
        if len(years) >= 2:
            first, last = trend[str(years[0])], trend[str(years[-1])]
            cagr = (pow(last / first, 1 / (len(years) - 1)) - 1) * 100
            # 标注"历史"：与第六章 AI 预测 CAGR 区分（避免同报告两个 CAGR 打架）
            factor_rows.append((f"出口 CAGR（{years[0]}-{years[-1]} 历史）",
                                f"{cagr:+.1f}%", "出口动能方向（历史）"))
    if landscape and landscape.get("top_brands"):
        factor_rows.append(("龙头品牌份额", f"{landscape['top_brands'][0].get('share', '')}",
                            "市场集中度决定进入难度"))
    if len(factor_rows) > 1:
        ftbl = doc.add_table(rows=len(factor_rows), cols=3)
        ftbl.style = "Light Grid Accent 1"
        for i, (a, b, c) in enumerate(factor_rows):
            ftbl.rows[i].cells[0].text = a
            ftbl.rows[i].cells[1].text = b
            ftbl.rows[i].cells[2].text = c
            if i == 0:
                for cell in ftbl.rows[0].cells:
                    if cell.paragraphs[0].runs:
                        cell.paragraphs[0].runs[0].bold = True
        _p()
        _p("上表每一项均为程序基于真实数据计算（UN Comtrade / World Bank / 行业检索），"
           "驱动因素分析基于真实数据而非主观判断。")

    _h("需求侧（经济与消费力）", 2)
    if market_context and market_context.get("gdp_per_capita"):
        _p(f"• 人均 GDP {market_context['gdp_per_capita']:,.0f} 美元：高收入市场对中高端产品需求强，"
           f"支撑 {product} 的溢价空间。")
    if market_context and market_context.get("population"):
        _p(f"• 人口 {market_context['population'] / 1e8:.2f} 亿：人口规模决定市场容量上限。", indent=False)
    if background and background.get("global_trade_growth"):
        _p(f"• 全球贸易增长预测 {background['global_trade_growth']}：宏观景气度影响整体出口节奏。", indent=False)
    _h("供给侧（出口能力）", 2)
    if competitiveness and competitiveness.get("tc") is not None:
        _p(f"• TC 指数 {competitiveness['tc']}：中国在该品类对{country}的贸易竞争力"
           f"（{'强' if competitiveness['tc'] > 0.5 else '中等'}）。")
    if trade_evidence and trade_evidence.get("trend"):
        trend = trade_evidence["trend"]
        years = sorted(trend.keys())
        if len(years) >= 2:
            first, last = trend[str(years[0])], trend[str(years[-1])]
            cagr = (pow(last / first, 1 / (len(years) - 1)) - 1) * 100
            _p(f"• {years[0]}-{years[-1]} 出口 CAGR {cagr:+.1f}%（历史）：反映该品类在目标市场的整体出口动能。", indent=False)
    _h("竞争侧（格局与份额）", 2)
    if landscape and landscape.get("top_brands"):
        brands = landscape["top_brands"]
        top = brands[0]
        _p(f"• 龙头 {top.get('name', '')} 份额 {top.get('share', '')}：市场集中度决定新进入者难度。", indent=False)
    if competitiveness and competitiveness.get("market_share") is not None:
        _p(f"• 中国占进口份额 {competitiveness['market_share']}%：现有渗透率是增长的基数。", indent=False)

    # ===== 八、风险分析 =====
    _h("八、风险分析", 1, blank_before=True)
    risks = ai.get("risks") or []
    if risks:
        tbl = doc.add_table(rows=1 + len(risks), cols=4)
        tbl.style = "Light Grid Accent 1"
        # 列宽分配（法规列加宽，避免"94/62/EC）、CE 认证"式截断）
        for row in tbl.rows:
            row.cells[0].width = Cm(2.5)
            row.cells[1].width = Cm(1.5)
            row.cells[2].width = Cm(6.5)
            row.cells[3].width = Cm(5.0)
        for j, head in enumerate(["风险类型", "等级", "说明", "相关法规"]):
            tbl.rows[0].cells[j].text = head
        for i, r in enumerate(risks, 1):
            if isinstance(r, dict):
                tbl.rows[i].cells[0].text = str(r.get("type", ""))
                tbl.rows[i].cells[1].text = str(r.get("level", ""))
                tbl.rows[i].cells[2].text = str(r.get("description", ""))
                tbl.rows[i].cells[3].text = str(r.get("regulation", ""))
    else:
        _p("（风险数据不足）")

    # ===== 九、用户画像与目标客群 =====
    _h("九、用户画像与目标客群", 1, blank_before=True)
    up = ai.get("user_profile") or {}
    if up.get("age_range") or up.get("income_level"):
        _p(f"年龄区间：{up.get('age_range', '未知')}  |  收入水平：{up.get('income_level', '未知')}")
    if up.get("key_needs"):
        _h("核心需求", 2)
        for n in up["key_needs"]:
            _p(f"• {n}", indent=False)
    if up.get("buying_habits"):
        _h("购买习惯", 2)
        for b in up["buying_habits"]:
            _p(f"• {b}", indent=False)
    _h("客群细分与触达建议", 2)
    if market_context and market_context.get("gdp_per_capita"):
        _p(f"• 高端客群：人均 GDP {market_context['gdp_per_capita']:,.0f} 美元支撑，主打品质与品牌溢价，"
           f"触达渠道以品牌官网 / 高端连锁为主。")
    _p(f"• 主流客群：注重性价比与口碑，触达以电商平台（如 Amazon 等）+ 社媒内容种草为主。", indent=False)
    _p(f"• 年轻客群：偏好新潮设计与社交属性，可借助 TikTok / Instagram 短视频与 KOL 合作扩大曝光。", indent=False)
    _h("购买决策旅程", 2)
    _p(f"1. 认知阶段：通过平台搜索、社媒种草、评测视频了解 {product} 品类的头部品牌与新品。")
    _p(f"2. 比较阶段：对比价格、参数、评论口碑，重点关注降噪、续航、佩戴舒适等核心卖点。")
    _p(f"3. 决策阶段：参考电商评分与销量榜单，高收入客群倾向品牌溢价，主流客群倾向性价比。")
    _p(f"4. 复购阶段：满意的佩戴体验与售后保障驱动复购，品牌生态（如智能互联）增强粘性。")
    _h("对营销策略的启示", 2)
    _p(f"• 定价分层：参考人均消费力与竞品价位，设置引流款（入门）+ 利润款（中高端）组合。", indent=False)
    _p(f"• 内容策略：围绕核心需求制作场景化内容（通勤降噪 / 运动佩戴 / 办公会议），匹配搜索意图。", indent=False)
    _p(f"• 渠道组合：电商平台为主力，社媒种草为杠杆，本地售后为信任背书。", indent=False)

    # ===== 十、行动路线 =====
    _h("十、行动路线", 1, blank_before=True)
    ap = ai.get("action_plan") or []
    if ap:
        for i, step in enumerate(ap, 1):
            _p(f"{i}. {step}")
    if ai.get("outlook"):
        _h("市场展望", 2)
        _p(ai["outlook"])
    if ai.get("summary"):
        _h("AI 总结", 2)
        _p(ai["summary"])

    # ===== 附录：数据来源与免责声明 =====
    # 附录自然接在正文后（不强制分页，避免产生空白页）
    _h("附录：数据来源与说明", 1, blank_before=True)
    src_rows = [("数据维度", "来源", "说明")]
    src_rows.append(("出口贸易数据", "UN Comtrade 联合国商品贸易统计数据库", "HS 编码口径，公共 API 实时查询"))
    src_rows.append(("经济环境数据", "World Bank 世界银行开放数据", "GDP / 人口 / 人均 GDP / 互联网普及率"))
    src_rows.append(("宏观背景", "WTO 全球贸易展望（Tavily 检索）", "30 天增量刷新"))
    src_rows.append(("竞争格局", "Tavily 行业检索", "龙头品牌 / 市场份额 / 格局变动原因，30 天缓存"))
    src_rows.append(("统计指标", "程序精确计算", "CAGR / TC 指数 / 市场出口份额 / 同比变化"))
    stbl = doc.add_table(rows=len(src_rows), cols=3)
    stbl.style = "Light Grid Accent 1"
    for i, (a, b, c) in enumerate(src_rows):
        stbl.rows[i].cells[0].text = a
        stbl.rows[i].cells[1].text = b
        stbl.rows[i].cells[2].text = c
        if i == 0:
            for cell in stbl.rows[0].cells:
                if cell.paragraphs[0].runs:
                    cell.paragraphs[0].runs[0].bold = True
    _p()
    _p("免责声明：本报告由 AI 大模型基于真实数据生成，市场估算部分（如市场规模数值）仅供参考，"
       "实际决策请以官方统计与一手调研为准。所有可溯源的统计指标均由程序计算，AI 不参与算术。")

    # 页脚页码（第 X 页 / 共 Y 页）
    _add_page_numbers(doc)
    # 关闭拼写检查（品牌/术语不标红波浪线）
    _disable_spellcheck(doc)

    # 目录条目（文档全部生成后填充：书签 + 点线页码条目）
    _add_toc_field(doc, toc_title)

    # 表格行禁止跨页断开（学术论文规范）
    _prevent_table_split(doc)

    # 兜底：run 级强制统一字体（模板直接格式/样式继承不完全时仍生效）
    _force_runs_font(doc)

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf


def _add_bar_chart(doc: Document, series: dict, title: str, unit: str, divisor: float = 1.0):
    """柱状图：多年数据 → matplotlib PNG → 嵌入 Word"""
    import matplotlib.pyplot as plt
    years = sorted(int(y) for y in series.keys())
    values = [series[y] / divisor for y in years]
    fig, ax = plt.subplots(figsize=(8, 4))
    fig.subplots_adjust(left=0.12, right=0.95, top=0.85, bottom=0.15)
    ax.bar([str(y) for y in years], values, color="#2e5bff", alpha=0.85)
    ax.set_title(f"{title}（{unit}）", fontsize=12)
    ax.grid(True, alpha=0.3, axis="y")
    for x, v in zip(range(len(years)), values):
        ax.annotate(f"{v:.2f}", (x, v), textcoords="offset points",
                    xytext=(0, 4), fontsize=9, ha="center")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130)
    plt.close(fig)
    buf.seek(0)
    doc.add_picture(buf, width=Cm(14))


def _add_line_chart(doc: Document, series: dict, title: str, unit: str, divisor: float = 1.0):
    """折线图：多年数据 → matplotlib PNG → 嵌入 Word"""
    import matplotlib.pyplot as plt
    years = sorted(int(y) for y in series.keys())
    values = [series[y] / divisor for y in years]
    fig, ax = plt.subplots(figsize=(8, 4))
    fig.subplots_adjust(left=0.12, right=0.95, top=0.85, bottom=0.15)
    ax.plot([str(y) for y in years], values, marker="o", linewidth=2.2, color="#2e5bff")
    ax.fill_between(range(len(years)), values, alpha=0.12, color="#2e5bff")
    ax.set_title(f"{title}（{unit}）", fontsize=12)
    ax.grid(True, alpha=0.3)
    for x, v in zip(range(len(years)), values):
        ax.annotate(f"{v:.2f}", (x, v), textcoords="offset points",
                    xytext=(0, -14), fontsize=9, ha="center")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130)
    plt.close(fig)
    buf.seek(0)
    doc.add_picture(buf, width=Cm(14))


def _add_gauge_chart(doc: Document, tc: float, title: str):
    """竞争力仪表图：TC 指数 → 半圆仪表 PNG"""
    import numpy as np
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(8, 3.5))
    # 半圆背景（-1 到 1）
    theta = np.linspace(0, np.pi, 100)
    x = np.cos(theta); y = np.sin(theta)
    ax.plot(x, y, color="#d0d0d0", linewidth=12, solid_capstyle="round")
    # 指示弧（0 到 TC）
    theta2 = np.linspace(np.pi, np.pi - (tc + 1) / 2 * np.pi, 50)
    x2 = np.cos(theta2); y2 = np.sin(theta2)
    ax.plot(x2, y2, color="#2e5bff" if tc >= 0 else "#c4452c", linewidth=12, solid_capstyle="round")
    ax.text(0, -0.2, f"TC = {tc:.2f}", ha="center", fontsize=16, fontweight="bold", color="#2e5bff")
    ax.text(-1.15, -0.1, "-1", ha="center", fontsize=10, color="#888")
    ax.text(1.15, -0.1, "1", ha="center", fontsize=10, color="#888")
    ax.text(0, 1.15, "竞争力弱 ← → 竞争力强", ha="center", fontsize=10, color="#666")
    ax.set_xlim(-1.3, 1.3); ax.set_ylim(-0.4, 1.3)
    ax.set_aspect("equal"); ax.axis("off")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130)
    plt.close(fig)
    buf.seek(0)
    doc.add_picture(buf, width=Cm(13))


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
