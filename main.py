"""main.py — FastAPI 入口：路由 + 报告渲染。

第一版保持扁平结构；后续模块（外贸/评论分析/贸易数据）上线时，
拆分到 routers/ 目录，本文件退化为"组装 app + include_router"。
llm.py / prompts.py 是所有模块共用的底座。
"""
import logging
import json
import os
import sys
from urllib.parse import quote

# 资源路径：打包(exe)用 _MEIPASS，开发用项目目录
BASE_DIR = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))


def res_path(name: str) -> str:
    """资源文件绝对路径（static/templates/data 下的文件）"""
    return os.path.join(BASE_DIR, name)

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from llm import analyze_market, analyze_trade_trend
from market_data import (get_competitive_landscape, get_market_context,
                         get_news, get_trade_background)
from business import (generate_followup_email, generate_outreach_email,
                      generate_outreach_from_idea, generate_product_intro,
                      simulate_customer)
from ecommerce import analyze_reviews, compare_products, generate_listing
from ebay import analyze_item, get_oauth_token, parse_ebay_url
from aliexpress import analyze_product, parse_aliexpress_url
import config as cfg
from trade import (AREA_MAP, GROUP_MEMBERS, HS_MAP, get_competitiveness,
                   get_competitor_comparison, get_destination_ranking,
                   get_latest_year, get_top_exporters, query_trade, query_trend,
                   summarize_stats, summarize_trend)
from hs_descriptions import get_hs_description
from export import build_csv, build_market_report, build_word_report

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

app = FastAPI(title="TradePilot AI", description="面向消费电子出海的 AI 市场分析平台")

# 仅开发用：允许直接双击打开 index.html（file:// 跨源）；未来前后端分离时收紧
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],  # 跨源下载时前端能读到文件名
)


class AnalyzeRequest(BaseModel):
    product: str
    country: str


def _collect_evidence(product: str, country: str) -> tuple:
    """聚合真实数据证据链（经济/贸易/竞争力/宏观背景/竞争格局），失败不阻断"""
    market_ctx = get_market_context(country)
    trade_evidence = {}
    competitiveness = {}
    try:
        hs, rows, trend = query_trend(product, country, list(range(get_latest_year() - 2, get_latest_year() + 1)))
        if trend:
            trade_evidence = {
                "hs_code": hs,
                "trend": {str(y): round(v["value"] / 1e8, 2) for y, v in trend.items()},
                "total_value": round(sum(v["value"] for v in trend.values()) / 1e8, 2),
            }
        if len(rows):
            competitiveness = get_competitiveness(product, country, str(get_latest_year()))
    except Exception:
        pass
    background = get_trade_background()
    # 竞争格局（龙头品牌/份额，30 天缓存）
    landscape = get_competitive_landscape(product, country)
    return market_ctx, trade_evidence, competitiveness, background, landscape


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
        # 聚合真实数据证据链（经济 + 贸易 + 竞争力 + 宏观背景）
        market_ctx, trade_evidence, competitiveness, background, landscape = _collect_evidence(product, country)
        data = analyze_market(product, country, market_ctx, trade_evidence,
                              competitiveness, background, landscape)
    except ValueError as e:
        raise HTTPException(status_code=502, detail=str(e))

    # 行业动态（Tavily 搜索，失败不阻断）
    news = get_news(product, country)
    data["_news"] = news if news.get("available") else {}
    data["_trade"] = trade_evidence if trade_evidence else {}
    data["_competitiveness"] = competitiveness if competitiveness.get("available") else {}

    logging.info("分析完成: %s / %s", product, country)
    return {
        "report": markdown_report(product, country, data),
        "news": data.get("_news"),
        "trade": data.get("_trade"),
        "competitiveness": data.get("_competitiveness"),
        "market_context": market_ctx if market_ctx and market_ctx.get("available") else {},
        "background": background or {},
    }


