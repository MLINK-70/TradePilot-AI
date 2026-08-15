# -*- coding: utf-8 -*-
"""重测：正确正式端点 data/v1/get + key"""
import requests

KEYS = ["3d040c7ea5c045ca99df538bf20cc9ab", "390f1b74dc73425281d3928b4e0bf7cd"]
BASE = "https://comtradeapi.un.org/data/v1/get/C/A/HS"

def test(key, label):
    for hdr_name in ("Ocp-Apim-Subscription-Key", "subscription-key"):
        headers = {hdr_name: key, "Accept": "application/json"}
        params = {"reporterCode": "276", "partnerCode": "156", "period": "2024",
                  "cmdCode": "8525", "flowCode": "X"}
        try:
            r = requests.get(BASE, params=params, headers=headers, timeout=60)
            print(f"[{label}] {hdr_name}: HTTP {r.status_code}")
            if r.status_code == 200:
                data = r.json().get("data", [])
                total = sum(d.get("primaryValue") or 0 for d in data)
                print(f"  记录 {len(data)} 条, 总额 {total/1e8:.4f} 亿美元")
                for d in data[:4]:
                    print(f"    mot={d.get('motCode')} desc={d.get('motDesc')} value={d.get('primaryValue')/1e8:.4f} 亿")
                return  # 成功就停
            elif r.status_code != 404:
                print("  ", r.text[:150])
        except Exception as e:
            print(f"  {hdr_name}: 错误 {e}")

test(KEYS[0], "key1")
test(KEYS[1], "key2")
