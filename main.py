"""main.py — FastAPI 入口：路由 + 报告渲染。

第一版保持扁平结构；后续模块（外贸/评论分析/贸易数据）上线时，
拆分到 routers/ 目录，本文件退化为"组装 app + include_router"。
llm.py / prompts.py 是所有模块共用的底座。
"""
import logging
import json
import os
import secrets
import sys
import time
import io
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from urllib.parse import quote

from starlette.concurrency import run_in_threadpool

# 资源路径：打包(exe)用 _MEIPASS，开发用项目目录
BASE_DIR = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))


def res_path(name: str) -> str:
    """资源文件绝对路径（static/templates/data 下的文件）"""
    return os.path.join(BASE_DIR, name)

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from llm import analyze_market, analyze_market_comparison, analyze_trade_trend
from market_data import (get_competitive_landscape, get_market_context,
                         get_news, get_trade_background)
from business import (generate_followup_email, generate_outreach_email,
                      generate_outreach_from_idea, generate_product_intro,
                      simulate_customer)
from ecommerce import analyze_product_profile, analyze_reviews, compare_products, generate_listing
from collectors import collect_product, CollectorError
from ebay import analyze_item, get_oauth_token, parse_ebay_url, EbayTokenExpired
from aliexpress import analyze_product, parse_aliexpress_url
import config as cfg
from trade import (AREA_MAP, GROUP_MEMBERS, HS_MAP, get_competitiveness,
                   get_competitiveness_matrix, get_competitor_comparison,
                   get_destination_ranking, get_hs_candidates, get_latest_year,
                   get_top_exporters, partner_lookup, query_trade, query_trend,
                   summarize_stats, summarize_trend, _use_formal)
from hs_descriptions import get_hs_description
from financials import get_company_financials
from export import build_csv, build_market_report, build_word_report

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

@asynccontextmanager
async def lifespan(app):
    """启动初始化（阶段 4）：建表 + WAL + 清理过期缓存，避免每请求重复 DDL"""
    try:
        from database import cleanup_expired_cache, enable_wal, init_db
        init_db()
        enable_wal()
        cleanup_expired_cache()
    except Exception:
        logging.exception("启动数据库初始化失败")
    yield


app = FastAPI(title="TradePilot AI", description="面向消费电子出海的 AI 市场分析平台",
              lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    """422 校验错误可读化（回归修复：FastAPI 默认 detail 是数组，
    前端到处 textContent 显示成 "[object Object]"，用户看不懂失败原因）"""
    msgs = []
    for e in exc.errors():
        loc = ".".join(str(x) for x in e.get("loc", []) if x not in ("body", "query", "path"))
        msg = str(e.get("msg", ""))
        msgs.append(f"{loc}: {msg}" if loc else msg)
    return JSONResponse(
        status_code=422,
        content={"detail": "；".join(msgs) if msgs else "请求参数不合法"},
    )

# 安全加固（v1.0 审查第一批）：
# 1) 移除宽 CORS（allow_origins=["*"] 曾允许任意网页跨源调用本机 API 烧 token/读历史）。
#    前端由下方 app.mount("/") 同源提供，无跨源需求；LAN 演示需放行时加 ALLOWED_HOSTS 环境变量。
# 2) Host 头白名单校验：防 DNS rebinding / 恶意 Host 注入（返回 400 而非处理请求）。
_ALLOWED_HOSTS = {h.strip().lower() for h in
                  os.getenv("ALLOWED_HOSTS", "127.0.0.1,localhost").split(",") if h.strip()}

# 匿名限流（v1.0 阶段 1.2）：消耗 token 的接口按 IP 滑动窗口限频，管理员会话豁免。
# 限额宽松（默认每小时 30 次），超限提示而非拒绝业务本身。
_RATE_LIMIT = int(os.getenv("ANON_RATE_LIMIT", "30"))   # 每窗口次数
_RATE_WINDOW = 3600                                      # 1 小时
# 回归修复 G6：/api/trade/ 一前缀覆盖 query/export/pricing/options/hs-candidates；
# 补 /api/watch/（订阅刷新消耗 UN Comtrade 额度）、/api/history、/api/company/
_RATE_PREFIXES = ("/api/analyze", "/api/trade/", "/api/business/", "/api/ecommerce/",
                  "/api/ebay/", "/api/aliexpress/", "/api/leads/", "/api/agent/",
                  "/api/watch/", "/api/history", "/api/company/")
_rate_hits: dict = {}


def _rate_allowed(ip: str) -> bool:
    """滑动窗口：窗口内命中数 <= 限额则放行"""
    now = time.time()
    hits = [t for t in _rate_hits.get(ip, []) if now - t < _RATE_WINDOW]
    hits.append(now)
    _rate_hits[ip] = hits
    # 防无界增长：超过 1000 个 IP 时清空窗口内无请求的 key（回归修复）
    if len(_rate_hits) > 1000:
        for k in [k for k, v in _rate_hits.items() if not [t for t in v if now - t < _RATE_WINDOW]]:
            _rate_hits.pop(k, None)
    return len(hits) <= _RATE_LIMIT


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


def _is_admin_request(request: Request) -> bool:
    """检查请求是否携带有效管理员会话（httpOnly cookie）"""
    token = request.cookies.get("admin_session")
    if not token:
        return False
    try:
        from database import check_admin_session
        return check_admin_session(token)
    except Exception:
        return False


def _extract_host(host_header: str) -> str:
    """从 Host 头提取主机名（去端口；支持 IPv6 [::1]:8000——回归修复）"""
    h = (host_header or "").strip().lower()
    if not h:
        return ""
    if h.startswith("[") and "]" in h:  # IPv6 字面量
        return h[1:h.index("]")]
    return h.split(":")[0]


def _apply_security_headers(resp) -> None:
    """安全响应头：CSP（内联脚本兼容期先放行 unsafe-inline）/ 防嗅探 / 禁止被嵌 frame"""
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "connect-src 'self'; object-src 'none'; frame-ancestors 'none'",
    )


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    ip = _client_ip(request)
    path = request.url.path
    # Host 头校验（去端口，支持 IPv6）：缺失或不在白名单一律 400（回归修复：
    # 此前 Host 缺失放行、IPv6 [::1] 被误拒；早返回的响应也带安全头）
    host = _extract_host(request.headers.get("host"))
    if not host or host not in _ALLOWED_HOSTS:
        logging.warning("拒绝非法/缺失 Host: %r", request.headers.get("host"))
        try:
            from database import log_access
            await run_in_threadpool(log_access, ip, path, "Host校验", "blocked")
        except Exception:
            logging.exception("access_log 写入失败（Host校验）")
        resp = JSONResponse(status_code=400, content={"detail": "非法 Host"})
        _apply_security_headers(resp)
        return resp
    # 匿名限流（管理员会话豁免）
    if path.startswith(_RATE_PREFIXES) and not _is_admin_request(request):
        if not _rate_allowed(ip):
            try:
                from database import log_access
                await run_in_threadpool(log_access, ip, path, "限流", "blocked")
            except Exception:
                logging.exception("access_log 写入失败（限流）")
            resp = JSONResponse(status_code=429,
                                content={"detail": f"请求过于频繁，请稍后再试（每小时 {_RATE_LIMIT} 次，管理员不限）"})
            _apply_security_headers(resp)
            return resp
    resp = await call_next(request)
    _apply_security_headers(resp)
    return resp


