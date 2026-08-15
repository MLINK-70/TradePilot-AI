"""agent.py — AI Agent 编排层（v1.0 阶段 3）

一句话输入 → 固定流水线（非开放式 agent，最强演示点）：
  意图解析 → 市场证据链 → AI 市场分析 → 结构化报告 → 客户线索 → 定制开发信

设计原则：
- 每步复用现有模块（llm/leads/business/market_data/main），不新写业务逻辑
- 某步失败跳过继续，最后汇总"哪步完成、哪步缺失"（不硬撑）
- 本模块为同步生成器：逐步 yield 进度事件；SSE 端点负责线程池 + 队列桥接
"""
import logging
import re

from llm import _chat, _parse_json

STEPS = [
    ("intent", "意图解析（一句话 → 产品/市场/任务）"),
    ("evidence", "聚合市场证据链（UN Comtrade / World Bank / 竞争格局）"),
    ("market", "AI 市场分析"),
    ("report", "生成结构化报告"),
    ("leads", "检索客户线索"),
    ("outreach", "定制开发信"),
]

INTENT_SYSTEM = """你是跨境贸易助手意图解析器。把用户的一句话拆解为结构化任务。

输出 JSON（只输出 JSON 对象，不要解释文字）：
{"product": "产品名", "country": "目标市场", "task": "任务类型"}

任务类型取值：market（仅市场分析）/ leads（仅客户线索）/ full（全流程，默认）。
规则：
- product 提取商品名（中英文均可，如"蓝牙耳机""TWS earphones"）
- country 提取目标国家/市场（如"德国""东南亚"），提取不到填空字符串
- 只输出 JSON 对象"""


def _s(v) -> str:
    """None → ''（回归修复：str(None) 会生成字面量 'None' 通过非空校验）"""
    return "" if v is None else str(v).strip()


def parse_intent(user_input: str) -> dict:
    """意图解析：LLM 优先，正则兜底（LLM 挂了也能跑）"""
    text = (user_input or "").strip()
    try:
        content = _chat([
            {"role": "system", "content": INTENT_SYSTEM},
            {"role": "user", "content": text[:500]},
        ], use_json=True)
        data = _parse_json(content)
        product = _s(data.get("product"))
        country = _s(data.get("country"))
        task = _s(data.get("task")) or "full"
        if product:
            task = task if task in ("market", "leads", "full") else "full"
            return {"product": product, "country": country, "task": task}
    except Exception as e:
        logging.warning("意图解析 LLM 失败，用正则兜底: %s", e)
    # 正则兜底："XX去德国卖" / "XX 卖到 德国" / "XX 销往德国"
    m = re.search(r"(.+?)(?:去|卖到|出口到|发往|销往)(.+?)(?:卖|销售|市场|$)", text)
    if m and m.group(1).strip() and m.group(2).strip():
        # 兜底也做任务类型识别（回归修复：原兜底一律 full，只想要线索的任务多烧 token）
        tail = m.group(2).strip()
        task = "leads" if re.search(r"线索|客户|经销商|分销|采购|找买家", tail) else "full"
        return {"product": m.group(1).strip(), "country": tail, "task": task}
    return {"product": text, "country": "", "task": "full"}


