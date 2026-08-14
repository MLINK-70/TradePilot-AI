# -*- coding: utf-8 -*-
import json, urllib.request

def post(path, payload, timeout=120):
    req = urllib.request.Request(
        "http://127.0.0.1:8003" + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8") or "{}")

s, j = post("/api/leads/search", {"product": "蓝牙耳机", "country": "德国"})
print(f"HTTP {s}")
if s == 200:
    leads = j.get("leads", [])
    print(f"线索数: {len(leads)}")
    for ld in leads[:4]:
        print(f"  - {ld['company']} | {ld['business_scope'][:50]} | {ld['source_url']}")
    if leads:
        s2, j2 = post("/api/leads/outreach", {"product": "蓝牙耳机", "country": "德国", "lead": leads[0]})
        print(f"开发信 HTTP {s2}")
        if s2 == 200:
            print(f"  主题: {j2.get('subject')}")
            print(f"  正文前80字: {j2.get('body','')[:80]}...")
            print(f"  要点: {j2.get('zh_notes', [])[:2]}")
        else:
            print(f"  失败: {j2}")
else:
    print(f"失败: {j}")