class AnalyzeRequest(BaseModel):
    # 输入长度上限（回归修复）：超长字符串会进 LLM 提示词烧 token、进历史表撑库、
    # 进下载文件名；统一在模型层截断校验，返回 422 而非继续处理
    product: str = Field(max_length=100)
    country: str = Field(max_length=50)


def _collect_evidence(product: str, country: str) -> tuple:
    """聚合真实数据证据链（经济/贸易/竞争力/宏观背景/竞争格局），失败不阻断

    阶段 4 优化：互不依赖的证据采集并行（ThreadPoolExecutor），
    原本串行 3-5 次网络往返压缩为 1 轮；get_latest_year 只取一次。
    注：市场分析不拦截同国（如"空调→中国"分析中国市场机会是合法的）——
    同国时 query_trend 抛错由 _trade_part 捕获降级（贸易证据缺失，
    市场规模/竞争格局/World Bank 经济数据照常），报告仍完整生成。
    """
    from trade import get_latest_year

    def _trade_part():
        try:
            from trade import AREA_MAP, GROUP_MEMBERS, partner_lookup
            ly = get_latest_year()  # 移入 try（回归修复：脏缓存解析曾在此处漏成 500）
            trade_evidence = {}
            competitiveness = {}
            # 同国（如"空调→中国"）时跳过 query_trend（中国→中国无出口数据），
            # 但市场总进口（该国从全球进口）仍然有效——市场规模底数不能丢
            _rep_code = AREA_MAP.get("中国", "156")
            _tgt_code = partner_lookup(country)
            _same = bool(_rep_code and _tgt_code and _rep_code == _tgt_code
                         and _tgt_code not in GROUP_MEMBERS)
            if not _same:
                # 近 5 年窗口（与多国对比 _collect_country_evidence 一致，保证 CAGR 口径可比）
                hs, rows, trend = query_trend(product, country, list(range(ly - 4, ly + 1)))
                if trend:
                    # 数据精度（回归修复）：round 2 位会把 <50 万美元出口额归零
                    # （0.005 亿美元 → 0.0），下游 CAGR 计算 first>0 判失败 → 指标缺失；
                    # 保留 4 位（最小 1 万美元精度），AI 注入与报告计算都不失真
                    trade_evidence = {
                        "hs_code": hs,
                        "trend": {str(y): round(v["value"] / 1e8, 4) for y, v in trend.items()},
                        "weight_trend": {str(y): round(v.get("weight", 0) / 1e6, 2) for y, v in trend.items()},
                        "total_value": round(sum(v["value"] for v in trend.values()) / 1e8, 2),
                    }
                if len(rows):
                    competitiveness = get_competitiveness(product, country, str(ly))
            else:
                # 同国：仅取市场总进口（市场规模底数），TC/出口趋势天然缺失
                try:
                    competitiveness = get_competitiveness(product, country, str(ly))
                except Exception:
                    logging.warning("同国市场分析竞争力数据获取失败（不阻断）", exc_info=True)
            return trade_evidence, competitiveness
        except Exception:
            logging.exception("贸易证据链采集失败（不阻断）")
            return {}, {}

    def _ctx_part():
        try:
            return get_market_context(country)
        except Exception:
            logging.exception("市场环境采集失败（不阻断）")
            return None

    def _bg_part():
        try:
            return get_trade_background()
        except Exception:
            logging.exception("宏观背景采集失败（不阻断）")
            return None

    def _land_part():
        try:
            return get_competitive_landscape(product, country)
        except Exception:
            logging.exception("竞争格局采集失败（不阻断）")
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        trade_fut = pool.submit(_trade_part)
        ctx_fut = pool.submit(_ctx_part)
        bg_fut = pool.submit(_bg_part)
        land_fut = pool.submit(_land_part)
        trade_evidence, competitiveness = trade_fut.result()
        market_ctx = ctx_fut.result()
        background = bg_fut.result()
        landscape = land_fut.result()
    return market_ctx, trade_evidence, competitiveness, background, landscape


@app.post("/api/analyze")
def analyze(req: AnalyzeRequest, request: Request):
    """输入产品 + 目标国家 → 返回 Markdown 格式市场分析报告

    用同步 def（而非 async）：内部 analyze_market 是同步阻塞调用，
    FastAPI 会把同步端点放入线程池执行，不阻塞事件循环。
    ?refresh=1 强制重新分析（跳过 7 天历史缓存，阶段 4）。
    """
    product = req.product.strip()
    country = req.country.strip()
    if not product or not country:
        raise HTTPException(status_code=400, detail="product 和 country 不能为空")

    # 历史命中：同产品同市场直接返回（不重复搜索/调 AI，省 token）
    from database import get_report_history, save_report_history
    refresh = request.query_params.get("refresh") == "1"
    if not refresh:
        cached = get_report_history("market", product, country)
        if cached:
            logging.info("历史命中: %s / %s", product, country)
            return cached

    try:
        # 聚合真实数据证据链（经济 + 贸易 + 竞争力 + 宏观背景）
        market_ctx, trade_evidence, competitiveness, background, landscape = _collect_evidence(product, country)
        # refresh=1 时跳过 LLM 内存缓存（report_history 已在上面跳过）
        data = analyze_market(product, country, market_ctx, trade_evidence,
                              competitiveness, background, landscape, refresh=refresh)
    except ValueError as e:
        raise HTTPException(status_code=502, detail=str(e))

    # 行业动态（Tavily 搜索，失败不阻断）
    news = get_news(product, country)
    data["_news"] = news if news.get("available") else {}
    data["_trade"] = trade_evidence if trade_evidence else {}
    data["_competitiveness"] = competitiveness if competitiveness.get("available") else {}

    logging.info("分析完成: %s / %s", product, country)
    result = {
        "report": markdown_report(product, country, data),
        "news": data.get("_news"),
        "trade": data.get("_trade"),
        "competitiveness": data.get("_competitiveness"),
        "market_context": market_ctx if market_ctx and market_ctx.get("available") else {},
        "background": background or {},
    }
    # 保存历史（同参数覆盖，供 UI 回看 + 后续缓存命中）
    # 回归修复 R3：历史结果带生成时间戳（前端可显示"数据截至"，7 天历史
    # 命中时用户能判断结果的新旧）
    result["_generated_at"] = datetime.now().isoformat(timespec="seconds")
    try:
        save_report_history("market", product, country, result)
    except Exception:
        logging.warning("保存市场历史失败: %s / %s", product, country)
    return result


