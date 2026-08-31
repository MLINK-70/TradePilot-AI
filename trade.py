"""trade.py — 贸易数据模块：UN Comtrade 查询层 + 命令行入口

实测确认（2026-08-05）：
- 接口免费、无需 key、直连可用
- 一次最多查 1 个 period，多年份需循环
- 免费版限流严格（429），必须缓存
- 欧盟组代码 97 / 东盟 948，字母代码无效
"""
import logging
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

import config as cfg
from countries import ALL_COUNTRIES
from database import get_cached, init_db, log_query, save_cache

# UN Comtrade 数据源（可切换）：
# - preview：免费，无需 key，有 500 条硬截断、部分国家申报数据质量较低
# - formal：需 subscription key（config.UN_COMTRADE_KEY），数据质量高
# 通过 .env 的 UN_COMTRADE_MODE 切换（默认 preview，向后兼容）
_PREVIEW_URL = "https://comtradeapi.un.org/public/v1/preview/C/A/HS"
_FORMAL_URL = "https://comtradeapi.un.org/data/v1/get/C/A/HS"


def _use_formal() -> bool:
    return cfg.UN_COMTRADE_MODE == "formal" and bool(cfg.UN_COMTRADE_KEY)

# 常用消费电子 HS 编码映射表（第二版起步用，可扩展）
HS_MAP = {
    # 音频设备（85.18）
    "蓝牙耳机": "8518", "无线耳机": "8518", "耳机": "8518",
    "音箱": "8518", "蓝牙音箱": "8518", "功放": "8518",
    # 手表（91.02）
    "智能手表": "9102", "手表": "9102",
    # 吸尘器（85.08）
    "扫地机器人": "8508", "吸尘器": "8508",
    # 电池（85.07）
    "充电宝": "8507", "移动电源": "8507", "电池": "8507",
    # 手机/通讯（85.17）
    "手机": "8517", "智能手机": "8517", "对讲机": "8517",
    # 充电器/电源（85.04）
    "充电器": "8504", "电源适配器": "8504", "电源": "8504", "逆变器": "8504",
    # 显示/电视（85.28）
    "电视": "8528", "电视机": "8528", "显示器": "8528", "投影仪": "8528",
    # 摄像头（85.25）
    "摄像头": "8525", "相机": "8525", "摄像机": "8525",
    # 电脑（84.71）
    "电脑": "8471", "笔记本电脑": "8471", "笔记本": "8471", "平板": "8471",
    "平板电脑": "8471", "台式机": "8471",
    # 小家电（85.16）
    "电饭煲": "8516", "电热水壶": "8516", "吹风机": "8516", "电熨斗": "8516",
    "微波炉": "8516", "烤箱": "8516", "空气炸锅": "8516",
    # 空净/通风（84.21）
    "空气净化器": "8421", "净化器": "8421",
    # 智能家居（85.36/94.05）
    "智能门锁": "8536", "智能插座": "8536", "智能灯泡": "9405", "智能灯": "9405",
    "LED灯": "9405", "台灯": "9405",
    # 电动工具（84.67）
    "电钻": "8467", "电动工具": "8467",
    # 其他
    "无人机": "8526", "路由器": "8517", "机顶盒": "8528", "电子烟": "8543",
    "电动牙刷": "8509", "按摩仪": "9019",
}

# 国家/组织代码表 = 完整国家清单 + 组织代码（ALL_COUNTRIES 来自 countries.py）
AREA_MAP = {**ALL_COUNTRIES, **{
    "欧盟": "97",
    "东盟": "948",
    "RCEP": "RCEP",
    "全球": "0",
}}

# 欧盟成员国清单（27 国，2020 年脱欧后口径；preview 接口组代码查不出，需聚合）
EU_COUNTRIES = [
    "德国", "法国", "意大利", "荷兰", "比利时", "卢森堡", "爱尔兰", "丹麦",
    "希腊", "西班牙", "葡萄牙", "奥地利", "芬兰", "瑞典", "波兰", "捷克",
    "斯洛伐克", "匈牙利", "斯洛文尼亚", "克罗地亚", "罗马尼亚", "保加利亚",
    "立陶宛", "拉脱维亚", "爱沙尼亚", "塞浦路斯", "马耳他",
]

# 东盟成员国清单（10 国）
ASEAN_COUNTRIES = [
    "印度尼西亚", "马来西亚", "菲律宾", "新加坡", "泰国", "文莱", "越南",
    "老挝", "缅甸", "柬埔寨",
]

# RCEP 成员国清单（15 国：东盟 10 国 + 中、日、韩、澳、新西兰；中国为报告国不查自己）
RCEP_COUNTRIES = [
    "日本", "韩国", "澳大利亚", "新西兰",
    "印度尼西亚", "马来西亚", "菲律宾", "新加坡", "泰国", "文莱",
    "越南", "老挝", "缅甸", "柬埔寨",
]

# 组织 → 成员国映射（preview 接口组代码查不出数据，统一走成员聚合）
GROUP_MEMBERS = {
    "97": EU_COUNTRIES,
    "948": ASEAN_COUNTRIES,
    "RCEP": RCEP_COUNTRIES,
}


# 最新年份探测锁（single-flight）：30 天 TTL 到期后并发请求同时探测会
# 各自发起 6 次串行 UN 请求（回归修复：compare 5 国并发 → 30 次请求风暴）
_latest_year_lock = threading.Lock()
# 探测失败熔断时间戳（回归修复 G1：失败后 10 分钟内不重探，防 429 风暴）
_latest_probe_fail_ts = 0.0


def _read_latest_year_cache() -> int | None:
    """读最新年份缓存；脏数据（旧结构/手工改库）解析失败返回 None（防 500 泄漏）"""
    from database import get_cached
    try:
        cached = get_cached("LATEST_YEAR", "0", "0", "X", "META", ttl_days=30)
        if cached:
            return int(cached[0]["year"])
    except Exception:
        logging.warning("最新年份缓存解析失败，按未命中处理", exc_info=True)
    return None


def get_latest_year() -> int:
    """探测 UN Comtrade 最新可用年份（从今年往前找第一个有数据的年份）

    探测结果写入缓存表（reporter_code='META'），避免每次查询都探测。
    30 天 TTL + single-flight：并发调用共享一次探测。
    """
    import datetime
    from database import init_db, save_cache

    # 回归修复：模块级 _latest_probe_fail_ts 被函数内赋值遮蔽 → 未赋值前读取
    # 抛 UnboundLocalError（149 行读、190 行写，Python 视为局部变量）
    global _latest_probe_fail_ts

    init_db()  # 确保缓存表存在（首次调用/无 db 文件时）

    # 30 天 TTL：数据修订后能自动重新探测（阶段 4，B 类审查 #10）
    latest = _read_latest_year_cache()
    if latest is not None:
        return latest

    # single-flight：探测期间其他线程等待后直接读缓存
    with _latest_year_lock:
        latest = _read_latest_year_cache()
        if latest is not None:
            return latest
        this_year = datetime.date.today().year
        # 进程内熔断（回归修复 G1）：最近 10 分钟探测失败过则不重探，
        # 防止限流窗口内每次 analyze/options/pricing/watch 都重发 6 连探测
        if time.time() - _latest_probe_fail_ts < 600:
            return this_year - 6
        # 探测范围 6 年（数据更新滞后时也能找到最新可用年份）
        for y in range(this_year, this_year - 6, -1):
            retries = 0  # 429 重试计数（回归修复 G1：429 是限流不是"该年无数据"）
            while True:
                try:
                    params = {
                        "reporterCode": "156",
                        "period": str(y),
                        "partnerCode": "0",
                        "cmdCode": "8518",
                        "flowCode": "X",
                        "maxRecords": 1,
                    }
                    hdrs = {"Accept": "application/json"}
                    if _use_formal():
                        hdrs["Ocp-Apim-Subscription-Key"] = cfg.UN_COMTRADE_KEY
                    resp = requests.get(
                        _FORMAL_URL if _use_formal() else _PREVIEW_URL, params=params,
                        headers=hdrs,
                        timeout=30,
                        proxies={"http": None, "https": None},
                    )
                    if resp.status_code == 200 and resp.json().get("count", 0) > 0:
                        save_cache("LATEST_YEAR", "0", "0", "X", [{"year": y}], "META")
                        logging.info("最新可用年份探测: %d", y)
                        return y
                    if resp.status_code == 429 and retries < 2:
                        # 429：退避后重试同一年（原实现把 429 当成无数据继续探测
                        # 下一年，6 连请求加剧限流）
                        retries += 1
                        wait = min(15, 5 * retries)
                        logging.warning("最新年份探测 429，%d 秒后重试同一年 %d", wait, y)
                        time.sleep(wait)
                        continue
                    break  # 非 200/429：该年视为无数据，探测下一年
                except Exception:
                    break
            time.sleep(1)
        logging.warning("最新年份探测失败，回退 %d（10 分钟内不再重探）", this_year - 6)
        _latest_probe_fail_ts = time.time()  # 熔断：避免限流雪崩
        return this_year - 6  # 动态兜底：探测范围的最后一年（不再是写死的 2024）


