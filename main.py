"""main.py — FastAPI 入口：路由 + 报告渲染。

第一版保持扁平结构；后续模块（外贸/评论分析/贸易数据）上线时，
拆分到 routers/ 目录，本文件退化为"组装 app + include_router"。
llm.py / prompts.py 是所有模块共用的底座。
"""
import logging
from urllib.parse import quote

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from llm import analyze_market
from trade import AREA_MAP, GROUP_MEMBERS, HS_MAP, query_trade, query_trend, summarize_trend
from export import build_csv, build_word_report

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

app = FastAPI(title="TradePilot AI", description="面向消费电子出海的 AI 市场分析平台")

# 仅开发用：允许直接双击打开 index.html（file:// 跨源）；未来前后端分离时收紧
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class AnalyzeRequest(BaseModel):
    product: str
    country: str


@app.post("/api/analyze")
def analyze(req: AnalyzeRequest):
    """输入产品 + 目标国家 → 返回 Markdown 格式市场分析报告

    用同步 def（而非 async）：内部 analyze_market 是同步阻塞调用，
    FastAPI 会把同步端点放入线程池执行，不阻塞事件循环。
    """
    product = req.product.strip()
    country = req.country.strip()
    if not product or not country:
        raise HTTPException(status_code=400, detail="product 和 country 不能为空")

    try:
        data = analyze_market(product, country)
    except ValueError as e:
        raise HTTPException(status_code=502, detail=str(e))

    logging.info("分析完成: %s / %s", product, country)
    return {"report": markdown_report(product, country, data)}


class TradeExportRequest(BaseModel):
    product: str
    target: str
    year: str


def _fetch_trade_data(req: TradeExportRequest) -> tuple[str, list]:
    """复用查询逻辑：产品 + 国家/组织 + 年份 → (hs_code, rows)"""
    product = req.product.strip()
    target = req.target.strip()
    year = req.year.strip()
    if not product or not target or not year:
        raise HTTPException(status_code=400, detail="product、target、year 不能为空")
    try:
        hs, rows = query_trade(product, target, year)
    except ValueError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return hs, rows


def _download_headers(filename: str) -> dict:
    """构建下载响应头，中文文件名用 RFC 5987 编码（HTTP 头只支持 latin-1）"""
    return {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
    }


@app.post("/api/trade/export/report")
def export_report(req: TradeExportRequest):
    """下载 Word 分析报告（数据总览 + 原始数据 + AI 分析）"""
    hs, rows = _fetch_trade_data(req)
    try:
        ai = analyze_market(req.product.strip(), req.target.strip())
    except ValueError:
        ai = {}  # AI 分析失败不阻断报告下载，数据部分仍可用
    buf = build_word_report(req.product.strip(), req.target.strip(), req.year.strip(),
                            hs, rows, ai)
    filename = f"TradePilot-{req.product.strip()}-{req.target.strip()}-{req.year.strip()}-报告.docx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers=_download_headers(filename),
    )


@app.post("/api/trade/export/data")
def export_data(req: TradeExportRequest):
    """下载 CSV 原始数据（UN Comtrade 原始记录）"""
    hs, rows = _fetch_trade_data(req)
    buf = build_csv(rows)
    filename = f"TradePilot-{req.product.strip()}-{req.target.strip()}-{req.year.strip()}-原始数据.csv"
    return StreamingResponse(
        buf,
        media_type="text/csv",
        headers=_download_headers(filename),
    )


class TradeQueryRequest(BaseModel):
    product: str
    target: str
    start_year: int          # 起始年
    end_year: int | None = None  # 截至年（可选，留空默认到最新）


def _years_from_range(start_year: int, end_year: int | None) -> list:
    """起止年 → 年份列表；end_year 为空默认到最新可用年份"""
    latest = 2024  # UN Comtrade 数据更新至最新可用年份（1-3 月延迟）
    if end_year is None or end_year > latest:
        end_year = latest
    if start_year > end_year:
        return []
    return list(range(start_year, end_year + 1))


@app.get("/api/trade/options")
def trade_options():
    """返回前端下拉选项：产品（HS映射）+ 国家/组织（含分组标记）"""
    return {
        "products": sorted(HS_MAP.keys()),
        "targets": [
            {"name": name, "code": code, "is_group": code in GROUP_MEMBERS}
            for name, code in sorted(AREA_MAP.items())
        ],
    }