@app.post("/api/analyze/export")
def export_market_report(req: AnalyzeRequest):
    """下载市场分析 Word 报告"""
    product = req.product.strip()
    country = req.country.strip()
    if not product or not country:
        raise HTTPException(status_code=400, detail="product 和 country 不能为空")

    try:
        # 完整证据链（与页面分析一致）
        market_ctx, trade_evidence, competitiveness, background, landscape = _collect_evidence(product, country)
        data = analyze_market(product, country, market_ctx, trade_evidence,
                              competitiveness, background, landscape)
    except ValueError as e:
        raise HTTPException(status_code=502, detail=str(e))

    buf = build_market_report(product, country, data, trade_evidence,
                              competitiveness, background)
    filename = f"TradePilot-{product}-{country}-市场分析报告.docx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers=_download_headers(filename),
    )


class TradeExportRequest(BaseModel):
    product: str
    target: str
    start_year: int          # 起始年
    end_year: int | None = None  # 截至年（可选，留空默认到最新）
    reporter: str = "中国"  # 出口国（报告国），默认中国


def _fetch_trade_data(req: TradeExportRequest) -> tuple[str, list]:
    """复用查询逻辑：产品 + 国家/组织 + 起止年 → (hs_code, rows)"""
    product = req.product.strip()
    target = req.target.strip()
    reporter = (req.reporter or "中国").strip()
    if not product or not target or not req.start_year:
        raise HTTPException(status_code=400, detail="product、target、start_year 不能为空")

    years = _years_from_range(req.start_year, req.end_year)
    if not years:
        raise HTTPException(status_code=400, detail="年份范围无效（截至年不能早于起始年）")

    try:
        if len(years) == 1:
            hs, rows = query_trade(product, target, str(years[0]), reporter=reporter)
        else:
            hs, rows, _ = query_trend(product, target, years, reporter=reporter)
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
    """下载 Word 分析报告（执行摘要 + 趋势图 + 数据 + AI 分析）"""
    hs, rows = _fetch_trade_data(req)
    try:
        ai = analyze_market(req.product.strip(), req.target.strip())
    except ValueError:
        ai = {}  # AI 分析失败不阻断报告下载，数据部分仍可用
    # 统计指标 + AI 趋势解读（供执行摘要引用）
    trend = summarize_trend(rows)
    stats = summarize_stats(trend) if len(trend) >= 3 else {}
    analysis = {}
    try:
        if len(trend) >= 3:
            analysis = analyze_trade_trend(req.product.strip(), req.target.strip(),
                                           req.reporter, trend, stats)
    except ValueError:
        pass
    # 年份标签：end_year 为空时解析出实际年份范围（默认到最新），避免只显示起始年误导
    years_actual = _years_from_range(req.start_year, req.end_year)
    year_label = f"{years_actual[0]}-{years_actual[-1]}" if len(years_actual) > 1 else str(years_actual[0])
    buf = build_word_report(req.product.strip(), req.target.strip(), year_label,
                            hs, rows, ai, get_hs_description(hs), stats, analysis)
    filename = f"TradePilot-{req.product.strip()}-{req.target.strip()}-{year_label}-报告.docx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers=_download_headers(filename),
    )


@app.post("/api/trade/export/data")
def export_data(req: TradeExportRequest):
    """下载 CSV 原始数据（UN Comtrade 完整原始记录）"""
    hs, rows = _fetch_trade_data(req)
    buf = build_csv(rows)
    # 年份标签：end_year 为空时解析出实际年份范围（默认到最新），避免只显示起始年误导
    years_actual = _years_from_range(req.start_year, req.end_year)
    year_label = f"{years_actual[0]}-{years_actual[-1]}" if len(years_actual) > 1 else str(years_actual[0])
    filename = f"TradePilot-{req.product.strip()}-{req.target.strip()}-{year_label}-原始数据.csv"
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
    reporter: str = "中国"   # 出口国（报告国），默认中国


