# -*- coding: utf-8 -*-
"""直接调 UN Comtrade preview API 验证真实贸易额（绕过缓存）"""
import requests

BASE = "https://comtradeapi.un.org/public/v1/preview/C/A/HS"

def query(reporter, partner, flow, period="2024", cmd="8525"):
    params = {
        "reporterCode": reporter, "period": period, "partnerCode": partner,
        "cmdCode": cmd, "flowCode": flow, "maxRecords": 500,
    }
    r = requests.get(BASE, params=params, headers={"Accept": "application/json"}, timeout=60)
    data = r.json().get("data", []) if r.status_code == 200 else []
    return r.status_code, data

# 1. 中国从全球进口 8525（reporter=156, partner=0, flow=M）
code, data = query("156", "0", "M")
print(f"中国从全球进口 8525/2024: HTTP {code}, {len(data)} 条")
if data:
    print("  前3条:", [(d.get('partnerCode'), d.get('primaryValue')) for d in data[:3]])

# 2. 德国对华出口 8525（reporter=276, partner=156, flow=X）
code, data = query("276", "156", "X")
print(f"德国对华出口 8525/2024: HTTP {code}, {len(data)} 条")
if data:
    from collections import Counter
    keys = Counter((d.get('cmdCode'), d.get('partnerCode'), d.get('motCode'), d.get('mosCode')) for d in data)
    print("  去重前记录数:", len(data), "唯一组合数:", len(keys))
    print("  去重前总额:", sum(d.get('primaryValue') or 0 for d in data) / 1e8, "亿美元")
    seen = set()
    uniq = []
    for d in data:
        k = (d.get('cmdCode'), d.get('partnerCode'), d.get('motCode'), d.get('mosCode'), d.get('refPeriodId'))
        if k not in seen:
            seen.add(k)
            uniq.append(d)
    print("  去重后记录数:", len(uniq), "去重后总额:", sum(d.get('primaryValue') or 0 for d in uniq) / 1e8, "亿美元")
    # 打印每条去重记录
    for d in uniq:
        print(f"    cmd={d.get('cmdCode')} mot={d.get('motCode')} mos={d.get('mosCode')} value={d.get('primaryValue')/1e8:.2f}亿")