@app.get("/api/history")
def history_list(report_type: str = ""):
    """最近 10 条查询历史（可选按类型过滤 market/trade）"""
    from database import list_report_history
    return list_report_history(report_type, limit=10)


class AnalyzeCompareRequest(BaseModel):
    product: str = Field(max_length=100)
    countries: list[str] = Field(max_length=20)  # 粗上限；业务上限 5 国在端点内校验


def _collect_country_evidence(product: str, country: str) -> dict:
    """聚合单国证据链（多国对比用）：市场环境/贸易趋势/竞争力，失败不阻断

    趋势窗口与单国分析一致（默认近五年），保证 CAGR 口径可比。
    """
    ev = {"country": country, "market_context": {}, "trade_evidence": {}, "competitiveness": {}}
    ev["market_context"] = get_market_context(country)
    try:
        latest = get_latest_year()
        hs, rows, trend = query_trend(product, country, list(range(latest - 4, latest + 1)))
        if trend:
            ev["trade_evidence"] = {
                "hs_code": hs,
                "trend": {str(y): round(v["value"] / 1e8, 4) for y, v in trend.items()},  # 4 位精度（与单国口径一致）
                "total_value": round(sum(v["value"] for v in trend.values()) / 1e8, 2),
            }
        if len(rows):
            ev["competitiveness"] = get_competitiveness(product, country, str(latest))
    except Exception:
        logging.warning("单国证据链采集失败（不阻断）: %s / %s", product, country)
    return ev


@app.post("/api/analyze/compare")
def analyze_compare(req: AnalyzeCompareRequest):
    """多国家横向对比：产品 + 2-3 国 → 各国真实证据链 + AI 对比解读"""
    product = req.product.strip()
    countries = [c.strip() for c in req.countries if c and c.strip()]
    # 去重（保持顺序）：重复国家会覆盖 per_country dict，AI 收到两行相同数据
    seen, dedup = set(), []
    for c in countries:
        if c not in seen:
            seen.add(c)
            dedup.append(c)
    countries = dedup
    if not product or not countries:
        raise HTTPException(status_code=400, detail="product 和 countries 不能为空")
    if len(countries) < 2:
        raise HTTPException(status_code=400, detail="请选择至少 2 个国家进行对比")
    if len(countries) > 5:
        raise HTTPException(status_code=400, detail="对比国家最多 5 个")

    # 并行聚合各国证据链（每国独立查贸易/经济/竞争力，网络等待不阻塞）
    from concurrent.futures import ThreadPoolExecutor
    try:
        with ThreadPoolExecutor(max_workers=len(countries)) as pool:
            per_country = dict(zip(
                countries,
                pool.map(lambda c: _collect_country_evidence(product, c), countries),
            ))
    except Exception:
        # 并行聚合异常兜底：退回串行（单国失败已被内部捕获，此路径极罕见）
        per_country = {c: _collect_country_evidence(product, c) for c in countries}

    # AI 对比解读（基于程序计算的各国指标，数据不足时返回降级结果）
    try:
        comparison = analyze_market_comparison(product, countries, per_country)
    except ValueError as e:
        raise HTTPException(status_code=502, detail=str(e))

    logging.info("多国对比: %s / %s", product, "、".join(countries))
    return {
        "product": product,
        "countries": countries,
        "per_country": per_country,  # 各国程序计算的证据链（前端渲染对比表）
        "comparison": comparison,    # AI 对比解读（overview/market_table/recommendations）
    }


class AnalyzeExportRequest(AnalyzeRequest):
    fmt: str = Field(default="docx", max_length=10)  # docx / pdf


@app.post("/api/analyze/export")
def export_market_report(req: AnalyzeExportRequest):
    """下载市场分析报告（Word 或 PDF）"""
    product = req.product.strip()
    country = req.country.strip()
    fmt = (req.fmt or "docx").lower()
    if not product or not country:
        raise HTTPException(status_code=400, detail="product 和 country 不能为空")
    if fmt not in ("docx", "pdf"):
        raise HTTPException(status_code=400, detail="fmt 仅支持 docx 或 pdf")

    try:
        # 完整证据链（与页面分析一致）
        market_ctx, trade_evidence, competitiveness, background, landscape = _collect_evidence(product, country)
        data = analyze_market(product, country, market_ctx, trade_evidence,
                              competitiveness, background, landscape)
    except ValueError as e:
        raise HTTPException(status_code=502, detail=str(e))

    buf = build_market_report(product, country, data, trade_evidence,
                              competitiveness, background, landscape, market_ctx)
    # 收尾：COM 更新域/修表格跨页/拼写检查；pdf 时转 PDF（失败降级 docx）
    from export import finalize_docx
    buf, actual_fmt = finalize_docx(buf, as_pdf=(fmt == "pdf"))
    filename = f"TradePilot-{product}-{country}-市场分析报告.{actual_fmt}"
    media_type = "application/pdf" if actual_fmt == "pdf" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    headers = _download_headers(filename)
    # PDF 降级为 docx 时显式提示（本机无 Word/LibreOffice 时发生），前端可据此提示用户
    if fmt == "pdf" and actual_fmt == "docx":
        headers["X-Export-Fallback"] = "docx"
    return StreamingResponse(buf, media_type=media_type, headers=headers)


class TradeExportRequest(BaseModel):
    product: str = Field(max_length=100)
    target: str = Field(max_length=50)
    start_year: int = Field(ge=1990, le=2100)  # 边界校验：防畸形年份生成巨量年份序列
    end_year: int | None = Field(default=None, ge=1990, le=2100)
    reporter: str = Field(default="中国", max_length=50)  # 出口国（报告国），默认中国
    fmt: str = Field(default="docx", max_length=10)  # docx / pdf