@app.post("/api/trade/query")
def trade_query(req: TradeQueryRequest):
    """贸易数据查询：产品 + 国家/组织 + 起止年 → 数据 + 趋势汇总"""
    product = req.product.strip()
    target = req.target.strip()
    if not product or not target or not req.start_year:
        raise HTTPException(status_code=400, detail="product、target、start_year 不能为空")

    years = _years_from_range(req.start_year, req.end_year)
    if not years:
        raise HTTPException(status_code=400, detail="年份范围无效（截至年不能早于起始年）")

    try:
        if len(years) == 1:
            hs, rows = query_trade(product, target, str(years[0]))
            trend = summarize_trend(rows)
        else:
            hs, rows, trend = query_trend(product, target, years)
    except ValueError as e:
        raise HTTPException(status_code=502, detail=str(e))

    logging.info("贸易查询: %s / %s / %d-%s", product, target, req.start_year, req.end_year or "最新")
    return {
        "hs_code": hs,
        "total_value": sum(r.get("primaryValue") or 0 for r in rows),
        "total_weight": sum(r.get("netWgt") or 0 for r in rows),
        "record_count": len(rows),
        "trend": [{"year": y, "value": v["value"], "weight": v["weight"]} for y, v in trend.items()],
        "rows": [
            {
                "year": r.get("refYear"),
                "partner_code": r.get("partnerCode"),
                "value": r.get("primaryValue") or 0,
                "weight": r.get("netWgt") or 0,
            }
            for r in rows
        ],
    }


# 挂载前端静态目录，前后端同源（必须放在所有 API 路由之后，
# 否则 "/" 挂载会拦截 /api 下的请求）
app.mount("/", StaticFiles(directory="static", html=True), name="static")


def _safe(value, default=""):
    """LLM 可能返回 null/None，统一兜底成字符串"""
    return default if value is None else str(value)


def markdown_report(product: str, country: str, d: dict) -> str:
    """把 DeepSeek 返回的结构化 JSON 渲染成 Markdown 报告"""
    lines = [f"# {product}市场分析（{country}）", ""]

    # 市场规模
    ms = d.get("market_size") or {}
    lines += [
        "## 市场规模",
        f"- **规模**：{_safe(ms.get('value'), '未知')}（{_safe(ms.get('year'), '未知')}年估算）",
        f"- **说明**：{_safe(ms.get('note'))}",
        "",
    ]

    # 增长趋势
    gt = d.get("growth_trend") or {}
    lines += [
        "## 增长趋势",
        f"- **年复合增长率（CAGR）**：{_safe(gt.get('cagr'), '未知')}",
        f"- **预测区间**：{_safe(gt.get('forecast_years'))}",
        f"- **趋势描述**：{_safe(gt.get('description'))}",
        "",
        "**关键驱动因素**：",
        *[f"- {_safe(item)}" for item in (gt.get("key_drivers") or [])],
        "",
    ]

    # 热门品牌
    brands = d.get("top_brands") or []
    lines += ["## 热门品牌", "| 品牌 | 所属国家 | 市场地位 | 备注 |", "| --- | --- | --- | --- |"]
    for b in brands:
        if not isinstance(b, dict):
            continue
        note = _safe(b.get("note"))
        lines.append(
            f"| {_safe(b.get('name'))} | {_safe(b.get('origin'))} | "
            f"{_safe(b.get('position'))} | {note} |"
        )
    lines.append("")

    # 用户画像
    up = d.get("user_profile") or {}
    lines += [
        "## 用户画像",
        f"- **年龄区间**：{_safe(up.get('age_range'))}",
        f"- **收入水平**：{_safe(up.get('income_level'))}",
        "",
        "**核心需求**：",
        *[f"- {_safe(item)}" for item in (up.get("key_needs") or [])],
        "",
        "**购买习惯**：",
        *[f"- {_safe(item)}" for item in (up.get("buying_habits") or [])],
        "",
    ]

    # 风险分析
    risks = d.get("risks") or []
    lines += ["## 风险分析", "| 风险类型 | 等级 | 说明 |", "| --- | --- | --- |"]
    for r in risks:
        if not isinstance(r, dict):
            continue
        lines.append(f"| {_safe(r.get('type'))} | {_safe(r.get('level'))} | {_safe(r.get('description'))} |")
    lines.append("")

    # 总结
    lines += ["## AI 总结", _safe(d.get("summary")), ""]

    return "\n".join(lines)
