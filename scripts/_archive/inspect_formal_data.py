# -*- coding: utf-8 -*-
"""打印正式 API 德国→中国 8525 完整记录，搞清数据结构与正确聚合方式"""
import json
import requests

KEY = "390f1b74dc73425281d3928b4e0bf7cd"
BASE = "https://comtradeapi.un.org/data/v1/get/C/A/HS"

headers = {"Ocp-Apim-Subscription-Key": KEY, "Accept": "application/json"}
params = {"reporterCode": "276", "partnerCode": "156", "period": "2024",
          "cmdCode": "8525", "flowCode": "X"}
r = requests.get(BASE, params=params, headers=headers, timeout=60)
data = r.json().get("data", [])
print(f"HTTP {r.status_code}，{len(data)} 条记录")
print("字段名:", list(data[0].keys()) if data else "空")
print()

# 打印每条记录的关键维度字段
for i, d in enumerate(data):
    print(f"[{i}] cmd={d.get('cmdCode')} mot={d.get('motCode')} mos={d.get('mosCode')} "
          f"customs={d.get('customsCode')} period={d.get('period')} refYear={d.get('refYear')} "
          f"value={d.get('primaryValue')/1e8:.4f}亿 isAggregate={d.get('isAggregate')} aggrLevel={d.get('aggrLevel')}")

print()
# 按维度组合统计
from collections import defaultdict
by_key = defaultdict(float)
by_key_cnt = defaultdict(int)
for d in data:
    k = (d.get('cmdCode'), d.get('motCode'), d.get('mosCode'), d.get('customsCode'))
    by_key[k] += d.get('primaryValue') or 0
    by_key_cnt[k] += 1
print("=== 按 (cmd, mot, mos, customs) 分组 ===")
for k, v in sorted(by_key.items(), key=lambda x: str(x[0])):
    print(f"  cmd={k[0]} mot={k[1]} mos={k[2]} customs={k[3]}: {by_key_cnt[k]}条 {v/1e8:.4f}亿")