def _fetch_trade_data(req: TradeExportRequest) -> tuple[str, list]:
    """复用查询逻辑：产品 + 国家/组织 + 起止年 → (hs_code, rows)"""
    product = req.product.strip()
    target = req.target.strip()
    reporter = _validate_reporter(req.reporter)  # 回归修复：未知出口国不再静默回退中国
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
    """下载贸易数据报告（Word 或 PDF：封面/目录/趋势图/数据表/AI 分析）"""
    fmt = (req.fmt or "docx").lower()
    if fmt not in ("docx", "pdf"):
        raise HTTPException(status_code=400, detail="fmt 仅支持 docx 或 pdf")
    hs, rows = _fetch_trade_data(req)
    # 复用市场分析的证据链采集，让 AI 分析基于真实数据
    # （B7 修复：与 /api/analyze 一致，不再纯 LLM 估算；查询有缓存，重复调用代价小）
    market_ctx, trade_evidence, competitiveness, background, landscape = _collect_evidence(
        req.product.strip(), req.target.strip())
    try:
        ai = analyze_market(req.product.strip(), req.target.strip(),
                            market_ctx, trade_evidence, competitiveness, background, landscape)
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
    # 出口大国对比矩阵（程序计算，供报告第四章）
    matrix = []
    try:
        from trade import get_competitiveness_matrix
        matrix = get_competitiveness_matrix(req.product.strip(), req.target.strip(), years_actual, req.reporter)
    except Exception:
        logging.warning("导出增强数据采集失败（不阻断）: %s / %s", req.product, req.target)
    # build_word_report 已不再需要 top_exporters 参数（矩阵内部已算，去掉冗余的 16 国轮询）
    buf = build_word_report(req.product.strip(), req.target.strip(), year_label,
                            hs, rows, ai, get_hs_description(hs), stats, analysis,
                            landscape, market_ctx, matrix, background, competitiveness,
                            reporter=req.reporter)
    # 收尾：COM 更新域/修表格跨页/拼写检查；pdf 时转 PDF（失败降级 docx）
    from export import finalize_docx
    buf, actual_fmt = finalize_docx(buf, as_pdf=(fmt == "pdf"))
    filename = f"TradePilot-{req.product.strip()}-{req.target.strip()}-{year_label}-报告.{actual_fmt}"
    media_type = "application/pdf" if actual_fmt == "pdf" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    headers = _download_headers(filename)
    # PDF 降级为 docx 时显式提示
    if fmt == "pdf" and actual_fmt == "docx":
        headers["X-Export-Fallback"] = "docx"
    return StreamingResponse(buf, media_type=media_type, headers=headers)


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
    product: str = Field(max_length=100)
    target: str = Field(max_length=50)
    start_year: int = Field(ge=1990, le=2100)  # 边界校验：防畸形年份生成巨量年份序列
    end_year: int | None = Field(default=None, ge=1990, le=2100)
    reporter: str = Field(default="中国", max_length=50)  # 出口国（报告国），默认中国


class PricingRequest(BaseModel):
    product: str = Field(max_length=100)
    market: str = Field(max_length=50)
    year: str = Field(default="", max_length=10)
    reporter: str = Field(default="中国", max_length=50)


@app.post("/api/trade/pricing")
def trade_pricing(req: PricingRequest):
    """定价建议：产品 + 目标市场 → 数据驱动的价格区间（程序计算，可追溯）"""
    from pricing import suggest_pricing
    product = req.product.strip()
    market = req.market.strip()
    reporter = _validate_reporter(req.reporter)  # 回归修复：未知出口国不再静默回退中国
    if not product or not market:
        raise HTTPException(status_code=400, detail="product 和 market 不能为空")
    try:
        return suggest_pricing(product, market, req.year, reporter)
    except Exception as e:
        logging.exception("定价建议失败: %s / %s", product, market)
        raise HTTPException(status_code=502, detail=f"定价建议失败：{e}")


def _years_from_range(start_year: int, end_year: int | None) -> list:
    """起止年 → 年份列表；end_year 为空默认到最新可用年份"""
    latest = get_latest_year()  # 动态探测最新可用年份
    if end_year is None or end_year > latest:
        end_year = latest
    if start_year > end_year:
        return []
    return list(range(start_year, end_year + 1))


def _validate_reporter(reporter: str) -> str:
    """出口国校验（回归修复）：未知国家曾静默回退成中国（156）并标注用户输入名"""
    r = (reporter or "中国").strip()
    if r not in AREA_MAP:
        raise HTTPException(status_code=400, detail=f"未知出口国/地区：{r}")
    return r


class HsCandidatesRequest(BaseModel):
    product: str = Field(max_length=100)


class CompanyFinancialsRequest(BaseModel):
    company: str = Field(max_length=100)


@app.post("/api/company/financials")
def company_financials(req: CompanyFinancialsRequest):
    """公司财报画像：营收/净利/毛利率/研发（SEC 官方或公开报道）"""
    company = req.company.strip()
    if not company:
        raise HTTPException(status_code=400, detail="公司名不能为空")
    result = get_company_financials(company)
    return result


