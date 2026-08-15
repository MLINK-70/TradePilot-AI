# -*- coding: utf-8 -*-
import requests
import time

BASE = "https://comtradeapi.un.org/public/v1/preview/C/A/HS"

def q(reporter, partner, flow, cmd):
    for i in range(5):
        r = requests.get(BASE, params={"reporterCode": reporter, "period": "2024",
                                       "partnerCode": partner, "cmdCode": cmd,
                                       "flowCode": flow, "maxRecords": 500},
                         headers={"Accept": "application/json"}, timeout=60)
        if r.status_code == 200:
            data = r.json().get("data", [])
            total = sum(d.get("primaryValue") or 0 for d in data) / 1e8
            return f"{len(data)}条 {total:.2f}亿美元"
        time.sleep(10)
    return "限流"

print("德国从全球进口 8518 (M):", q("276", "0", "M", "8518"))
print("中国从全球进口 8518 (M):", q("156", "0", "M", "8518"))
print("德国从全球进口 8525 (M):", q("276", "0", "M", "8525"))
print("中国从全球进口 8525 (M):", q("156", "0", "M", "8525"))