def run_agent_pipeline(user_input: str, stop_event=None):
    """固定流水线执行（同步生成器，逐个产出进度/结果事件）

    stop_event（threading.Event，可选）：客户端断开时置位，每步前检查，
    提前终止不再烧 token（回归修复：SSE 断开后 worker 继续跑完整流水线）。

    事件格式：
    - {"type": "progress", "step": int, "total": int, "title": str, "status": "running|done|skipped", "detail": str}
    - {"type": "result", "product", "country", "report", "leads", "outreach", "summary"}
    - {"type": "error", "detail": str}（流水线级异常，正常降级不会出现）
    """
    total = len(STEPS)
    step_results: list = [None] * total  # 每步一条最终状态（回归修复：原 append 双份记录）
    product, country, task = "", "", "full"
    report, leads, outreach = "", [], None

    def _stopped() -> bool:
        return stop_event is not None and stop_event.is_set()

    def emit(step_idx, status, detail=""):
        ev = {"type": "progress", "step": step_idx, "total": total,
              "title": STEPS[step_idx][1], "status": status, "detail": detail}
        step_results[step_idx] = {"title": STEPS[step_idx][1], "status": status, "detail": detail}
        return ev

    # 步骤 0：意图解析
    yield emit(0, "running")
    try:
        intent = parse_intent(user_input)
        product, country, task = intent["product"], intent["country"], intent["task"]
    except Exception as e:
        logging.warning("意图解析失败（不阻断）: %s", e)
        product, country, task = "", "", "full"
    if not product:
        yield emit(0, "skipped", "未能识别产品，请换个说法，如「蓝牙耳机去德国卖」")
        yield {"type": "result", "product": product, "country": country,
               "report": "", "leads": [], "outreach": None,
               "steps": step_results,  # 结构统一（回归修复：提前返回缺 steps 键）
               "summary": "未能识别产品，请补充产品与目标市场"}
        return
    if not country:
        yield emit(0, "skipped", "未能识别目标市场，请补充国家，如「蓝牙耳机去德国卖」")
        yield {"type": "result", "product": product, "country": country,
               "report": "", "leads": [], "outreach": None,
               "steps": step_results,
               "summary": "未能识别目标市场，请补充国家"}
        return
    yield emit(0, "done", f"产品={product}，市场={country}，任务={task}")

    # 步骤 1-3：市场分析链路（task=market/full）
    if task in ("full", "market"):
        if _stopped():
            return
        yield emit(1, "running")
        try:
            from main import _collect_evidence
            market_ctx, trade_evidence, competitiveness, background, landscape = \
                _collect_evidence(product, country)
            yield emit(1, "done", "证据链聚合完成（贸易/经济/竞争力/宏观/格局）")
        except Exception as e:
            market_ctx = trade_evidence = competitiveness = background = landscape = None
            yield emit(1, "skipped", f"证据链部分缺失：{e}")

        if _stopped():
            return
        yield emit(2, "running")
        try:
            from llm import analyze_market
            data = analyze_market(product, country, market_ctx, trade_evidence,
                                  competitiveness, background, landscape)
            yield emit(2, "done", "AI 市场分析完成")
        except Exception as e:
            data = None
            yield emit(2, "skipped", f"市场分析失败：{e}")

        if _stopped():
            return
        yield emit(3, "running")
        try:
            if data:
                from main import markdown_report
                report = markdown_report(product, country, data)
                yield emit(3, "done", f"报告已生成（{len(report)} 字）")
            else:
                report = ""
                yield emit(3, "skipped", "无分析结果，跳过报告")
        except Exception as e:
            report = ""
            yield emit(3, "skipped", f"报告生成失败：{e}")
    else:
        yield emit(1, "skipped", "任务不包含市场分析")
        yield emit(2, "skipped", "任务不包含市场分析")
        yield emit(3, "skipped", "任务不包含市场分析")

    # 步骤 4-5：客户线索 + 开发信（task=leads/full）
    if task in ("full", "leads"):
        if _stopped():
            return
        yield emit(4, "running")
        try:
            from leads import find_leads
            res = find_leads(product, country)
            leads = res.get("leads", [])
            yield emit(4, "done", f"找到 {len(leads)} 条线索" if leads else "未找到匹配线索")
        except Exception as e:
            leads = []
            yield emit(4, "skipped", f"线索检索失败：{e}")

        if leads:
            if _stopped():
                return
            yield emit(5, "running")
            try:
                from leads import build_lead_outreach
                outreach = build_lead_outreach(leads[0], product, country)
                yield emit(5, "done", f"已为「{leads[0]['company']}」定制开发信")
            except Exception as e:
                yield emit(5, "skipped", f"开发信生成失败：{e}")
        else:
            yield emit(5, "skipped", "无线索，跳过开发信")
    else:
        yield emit(4, "skipped", "任务不包含线索检索")
        yield emit(5, "skipped", "任务不包含开发信")

    done_n = sum(1 for r in step_results if r and r["status"] == "done")
    missing = [r["title"] for r in step_results if r and r["status"] == "skipped"]
    summary = f"完成 {done_n}/{total} 步" + (f"，缺失：{'；'.join(missing)}" if missing else "，全部完成")
    yield {"type": "result", "product": product, "country": country,
           "report": report, "leads": leads, "outreach": outreach,
           "steps": step_results, "summary": summary}
