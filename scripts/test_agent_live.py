# -*- coding: utf-8 -*-
"""Agent SSE 流式实测"""
import json, urllib.request, time

req = urllib.request.Request(
    "http://127.0.0.1:8004/api/agent/run",
    data=json.dumps({"input": "蓝牙耳机去德国卖"}, ensure_ascii=False).encode("utf-8"),
    headers={"Content-Type": "application/json"}, method="POST")
t0 = time.time()
with urllib.request.urlopen(req, timeout=240) as r:
    print(f"HTTP {r.status} media={r.headers.get('Content-Type')}")
    buf = b""
    results = []
    while True:
        chunk = r.read(512)
        if not chunk:
            break
        buf += chunk
        while b"\n\n" in buf:
            part, buf = buf.split(b"\n\n", 1)
            line = part.decode("utf-8", "replace").strip()
            if line.startswith("data: "):
                ev = json.loads(line[6:])
                if ev["type"] == "progress":
                    icon = {"running": "⏳", "done": "✅", "skipped": "⚠️"}[ev["status"]]
                    print(f"  [{time.time()-t0:5.1f}s] {icon} 步骤{ev['step']+1}/{ev['total']} {ev['title']} {ev.get('detail','')[:40]}")
                elif ev["type"] == "result":
                    results.append(ev)
                    print(f"  [{time.time()-t0:5.1f}s] RESULT: {ev['summary']}")
                    print(f"    报告长度: {len(ev['report'])} | 线索数: {len(ev['leads'])} | 开发信: {'有' if ev['outreach'] else '无'}")
                elif ev["type"] == "error":
                    print(f"  ERROR: {ev['detail']}")
    print(f"总耗时 {time.time()-t0:.1f}s")
    if not results:
        print("FAIL: 未收到 result 事件")