def _years_from_range(start_year: int, end_year: int | None) -> list:
    """起止年 → 年份列表；end_year 为空默认到最新可用年份"""
    latest = get_latest_year()  # 动态探测最新可用年份
    if end_year is None or end_year > latest:
        end_year = latest
    if start_year > end_year:
        return []
    return list(range(start_year, end_year + 1))


@app.get("/api/trade/options")
def trade_options():
    """返回前端下拉选项：产品（HS映射）+ 国家/组织（含分组标记）+ 最新年份"""
    return {
        "products": sorted(HS_MAP.keys()),
        "targets": [
            {"name": name, "code": code, "is_group": code in GROUP_MEMBERS}
            for name, code in sorted(AREA_MAP.items())
        ],
        "latest_year": get_latest_year(),
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
            hs, rows = query_trade(product, target, str(years[0]), reporter=req.reporter)
            trend = summarize_trend(rows)
        else:
            hs, rows, trend = query_trend(product, target, years, reporter=req.reporter)
    except ValueError as e:
        raise HTTPException(status_code=502, detail=str(e))

    logging.info("贸易查询: %s→%s / %s / %d-%s", req.reporter, target, product, req.start_year, req.end_year or "最新")

    # AI 解读真实贸易数据（失败不阻断查询，前端显示"解读生成失败"）
    analysis = {}
    market_ctx = {}
    competitiveness = {}
    landscape = {}
    stats = {}
    try:
        # 单年数据无趋势可解读（首末同年变化 0% 无意义），跳过 AI 解读
        if len(trend) >= 3:
            stats = summarize_stats(trend)  # 程序先算好已核实指标
            # 注入 World Bank 市场环境（双证据链：贸易 + 经济）
            market_ctx = get_market_context(target)
            # 注入竞争格局（龙头品牌/变动原因/产业链，Tavily 检索）
            landscape = get_competitive_landscape(product, target)
            analysis = analyze_trade_trend(product, target, req.reporter, trend, stats,
                                           market_ctx, landscape)
            # Citation：解读数据区间（数字可溯源）
            if stats:
                analysis["_data_range"] = f"{stats['first_year']}-{stats['last_year']}"
    except ValueError:
        pass

    # 竞争力指标（TC + 市场出口份额，失败不阻断）
    try:
        if len(years) == 1:
            competitiveness = get_competitiveness(product, target, str(years[0]), req.reporter)
        else:
            competitiveness = get_competitiveness(product, target, str(years[-1]), req.reporter)
    except Exception:
        competitiveness = {}

    # 竞争对手出口对比 + 目的地排名（失败不阻断，仅单年查询时）
    competitor_cmp = {}
    destination_rank = {}
    top_exporters = []
    try:
        y = str(years[0] if len(years) == 1 else years[-1])
        # 动态识别品类出口大国：全球 TOP 出口国作为竞争对手
        top_exporters = get_top_exporters(product, y)
        top_names = [t["country"] for t in top_exporters]
        # 出口国必须在列表里，否则对比没有出口国自身
        if req.reporter not in top_names:
            top_names = [req.reporter] + [n for n in top_names if n != req.reporter]
        competitor_cmp = get_competitor_comparison(product, target, y,
                                                    competitors=top_names[:6],
                                                    reporter=req.reporter)
        destination_rank = get_destination_ranking(product, target, y, req.reporter)
    except Exception:
        pass

    return {
        "hs_code": hs,
        "hs_description": get_hs_description(hs),  # HS 编码品名解释
        "total_value": sum(r.get("primaryValue") or 0 for r in rows),
        "total_weight": sum(r.get("netWgt") or 0 for r in rows),
        "record_count": len(rows),
        "trend": [{"year": y, "value": v["value"], "weight": v["weight"]} for y, v in trend.items()],
        "analysis": analysis,  # AI 市场解读
        "stats": stats,  # 程序精确计算的统计指标（CAGR/峰值/单价等）
        "landscape": landscape if landscape.get("top_brands") else {},  # 竞争格局（龙头品牌/变动原因/产业链）
        "market_context": market_ctx,  # World Bank 经济环境（前端展示来源）
        "competitiveness": competitiveness,  # TC + 市场出口份额
        "competitors": competitor_cmp,  # 竞争对手出口对比
        "top_exporters": top_exporters,  # 品类全球出口大国（含全球出口额）
        "destinations": destination_rank,  # 出口目的地排名
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


class BusinessEmailRequest(BaseModel):
    product: str
    market: str
    customer_type: str = "经销商"
    company: str = ""
    contact: str = ""
    email: str = ""
    selling_points: str = ""
    customer_company: str = ""
    customer_contact: str = ""
    customer_title: str = ""
    hook: str = "免费样品"
    credentials: str = ""


@app.post("/api/business/outreach")
def business_outreach(req: BusinessEmailRequest):
    """生成英文开发信 + 中文要点"""
    product = req.product.strip()
    market = req.market.strip()
    if not product or not market:
        raise HTTPException(status_code=400, detail="product 和 market 不能为空")
    try:
        data = generate_outreach_email(
            product, market, req.customer_type.strip() or "经销商",
            req.company.strip(), req.contact.strip(), req.email.strip(),
            req.selling_points.strip(),
            req.customer_company.strip(), req.customer_contact.strip(),
            req.customer_title.strip(),
            req.hook.strip() or "免费样品", req.credentials.strip(),
        )
    except ValueError as e:
        raise HTTPException(status_code=502, detail=str(e))
    logging.info("开发信生成: %s / %s", product, market)
    return data


class BusinessFollowupRequest(BaseModel):
    product: str
    market: str
    customer_type: str = "经销商"
    original_subject: str = ""
    company: str = ""
    contact: str = ""
    email: str = ""
    customer_company: str = ""
    customer_contact: str = ""
    customer_title: str = ""


@app.post("/api/business/followup")
def business_followup(req: BusinessFollowupRequest):
    """生成跟进邮件（基于开发信上下文）"""
    product = req.product.strip()
    market = req.market.strip()
    if not product or not market:
        raise HTTPException(status_code=400, detail="product 和 market 不能为空")
    try:
        data = generate_followup_email(
            product, market, req.customer_type.strip() or "经销商",
            req.original_subject.strip(),
            req.company.strip(), req.contact.strip(), req.email.strip(),
            req.customer_contact.strip(), req.customer_title.strip(),
            req.customer_company.strip(),
        )
    except ValueError as e:
        raise HTTPException(status_code=502, detail=str(e))
    logging.info("跟进邮件生成: %s / %s", product, market)
    return data


class BusinessIdeaRequest(BaseModel):
    idea: str
    company: str = ""
    contact: str = ""
    email: str = ""
    customer_company: str = ""
    customer_contact: str = ""
    customer_title: str = ""


@app.post("/api/business/from-idea")
def business_from_idea(req: BusinessIdeaRequest):
    """核心思路 → 完整开发信（AI 拆解扩写）"""
    idea = req.idea.strip()
    if not idea:
        raise HTTPException(status_code=400, detail="请填写核心思路")
    try:
        data = generate_outreach_from_idea(
            idea,
            req.company.strip(), req.contact.strip(), req.email.strip(),
            req.customer_company.strip(), req.customer_contact.strip(),
            req.customer_title.strip(),
        )
    except ValueError as e:
        raise HTTPException(status_code=502, detail=str(e))
    logging.info("思路扩写: %s", idea[:50])
    return data


@app.post("/api/business/product-intro")
def business_product_intro(req: BusinessIdeaRequest):
    """核心思路 → 产品介绍 + FAQ"""
    idea = req.idea.strip()
    if not idea:
        raise HTTPException(status_code=400, detail="请填写核心思路")
    try:
        data = generate_product_intro(idea)
    except ValueError as e:
        raise HTTPException(status_code=502, detail=str(e))
    logging.info("产品介绍生成: %s", idea[:50])
    return data


class SimulateRequest(BaseModel):
    product: str
    market: str
    customer_type: str = "经销商"
    user_message: str
    history: list = []  # [{"role": "user"/"assistant", "content": "..."}]


@app.post("/api/business/simulate")
def business_simulate(req: SimulateRequest):
    """AI 扮演采购商回复（模拟客户沟通练习）"""
    product = req.product.strip()
    market = req.market.strip()
    if not product or not market or not req.user_message.strip():
        raise HTTPException(status_code=400, detail="product、market、user_message 不能为空")
    try:
        data = simulate_customer(product, market, req.customer_type.strip() or "经销商",
                                 req.user_message.strip(), req.history or [])
    except ValueError as e:
        raise HTTPException(status_code=502, detail=str(e))
    logging.info("模拟客户: %s / %s", product, market)
    return data


class EcommerceAnalyzeRequest(BaseModel):
    reviews: list = []       # 用户粘贴的评论（每行一条）
    use_sample: bool = False  # 使用内置演示数据


@app.post("/api/ecommerce/analyze")
def ecommerce_analyze(req: EcommerceAnalyzeRequest):
    """评论分析：粘贴评论或演示数据 → 痛点/卖点/建议"""
    reviews = [r.strip() for r in req.reviews if r and r.strip()]
    product_hint = ""
    if req.use_sample or not reviews:
        try:
            with open(res_path("data/sample_reviews.json"), encoding="utf-8") as f:
                sample = json.load(f)
            reviews = sample.get("reviews", [])
            product_hint = sample.get("product", "")
        except (FileNotFoundError, json.JSONDecodeError):
            raise HTTPException(status_code=500, detail="演示数据加载失败")
    if not reviews:
        raise HTTPException(status_code=400, detail="请粘贴评论或使用演示数据")
    try:
        data = analyze_reviews(reviews)
        data["product"] = product_hint
    except ValueError as e:
        raise HTTPException(status_code=502, detail=str(e))
    logging.info("评论分析: %d 条", len(reviews))
    return data


class EcommerceListingRequest(BaseModel):
    product: str
    platform: str = "亚马逊"
    analysis: dict = {}


@app.post("/api/ecommerce/listing")
def ecommerce_listing(req: EcommerceListingRequest):
    """基于评论分析结果生成平台风格 Listing"""
    if not req.product.strip() or not req.analysis:
        raise HTTPException(status_code=400, detail="product 和 analysis 不能为空")
    try:
        data = generate_listing(req.product.strip(), req.platform.strip() or "亚马逊", req.analysis)
    except ValueError as e:
        raise HTTPException(status_code=502, detail=str(e))
    logging.info("Listing 生成: %s / %s", req.product, req.platform)
    return data


@app.get("/api/ecommerce/sample")
def ecommerce_sample():
    """返回内置演示数据"""
    try:
        with open(res_path("data/sample_reviews.json"), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        raise HTTPException(status_code=500, detail="演示数据加载失败")


class EbayAnalyzeRequest(BaseModel):
    url: str


@app.post("/api/ebay/analyze")
def ebay_analyze(req: EbayAnalyzeRequest):
    """eBay 商品链接 → 商品信息 + AI 分析"""
    url = req.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="请提供 eBay 商品链接")
    item_id = parse_ebay_url(url)
    if not item_id:
        raise HTTPException(status_code=400, detail="无法从链接提取 eBay 商品 ID")
    if not cfg.RUNTIME_KEYS.get("EBAY_APP_ID") or not cfg.RUNTIME_KEYS.get("EBAY_CLIENT_SECRET"):
        raise HTTPException(status_code=503, detail="eBay 密钥未配置（需 App ID + Client Secret，见设置面板）")
    try:
        token = get_oauth_token(cfg.RUNTIME_KEYS.get("EBAY_APP_ID", ""),
                                cfg.RUNTIME_KEYS.get("EBAY_CLIENT_SECRET", ""))
        data = analyze_item(item_id, token)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"eBay 查询失败: {e}")
    logging.info("eBay 分析: %s", item_id)
    return data


class AliexpressAnalyzeRequest(BaseModel):
    url: str


@app.post("/api/aliexpress/analyze")
def aliexpress_analyze(req: AliexpressAnalyzeRequest):
    """速卖通商品链接 → 商品信息 + AI 分析"""
    url = req.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="请提供速卖通商品链接")
    item_id = parse_aliexpress_url(url)
    if not item_id:
        raise HTTPException(status_code=400, detail="无法从链接提取速卖通商品 ID")
    if not cfg.RUNTIME_KEYS.get("ALIEXPRESS_APP_KEY") or not cfg.RUNTIME_KEYS.get("ALIEXPRESS_APP_SECRET"):
        raise HTTPException(status_code=503, detail="速卖通密钥未配置（需 App Key + App Secret，见设置面板）")
    try:
        data = analyze_product(cfg.RUNTIME_KEYS.get("ALIEXPRESS_APP_KEY", ""),
                               cfg.RUNTIME_KEYS.get("ALIEXPRESS_APP_SECRET", ""), item_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"速卖通查询失败: {e}")
    logging.info("速卖通分析: %s", item_id)
    return data