# AI 辅助 HS 编码解析缓存（产品名 → 编码，避免重复调用）
# 回归修复：原 dict 无上限（产品词可无界增长）；超限清空（持引用者不受影响）
_HS_AI_CACHE: dict = {}
_HS_AI_CACHE_MAX = 512
# 内置表动态条目的上限（回归修复 G2：HS_MAP 被 AI 解析结果持续追加）
_HS_MAP_MAX = 600


def _hs_cache_set(product: str, hs: str) -> None:
    if len(_HS_AI_CACHE) >= _HS_AI_CACHE_MAX:
        _HS_AI_CACHE.clear()
    _HS_AI_CACHE[product] = hs


def get_hs_candidates(product: str, top_n: int = 3) -> list:
    """AI 辅助：产品名 → 3 个候选 HS 编码（编码 + 描述），供用户确认

    返回 [{hs_code, description}]。用户点选后 hs_lookup 用确认的编码查询，
    防止 AI 单次解析错编码 → 错误数据 → 漂亮但错误的报告。
    结果持久化到 SQLite（cache_key=产品名|cand）。
    """
    product = product.strip()
    if len(product) < 2:
        return []
    try:
        from database import get_cached
        cached = get_cached("HSCAND", "0", "0", "X", "0", cache_key=product)
        if cached is not None and isinstance(cached, list) and cached:
            return cached[:top_n]
    except Exception:
        pass
    try:
        from llm import _chat, _parse_json
        content = _chat([
            {"role": "system", "content": "你是 HS 编码专家。根据产品名，返回 3 个最可能的 HS 编码候选（4-6 位），每个带中文品名描述，按匹配度排序。只输出 JSON：{\"candidates\": [{\"hs_code\": \"9506\", \"description\": \"体育器械：羽毛球拍等\"}, ...]}"},
            {"role": "user", "content": f"产品: {product}"},
        ], use_json=True)
        data = _parse_json(content)
        candidates = []
        for c in (data.get("candidates") or [])[:top_n]:
            hs = str(c.get("hs_code", "")).strip()
            desc = str(c.get("description", "")).strip()
            if hs.isdigit() and 4 <= len(hs) <= 6:
                candidates.append({"hs_code": hs, "description": desc})
        if candidates:
            try:
                from database import save_cache
                save_cache("HSCAND", "0", "0", "X", candidates, "0", cache_key=product)
            except Exception:
                pass
            return candidates
    except Exception:
        # 回归修复（遗留项 6）：AI 候选解析失败要留痕——否则前端无法区分
        # "产品未收录"（正常）和"AI 服务挂了"（异常），两者都显示"未收录"
        logging.warning("AI HS 候选解析失败: %s", product, exc_info=True)
    return []


def _hs_via_ai(product: str) -> str:
    """AI 辅助：产品名 → HS 编码（4-6 位）。失败返回空字符串。

    用 DeepSeek 知识库解析（如"羽毛球拍"→9506），成功后写入 SQLite 持久缓存 + 内置表。
    """
    # 太短的输入无法识别（防 AI 幻觉乱猜编码），直接返回空
    if len(product.strip()) < 2:
        return ""
    if product in _HS_AI_CACHE:
        return _HS_AI_CACHE[product]
    # 先查 SQLite 持久缓存（重启不丢），cache_key 专用字段存产品名
    try:
        from database import get_cached
        cached = get_cached("HSAI", "0", "0", "X", "0", cache_key=product)
        if cached:
            hs = str(cached[0].get("hs", ""))
            if hs.isdigit():
                _hs_cache_set(product, hs)
                if len(HS_MAP) < _HS_MAP_MAX:  # 回归修复 G2：动态条目设上限防无界增长
                    HS_MAP[product] = hs
                desc = str(cached[0].get("desc", "")).strip()
                if desc:
                    try:
                        from hs_descriptions import HS_DESCRIPTIONS
                        HS_DESCRIPTIONS[str(hs)] = desc
                        from database import save_cache
                        save_cache("HSDESC", "0", "0", "X", [{"hs": hs, "desc": desc}], "0", cache_key=hs)
                    except Exception:
                        pass
                return hs
    except Exception:
        pass
    try:
        from llm import _chat, _parse_json
        content = _chat([
            {"role": "system", "content": "你是 HS 编码专家。根据产品名，返回对应的 HS 编码（4-6 位数字）和中文品名描述。只输出 JSON：{\"hs_code\": \"9506\", \"description\": \"体育器械：羽毛球拍等\"}"},
            {"role": "user", "content": f"产品: {product}"},
        ], use_json=True)
        data = _parse_json(content)
        hs = str(data.get("hs_code", "")).strip()
        desc = str(data.get("description", "")).strip()
        if hs.isdigit() and 4 <= len(hs) <= 6:
            _hs_cache_set(product, hs)
            if len(HS_MAP) < _HS_MAP_MAX:  # 回归修复 G2：动态条目设上限防无界增长
                HS_MAP[product] = hs  # 写进内置表，下次直接命中
            # 描述持久化：写进 SQLite（cache_key 存产品名）+ HSDESC 反向缓存（按编码精确查）+ 内存表
            try:
                from database import save_cache
                save_cache("HSAI", "0", "0", "X", [{"hs": hs, "desc": desc}], "0", cache_key=product)
                if desc:
                    save_cache("HSDESC", "0", "0", "X", [{"hs": hs, "desc": desc}], "0", cache_key=hs)
            except Exception:
                pass
            if desc:
                try:
                    from hs_descriptions import HS_DESCRIPTIONS
                    HS_DESCRIPTIONS[str(hs)] = desc
                except Exception:
                    pass
            return hs
    except Exception:
        # 回归修复（遗留项 6）：AI HS 解析失败留痕——区分"产品未收录"（正常，
        # 前端提示手输）和"AI 服务异常"（需排查），两者此前都静默返回空
        logging.warning("AI HS 编码解析失败: %s", product, exc_info=True)
    _hs_cache_set(product, "")  # 失败也缓存空（防重复调 AI），超限自动清空
    return ""


def hs_lookup(product: str) -> str:
    """产品名 → HS 编码；支持直接传 4-6 位数字编码

    优先内置表；匹配不到时 AI 辅助解析（成功后写缓存），再失败返回空（前端提示手输）。
    """
    product = product.strip()
    if product.isdigit() and 4 <= len(product) <= 6:
        return product
    hs = HS_MAP.get(product, "")
    if not hs:
        hs = _hs_via_ai(product)
    return hs


