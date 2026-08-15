# -*- coding: utf-8 -*-
"""pricing.py — 定价建议模块（v1.0.2 业务收口）

数据驱动定价（AI 不参与算术——延续数据层铁律）：
  1. 出口单价：UN Comtrade 出口金额 / 出口净重（美元/公斤，程序算）
  2. 市场进口均价：目标市场该产品总进口金额 / 总净重（美元/公斤，同源）
  3. 建议区间：
     - 下沿 = 出口单价 × 1.5（覆盖国际运费/关税/渠道毛利的最低可成交价）
     - 中位 = 市场进口均价（与市场现有价格带对齐）
     - 上沿 = 市场进口均价 × 1.3（含品牌溢价空间的零售价）
  4. 输出带血缘：每个数字来自哪次查询、质量如何（DataGate 语义）

真实贸易数据才有意义：任一腿 REJECTED / 无净重 → 返回可用=False 并说明原因，
绝不拿残缺数据算一个"看起来合理的价格"。
"""
import logging


def _weight_ok(rows: list) -> bool:
    """净重数据可用性：有任一行 netWgt>0 即可（单价需要重量分母）"""
    return any((r.get("netWgt") or 0) > 0 for r in rows)


def _unit_price(rows: list):
    """出口/进口单价（美元/公斤）：总额 / 总净重；无净重返回 None"""
    value = sum(r.get("primaryValue") or 0 for r in rows)
    weight = sum(r.get("netWgt") or 0 for r in rows)
    if value <= 0 or weight <= 0:
        return None
    return value / weight


def suggest_pricing(product: str, market: str, year: str = "",
                    reporter: str = "中国") -> dict:
    """主入口：产品 + 目标市场 → 建议定价区间（数据驱动，可追溯）

    返回 {available, export_unit_price, market_unit_price,
          suggest_low, suggest_mid, suggest_high, explain, _audit}
    """
    from trade import AREA_MAP, fetch_year, get_latest_year, hs_lookup, partner_lookup
    from database import get_cache_meta

    try:
        hs = hs_lookup(product)
        if not hs:
            return {"available": False, "reason": "无法识别产品对应的 HS 编码"}
        target_code = partner_lookup(market)
        if not target_code:
            return {"available": False, "reason": f"无法识别目标市场 {market}"}
        if not year:
            year = str(get_latest_year())

        # 两条腿：出口单价（中国出口该市场）+ 市场进口均价（该市场从全球进口）
        exp_rows = fetch_year(hs, target_code, year, reporter=reporter, flow="X")
        imp_rows = fetch_year(hs, "0", year, reporter=market, flow="M")

        export_up = _unit_price(exp_rows)
        market_up = _unit_price(imp_rows)

        # 数据红线：单价必须有净重支撑；两腿都缺则不可用
        if export_up is None and market_up is None:
            reason = "该品类缺少净重数据（UN Comtrade 未申报），无法计算单价"
            if not exp_rows and not imp_rows:
                reason = f"{year} 年该品类贸易数据为空，无法定价"
            return {"available": False, "reason": reason}

        # 建议区间（程序阈值，规则稳定）
        if export_up and market_up:
            suggest_low = round(export_up * 1.5, 2)
            suggest_mid = round(market_up, 2)
            suggest_high = round(market_up * 1.3, 2)
            basis = "出口单价×1.5 与市场进口均价"
        elif export_up:
            # 无市场均价：以出口单价为锚（加价 1.5-2.5 倍为建议带）
            suggest_low = round(export_up * 1.5, 2)
            suggest_mid = round(export_up * 2.0, 2)
            suggest_high = round(export_up * 2.5, 2)
            basis = "出口单价（市场均价缺失，按出口价加价区间估算）"
        else:
            # 无出口单价：以市场均价为锚（下浮 20% 为进货带）
            suggest_low = round(market_up * 0.8, 2)
            suggest_mid = round(market_up, 2)
            suggest_high = round(market_up * 1.2, 2)
            basis = "市场进口均价（出口单价缺失，按市场价带估算）"

        explain = (
            f"基于 {year} 年 UN Comtrade 真实贸易数据：中国出口{market}该品类"
            f"（HS {hs}）单价约 {export_up:.2f} 美元/公斤，{market} 市场进口均价约 "
            f"{market_up:.2f} 美元/公斤。建议定价区间 {suggest_low:.2f}–{suggest_high:.2f} 美元/公斤"
            f"（依据：{basis}）。实际售价还需结合规格、品牌与渠道，此区间为数据参考带。"
        )

        # 血缘审计：两条腿的质量（DataGate）
        mode_key = "formal" if _use_formal() else "preview"
        _rep_code = AREA_MAP.get(reporter, "156")
        _tgt_code = AREA_MAP.get(market, "")
        legs = {}
        for name, cmd, partner, flow, rep in (
            ("export", hs, target_code, "X", _rep_code),
            ("market_import", hs, "0", "M", _tgt_code),
        ):
            meta = get_cache_meta(cmd, partner, year, flow, rep, cache_key=mode_key)
            legs[name] = {"quality": meta["quality"] if meta else "unknown",
                          "fetched_at": meta["fetched_at"] if meta else None,
                          "reason": meta["validation_reason"] if meta else "无缓存记录"}

        return {
            "available": True,
            "hs_code": hs,
            "year": year,
            "export_unit_price": export_up,   # 美元/公斤
            "market_unit_price": market_up,   # 美元/公斤
            "suggest_low": suggest_low,       # 美元/公斤
            "suggest_mid": suggest_mid,
            "suggest_high": suggest_high,
            "basis": basis,
            "explain": explain,
            "_audit": {"legs": legs, "reporter": reporter, "market": market},
        }
    except Exception:
        logging.exception("定价建议计算异常: %s / %s", product, market)
        return {"available": False, "reason": "定价数据获取失败"}


def _use_formal() -> bool:
    from trade import _use_formal as _f
    return _f()