class EcommerceCompareRequest(BaseModel):
    product_a: str
    reviews_a: list = []
    product_b: str
    reviews_b: list = []


@app.post("/api/ecommerce/compare")
def ecommerce_compare(req: EcommerceCompareRequest):
    """竞品对比：两组评论 → 差异化洞察"""
    reviews_a = [r.strip() for r in req.reviews_a if r and r.strip()]
    reviews_b = [r.strip() for r in req.reviews_b if r and r.strip()]
    if not reviews_a or not reviews_b:
        raise HTTPException(status_code=400, detail="两组评论都不能为空")
    try:
        analysis_a = analyze_reviews(reviews_a)
        analysis_b = analyze_reviews(reviews_b)
        result = compare_products(analysis_a, analysis_b)
        result["_product_a"] = req.product_a.strip()
        result["_product_b"] = req.product_b.strip()
    except ValueError as e:
        raise HTTPException(status_code=502, detail=str(e))
    logging.info("竞品对比: %s vs %s", req.product_a, req.product_b)
    return result


class SettingsRequest(BaseModel):
    deepseek_key: str = ""
    tavily_key: str = ""
    ebay_app_id: str = ""
    ebay_client_secret: str = ""
    aliexpress_app_key: str = ""
    aliexpress_app_secret: str = ""
    ai_provider: str = ""
    ai_model: str = ""
    ai_base_url: str = ""
    search_provider: str = ""


