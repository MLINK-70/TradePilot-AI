"""build_sample_reviews.py - 从 McAuley Amazon Reviews 2023 抽取多品类真实评论样本

工具脚本（属毕设一，不打包进发行版），生成 D:/毕设一/data/samples/ 下的品类样本库，
供评论分析模块演示用。数据源合法公开（学术界标准研究数据集 McAuley-Lab/Amazon-Reviews-2023），
非爬虫、不登录、不触发风控。

原理：直连国内镜像 hf-mirror.com，流式逐行读取品类 jsonl，命中关键词的评论按 helpful 票数取前 N。
绕过 datasets/huggingface_hub 的 Hub 连接（国内 huggingface.co 不稳，镜像可达且 sandbox 内可达）。

用法（在毕设一 venv 里跑）：
  python scripts/build_sample_reviews.py
单品类失败不中断，跑完汇总成功/失败清单并写 index.json。
"""
import json
import os
import sys
import time

import requests

# Windows 控制台默认 GBK，emoji/中文混合输出易 UnicodeEncodeError；强制 utf-8
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

MIRROR = "https://hf-mirror.com"
REPO = "McAuley-Lab/Amazon-Reviews-2023"
BASE_URL = f"{MIRROR}/datasets/{REPO}/resolve/main/raw/review_categories"

SAMPLES_DIR = r"D:\毕设一\data\samples"
PER_CATEGORY = 40       # 每品类抽取条数
MAX_SCAN = 20000        # 单品类最多扫描多少条（防无限扫描，命中即收）
MIN_KEEP = 10           # 命中不足此数则跳过该品类

# 品类映射：(slug, 中文品名, jsonl 文件名, 关键词列表)
# McAuley 2023 无 Camera/PC 独立品类，相机/电脑归 Electronics，靠关键词精筛
CATEGORIES = [
    ("headphones", "耳机/音箱", "Electronics",
     ["headphone", "earbud", "earphone", "speaker", "anc", "noise cancel", "bass"]),
    ("smartwatch", "智能手表", "Electronics",
     ["smartwatch", "smart watch", "fitness tracker", "apple watch", "garmin", "fitbit"]),
    ("vacuum", "吸尘器/扫地机器人", "Appliances",
     ["vacuum", "robot vacuum", "dyson", "shark", "roomba", "robot mop"]),
    ("powerbank", "充电宝/移动电源", "Cell_Phones_and_Accessories",
     ["power bank", "portable charger", "battery pack", "powerbank"]),
    ("smartphone", "手机", "Cell_Phones_and_Accessories",
     ["phone", "smartphone", "iphone", "samsung galaxy", "google pixel"]),
    ("charger", "充电器/电源适配器", "Cell_Phones_and_Accessories",
     ["charger", "charging adapter", "wall charger", "usb-c charger", "gan charger"]),
    ("tv", "电视/显示器", "Electronics",
     ["tv", "television", "4k tv", "oled tv", "qled", "monitor"]),
    ("camera", "相机/摄像头", "Electronics",
     ["camera", "lens", "dslr", "mirrorless", "vlogging", "camcorder"]),
    ("laptop", "电脑/笔记本", "Electronics",
     ["laptop", "notebook", "chromebook", "macbook", "thinkpad", "ultrabook"]),
    ("kitchen", "小家电", "Appliances",
     ["rice cooker", "kettle", "hair dryer", "microwave", "air fryer", "blender", "toaster"]),
    ("airpurifier", "空气净化器", "Appliances",
     ["air purifier", "hepa", "levoit", "coway"]),
    ("smarthome", "智能家居", "Home_and_Kitchen",
     ["smart plug", "smart bulb", "smart lock", "alexa", "ring", "smart home"]),
    ("powertool", "电动工具", "Tools_and_Home_Improvement",
     ["drill", "impact driver", "circular saw", "dewalt", "milwaukee", "power tool"]),
    ("drone", "无人机", "Electronics",
     ["drone", "quadcopter", "dji", "mavic", "mini drone"]),
    ("vape", "电子烟", "Health_and_Personal_Care",
     ["vape", "e-cigarette", "vaping", "pod system", "ejuice"]),
    ("toothbrush", "电动牙刷", "Health_and_Personal_Care",
     ["toothbrush", "electric toothbrush", "sonicare", "oral-b", "oral b"]),
    ("massager", "按摩仪", "Health_and_Personal_Care",
     ["massager", "massage gun", "percussion", "tENS", "neck massager"]),
    ("router", "路由器/机顶盒", "Electronics",
     ["router", "wifi router", "mesh", "netgear", "tp-link", "fire tv", "roku", "chromecast"]),
]