@app.post("/api/trade/hs-candidates")
def hs_candidates(req: HsCandidatesRequest):
    """产品名 → 3 个候选 HS 编码（编码 + 描述），供用户点选确认"""
    product = req.product.strip()
    if not product:
        raise HTTPException(status_code=400, detail="产品不能为空")
    candidates = get_hs_candidates(product)
    if not candidates:
        raise HTTPException(status_code=502, detail="HS 编码解析失败，请手输 4-6 位数字编码")
    return {"candidates": candidates}


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
    reporter = _validate_reporter(req.reporter)  # 回归修复：未知出口国不再静默回退中国
    if not product or not target or not req.start_year:
        raise HTTPException(status_code=400, detail="product、target、start_year 不能为空")

    years = _years_from_range(req.start_year, req.end_year)
    if not years:
        raise HTTPException(status_code=400, detail="年份范围无效（截至年不能早于起始年）")

    # 历史命中：同参数直接返回（不重复查询 UN Comtrade，省时间/限流额度）
    from database import get_report_history
    hist_params = json.dumps({"start_year": req.start_year, "end_year": req.end_year,
                              "reporter": reporter}, ensure_ascii=False)
    cached = get_report_history("trade", product, target, hist_params)
    if cached:
        logging.info("贸易历史命中: %s → %s", product, target)
        return cached

    try:
        if len(years) == 1:
            hs, rows = query_trade(product, target, str(years[0]), reporter=reporter)
            trend = summarize_trend(rows)
        else:
            hs, rows, trend = query_trend(product, target, years, reporter=reporter)
    except ValueError as e:
        raise HTTPException(status_code=502, detail=str(e))

    logging.info("贸易查询: %s→%s / %s / %d-%s", reporter, target, product, req.start_year, req.end_year or "最新")

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
    matrix = []
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
        # 竞争力矩阵：出口大国 × {出口额/份额/CAGR/单价/判断}（程序计算）
        matrix = get_competitiveness_matrix(product, target, years, req.reporter)
    except Exception:
        logging.warning("贸易增强数据采集失败（不阻断）: %s / %s", product, target)

    result = {
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
        "matrix": matrix,  # 竞争力矩阵（出口大国 × 出口额/份额/CAGR/单价/判断）
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
    # 数据新鲜度 + 血缘（v1.0.2）：每年度数据"有多新、质量如何"，前端可显示 🟢🟡🔴
    try:
        from database import get_cache_meta
        _mode_key = "formal" if _use_formal() else "preview"
        _rep_code = AREA_MAP.get(reporter, "156")
        _target_code = partner_lookup(target)
        freshness = []
        for y in years:
            meta = get_cache_meta(hs, _target_code, str(y), "X", _rep_code, cache_key=_mode_key)
            if meta:
                freshness.append({"year": y, "fetched_at": meta["fetched_at"],
                                  "quality": meta["quality"],
                                  "reason": meta["validation_reason"]})
        if freshness:
            result["_freshness"] = freshness
    except Exception:
        logging.warning("新鲜度元数据读取失败（不阻断）: %s / %s", product, target)
    # 保存历史（同参数覆盖，供 UI 回看 + 后续缓存命中）
    try:
        from database import save_report_history
        params = json.dumps({"start_year": req.start_year, "end_year": req.end_year,
                             "reporter": req.reporter}, ensure_ascii=False)
        # 回归修复 R3：历史结果带生成时间戳
        result["_generated_at"] = datetime.now().isoformat(timespec="seconds")
        save_report_history("trade", product, target, result, params)
    except Exception:
        logging.warning("保存贸易历史失败: %s / %s", product, target)
    return result


class BusinessEmailRequest(BaseModel):
    product: str = Field(max_length=100)
    market: str = Field(max_length=50)
    customer_type: str = Field(default="经销商", max_length=50)
    company: str = Field(default="", max_length=200)
    contact: str = Field(default="", max_length=100)
    email: str = Field(default="", max_length=200)
    selling_points: str = Field(default="", max_length=2000)
    customer_company: str = Field(default="", max_length=200)
    customer_contact: str = Field(default="", max_length=100)
    customer_title: str = Field(default="", max_length=100)
    hook: str = Field(default="免费样品", max_length=200)
    credentials: str = Field(default="", max_length=2000)


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
    product: str = Field(max_length=100)
    market: str = Field(max_length=50)
    customer_type: str = Field(default="经销商", max_length=50)
    original_subject: str = Field(default="", max_length=500)
    company: str = Field(default="", max_length=200)
    contact: str = Field(default="", max_length=100)
    email: str = Field(default="", max_length=200)
    customer_company: str = Field(default="", max_length=200)
    customer_contact: str = Field(default="", max_length=100)
    customer_title: str = Field(default="", max_length=100)


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
    idea: str = Field(max_length=2000)
    company: str = Field(default="", max_length=200)
    contact: str = Field(default="", max_length=100)
    email: str = Field(default="", max_length=200)
    customer_company: str = Field(default="", max_length=200)
    customer_contact: str = Field(default="", max_length=100)
    customer_title: str = Field(default="", max_length=100)


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
    product: str = Field(max_length=100)
    market: str = Field(max_length=50)
    customer_type: str = Field(default="经销商", max_length=50)
    user_message: str = Field(max_length=5000)
    history: list = Field(default_factory=list, max_length=100)  # 对话历史条数上限


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


class ProductCollectRequest(BaseModel):
    url: str = Field(default="", max_length=2000)
    pasted_text: str = Field(default="", max_length=50000)


@app.post("/api/ecommerce/collect")
def ecommerce_collect(req: ProductCollectRequest):
    """商品 URL/粘贴文本 → 采集画像 + AI 选品分析

    无 AI Key 时降级：URL 采集成功 → 返回 item + 空 analysis（前端可显示画像）；
    粘贴路径（必须 AI 提取）→ 502 提示配置 Key。
    """
    item = None
    try:
        item = collect_product(req.url, req.pasted_text)
        analysis = analyze_product_profile(item)
    except CollectorError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except ValueError as e:
        # AI 不可用（无 Key/额度）：URL 采集成功则降级返回画像，否则明确报错
        if item:
            logging.warning("商品画像分析失败（降级返回画像）: %s", e)
            return {"item": item, "analysis": {}}
        raise HTTPException(status_code=502, detail=f"AI 分析不可用：{e}")
    logging.info("商品采集: %s", item.get("title", "")[:40])
    return {"item": item, "analysis": analysis}


class EcommerceAnalyzeRequest(BaseModel):
    reviews: list = Field(default_factory=list, max_length=500)  # 评论条数上限
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
    product: str = Field(max_length=100)
    platform: str = Field(default="亚马逊", max_length=50)
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


@app.get("/api/ecommerce/sample-products")
def ecommerce_sample_products():
    """返回商品采集演示画像（供前端演示按钮）"""
    try:
        with open(res_path("data/sample_products.json"), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        raise HTTPException(status_code=500, detail="演示画像加载失败")


@app.get("/api/ecommerce/samples")
def ecommerce_samples():
    """返回评论样本品类列表（data/samples/index.json，18 个 HS 品类真实评论库）"""
    try:
        with open(res_path("data/samples/index.json"), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []  # 样本库未生成时返回空列表，前端降级为旧演示数据


@app.get("/api/ecommerce/sample")
def ecommerce_sample(slug: str = ""):
    """返回演示数据：指定 slug 读对应品类样本，否则读旧 sample_reviews.json"""
    if slug:
        # 防路径穿越：slug 仅允许字母数字下划线连字符
        if not slug.replace("-", "").replace("_", "").isalnum():
            raise HTTPException(status_code=400, detail="非法 slug")
        try:
            with open(res_path(f"data/samples/{slug}.json"), encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            raise HTTPException(status_code=404, detail=f"未找到品类样本: {slug}")
    try:
        with open(res_path("data/sample_reviews.json"), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        raise HTTPException(status_code=500, detail="演示数据加载失败")


class EbayAnalyzeRequest(BaseModel):
    url: str = Field(max_length=2000)


@app.post("/api/ebay/analyze")
def ebay_analyze(req: EbayAnalyzeRequest):
    """eBay 商品链接 → 商品信息 + AI 分析（回归修复：401 token 过期自动刷新重试一次）"""
    url = req.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="请提供 eBay 商品链接")
    item_id = parse_ebay_url(url)
    if not item_id:
        raise HTTPException(status_code=400, detail="无法从链接提取 eBay 商品 ID")
    if not cfg.RUNTIME_KEYS.get("EBAY_APP_ID") or not cfg.RUNTIME_KEYS.get("EBAY_CLIENT_SECRET"):
        raise HTTPException(status_code=503, detail="eBay 密钥未配置（需 App ID + Client Secret，见设置面板）")
    try:
        app_id = cfg.RUNTIME_KEYS.get("EBAY_APP_ID", "")
        secret = cfg.RUNTIME_KEYS.get("EBAY_CLIENT_SECRET", "")
        token = get_oauth_token(app_id, secret)
        try:
            data = analyze_item(item_id, token)
        except EbayTokenExpired:
            # token 失效（缓存边界/被吊销）：强制刷新后重试一次
            token = get_oauth_token(app_id, secret, force=True)
            data = analyze_item(item_id, token)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"eBay 查询失败: {e}")
    logging.info("eBay 分析: %s", item_id)
    return data


class AliexpressAnalyzeRequest(BaseModel):
    url: str = Field(max_length=2000)


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
    product_a: str = Field(max_length=100)
    reviews_a: list = Field(default_factory=list, max_length=500)
    product_b: str = Field(max_length=100)
    reviews_b: list = Field(default_factory=list, max_length=500)


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
    deepseek_key: str = Field(default="", max_length=300)   # 兼容旧面板：provider=deepseek 时等价于 AI Key
    ai_api_key: str = Field(default="", max_length=300)     # 新面板：AI Key（当前 provider 的 key，统一入口）
    tavily_key: str = Field(default="", max_length=300)
    ebay_app_id: str = Field(default="", max_length=300)
    ebay_client_secret: str = Field(default="", max_length=300)
    aliexpress_app_key: str = Field(default="", max_length=300)
    aliexpress_app_secret: str = Field(default="", max_length=300)
    ai_provider: str = Field(default="", max_length=50)
    ai_model: str = Field(default="", max_length=100)
    ai_base_url: str = Field(default="", max_length=500)
    search_provider: str = Field(default="", max_length=50)
    un_comtrade_mode: str = Field(default="", max_length=20)   # preview（免费）/ formal（正式接口，需 key）


# 登录失败限速（防暴力破解）：同 IP 5 次失败/10 分钟（回归修复）
_LOGIN_FAIL: dict = {}
_LOGIN_FAIL_WINDOW = 600
_LOGIN_FAIL_MAX = 5


def _login_failed_too_many(ip: str) -> bool:
    now = time.time()
    fails = [t for t in _LOGIN_FAIL.get(ip, []) if now - t < _LOGIN_FAIL_WINDOW]
    _LOGIN_FAIL[ip] = fails
    return len(fails) >= _LOGIN_FAIL_MAX


def _record_login_fail(ip: str):
    _LOGIN_FAIL.setdefault(ip, []).append(time.time())
    # 防无界增长：超过 500 个 IP 时清理过期项
    if len(_LOGIN_FAIL) > 500:
        now = time.time()
        for k in [k for k, v in _LOGIN_FAIL.items() if not [t for t in v if now - t < _LOGIN_FAIL_WINDOW]]:
            _LOGIN_FAIL.pop(k, None)


class LoginRequest(BaseModel):
    password: str = Field(max_length=200)


class LeadsRequest(BaseModel):
    product: str = Field(max_length=100)
    country: str = Field(max_length=50)


class LeadOutreachRequest(BaseModel):
    product: str = Field(max_length=100)
    country: str = Field(max_length=50)
    lead: dict = {}          # 线索画像（来自 /api/leads/search 结果）
    company: str = Field(default="", max_length=200)
    contact: str = Field(default="", max_length=100)
    email: str = Field(default="", max_length=200)
    hook: str = Field(default="免费样品", max_length=200)


class AgentRequest(BaseModel):
    input: str = Field(max_length=500)


@app.post("/api/agent/run")
async def agent_run(req: AgentRequest):
    """AI Agent：一句话 → 全流程（SSE 流式进度 + 最终结果）

    同步流水线跑在线程池（asyncio.to_thread），进度经 asyncio.Queue
    + call_soon_threadsafe 桥接推送（修订 #2 方案），不阻塞事件循环。
    回归修复：客户端断开（CancelledError）时置停止标记让流水线提前退出，
    不再继续烧 token；空闲超 15s 发 SSE 心跳注释（防中间代理断流）。
    """
    import asyncio
    import threading
    from agent import run_agent_pipeline
    text = (req.input or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="请输入任务，如「蓝牙耳机去德国卖」")

    async def gen():
        aq = asyncio.Queue()
        loop = asyncio.get_running_loop()
        stop_event = threading.Event()

        def worker():
            try:
                for ev in run_agent_pipeline(text, stop_event=stop_event):
                    if stop_event.is_set():
                        break  # 客户端已断开：提前终止，不继续跑后续步骤
                    loop.call_soon_threadsafe(aq.put_nowait, ev)
            except Exception as e:  # 流水线级异常：作为错误事件推送，不让 SSE 挂死
                logging.exception("Agent 流水线异常")
                loop.call_soon_threadsafe(aq.put_nowait, {"type": "error", "detail": str(e)})
            finally:
                loop.call_soon_threadsafe(aq.put_nowait, None)  # 结束信号

        loop.run_in_executor(None, worker)
        while True:
            try:
                ev = await asyncio.wait_for(aq.get(), timeout=15.0)
            except asyncio.TimeoutError:
                yield ": ping\n\n"  # SSE 心跳（注释行），防长退避期断流
                continue
            except asyncio.CancelledError:
                stop_event.set()  # 客户端断开：通知 worker 停止
                raise
            if ev is None:
                break
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class AgentExportRequest(BaseModel):
    report: str = Field(max_length=300000)  # Agent 报告 markdown（长报告可到几百 KB）
    product: str = Field(max_length=100)
    country: str = Field(max_length=50)
    fmt: str = Field(default="docx", max_length=10)


@app.post("/api/agent/export")
def export_agent_report(req: AgentExportRequest):
    """AI Agent 的 markdown 报告 → 学术式 Word/PDF 下载

    Agent 全流程在前端跑完后，把 markdown 报告发回这里排版导出
    （复用 export.py 的字体/封面/收尾体系，与正式报告同风格）。
    """
    from export import build_agent_report, finalize_docx
    report = (req.report or "").strip()
    product = (req.product or "").strip()
    country = (req.country or "").strip()
    fmt = (req.fmt or "docx").lower()
    if not report or not product:
        raise HTTPException(status_code=400, detail="报告内容和产品名不能为空")
    if fmt not in ("docx", "pdf"):
        raise HTTPException(status_code=400, detail="fmt 仅支持 docx 或 pdf")

    buf = build_agent_report(report, product, country)
    buf, actual_fmt = finalize_docx(buf, as_pdf=(fmt == "pdf"))
    filename = f"TradePilot-Agent-{product}-{country}-市场分析报告.{actual_fmt}"
    media_type = "application/pdf" if actual_fmt == "pdf" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    headers = _download_headers(filename)
    if fmt == "pdf" and actual_fmt == "docx":
        headers["X-Export-Fallback"] = "docx"
    return StreamingResponse(buf, media_type=media_type, headers=headers)


@app.post("/api/leads/search")
def search_leads(req: LeadsRequest):
    """客户线索检索：产品 + 目标市场 → 线索列表（Tavily 检索 + LLM 画像 + 防幻觉硬约束）"""
    from leads import find_leads
    try:
        return find_leads(req.product, req.country)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # 回归修复：LLM/网络异常不再冒泡成裸 500
        logging.exception("线索检索异常: %s / %s", req.product, req.country)
        raise HTTPException(status_code=502, detail=f"线索检索失败：{e}")


@app.post("/api/leads/outreach")
def lead_outreach(req: LeadOutreachRequest):
    """闭环：线索画像 → 针对该公司的开发信"""
    from leads import build_lead_outreach
    try:
        return build_lead_outreach(req.lead, req.product, req.country,
                                   req.company, req.contact, req.email, req.hook)
    except ValueError as e:
        raise HTTPException(status_code=502, detail=str(e))


# ── 线索销售漏斗（v1.0.2 业务收口）──
class LeadStatusRequest(BaseModel):
    status: str = ""
    product: str = ""
    country: str = ""


class LeadUpdateRequest(BaseModel):
    lead_id: int
    status: str = ""
    note: str = ""


class LeadDeleteRequest(BaseModel):
    lead_id: int


@app.post("/api/leads/funnel/list")
def funnel_list(req: LeadStatusRequest):
    """漏斗线索列表（可按状态/产品/市场筛选）"""
    from database import list_funnel_leads
    return {"leads": list_funnel_leads(req.status, req.product, req.country)}


@app.get("/api/leads/funnel/stats")
def funnel_stats():
    """漏斗统计：各状态数量（销售管道看板）"""
    from database import funnel_stats
    return funnel_stats()


@app.post("/api/leads/funnel/update")
def funnel_update(req: LeadUpdateRequest):
    """状态流转：new → sent → replied → quoted → won/lost（校验合法转移）"""
    from database import update_lead_status
    try:
        ok = update_lead_status(req.lead_id, req.status, req.note)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not ok:
        raise HTTPException(status_code=404, detail="线索不存在")
    return {"ok": True}


@app.post("/api/leads/funnel/delete")
def funnel_delete(req: LeadDeleteRequest):
    """删除一条线索"""
    from database import delete_lead
    if not delete_lead(req.lead_id):
        raise HTTPException(status_code=404, detail="线索不存在")
    return {"ok": True}


# ── 我的市场订阅（v1.0.2 业务收口）──
class WatchRequest(BaseModel):
    product: str = Field(max_length=100)
    market: str = Field(max_length=50)
    reporter: str = Field(default="中国", max_length=50)


class WatchDeleteRequest(BaseModel):
    watch_id: int


class WatchRefreshRequest(BaseModel):
    watch_id: int


@app.get("/api/watch/list")
def watch_list():
    """订阅列表（含上次快照与变化）"""
    from database import list_market_watch
    return {"watches": list_market_watch()}


@app.post("/api/watch/add")
def watch_add(req: WatchRequest):
    """添加关注 (产品, 市场)"""
    from database import add_market_watch
    product = req.product.strip()
    market = req.market.strip()
    reporter = _validate_reporter(req.reporter)  # 回归修复：未知出口国不再静默回退中国
    if not product or not market:
        raise HTTPException(status_code=400, detail="product 和 market 不能为空")
    # 目标市场也校验（避免无效市场进库，刷新时 502 才能发现）
    from trade import partner_lookup
    if not partner_lookup(market):
        raise HTTPException(status_code=400, detail=f"未知国家/地区：{market}")
    ok = add_market_watch(product, market, reporter)
    return {"ok": True, "added": ok}


@app.post("/api/watch/remove")
def watch_remove(req: WatchDeleteRequest):
    """删除订阅"""
    from database import remove_market_watch
    if not remove_market_watch(req.watch_id):
        raise HTTPException(status_code=404, detail="订阅不存在")
    return {"ok": True}


@app.post("/api/watch/refresh")
def watch_refresh(req: WatchRefreshRequest):
    """刷新一条订阅：拉最新出口额 → 与上次快照对比变化"""
    from database import list_market_watch, update_watch_snapshot
    from trade import get_latest_year, partner_lookup, query_trade

    watches = {w["id"]: w for w in list_market_watch()}
    w = watches.get(req.watch_id)
    if not w:
        raise HTTPException(status_code=404, detail="订阅不存在")
    value = year = None  # 回归修复 G8：try 外预置，防异常面加宽后 UnboundLocalError
    try:
        year = str(get_latest_year())
        hs, rows = query_trade(w["product"], w["market"], year, reporter=w["reporter"])
        value = sum(r.get("primaryValue") or 0 for r in rows)
        update_watch_snapshot(req.watch_id, value, year)
    except ValueError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception:
        # 回归修复 G8：DB/其他异常不再裸 500
        logging.exception("订阅刷新异常: id=%s", req.watch_id)
        raise HTTPException(status_code=502, detail="订阅刷新失败，请稍后重试")
    if value is None or year is None:
        raise HTTPException(status_code=502, detail="订阅刷新失败：无数据返回")

    prev = w.get("last_value")
    prev_year = w.get("last_year")
    change_pct = None
    if prev is not None and prev > 0 and value > 0:
        change_pct = round((value - prev) / prev * 100, 1)
    return {
        "ok": True,
        "product": w["product"],
        "market": w["market"],
        "value": value,
        "year": year,
        "prev_value": prev,
        "prev_year": prev_year,
        "change_pct": change_pct,
    }


@app.get("/api/admin/access-log")
def admin_access_log(request: Request):
    """管理面板：安全拦截记录（仅管理员；未登录 401）"""
    if not _is_admin_request(request):
        from database import log_access
        log_access(_client_ip(request), "/api/admin/access-log", "查看拦截记录", "blocked")
        raise HTTPException(status_code=401, detail="仅管理员可查看拦截记录")
    from database import count_access, list_access
    return {
        "blocked_count": count_access("blocked"),
        "ok_count": count_access("ok"),
        "recent": list_access(limit=50),
    }


@app.post("/api/admin/login")
def admin_login(req: LoginRequest, response: Response, request: Request):
    """管理员登录：校验 ADMIN_PASSWORD，成功发 httpOnly session cookie

    密码比对用 secrets.compare_digest（防时序攻击）；登录成败都记 access_log；
    失败限速：同 IP 5 次/10 分钟 → 429（回归修复：防暴力破解）。
    """
    from database import create_admin_session, log_access
    ip = _client_ip(request)
    if _login_failed_too_many(ip):
        log_access(ip, "/api/admin/login", "管理员登录", "blocked")
        raise HTTPException(status_code=429, detail="登录尝试过于频繁，请 10 分钟后再试")
    ok = secrets.compare_digest((req.password or "").encode("utf-8"),
                                cfg.ADMIN_PASSWORD.encode("utf-8"))
    log_access(ip, "/api/admin/login", "管理员登录", "ok" if ok else "blocked")
    if not ok:
        _record_login_fail(ip)
        raise HTTPException(status_code=401, detail="密码错误")
    token = secrets.token_urlsafe(32)
    expires = datetime.now() + timedelta(days=cfg.ADMIN_SESSION_TTL_DAYS)
    create_admin_session(token, expires.isoformat())
    response.set_cookie(
        "admin_session", token,
        httponly=True, samesite="strict", path="/",
        max_age=cfg.ADMIN_SESSION_TTL_DAYS * 86400,
    )
    return {"ok": True}


@app.post("/api/admin/logout")
def admin_logout(request: Request, response: Response):
    """管理员登出：删除会话 + 清 cookie"""
    from database import delete_admin_session
    token = request.cookies.get("admin_session")
    if token:
        delete_admin_session(token)
    response.delete_cookie("admin_session", path="/")
    return {"ok": True}


@app.get("/api/settings")
def get_settings(request: Request):
    """返回各 Key 配置状态（不返回值，安全）+ 管理端状态（前端据此解锁设置面板）"""
    st = cfg.get_keys_status()
    st["admin_required"] = True
    st["is_admin"] = _is_admin_request(request)
    return st


@app.post("/api/settings")
def save_settings(req: SettingsRequest, request: Request):
    """保存设置：写入 config + .env，运行时立即生效

    仅管理员可调用（httpOnly 会话 cookie 校验）；未登录返回 401 并记 access_log。
    AI Key 统一入口：ai_api_key 直接写 AI_API_KEY（provider 无关），
    兼容旧 deepseek_key 字段（仅 provider=deepseek 时联动）。
    非法值（换行/# 注入、危险 AI_BASE_URL）返回 400 并说明原因。
    """
    if not _is_admin_request(request):
        from database import log_access
        log_access(_client_ip(request), "/api/settings", "保存设置", "blocked")
        raise HTTPException(status_code=401, detail="仅管理员可修改设置，请先登录")
    try:
        if req.ai_api_key:
            cfg.set_key("AI_API_KEY", req.ai_api_key)
        elif req.deepseek_key:
            # 旧字段：provider=deepseek 时等价 AI Key（config 联动），其他 provider 时
            # 视为兜底存 DeepSeek key（AI_API_KEY 优先于回退链，不覆盖已配置的）
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
        if req.un_comtrade_mode:
            # 白名单校验（防乱值写入 .env）；formal 模式需已配置 key 才允许切换
            if req.un_comtrade_mode not in ("preview", "formal"):
                raise HTTPException(status_code=400, detail="UN Comtrade 模式只支持 preview / formal")
            if req.un_comtrade_mode == "formal" and not cfg.UN_COMTRADE_KEY:
                raise HTTPException(status_code=400, detail="切换到正式接口需要先在 .env 配置 UN_COMTRADE_KEY")
            cfg.set_key("UN_COMTRADE_MODE", req.un_comtrade_mode)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    logging.info("设置已保存")
    return cfg.get_keys_status()


# 挂载前端静态目录，前后端同源（必须放在所有 API 路由之后，
# 否则 "/" 挂载会拦截 /api 下的请求）
app.mount("/", StaticFiles(directory=res_path("static"), html=True), name="static")


def _safe(value, default=""):
    """LLM 可能返回 null/None，统一兜底成字符串"""
    return default if value is None else str(value)


def _list_of(value) -> list:
    """LLM 可能把数组字段返回成 dict/int/字符串（回归修复：直接迭代会 TypeError 崩 500）"""
    return value if isinstance(value, list) else []


def markdown_report(product: str, country: str, d: dict) -> str:
    """把 DeepSeek 返回的结构化 JSON 渲染成 Markdown 报告"""
    lines = [f"# {product}市场分析（{country}）", ""]

    # 核心结论速览（摘要五段式：背景→数据→发现→挑战→建议）
    es = d.get("executive_summary") or {}
    if es and (es.get("background") or es.get("data_points")):
        lines += ["> **摘要**", ""]
        if es.get("background"):
            lines += [f"> **背景**：{_safe(es['background'])}", ""]
        data_points = _list_of(es.get("data_points"))
        if data_points:
            lines += ["> **关键数据**：", *[f"> - {_safe(item)}" for item in data_points], ""]
        key_findings = _list_of(es.get("key_findings"))
        if key_findings:
            lines += ["> **核心发现**：", *[f"> - {_safe(item)}" for item in key_findings], ""]
        challenges = _list_of(es.get("challenges"))
        if challenges:
            lines += ["> **主要挑战**：", *[f"> - {_safe(item)}" for item in challenges], ""]
        if es.get("recommendation"):
            lines += [f"> **建议**：{_safe(es['recommendation'])}", ""]
        lines.append("")

    # 市场规模（回归修复 C4：AI 漏 value 时显示"未知（2026年估算）"、
    # 纯数字无单位时显示"123（2026年估算）"——都是误导性展示，统一降级为"数据不足"）
    ms = d.get("market_size") or {}
    ms_raw = (ms.get("value") or "").strip()
    ms_value = _safe(ms.get("value"), "未知")
    ms_year = _safe(ms.get("year"), "")
    # 无效值判定：空 / 纯数字（无单位）/ 0
    _bare_number = bool(ms_raw) and not any(ch.isalpha() for ch in ms_raw) and \
        ms_raw.replace(",", "").replace(".", "").replace("%", "").replace(" ", "").isdigit()
    if not ms_raw or ms_raw in ("0", "未知", "—") or _bare_number:
        ms_value = "数据不足"
        ms_year = ""
    # 防重复：AI 的 value 常自带"（2026年估算）"，再追加会变成双份（渲染 bug 修复）
    suffix = f"（{ms_year}年估算）" if ms_year and "估算" not in ms_value else ""
    lines += [
        "## 市场规模",
        f"- **规模**：{ms_value}{suffix}",
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
        *[f"- {_safe(item)}" for item in _list_of(gt.get("key_drivers"))],
        "",
    ]

    # 热门品牌（含点评列，IDC 风格）
    brands = _list_of(d.get("top_brands"))
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
        *[f"- {_safe(item)}" for item in _list_of(up.get("key_needs"))],
        "",
        "**购买习惯**：",
        *[f"- {_safe(item)}" for item in _list_of(up.get("buying_habits"))],
        "",
    ]

    # 风险分析（含具体法规条款）
    risks = _list_of(d.get("risks"))
    lines += ["## 风险分析", "| 风险类型 | 等级 | 说明 | 相关法规 |", "| --- | --- | --- | --- |"]
    for r in risks:
        if not isinstance(r, dict):
            continue
        lines.append(f"| {_safe(r.get('type'))} | {_safe(r.get('level'))} | {_safe(r.get('description'))} | {_safe(r.get('regulation'))} |")
    lines.append("")

    # 行动路线（分步可执行）
    ap = _list_of(d.get("action_plan"))
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