def partner_lookup(name: str) -> str:
    """国家/组织名 → 数字代码"""
    return AREA_MAP.get(name.strip(), "")


def _ttl_for_period(period: str) -> int:
    """贸易数据动态 TTL：UN Comtrade 持续修订（近 2 年多为初步值，
    参考年后约 2 年趋于终值，晚申报国更晚）

    回归修复 P1-11：原实现 2 年零 1 天即永久——晚申报国 3-4 年前数据
    仍在修订却永不更新；现 3-4 年每年刷新一次，5 年以上才永久。
    """
    try:
        y = int(str(period)[:4])
    except (TypeError, ValueError):
        return 0
    import datetime
    diff = datetime.date.today().year - y
    if diff <= 2:
        return 90   # 近 2 年：初步值，频繁修订
    if diff <= 4:
        return 365  # 3-4 年：晚申报国仍在修订，每年刷新
    return 0        # 5 年以上：接近终值，永久缓存


def fetch_year(cmd_code: str, partner_code: str, period: str, reporter: str = "中国",
               flow: str = "X") -> list:
    """查单年数据：先查缓存，未命中打 API 并写缓存

    reporter: 出口国（报告国），默认中国
    flow: X=出口 / M=进口（reporter 为报告国时的流向）
    缓存：近期年份 90 天 TTL（UN Comtrade 每年修订数据）；空结果也写缓存
    （30 天内不再重复打 API，避免 429 限流下反复撞墙）。
    血缘（v1.0.2）：每次写缓存同时记录 source/raw_count/clean_count/quality/
    validation_reason，可用 get_cache_meta 追溯"这个数字怎么来的"。
    """
    reporter_code = AREA_MAP.get(reporter, "156")
    flow_code = flow
    # 回归修复：缓存键含数据源模式（preview/formal 切换后旧缓存不得继续命中）
    mode_key = "formal" if _use_formal() else "preview"

    def _write_cache(data, **kw):
        """写缓存但失败不阻断本次查询（回归修复 G3：原 save_cache 的 sqlite 异常
        会让 UN 数据查询成功却整体 500；数据本体已取到，缓存失败只影响下次命中）"""
        try:
            save_cache(cmd_code, partner_code, period, flow_code, data, reporter_code,
                       cache_key=mode_key, **kw)
        except Exception:
            logging.warning("缓存写入失败（不影响本次结果）: %s/%s/%s", cmd_code, partner_code, period)

    # 回归修复 P0-1：读缓存前先查血缘——REJECTED 缓存（截断/C00 缺失拒绝）不得
    # 当"合法空结果"返回（原实现 get_cached 返回 [] 被直接当合法空，拒绝原因永久
    # 丢失；fetch_group 成员被静默计 0 → 组织总额系统性偏低）
    try:
        from database import get_cache_meta
        _meta = get_cache_meta(cmd_code, partner_code, period, flow_code, reporter_code,
                               cache_key=mode_key)
        if _meta and _meta["quality"] == "rejected":
            # 键名修正：get_cache_meta 返回 validation_reason（无 reason 键），
            # 原写法 .get("reason") 恒为 None，日志里括号永远空白，"留痕"失效
            logging.warning("缓存为 REJECTED（%s），按未命中重新请求: %s/%s/%s",
                            (_meta.get("validation_reason") or "")[:60], cmd_code, partner_code, period)
            cached = None
        else:
            cached = get_cached(cmd_code, partner_code, period, flow_code, reporter_code,
                                cache_key=mode_key, ttl_days=_ttl_for_period(period))
    except Exception:
        cached = get_cached(cmd_code, partner_code, period, flow_code, reporter_code,
                            cache_key=mode_key, ttl_days=_ttl_for_period(period))
    if cached is not None:
        return cached

    params = {
        "reporterCode": reporter_code,
        "period": period,
        "partnerCode": partner_code,
        "cmdCode": cmd_code,
        "flowCode": flow_code,
        # 原产国/目的国（partner2）维度关掉，只要合计行。
        # 原因：UN Comtrade 对 partnerCode=0（全球）的查询会按 partner2Code 拆行——
        # 德国 2024 年进口 HS 8518 拆出 106 个原产国 × 3 种 customsCode × 多种 motCode
        # = 500+ 行，直接撞上 maxRecords 上限被判"结果不完整"而拒绝。
        # 后果是"市场总进口"（份额分母）永远取不到，竞争力指标 TC + 市场份额整体失效，
        # 定价建议（依赖市场进口均价）同死。实测：加此参数后同一查询 500 行 → 18 行，
        # 总额 38.73 亿美元（正确）；对非全球查询无副作用（中国→德国、美国→德国
        # 加参数前后均 1 行同值）。
        "partner2Code": 0,
        "maxRecords": 500,
    }
    last_error = None
    # 数据源：formal（带 subscription key 头）/ preview（免费）
    use_formal = _use_formal()
    base_url = _FORMAL_URL if use_formal else _PREVIEW_URL
    headers = {"Accept": "application/json"}
    if use_formal:
        headers["Ocp-Apim-Subscription-Key"] = cfg.UN_COMTRADE_KEY
    for attempt in range(3):
        try:
            resp = requests.get(
                base_url,
                params=params,
                headers=headers,
                timeout=60,
                proxies={"http": None, "https": None},  # 强制直连（防梯子劫持）
            )
            if resp.status_code == 429:
                last_error = f"429 限流（第 {attempt + 1} 次）"
                # 指数退避：2/5/10 秒（preview 接口限流窗口通常 1 分钟，
                # 快速重试只会加剧限流；等更久让窗口过去）
                wait = (2, 5, 10)[attempt]
                print(f"[限流] 429，{wait} 秒后重试...")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            try:
                payload = resp.json()
            except ValueError:
                raise ValueError(f"UN Comtrade 返回非 JSON 响应（HTTP {resp.status_code}）")
            if not isinstance(payload, dict) or "data" not in payload:
                # 回归修复：200 + 错误响应体（无 data 键）不得当作"合法空结果"写入缓存，
                # 否则用户会看到假"无贸易数据"且被永久缓存
                raise ValueError("UN Comtrade 返回异常结构（缺少 data 字段），已拒绝")
            raw_data = payload.get("data", []) or []
            if not isinstance(raw_data, list):
                # 回归修复：data 为 dict/字符串时逐行解析会 AttributeError
                raise ValueError("UN Comtrade 返回异常结构（data 非数组），已拒绝")
            raw_count = len(raw_data)
            # 截断检测必须在过滤之前（回归修复：原实现过滤后才检查，500 条原始行里
            # 若恰无 C00/mot=0 总额行，残缺结果会被静默当作完整数据缓存）
            if raw_count >= 500:
                # 截断即残缺：写 REJECTED 元数据（不写数据缓存）+ 报错拒绝，
                # DataGate 能查到 rejected 记录并向前端解释"为什么不可用"
                _write_cache([], source="uncomtrade/" + mode_key,
                             raw_count=raw_count, clean_count=0,
                             quality="rejected",
                             validation_reason=f"原始数据达到 {raw_count} 条记录上限（结果不完整），完整性校验未通过")
                raise ValueError(
                    f"UN Comtrade 返回 {raw_count} 条达到记录上限（结果不完整，已拒绝）")
            # 空数据 = 合法空结果（某国某年确实无贸易记录）→ 立即写 valid 空缓存，
            # 与"有数据但缺总额行"（残缺，rejected）严格区分——没有数据 ≠ 数据没找到
            if not raw_data:
                _write_cache([], source="uncomtrade/" + mode_key,
                             raw_count=0, clean_count=0,
                             quality="valid", validation_reason="查询成功，无贸易记录（合法空结果）")
                return []
            # 正确聚合（数据准确性，实测验证）：UN Comtrade 按 customsCode(贸易方式：
            # C00=总计=C03+C04+…) 和 motCode(运输方式：0=全部) 拆分为多条，且部分查询
            # 有成对重复行。正确总额 = customsCode=C00 且 motCode=0 的唯一记录。
            # 此前"sum 所有行"(55亿) 和 "mot=0 优先"(27.5亿) 均错误，真实值为 6.88 亿。
            seen = set()
            unique = []
            for r in raw_data:
                # partner2Code（原产国/目的国）必须进去重键：它也是一条拆分维度，
                # 漏掉会把 106 个原产国行折叠成 1 行，取到的值差约 1900 倍
                # （实测德国 2024 进口 HS 8518：折叠后 200 万美元 vs 真实 38.73 亿美元）。
                key = (r.get("reporterCode"), r.get("partnerCode"), r.get("cmdCode"),
                       r.get("period"), r.get("motCode"), r.get("mosCode"),
                       r.get("customsCode"), r.get("partner2Code"))
                if key in seen:
                    continue
                seen.add(key)
                unique.append(r)
            total_rows = [r for r in unique
                          if str(r.get("customsCode")) == "C00" and str(r.get("motCode")) == "0"]
            clean_count = len(total_rows)
            if not total_rows:
                # 兜底（回归修复）：无 C00+mot=0 时依次取 customs=C00 行、mot=0 行；
                # 不再回退到"全部去重行"（明细行 C03/C04 求和 ≈ 总额 2~3 倍，系统性偏大）
                total_rows = [r for r in unique if str(r.get("customsCode")) == "C00"] or \
                             [r for r in unique if str(r.get("motCode")) == "0"]
                clean_count = len(total_rows)
            if not total_rows:
                # 数据准确性红线（宁缺勿错）：无任何总额行说明数据残缺（截断/申报口径），
                # 记 REJECTED 元数据 + 报错不写数据缓存，防止调用方把残缺/翻倍数字当完整结果
                _write_cache([], source="uncomtrade/" + mode_key,
                             raw_count=raw_count, clean_count=0,
                             quality="rejected",
                             validation_reason="原始数据缺少总额行（customsCode=C00），完整性校验未通过")
                raise ValueError("UN Comtrade 返回数据缺少总额行（customsCode=C00），已拒绝")
            data = total_rows
            # 空结果也写缓存（30 天短 TTL 由读取侧 _ttl_for_period 控制）：
            # 某国某年无贸易数据是合法结果，不缓存会导致每次查询都打 API
            _write_cache(data, source="uncomtrade/" + mode_key,
                         raw_count=raw_count, clean_count=clean_count,
                         quality="valid", validation_reason="C00+mot=0 完整总额行")
            return data
        except requests.exceptions.RequestException as e:
            last_error = str(e)
            if attempt == 2:
                raise ValueError(f"UN Comtrade 查询失败：{e}")
            print(f"[网络] {e}，2 秒后重试...")
            time.sleep(2)
    # 3 次重试全失败（如持续 429）：抛异常而非静默返回空，
    # 防止单国误报 0 / 组织聚合写入残缺缓存
    raise ValueError(f"UN Comtrade 查询失败：{last_error}（重试 3 次仍失败）")


