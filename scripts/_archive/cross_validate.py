# -*- coding: utf-8 -*-
"""交叉验证 UN Comtrade 数据清洗正确性：
镜像验证：中国从德国进口(M流) ≈ 德国对华出口(X流)，FOB/CIF 差 5-10% 属正常。
同时验证"mot=0 合计行"假设是否正确。
"""
import requests
from collections import Counter

BASE = "https://comtradeapi.un.org/public/v1/preview/C/A/HS"

def query(reporter, partner, flow, period="2024", cmd="8525"):
    params = {"reporterCode": reporter, "period": period, "partnerCode": partner,
              "cmdCode": cmd, "flowCode": flow, "maxRecords": 500}
    r = requests.get(BASE, params=params, headers={"Accept": "application/json"}, timeout=60)
    return r.status_code, (r.json().get("data", []) if r.status_code == 200 else [])

def analyze(rows, label):
    print(f"\n=== {label}：{len(rows)} 条原始记录 ===")
    if not rows:
        print("  （无数据）")
        return
    # 按 mot 统计
    mot_sum = Counter()
    for d in rows:
        mot_sum[d.get("motCode")] += d.get("primaryValue") or 0
    for mot, v in sorted(mot_sum.items(), key=lambda x: str(x[0])):
        print(f"  mot={mot}: {v/1e8:.4f} 亿美元")
    # 唯一键数（按我的去重键）
    seen = set()
    for d in rows:
        seen.add((d.get("reporterCode"), d.get("partnerCode"), d.get("cmdCode"),
                  d.get("period"), d.get("motCode"), d.get("mosCode"), d.get("customsCode")))
    print(f"  去重后唯一记录数: {len(seen)}（原始 {len(rows)}）")
    total = sum(d.get("primaryValue") or 0 for d in rows)
    print(f"  原始 sum: {total/1e8:.4f} 亿美元")

# 1. 德国对华出口（X 流，此前清洗的数据）
analyze(query("276", "156", "X")[1], "德国对华出口 X流（reporter=276, partner=156）")
# 2. 中国从德国进口（M 流，镜像验证）
analyze(query("156", "276", "M")[1], "中国从德国进口 M流（reporter=156, partner=276）——镜像")
# 3. 中国从全球进口（分母）
code, data = query("156", "0", "M")
print(f"\n=== 中国从全球进口 8525（分母）=== {len(data)} 条")
for d in data:
    print(f"  partner={d.get('partnerCode')} value={d.get('primaryValue')/1e8:.4f} 亿美元")
