"""main.py — FastAPI 入口：路由 + 报告渲染。

第一版保持扁平结构；后续模块（外贸/评论分析/贸易数据）上线时，
拆分到 routers/ 目录，本文件退化为"组装 app + include_router"。
llm.py / prompts.py 是所有模块共用的底座。
"""
import logging

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from llm import analyze_market

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
        f"- **规模**：{_safe(ms.get('value'), '未知')}（{_safe(ms.get('year'))}年估算）",
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
    lines += ["## 热门品牌", "| 品牌 | 所属国家 | 市场地位 |", "| --- | --- | --- |"]
    for b in brands:
        if not isinstance(b, dict):
            continue
        lines.append(f"| {_safe(b.get('name'))} | {_safe(b.get('origin'))} | {_safe(b.get('position'))} |")
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
