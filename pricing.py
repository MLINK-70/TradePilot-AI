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


def _num(v):
    """数值兜底（回归修复：UN 脏数据行 primaryValue 可能是 'N/A'/带逗号字符串，
    直接参与 sum 会 TypeError 被吞成"获取失败"）"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _unit_price(rows: list):
    """出口/进口单价（美元/公斤）：总额 / 总净重；无净重返回 None"""
    value = sum(_num(r.get("primaryValue")) for r in rows)
    weight = sum(_num(r.get("netWgt")) for r in rows)
    if value <= 0 or weight <= 0:
        return None
    return value / weight


def suggest_pricing(product: str, market: str, year: str = "",
                    reporter: str = "中国") -> dict:
    """主入口：产品 + 目标市场 → 建议定价区间（数据驱动，可追溯）

    返回 {available, export_unit_price, market_unit_price,
          suggest_low, suggest_mid, suggest_high, explain, _audit}
    """
    from trade import (AREA_MAP, GROUP_MEMBERS, fetch_group, fetch_group_world_imports,
                       fetch_year, get_latest_year, hs_lookup, partner_lookup)
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

        # 两条腿：出口单价（出口国出口该市场）+ 市场进口均价（该市场从全球进口）
        # 回归修复 G7：组织市场（欧盟/东盟/RCEP）preview 接口对组代码不返回数据，
        # 必须走成员聚合（fetch_group/fetch_group_world_imports），否则两腿皆空误导用户
        is_group = target_code in GROUP_MEMBERS
        if is_group:
            exp_rows = fetch_group(hs, year, target_code, reporter=reporter, flow="X")
            imp_rows = fetch_group_world_imports(hs, year, target_code)
        else:
            exp_rows = fetch_year(hs, target_code, year, reporter=reporter, flow="X")
            imp_rows = fetch_year(hs, "0", year, reporter=market, flow="M")

        # 回归修复 P1-10：任一腿 REJECTED（完整性校验未过）→ 整体不可用。
        # 原实现把 REJECTED 空腿当"缺失"，用另一腿算价格带——违反本模块
        # docstring"任一腿 REJECTED → 可用=False"的承诺
        mode_key = "formal" if _use_formal() else "preview"
        legs_meta = (
            ("出口腿", hs, target_code, "X" if not is_group else "X",
             AREA_MAP.get(reporter, "156")),
            ("市场进口腿", hs, "0" if not is_group else target_code,
             "M" if not is_group else "MW",
             AREA_MAP.get(market, "") if not is_group else "0"),
        )
        for leg_name, _cmd, _partner, _flow, _rep in legs_meta:
            try:
                meta = get_cache_meta(_cmd, _partner, year, _flow, _rep, cache_key=mode_key)
            except Exception:
                meta = None
            if meta and meta["quality"] == "rejected":
                # 键名修正：get_cache_meta 返回 validation_reason（无 reason 键），
                # 原写法 meta['reason'] 命中即 KeyError → 被外层 except 吞成
                # 通用文案"定价数据获取失败"，G10 的"具体原因透出"失效
                return {"available": False,
                        "reason": f"{leg_name}数据被拒绝（完整性校验未通过）：{meta['validation_reason']}"}

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
            # 回归修复：数据异常时区间可能反转（出口单价远高于市场均价），
            # 此时输出反转区间会误导；改为以市场价为锚并显式标注异常
            if suggest_low > suggest_high:
                suggest_low = round(market_up * 0.8, 2)
                suggest_high = round(market_up * 1.2, 2)
                basis = "市场进口均价（出口单价异常高于市场价，区间已按市场价带修正）"
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

        # 回归修复：单腿缺失（export_up/market_up 为 None）时格式化会崩（None:.2f），
        # 文案条件化显示"—"，让"只有一条腿"是正常降级而非"获取失败"
        exp_txt = f"{export_up:.2f}" if export_up is not None else "—"
        mkt_txt = f"{market_up:.2f}" if market_up is not None else "—"
        explain = (
            # 回归修复 G10：出口国文案用 reporter 变量（原硬编码"中国"，reporter≠中国时输出错误）
            f"基于 {year} 年 UN Comtrade 真实贸易数据：{reporter}出口{market}该品类"
            f"（HS {hs}）单价约 {exp_txt} 美元/公斤，{market} 市场进口均价约 "
            f"{mkt_txt} 美元/公斤。建议定价区间 {suggest_low:.2f}–{suggest_high:.2f} 美元/公斤"
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
            "unit": "USD/kg",  # 单位绑定（防 USD/kg 与 USD 混用——贸易数据产品的隐藏雷）
            "export_unit_price": export_up,
            "market_unit_price": market_up,
            "suggest_low": suggest_low,
            "suggest_mid": suggest_mid,
            "suggest_high": suggest_high,
            "basis": basis,
            "explain": explain,
            "_audit": {"legs": legs, "reporter": reporter, "market": market},
        }
    except ValueError as e:
        # 回归修复 G10：UN 查询失败/数据被拒绝的根因不再被吞成通用文案
        # （429 耗尽/REJECTED 的具体原因透出给用户）
        return {"available": False, "reason": str(e)}
    except Exception:
        logging.exception("定价建议计算异常: %s / %s", product, market)
        return {"available": False, "reason": "定价数据获取失败"}


def _use_formal() -> bool:
    from trade import _use_formal as _f
    return _f()