# ── DataGate 总闸（数据层架构收口，v1.0.2）──────────────────────────────
# 原则：数据能不能用由程序判定（校验器→质量标记→统计引擎→AI），
# AI 只负责解释数据，不决定数据可信度。所有分析入口引用数字前过此闸。
QUALITY_ORDER = {"rejected": 0, "invalid": 1, "suspicious": 2, "valid": 3}


def check_data_gate(cmd_code: str, partner_code: str, period: str, flow_code: str,
                    reporter_code: str = "156", cache_key: str = "") -> dict:
    """DataGate 校验：查该查询的血缘元数据，返回质量判定

    返回 {allowed: bool, quality, reason, meta}：
    - allowed=False 且 quality=rejected：原始数据未通过完整性校验（截断/C00 缺失），
      调用方不得使用数字（宁可显示"数据无法用于本次分析"也不给错值）
    - allowed=True 但 quality=suspicious：可用但需在报告标注谨慎解读
    - 无缓存记录（未查询过）：allowed=False, quality='unknown'——不能当作"无数据"
    """
    from database import get_cache_meta
    meta = get_cache_meta(cmd_code, partner_code, period, flow_code, reporter_code, cache_key)
    if meta is None:
        return {"allowed": False, "quality": "unknown",
                "reason": "该查询无缓存记录（未查询或已过期），不能当作无数据",
                "meta": None}
    q = meta.get("quality", "valid")
    allowed = QUALITY_ORDER.get(q, 1) >= QUALITY_ORDER["suspicious"]
    reason = meta.get("validation_reason", "") or (
        "Suspicious：出口方与进口方申报口径存在镜像差异" if q == "suspicious" else "")
    return {"allowed": allowed, "quality": q, "reason": reason, "meta": meta}


def data_gate_report(cmd_code: str, partner_code: str, period: str, flow_code: str,
                     reporter_code: str = "156", cache_key: str = "") -> dict:
    """DataGate 的前端友好版：返回可展示的质量说明（供报告/前端直接渲染）"""
    g = check_data_gate(cmd_code, partner_code, period, flow_code, reporter_code, cache_key)
    if g["quality"] == "rejected":
        return {"usable": False,
                "label": "数据无法用于本次分析",
                "detail": f"原因：{g['reason']}（原始数据未通过完整性校验）"}
    if g["quality"] == "suspicious":
        return {"usable": True,
                "label": "数据存疑（镜像口径差异）",
                "detail": g["reason"]}
    if g["quality"] == "unknown":
        return {"usable": False,
                "label": "暂无数据记录",
                "detail": "该查询没有可用数据记录，请稍后重试或检查数据源配置"}
    return {"usable": True, "label": "数据可信", "detail": g["reason"] or "通过完整性校验"}


def fetch_group(cmd_code: str, period: str, group_code: str, reporter: str = "中国", flow: str = "X") -> list:
    """组织聚合查询（欧盟/东盟/RCEP）：并发查成员国数据并缓存聚合结果

    preview 免费接口对组代码（97/948）不返回数据，统一走成员清单聚合。
    阶段 4 优化：3 线程并发（原串行 27 国 ≈ 30s+）；已缓存成员不重复请求；
    限流只在 429 时由 fetch_year 内部退避（plan 修订 #3：并发撞窗再退串行）。
    """
    reporter_code = AREA_MAP.get(reporter, "156")
    mode_key = "formal" if _use_formal() else "preview"
    cached = get_cached(cmd_code, group_code, period, flow, reporter_code,
                        cache_key=mode_key, ttl_days=_ttl_for_period(period))
    if cached is not None:
        return cached

    members = GROUP_MEMBERS[group_code]
    todo = []
    for country in members:
        code = AREA_MAP.get(country, "")
        if not code:
            logging.warning("[跳过] %s: 无代码", country)
            continue
        # 已缓存成员不重复请求（回归修复 P1-7：预检与 fetch_year 写缓存同键 mode_key，
        # 原不传 cache_key 永远 miss → 每次组织查询全量重走 N 国）
        if get_cached(cmd_code, code, period, flow, reporter_code, cache_key=mode_key,
                      ttl_days=_ttl_for_period(period)) is not None:
            continue
        todo.append((country, code))

    results = {}
    failed = []
    lock = threading.Lock()

    def _fetch_one(item):
        country, code = item
        try:
            rows = fetch_year(cmd_code, code, period, reporter, flow=flow)
            with lock:
                results[country] = rows
        except ValueError as e:
            with lock:
                failed.append(f"{country}: {e}")
            logging.warning("[跳过] %s: %s", country, e)

    if todo:
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(_fetch_one, todo))
        logging.info("[进度] 组织聚合 %s 已查 %d/%d 国", group_code, len(results), len(members))

    # 按成员顺序稳定输出（新请求结果 + 缓存命中成员）
    all_rows = []
    for country in members:
        if country in results:
            all_rows.extend(results[country])
            continue
        code = AREA_MAP.get(country, "")
        if code:
            cached_rows = get_cached(cmd_code, code, period, flow, reporter_code,
                                     cache_key=mode_key, ttl_days=_ttl_for_period(period))
            if cached_rows is not None:
                all_rows.extend(cached_rows)

    if failed:
        logging.error("[数据准确性] %d 国查询失败，组织聚合结果残缺，返回空（宁缺勿错）: %s",
                      len(failed), "、".join(failed))
        # 数据准确性红线：任一成员失败导致聚合数字系统性偏小（TC/份额失真），
        # 宁可返回空（调用方置 available=False/None）也不返回残缺数字
        return []

    # 全部成功才写缓存（残缺结果被永久缓存会让错误长期留存）
    # 回归修复 P1-6：聚合缓存带血缘 source（原默认 source='' 被下游当可信 valid）
    if all_rows:
        save_cache(cmd_code, group_code, period, flow, all_rows, reporter_code,
                   cache_key=mode_key, source="uncomtrade/" + mode_key)
    return all_rows


