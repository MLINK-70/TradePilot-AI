# -*- coding: utf-8 -*-
"""验证正确聚合逻辑：customs=C00 + mot=0，并对照干净数据"""
import requests

KEY = "390f1b74dc73425281d3928b4e0bf7cd"
BASE = "https://comtradeapi.un.org/data/v1/get/C/A/HS"
H = {"Ocp-Apim-Subscription-Key": KEY, "Accept": "application/json"}

def fetch(reporter, partner, flow, cmd, period="2024"):
    r = requests.get(BASE, params={"reporterCode": reporter, "partnerCode": partner,
                                   "period": period, "cmdCode": cmd, "flowCode": flow},
                     headers=H, timeout=60)
    return r.json().get("data", []) if r.status_code == 200 else []

def aggregate(data):
    """正确聚合：去重后取 customs=C00 且 mot=0 的记录"""
    seen = set()
    uniq = []
    for d in data:
        k = (d.get("cmdCode"), d.get("motCode"), d.get("mosCode"), d.get("customsCode"))
        if k in seen:
            continue
        seen.add(k)
        uniq.append(d)
    # customs=C00（总计）优先；mot=0（全部运输）
    total_rows = [d for d in uniq if str(d.get("customsCode")) == "C00" and str(d.get("motCode")) == "0"]
    if not total_rows:
        total_rows = [d for d in uniq if str(d.get("customsCode")) == "C00"]
    return sum(d.get("primaryValue") or 0 for d in total_rows), len(uniq)

cases = [
    ("德国→中国 8525 X (脏案例)", "276", "156", "8525", "X"),
    ("中国→德国 8518 X (干净案例)", "156", "276", "8518", "X"),
    ("日本→中国 8525 X", "392", "156", "8525", "X"),
    ("中国从全球进口 8525 M (分母)", "156", "0", "8525", "M"),
    ("德国从全球进口 8518 M", "276", "0", "8518", "M"),
]
for label, rep, par, cmd, flow in cases:
    data = fetch(rep, par, flow, cmd)
    total, uniq = aggregate(data)
    print(f"{label}: 原始{len(data)}条 -> 去重{uniq}条 -> 聚合 {total/1e8:.4f} 亿美元")

print()
print("=== 德国→中国 8525 的 customs/mot 完整矩阵（去重后）===")
data = fetch("276", "156", "8525", "X")
seen = set()
for d in data:
    k = (d.get("customsCode"), d.get("motCode"))
    if k in seen:
        continue
    seen.add(k)
    print(f"  customs={d.get('customsCode')} mot={d.get('motCode')}: {d.get('primaryValue')/1e8:.4f} 亿")
