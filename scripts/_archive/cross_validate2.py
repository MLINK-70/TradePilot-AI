# -*- coding: utf-8 -*-
"""镜像对照：判断 X 流 vs M 流哪个可靠（进口方申报通常更权威）"""
import requests

BASE = "https://comtradeapi.un.org/public/v1/preview/C/A/HS"

def q(reporter, partner, flow, cmd, period="2024"):
    params = {"reporterCode": reporter, "period": period, "partnerCode": partner,
              "cmdCode": cmd, "flowCode": flow, "maxRecords": 500}
    try:
        r = requests.get(BASE, params=params, headers={"Accept": "application/json"}, timeout=60)
        data = r.json().get("data", []) if r.status_code == 200 else []
        total = sum(d.get("primaryValue") or 0 for d in data)
        return r.status_code, len(data), total
    except Exception as e:
        return "ERR", 0, str(e)

def pair(desc, x_reporter, x_partner, m_reporter, m_partner, cmd):
    sx = q(x_reporter, x_partner, "X", cmd)
    sm = q(m_reporter, m_partner, "M", cmd)
    print(f"\n{desc} (HS {cmd})")
    print(f"  X流 出口方申报: HTTP={sx[0]} 条数={sx[1]} 总额={sx[2]/1e8:.4f} 亿美元")
    print(f"  M流 进口方申报: HTTP={sm[0]} 条数={sm[1]} 总额={sm[2]/1e8:.4f} 亿美元")
    if isinstance(sx[2], float) and isinstance(sm[2], float) and sm[2] > 0:
        print(f"  差异: X/M = {sx[2]/sm[2]:.2f}x")

# 1. 蓝牙耳机 8518：中国对德出口 vs 德国从中国进口（主报告数据）
pair("蓝牙耳机 中国→德国", "156", "276", "276", "156", "8518")
# 2. 日本对华相机出口 vs 中国从日本进口
pair("相机 日本→中国", "392", "156", "156", "392", "8525")
# 3. 中国从全球进口 8525（分母，重试）
print("\n=== 中国从全球进口 8525（分母）===")
print("  ", q("156", "0", "M", "8525"))