def fetch_group_world_imports(cmd_code: str, period: str, group_code: str) -> list:
    """组织成员从全球的进口总额（市场进口份额的分母，回归修复 #2）

    逐成员查 reporter=成员、partner=0、flow=M 并求和（与 fetch_group 同并发/缓存骨架，
    但 reporter 是每个成员而非固定出口国，语义不同不能复用）。
    回归修复 P0-3：缓存键含数据源模式（preview/formal 聚合质量不同，不得互用）
    """
    mode_key = "formal" if _use_formal() else "preview"
    cached = get_cached(cmd_code, group_code, period, "MW", "0", cache_key=mode_key,
                        ttl_days=_ttl_for_period(period))
    if cached is not None:
        return cached

    members = GROUP_MEMBERS[group_code]
    todo = []
    for country in members:
        code = AREA_MAP.get(country, "")
        if not code:
            continue
        # 成员已缓存（该成员从全球的进口）则不重复请求
        # 回归修复 P1-7：预检必须与 fetch_year 写缓存同键（原不传 cache_key
        # 永远 miss，每次组织查询全量重走 N 国）
        if get_cached(cmd_code, "0", period, "M", code, cache_key=mode_key,
                      ttl_days=_ttl_for_period(period)) is not None:
            continue
        todo.append((country, code))

    results = {}
    failed = []
    lock = threading.Lock()

    def _fetch_one(item):
        country, code = item
        try:
            rows = fetch_year(cmd_code, "0", period, reporter=country, flow="M")
            with lock:
                results[country] = rows
        except ValueError as e:
            with lock:
                failed.append(f"{country}: {e}")

    if todo:
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(_fetch_one, todo))

    all_rows = []
    for country in members:  # 按成员顺序稳定输出
        code = AREA_MAP.get(country, "")
        if country in results:
            all_rows.extend(results[country])
        elif code:
            cached_rows = get_cached(cmd_code, "0", period, "M", code, cache_key=mode_key,
                                     ttl_days=_ttl_for_period(period))
            if cached_rows is not None:
                all_rows.extend(cached_rows)

    if failed:
        logging.error("[数据准确性] %d 国查询失败，组织进口聚合残缺，返回空（宁缺勿错）: %s",
                      len(failed), "、".join(failed))
        return []
    if all_rows:
        save_cache(cmd_code, group_code, period, "MW", all_rows, "0", cache_key=mode_key,
                   source="uncomtrade/" + mode_key)
    return all_rows


def query_trade(product: str, target: str, year: str, reporter: str = "中国"):
    """主入口：产品 + 国家/组织 + 年份 + 出口国 → 数据列表"""
    init_db()

    hs = hs_lookup(product)
    if not hs:
        raise ValueError(
            f"暂未收录产品「{product}」的 HS 编码，可手输 4-6 位数字 HS 编码查询"
        )

    target_code = partner_lookup(target)
    if not target_code:
        raise ValueError(f"未找到国家/组织「{target}」的代码")

    # 回归修复（v1.0.3 收尾）：同国查询语义错误——reporter==target 时 UN Comtrade
    # 返回 0 条（合法空），前端显示"出口数据缺失"误导用户（如"空调→中国"且出口国
    # 默认中国 = 中国出口到中国，无意义）。明确报错让用户换目标市场或出口国。
    reporter_code = AREA_MAP.get(reporter, "")
    if reporter_code and reporter_code == target_code and target_code not in GROUP_MEMBERS:
        raise ValueError(
            f"目标市场「{target}」与出口国「{reporter}」相同——同国贸易查询无意义，"
            f"请更换目标市场（或在高级选项修改出口国）"
        )

    if target_code in GROUP_MEMBERS:
        rows = fetch_group(hs, year, target_code, reporter)
    else:
        rows = fetch_year(hs, target_code, year, reporter)

    # 回归修复：查询日志是辅助记录，DB 写失败不得让成功的数据查询整体失败
    try:
        log_query(product, hs, target)
    except Exception:
        logging.warning("查询日志写入失败（不影响结果）: %s / %s", product, target)
    return hs, rows


def query_trend(product: str, target: str, years: list, reporter: str = "中国") -> tuple[str, list, dict]:
    """年份范围查询：产品 + 国家/组织 + 年份列表 + 出口国 → (hs_code, 全部行, 逐年汇总)

    years 如 [2018, 2019, 2020, 2021, 2022]，每年代价同单年查询；
    单国每年 1 次请求，组织每年 N 国请求（已有缓存则秒回）。
    """
    init_db()

    hs = hs_lookup(product)
    if not hs:
        raise ValueError(
            f"暂未收录产品「{product}」的 HS 编码，可手输 4-6 位数字 HS 编码查询"
        )

    target_code = partner_lookup(target)
    if not target_code:
        raise ValueError(f"未找到国家/组织「{target}」的代码")

    # 同国查询语义错误防护（与 query_trade 一致）：reporter==target → 明确报错
    reporter_code = AREA_MAP.get(reporter, "")
    if reporter_code and reporter_code == target_code and target_code not in GROUP_MEMBERS:
        raise ValueError(
            f"目标市场「{target}」与出口国「{reporter}」相同——同国贸易查询无意义，"
            f"请更换目标市场（或在高级选项修改出口国）"
        )

    all_rows = []
    if target_code in GROUP_MEMBERS:
        for year in years:
            all_rows.extend(fetch_group(hs, str(year), target_code, reporter))
    else:
        for year in years:
            all_rows.extend(fetch_year(hs, target_code, str(year), reporter))

    try:
        log_query(product, hs, target)  # 回归修复：日志失败不阻断结果
    except Exception:
        logging.warning("查询日志写入失败（不影响结果）: %s / %s", product, target)
    return hs, all_rows, summarize_trend(all_rows)


def summarize_trend(rows: list) -> dict:
    """逐年汇总：{year: {"value": float, "weight": float}}，按 refYear 聚合

    数据准确性：refYear 缺失时回退 period 前 4 位（防静默丢整条记录），
    仍无效才跳过。
    """
    by_year: dict[int, dict] = {}
    for r in rows:
        year = r.get("refYear")
        if not year:
            period = str(r.get("period") or "")
            year = int(period[:4]) if len(period) >= 4 and period[:4].isdigit() else None
        if not year:
            continue
        try:
            value = float(r.get("primaryValue") or 0)
            weight = float(r.get("netWgt") or 0)
        except (TypeError, ValueError):
            # 回归修复：脏数据（"N/A"、带逗号数字等）单行跳过并告警，
            # 不再让整条查询以 502 崩溃
            logging.warning("跳过脏数据行: refYear=%s primaryValue=%r", year, r.get("primaryValue"))
            continue
        entry = by_year.setdefault(year, {"value": 0.0, "weight": 0.0})
        entry["value"] += value
        entry["weight"] += weight
    return {y: v for y, v in sorted(by_year.items())}