@app.get("/api/settings")
def get_settings():
    """返回各 Key 配置状态（不返回值，安全）"""
    return cfg.get_keys_status()


@app.post("/api/settings")
def save_settings(req: SettingsRequest):
    """保存设置：写入 config + .env，运行时立即生效"""
    if req.deepseek_key:
        cfg.set_key("DEEPSEEK_API_KEY", req.deepseek_key)
    if req.tavily_key:
        cfg.set_key("TAVILY_API_KEY", req.tavily_key)
    if req.ebay_app_id:
        cfg.set_key("EBAY_APP_ID", req.ebay_app_id)
    if req.ebay_client_secret:
        cfg.set_key("EBAY_CLIENT_SECRET", req.ebay_client_secret)
    if req.aliexpress_app_key:
        cfg.set_key("ALIEXPRESS_APP_KEY", req.aliexpress_app_key)
    if req.aliexpress_app_secret:
        cfg.set_key("ALIEXPRESS_APP_SECRET", req.aliexpress_app_secret)
    if req.ai_provider:
        cfg.set_key("AI_PROVIDER", req.ai_provider)
    if req.ai_model:
        cfg.set_key("AI_MODEL", req.ai_model)
    if req.ai_base_url:
        cfg.set_key("AI_BASE_URL", req.ai_base_url)
    if req.search_provider:
        cfg.set_key("SEARCH_PROVIDER", req.search_provider)
    logging.info("设置已保存")
    return cfg.get_keys_status()


