# -*- coding: utf-8 -*-
"""测试 UN Comtrade 正式 API：key 有效性 + 德国 8525 案例数据质量"""
import requests

KEYS = [
    "3d040c7ea5c045ca99df538bf20cc9ab",
    "390f1b74dc73425281d3928b4e0bf7cd",
]

# 正式 API 端点（新版）
BASE_FORMAL = "https://comtradeapi.un.org/data/v1/C/A/HS"

def test_formal(key):
    headers = {"Ocp-Apim-Subscription-Key": key, "Accept": "application/json"}
    # 德国对华出口 8525（之前 preview 脏数据案例）
    params = {"reporterCode": "276", "partnerCode": "156", "period": "2024",
              "cmdCode": "8525", "flowCode": "X", "motCode": 0}
    try:
        r = requests.get(BASE_FORMAL, params=params, headers=headers, timeout=60)
        print(f"\n=== key ...{key[-6:]} 正式API 德国→中国 8525 X流 (motCode=0) ===")
        print(f"  HTTP {r.status_code}")
        if r.status_code == 200:
            data = r.json().get("data", [])
            total = sum(d.get("primaryValue") or 0 for d in data)
            print(f"  记录数: {len(data)}, 总额: {total/1e8:.4f} 亿美元")
            for d in data[:3]:
                print(f"    mot={d.get('motCode')} motDesc={d.get('motDesc')} value={d.get('primaryValue')/1e8:.4f}")
        else:
            print("  响应:", r.text[:200])
    except Exception as e:
        print(f"  错误: {e}")

# 也测不带 motCode（全部运输方式合计）
def test_no_mot(key):
    headers = {"Ocp-Apim-Subscription-Key": key, "Accept": "application/json"}
    params = {"reporterCode": "276", "partnerCode": "156", "period": "2024",
              "cmdCode": "8525", "flowCode": "X"}
    r = requests.get(BASE_FORMAL, params=params, headers=headers, timeout=60)
    print(f"\n=== key ...{key[-6:]} 正式API 德国→中国 8525 X流 (无mot筛选) ===")
    print(f"  HTTP {r.status_code}")
    if r.status_code == 200:
        data = r.json().get("data", [])
        total = sum(d.get("primaryValue") or 0 for d in data)
        print(f"  记录数: {len(data)}, 总额: {total/1e8:.4f} 亿美元")
    else:
        print("  响应:", r.text[:200])

for k in KEYS:
    test_formal(k)
    test_no_mot(k)