def summarize_stats(trend: dict) -> dict:
    """程序精确计算趋势统计指标（供 AI 解读引用，杜绝 AI 自己算错）

    返回：总量、年均增速、峰值/谷值年份、首末变化、最大单年波动、单价趋势
    """
    years = sorted(trend.keys())
    if not years:
        return {}

    first_y, last_y = years[0], years[-1]
    first_v = trend[first_y]["value"]
    last_v = trend[last_y]["value"]
    total = sum(v["value"] for v in trend.values())

    # 年复合增长率 CAGR = (last/first)^(1/n) - 1
    # n 用实际年差（years[-1]-years[0]）：非连续年份（如 [2018,2020,2022]）时
    # 用"数据点间隔数"会严重高估（修复：B 类审查 #1）。年份键为字符串，先转 int。
    n = int(years[-1]) - int(years[0])
    # 防负数开方崩溃（返回 complex -> round 抛 TypeError）：仅 first_v>0 且 last_v>=0 时计算（B8 修复）
    cagr = ((last_v / first_v) ** (1 / n) - 1) * 100 if n > 0 and first_v > 0 and last_v >= 0 else None

    # 峰值/谷值
    peak_y = max(trend, key=lambda y: trend[y]["value"])
    trough_y = min(trend, key=lambda y: trend[y]["value"])

    # 最大单年波动（相邻年变化率最大）
    max_chg = 0.0
    max_chg_year = None
    for i in range(1, len(years)):
        prev, cur = trend[years[i - 1]]["value"], trend[years[i]]["value"]
        if prev:
            chg = (cur - prev) / prev * 100
            if abs(chg) > abs(max_chg):
                max_chg = chg
                max_chg_year = years[i]

    # 单价趋势（金额/净重）
    unit_prices = []
    for y in years:
        w = trend[y].get("weight") or 0
        if w > 0:
            unit_prices.append({"year": y, "price": trend[y]["value"] / w})

    return {
        "years": years,
        "total_value": total,
        "first_year": first_y,
        "last_year": last_y,
        "first_value": first_v,
        "last_value": last_v,
        "change_over_period_pct": (last_v - first_v) / first_v * 100 if first_v else None,
        "cagr_pct": round(cagr, 2) if cagr is not None else None,
        "peak_year": peak_y,
        "trough_year": trough_y,
        "max_swing_year": max_chg_year,
        "max_swing_pct": round(max_chg, 2) if max_chg_year else None,
        "unit_prices": unit_prices,
    }


def compute_tc(export_value: float, import_value: float) -> float | None:
    """贸易竞争力指数 TC = (出口-进口)/(出口+进口)

    范围 [-1, 1]：>0 顺差（竞争力强），<0 逆差（竞争力弱）。
    """
    total = export_value + import_value
    if total == 0:
        return None
    return round((export_value - import_value) / total, 4)


def compute_rca(product_export: float, country_export: float,
                product_world_export: float, world_export: float) -> float | None:
    """显性比较优势 RCA = (产品出口/国家总出口) / (全球产品出口/全球总出口)

    RCA > 1：该产品在目标国具有显性比较优势；< 1：劣势。
    """
    if not country_export or not world_export or not product_world_export:
        return None
    share_c = product_export / country_export
    share_w = product_world_export / world_export
    if share_w == 0:
        return None
    return round(share_c / share_w, 4)


def get_competitiveness(product: str, target: str, year: str, reporter: str = "中国") -> dict:
    """竞争力指标：TC（贸易竞争力指数）+ 市场出口份额

    - TC：出口国对该市场该产品的出口 + 进口（flow X + M），TC=(X-M)/(X+M)
    - 市场出口份额：出口国对该市场该产品出口 / 该市场该产品总进口
      （份额 = 出口国占目标市场进口的比重，真实可算、有业务含义）

    说明：标准 RCA（显性比较优势）需要全球总出口数据（reporter=0），
    UN Comtrade preview 免费接口不提供，故用"市场出口份额"替代——
    同样衡量竞争力，且数据严谨可溯源。任一数据缺失返回空 dict，不阻断。
    """
    try:
        hs = hs_lookup(product)
        if not hs:
            return {}
        target_code = partner_lookup(target)
        if not target_code:
            return {}

        # TC：出口 + 进口（出口国对该市场）
        # 组织目标（欧盟等）：组代码作 partner preview 接口不返回数据，出口/进口
        # 两条腿都走成员聚合（回归修复：此前仅份额分母聚合，TC 腿仍是空结果）
        if target_code in GROUP_MEMBERS:
            exp_rows = fetch_group(hs, year, target_code, reporter, flow="X")
            imp_rows = fetch_group(hs, year, target_code, reporter, flow="M")
        else:
            exp_rows = fetch_year(hs, target_code, year, reporter, flow="X")
            imp_rows = fetch_year(hs, target_code, year, reporter, flow="M")
        export_value = sum(r.get("primaryValue") or 0 for r in exp_rows)
        import_value = sum(r.get("primaryValue") or 0 for r in imp_rows)
        # 数据准确性：合法空结果（无贸易记录）≠ 真实为 0——
        # 缺失时算 TC 会得到 ±1.0 极端值（假"完美竞争力"），一律置 None
        if not exp_rows or not imp_rows or (export_value <= 0 and import_value <= 0):
            tc = None
        else:
            tc = compute_tc(export_value, import_value)

        # 市场出口份额：目标市场该产品总进口（flow=M, partner=0 全球）
        # 组织目标：逐成员查"成员从全球进口"并求和（partner=0，非出口国对成员进口）
        if target_code in GROUP_MEMBERS:
            market_import_rows = fetch_group_world_imports(hs, year, target_code)
        else:
            market_import_rows = fetch_year(hs, "0", year, target, flow="M")
        market_import_value = sum(r.get("primaryValue") or 0 for r in market_import_rows)
        market_share = round(export_value / market_import_value * 100, 2) if market_import_value else None

        # 三态数据质量标记（数据准确性）：内部自洽检查
        # suspicious：出口申报额 > 目标市场总进口（份额>100%，数学不自洽，多为
        #   出口方申报与进口方申报口径差异——如德国转口/统计制度，需谨慎解读）
        quality = "valid"
        quality_note = ""
        if market_import_value and export_value > market_import_value:
            quality = "suspicious"
            quality_note = ("该贸易流出口方申报额大于目标市场总进口（份额超 100%），"
                            "存在镜像口径差异，数据可信度降低，相关结论需谨慎解读。")
        # DataGate 复核（v1.0.2）：若任一条腿被 REJECTED（完整性校验未过），
        # 整体降级为 rejected——宁缺勿错，报告层看到 quality 即知不可用
        # 注意：缓存以 reporter_code（数字代码）为键，这里用 AREA_MAP 转换
        _rep_code = AREA_MAP.get(reporter, "156")
        for _leg, _cmd, _partner, _flow in (
            ("出口", hs, target_code, "X"),
            ("进口", hs, target_code, "M"),
            ("市场总进口", hs, "0", "M"),
        ):
            gate = check_data_gate(_cmd, _partner, year, _flow, reporter_code=_rep_code,
                                   cache_key=("formal" if _use_formal() else "preview"))
            if gate["quality"] == "rejected":
                quality = "rejected"
                quality_note = f"{_leg}腿数据被拒绝：{gate['reason']}"
                break

        return {
            "tc": tc,
            "export_value": export_value,
            "import_value": import_value,
            "market_import_value": market_import_value,
            "market_share": market_share,  # 出口国占目标市场该产品进口的份额（%）
            "available": True,
            "quality": quality,      # valid / suspicious / invalid / rejected
            "quality_note": quality_note,
            # 计算审计（v1.0.2，调试用）：每个数字的来源 + 公式 + 血缘元数据，
            # 排查"这个数字为什么不对"时直接看这里，不用翻代码
            "_audit": {
                "product": product,
                "target": target,
                "year": year,
                "hs_code": hs,
                "reporter": reporter,
                "reporter_code": _rep_code,
                "tc_formula": f"TC = (X - M) / (X + M) = ({export_value:.0f} - {import_value:.0f}) / ({export_value:.0f} + {import_value:.0f}) = {tc}" if tc is not None else "TC = None（出口或进口数据缺失，防 ±1.0 假完美值）",
                "share_formula": f"份额 = 出口 / 市场总进口 = {export_value:.0f} / {market_import_value:.0f} = {market_share}%" if market_share is not None else "份额 = None（市场总进口缺失）",
                "legs": {
                    "export": check_data_gate(hs, target_code, year, "X", reporter_code=_rep_code,
                                              cache_key=("formal" if _use_formal() else "preview")),
                    "import": check_data_gate(hs, target_code, year, "M", reporter_code=_rep_code,
                                              cache_key=("formal" if _use_formal() else "preview")),
                    "market_import": check_data_gate(hs, "0", year, "M",
                                                     reporter_code=AREA_MAP.get(target, "156"),
                                                     cache_key=("formal" if _use_formal() else "preview")),
                },
            },
        }
    except Exception:
        # 静默失败红线：竞争力数据获取异常必须留痕，防止"系统坏了"伪装成"无数据"
        logging.exception("get_competitiveness 异常（%s/%s/%s）", product, target, year)
        return {}