def extract(jsonl_name: str, keywords: list, n: int) -> list:
    """分块 Range 请求读取品类 jsonl，取 text 含任一关键词的评论，按 helpful 票数取前 n。

    大文件（Electronics/Cell_Phones/Home_and_Kitchen 等可达数 GB~31GB）整体流式读易被
    hf-mirror 中途断连（SSL EOF / IncompleteRead）。改用 2MB Range 分块，每块独立短连接
    + 失败重试 3 次，显著稳定；只读到收够为止，不下载整文件。
    """
    url = f"{BASE_URL}/{jsonl_name}.jsonl"
    kws = [k.lower() for k in keywords]
    collected = []
    scanned = 0
    buf = ""                  # 跨块行缓冲（块边界可能截断一行）
    offset = 0
    CHUNK = 2 * 1024 * 1024   # 每次 Range 请求 2MB
    while scanned < MAX_SCAN and len(collected) < n * 3:
        end = offset + CHUNK - 1
        chunk = None
        for attempt in range(3):
            try:
                r = requests.get(url, headers={"Range": f"bytes={offset}-{end}"},
                                 timeout=(15, 60))
                if r.status_code not in (200, 206):
                    raise RuntimeError(f"HTTP {r.status_code}")
                chunk = r.content
                break
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(1.5)
        if not chunk:
            break
        buf += chunk.decode("utf-8", errors="replace")
        lines = buf.split("\n")
        buf = lines.pop()  # 末段可能不完整，留待下一块拼接
        for line in lines:
            if not line.strip():
                continue
            scanned += 1
            if scanned > MAX_SCAN:
                break
            try:
                ex = json.loads(line)
            except Exception:
                continue
            text = (ex.get("text") or "").strip()
            # 过滤太短（无信息）或太长（喂给 AI 慢且占 token）的评论
            if len(text) < 20 or len(text) > 500:
                continue
            low = text.lower()
            if not any(k in low for k in kws):
                continue
            collected.append({"text": text, "helpful": ex.get("helpful_vote", 0) or 0})
        offset += len(chunk)
        if len(chunk) < CHUNK:
            break  # 文件已读完
    collected.sort(key=lambda x: x["helpful"], reverse=True)
    return [c["text"] for c in collected[:n]]


def main():
    os.makedirs(SAMPLES_DIR, exist_ok=True)
    index = []
    ok, fail = [], []
    total = len(CATEGORIES)
    for i, (slug, name_cn, jsonl_name, kws) in enumerate(CATEGORIES, 1):
        print(f"[{i}/{total}] {name_cn} ({jsonl_name})...", flush=True)
        t0 = time.time()
        try:
            reviews = extract(jsonl_name, kws, PER_CATEGORY)
            dt = time.time() - t0
            if len(reviews) < MIN_KEEP:
                fail.append(f"{name_cn}: only {len(reviews)} hits (< {MIN_KEEP}, skip)")
                print(f"   [WARN] only {len(reviews)} hits, skip ({dt:.1f}s)")
                continue
            out = {
                "product": name_cn,
                "category_slug": slug,
                "source": "McAuley Amazon Reviews 2023（公开研究数据集）",
                "review_count": len(reviews),
                "reviews": reviews,
            }
            path = os.path.join(SAMPLES_DIR, f"{slug}.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(out, f, ensure_ascii=False, indent=2)
            index.append({"slug": slug, "product": name_cn, "count": len(reviews)})
            ok.append(name_cn)
            print(f"   [OK] {len(reviews)} reviews -> {path} ({dt:.1f}s)")
        except Exception as e:
            fail.append(f"{name_cn}: {e}")
            print(f"   [FAIL] {e}")

    with open(os.path.join(SAMPLES_DIR, "index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)

    print("\n=== Summary ===")
    print(f"OK {len(ok)}: {', '.join(ok)}")
    if fail:
        print(f"FAIL {len(fail)}:")
        for fmsg in fail:
            print(f"  - {fmsg}")
    print(f"\nindex -> {SAMPLES_DIR}\\index.json")


if __name__ == "__main__":
    main()