# 挂载前端静态目录，前后端同源（必须放在所有 API 路由之后，
# 否则 "/" 挂载会拦截 /api 下的请求）
app.mount("/", StaticFiles(directory=res_path("static"), html=True), name="static")


def _safe(value, default=""):
    """LLM 可能返回 null/None，统一兜底成字符串"""
    return default if value is None else str(value)


def markdown_report(product: str, country: str, d: dict) -> str:
    """把 DeepSeek 返回的结构化 JSON 渲染成 Markdown 报告"""
    lines = [f"# {product}市场分析（{country}）", ""]

    # 核心结论速览（摘要五段式：背景→数据→发现→挑战→建议）
    es = d.get("executive_summary") or {}
    if es and (es.get("background") or es.get("data_points")):
        lines += ["> **摘要**", ""]
        if es.get("background"):
            lines += [f"> **背景**：{_safe(es['background'])}", ""]
        if es.get("data_points"):
            lines += ["> **关键数据**：", *[f"> - {_safe(item)}" for item in es["data_points"]], ""]
        if es.get("key_findings"):
            lines += ["> **核心发现**：", *[f"> - {_safe(item)}" for item in es["key_findings"]], ""]
        if es.get("challenges"):
            lines += ["> **主要挑战**：", *[f"> - {_safe(item)}" for item in es["challenges"]], ""]
        if es.get("recommendation"):
            lines += [f"> **建议**：{_safe(es['recommendation'])}", ""]
        lines.append("")

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

    # 热门品牌（含点评列，IDC 风格）
    brands = d.get("top_brands") or []
    lines += ["## 热门品牌", "| 品牌 | 所属国家 | 市场地位 | 点评 |", "| --- | --- | --- | --- |"]
    for b in brands:
        if not isinstance(b, dict):
            continue
        note = _safe(b.get("comment")) or _safe(b.get("note"))
        ship = _safe(b.get("shipment"))
        growth = _safe(b.get("growth"))
        pos = _safe(b.get("position"))
        if ship or growth:
            pos = pos + (f" · 出货{ship}" if ship else "") + (f" · 同比{growth}" if growth else "")
        lines.append(
            f"| {_safe(b.get('name'))} | {_safe(b.get('origin'))} | "
            f"{pos} | {note} |"
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

    # 风险分析（含具体法规条款）
    risks = d.get("risks") or []
    lines += ["## 风险分析", "| 风险类型 | 等级 | 说明 | 相关法规 |", "| --- | --- | --- | --- |"]
    for r in risks:
        if not isinstance(r, dict):
            continue
        lines.append(f"| {_safe(r.get('type'))} | {_safe(r.get('level'))} | {_safe(r.get('description'))} | {_safe(r.get('regulation'))} |")
    lines.append("")

    # 行动路线（分步可执行）
    ap = d.get("action_plan") or []
    if ap:
        lines += ["## 行动路线"]
        for i, step in enumerate(ap, 1):
            lines.append(f"{i}. {_safe(step)}")
        lines.append("")

    # 总结
    lines += ["## AI 总结", _safe(d.get("summary")), ""]

    # 展望（IDC 风格：给方向性预测）
    outlook = _safe(d.get("outlook"))
    if outlook and outlook != "":
        lines += ["## 市场展望", outlook, ""]

    return "\n".join(lines)