def get_competitor_comparison(product: str, target: str, year: str,
                              competitors: list = None,
                              reporter: str = "中国") -> dict:
    """竞争对手出口对比：出口国 vs 同类主要出口国对目标市场的同类产品出口

    competitors 默认 [中国, 日本, 韩国, 越南]（消费电子主要出口国）；
    若出口国不在其中（如德国），自动加入并放在第一位，保证对比包含出口国自身。
    返回 {competitors: [{country, value, share}], available: bool}
    """
    if competitors is None:
        competitors = ["中国", "日本", "韩国", "越南"]
    # 出口国必须是竞争对手之一（否则对比表里没有出口国自身，占比失真）
    if reporter and reporter not in competitors:
        competitors = [reporter] + [c for c in competitors if c != reporter]
    try:
        hs = hs_lookup(product)
        if not hs:
            return {}
        # 结果缓存（HS+目标+年份+出口国+名单 → 对比结果），避免重复轮询多国 UN Comtrade
        # 版本签名 V1：未来改 share 计算/候选人名单时递增，旧缓存自动失效
        # 回归修复：缓存键含数据源模式（preview/formal 数据质量不同，切换后不得互用）；
        # 回归修复 P1-9：键含排序后的竞争对手名单（原键不含名单，main 动态传 top_names[:6]
        # 变化时命中旧对比，share 分母基于旧国家集合）
        mode_tag = "formal" if _use_formal() else "preview"
        comp_sig = "|".join(sorted(competitors))
        cache_k = f"{mode_tag}|V1|{target}|{reporter}|{comp_sig}"
        try:
            from database import get_cached
            cached = get_cached("COMPARE", hs, year, "X", "0", cache_key=cache_k, ttl_days=_ttl_for_period(year))
            if cached is not None and isinstance(cached, list):
                return {"competitors": cached, "available": True}
        except Exception:
            pass
        results = []
        total = 0
        target_code = partner_lookup(target)
        for country in competitors:
            try:
                # 组织目标：成员聚合（partner 用组代码 preview 不返回数据，B 类审查 #4）
                if target_code in GROUP_MEMBERS:
                    rows = fetch_group(hs, year, target_code, country)
                else:
                    rows = fetch_year(hs, target_code or "0", year, reporter=country)
                value = sum(r.get("primaryValue") or 0 for r in rows)
                results.append({"country": country, "value": value, "error": None})
                total += value
            except Exception as e:
                # 静默失败红线（v1.0.2）：数据获取失败 ≠ 该国出口为 0。
                # 记 error 标记该行不可用，前端/报告可显示"数据缺失"，绝不用 0 冒充
                logging.warning("竞争力对比 %s 数据获取失败: %s", country, e)
                results.append({"country": country, "value": None, "error": str(e)[:120]})
        # 份额诚实性（宁缺勿错）：任一国家数据获取失败时分母就不完整——
        # 此时给成功国算 share，等于把缺失国家的份额等比摊到剩下国家头上
        # （实测：英国失败被剔除后，中国 34% 被放大成 52%）。统一置 None，
        # 让前端/报告显示"数据缺失"而不是一个虚高的占比。
        has_error = any(r.get("error") for r in results)
        for r in results:
            if has_error:
                r["share"] = None
            else:
                r["share"] = round(r["value"] / total * 100, 1) if r["value"] is not None and total else None
        # 回归修复：有 error 行时不写缓存（瞬时失败不固化成"数据缺失"）；
        # 写缓存带血缘 source（P1-6）
        if not any(r.get("error") for r in results):
            try:
                from database import save_cache
                save_cache("COMPARE", hs, year, "X", results, "0", cache_key=cache_k,
                           source="uncomtrade/" + mode_tag)
            except Exception:
                pass
        return {"competitors": results, "available": True}
    except Exception:
        logging.exception("get_competitor_comparison 异常（%s/%s）", product, target)
        return {}


def get_competitiveness_matrix(product: str, target: str, years: list,
                               reporter: str = "中国") -> list:
    """竞争力矩阵：品类出口大国 × {出口额/份额/5年CAGR/单价/判断}

    对每个出口大国查其对目标市场的多年出口趋势（复用 query_trend + 缓存），
    程序计算 CAGR/单价/份额，判断列用 CAGR 阈值（非 AI，守住"AI 不参与算术"）。
    返回 [{country, export_value, market_share, cagr_pct, unit_price, verdict}]，失败返回 []。
    """
    try:
        # 结果缓存（HS+目标+完整年份序列+出口国 → 矩阵），避免每次查询重算多国多年。
        # key 用完整年份元组：中间年份不同（如 [2018,2019,2022] vs [2018,2020,2022]）
        # 不再命中同一缓存（B 类审查 #3）；含数据源模式（回归修复）
        mode_tag = "formal" if _use_formal() else "preview"
        cache_k = f"{mode_tag}|V1|{target}|{'-'.join(map(str, years))}|{reporter}"
        try:
            from database import get_cached
            cached = get_cached("MATRIX", "0", "0", "X", "0", cache_key=cache_k, ttl_days=_ttl_for_period(str(years[-1])))
            if cached is not None and isinstance(cached, list):
                return cached
        except Exception:
            pass
        # 出口大国名单（动态识别，复用 TOPEXP 缓存）
        top = get_top_exporters(product, str(years[-1]))
        top_names = [t["country"] for t in top]
        if reporter not in top_names:
            top_names = [reporter] + [n for n in top_names if n != reporter]
        top_names = top_names[:6]

        matrix = []
        failed = 0
        for country in top_names:
            try:
                hs, rows, trend = query_trend(product, target, years, reporter=country)
                if not trend or len(trend) < 3:
                    continue
                stats = summarize_stats(trend)
                # 份额：该国对目标市场出口 / 目标市场总进口（复用 get_competitiveness 逻辑）
                cmp = get_competitiveness(product, target, str(years[-1]), reporter=country)
                share = cmp.get("market_share")
                # 判断：程序阈值（CAGR + 份额）
                cagr = stats.get("cagr_pct")
                if cagr is None:
                    verdict = "数据不足"
                elif cagr > 10:
                    verdict = "快速上升"
                elif cagr > 3:
                    verdict = "稳步增长"
                elif cagr > -3:
                    verdict = "稳定"
                else:
                    verdict = "下降"
                # 单价：最新年份
                ups = stats.get("unit_prices") or []
                unit_price = ups[-1]["price"] if ups else None
                matrix.append({
                    "country": country,
                    "export_value": stats.get("last_value"),
                    "market_share": share,
                    "cagr_pct": cagr,
                    "unit_price": unit_price,
                    "verdict": verdict,
                })
            except Exception as e:
                # 失败国不再无声消失（原先 except: continue 连日志都没有，
                # 排查"为什么矩阵里没有 X 国"时无迹可寻）
                failed += 1
                logging.warning("竞争力矩阵 %s 数据获取失败: %s", country, e)
                continue
        matrix.sort(key=lambda x: (x["export_value"] or 0), reverse=True)
        # 残缺矩阵不得写缓存（与 get_top_exporters 同口径）：某国瞬时失败被固化，
        # 之后数周都命中这份缺国排名。失败时只返回当次结果，下次重新拉取
        if failed == 0:
            try:
                from database import save_cache
                save_cache("MATRIX", "0", "0", "X", matrix, "0", cache_key=cache_k,
                           source="uncomtrade/" + mode_tag)  # P1-6：血缘
            except Exception:
                pass
        return matrix
    except Exception:
        logging.exception("get_competitiveness_matrix 异常（%s/%s）", product, target)
        return []


