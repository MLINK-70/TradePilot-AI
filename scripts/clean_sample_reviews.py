"""clean_sample_reviews.py — 一次性清洗现有品类样本文件（不需重新下载）

修复两个问题：
1. HTML 实体未解码（&#34; / &lt;br /&gt; / &amp;amp;）→ 引用校验误删真实评论
2. 跨品类噪音评论（耳机品类混入 AV 功放/照片收纳盒等）→ 演示说服力受损

用法（毕设一 venv）：python scripts/clean_sample_reviews.py
只读改写 data/samples/*.json，不改 index.json（品类结构不变）。
"""
import html
import json
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

SAMPLES_DIR = r"D:\毕设一\data\samples"

# 与 build_sample_reviews.py 保持一致的品类排除词
EXCLUDE = {
    "headphones": ["photo", "album", "coax", "antenna", "receiver", "amplifier", "turntable",
                   "cd player", "dvd", "projector", "camcorder", "camera"],
    "tv": ["camera", "photo", "projector", "laptop", "desktop", "phone"],
    "camera": ["tv", "monitor", "speaker", "headphone"],
    "smartphone": ["laptop", "tablet", "desktop", "router", "tv"],
    "laptop": ["tv", "monitor", "desktop", "phone", "server"],
    "smarthome": ["vacuum", "robot vacuum", "camera", "smartwatch", "tv"],
    "router": ["tv", "monitor", "camera", "phone case", "screen protector"],
    "drone": ["camera", "tv", "laptop"],
}


def clean_text(text: str) -> str:
    text = html.unescape(text)
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def main():
    total_removed = 0
    total_cleaned = 0
    counts = {}
    for path in sorted(os.listdir(SAMPLES_DIR)):
        if not path.endswith(".json") or path == "index.json":
            continue
        slug = path[:-5]
        with open(os.path.join(SAMPLES_DIR, path), encoding="utf-8") as f:
            data = json.load(f)
        excl = [e.lower() for e in EXCLUDE.get(slug, [])]
        before = len(data["reviews"])
        kept = []
        for r in data["reviews"]:
            text = clean_text(r)
            low = text.lower()
            if any(e in low for e in excl):
                total_removed += 1
                continue
            kept.append(text)
        data["reviews"] = kept
        data["review_count"] = len(kept)
        # 清洗后仍不足下限的品类提示（不自动删文件，留人工决定）
        with open(os.path.join(SAMPLES_DIR, path), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        total_cleaned += before - len(kept)
        if before != len(kept):
            print(f"{slug}: {before} -> {len(kept)} (removed {before - len(kept)})")
        counts[slug] = len(kept)

    # 同步 index.json 计数（否则前端下拉显示清洗前的数量）
    index_path = os.path.join(SAMPLES_DIR, "index.json")
    if os.path.exists(index_path):
        with open(index_path, encoding="utf-8") as f:
            index = json.load(f)
        for e in index:
            if e["slug"] in counts:
                e["count"] = counts[e["slug"]]
        with open(index_path, "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False, indent=2)
        print("index.json updated")
    print(f"\nDone. removed={total_removed} reviews cleaned={total_cleaned}")


if __name__ == "__main__":
    main()