def get_top_exporters(product: str, year: str, top_n: int = 6) -> list:
    """动态识别品类出口大国：候选出口国对该品类全球出口额排名

    UN Comtrade preview 不支持 reporterCode=0 的全球分组查询（返回空），
    改为轮询候选出口大国（消费电子主要出口国名单）对该品类的全球出口，
    按出口额降序取 TOP N。返回 [{country, value}]，失败返回 []。

    结果按 (HS, 年份) 缓存到 SQLite——首次轮询 16 国后，后续查询直接命中，
    避免每次贸易查询都打 16 次 UN Comtrade（免费版 429 限流下会显著拖慢）。
    """
    candidates = [
        "中国", "德国", "日本", "韩国", "越南", "美国",
        "英国", "荷兰", "意大利", "新加坡", "法国", "马来西亚",
        "泰国", "墨西哥", "波兰", "印度",
    ]
    try:
        hs = hs_lookup(product)
        if not hs:
            return []
        # 数据源模式标记（回归修复：缓存键区分 preview/formal；定义在 try 外避免
        # 内层缓存异常时写入分支 UnboundLocalError）
        mode_tag = "formal" if _use_formal() else "preview"
        # 先查缓存（HS+年份 → TOP 出口国），版本签名 V1：改候选人名单/计算时递增；
        # 回归修复：缓存键含数据源模式（preview/formal 排名质量不同）
        try:
            from database import get_cached
            cached = get_cached("TOPEXP", hs, year, "X", "0", cache_key=f"{mode_tag}|V1|rank",
                                ttl_days=_ttl_for_period(year))
            if cached is not None and isinstance(cached, list):
                return cached[:top_n]
        except Exception:
            pass
        results = []
        failed = 0
        for country in candidates:
            try:
                rows = fetch_year(hs, "0", year, reporter=country, flow="X")
                value = sum(r.get("primaryValue") or 0 for r in rows)
                if value > 0:
                    results.append({"country": country, "value": value})
            except Exception as e:
                # 回归修复：失败不再静默——记录数量，残缺排名不得写缓存
                failed += 1
                logging.warning("TOP 出口国轮询 %s 失败: %s", country, e)
        results.sort(key=lambda x: x["value"], reverse=True)
        top = results[:top_n]
        # 回归修复：有失败国时只返回不缓存（瞬时网络失败会变成数周错误排名）；
        # 全部成功才持久化（缓存键含数据源模式 + 血缘 source）
        if failed == 0:
            try:
                from database import save_cache
                save_cache("TOPEXP", hs, year, "X", top, "0", cache_key=f"{mode_tag}|V1|rank",
                           source="uncomtrade/" + mode_tag)
            except Exception:
                pass
        elif failed > len(candidates) // 2:
            # 过半失败：结果不可信，整体降级（宁缺勿错）
            logging.error("TOP 出口国轮询失败 %d/%d 国，返回空（宁缺勿错）", failed, len(candidates))
            return []
        return top
    except Exception:
        logging.exception("get_top_exporters 异常（%s/%s）", product, year)
        return []


def get_destination_ranking(product: str, target: str, year: str,
                            reporter: str = "中国") -> dict:
    """出口目的地排名：目标市场（如欧盟）内部各国进口该产品排名

    返回 {destinations: [{country, value, share}], available: bool}
    """
    try:
        hs = hs_lookup(product)
        if not hs:
            return {}
        target_code = partner_lookup(target)
        if target_code not in GROUP_MEMBERS:
            return {}  # 仅对组织（欧盟/东盟/RCEP）有效
        members = GROUP_MEMBERS[target_code]
        results = []
        total = 0
        failed = 0
        for country in members:
            code = AREA_MAP.get(country, "")
            if not code:
                continue
            try:
                rows = fetch_year(hs, code, year, reporter=reporter)
                value = sum(r.get("primaryValue") or 0 for r in rows)
                results.append({"country": country, "value": value})
                total += value
            except Exception as e:
                # 回归修复：失败不再静默——残缺排名会误导"哪些国家是大市场"
                failed += 1
                logging.warning("目的地排名 %s 查询失败: %s", country, e)
        results.sort(key=lambda x: x["value"], reverse=True)
        for r in results:
            r["share"] = round(r["value"] / total * 100, 1) if total else 0
        if failed:
            return {"destinations": results[:10], "available": False,
                    "message": f"{failed} 个成员国数据缺失，排名不完整"}
        return {"destinations": results[:10], "available": True}
    except Exception:
        logging.exception("get_destination_ranking 异常（%s/%s）", product, target)
        return {}


def _parse_years(arg: str) -> list:
    """解析年份参数：'2022' / '2020-2022' / '2018,2020,2022'"""
    arg = arg.strip()
    if "-" in arg:
        start, end = arg.split("-")
        return list(range(int(start), int(end) + 1))
    return [int(y) for y in arg.split(",") if y.strip().isdigit()]


if __name__ == "__main__":
    # 用法: python trade.py 蓝牙耳机 德国 2022 | 2020-2022 | 2018,2020,2022
    if len(sys.argv) < 4:
        print("用法: python trade.py <产品名或HS编码> <国家/组织> <年份或范围>")
        sys.exit(1)

    product_arg, target_arg, year_arg = sys.argv[1], sys.argv[2], sys.argv[3]
    years = _parse_years(year_arg)
    if not years:
        print(f"错误: 无法解析年份「{year_arg}」，支持 2022 / 2020-2022 / 2018,2020,2022")
        sys.exit(1)

    try:
        if len(years) == 1:
            hs_code, data = query_trade(product_arg, target_arg, str(years[0]))
            trend = summarize_trend(data)
        else:
            hs_code, data, trend = query_trend(product_arg, target_arg, years)
        total_value = sum(r.get("primaryValue") or 0 for r in data)
        total_wgt = sum(r.get("netWgt") or 0 for r in data)
        print(f"\n=== {product_arg}(HS{hs_code}) 中国出口 {target_arg} {year_arg} ===")
        print(f"记录数: {len(data)}")
        print(f"贸易总额: {total_value:,.0f} 美元")
        print(f"总净重: {total_wgt:,.0f} 公斤")
        print("逐年趋势:")
        for y, v in trend.items():
            print(f"  {y}: {v['value']:,.0f} 美元 | {v['weight']:,.0f} 公斤")
        for r in data[:5]:
            print(f"  {r.get('cmdDesc') or 'N/A'} | {r.get('partnerDesc')} | {r.get('primaryValue') or 0:,.0f} 美元")
    except ValueError as e:
        print(f"错误: {e}")
        sys.exit(1)
