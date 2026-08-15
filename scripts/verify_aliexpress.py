# -*- coding: utf-8 -*-
"""速卖通联盟开放平台联调脚本（验证 timestamp 格式 + 签名正确性）

用法：把 App Key / App Secret 填到 .env（ALIEXPRESS_APP_KEY / ALIEXPRESS_APP_SECRET），
然后运行本脚本，填入一个真实速卖通商品 ID（或从速卖通链接复制）。

能确认的点：
1. timestamp 格式（yyyy-MM-dd HH:mm:ss GMT+8）是否为网关接受
2. 签名（大写 hex）是否正确 —— 返回商品数据 = 全链路通
3. 若返回 isv.sign-check-failure / IncompleteSignature = 签名或参数格式仍有问题
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from aliexpress import request_product_detail, _timestamp, _sign, ALIEXPRESS_API_PATH

def main():
    app_key = config.ALIEXPRESS_APP_KEY or os.getenv("ALIEXPRESS_APP_KEY", "")
    app_secret = config.ALIEXPRESS_APP_SECRET or os.getenv("ALIEXPRESS_APP_SECRET", "")
    if not app_key or not app_secret:
        print("❌ 未配置 ALIEXPRESS_APP_KEY / ALIEXPRESS_APP_SECRET（.env）")
        print("   获取：https://pub.aliexpress.com 联盟开放平台 → 创建应用 → App Key + App Secret")
        return 1

    product_id = sys.argv[1] if len(sys.argv) > 1 else input("商品 ID（或速卖通链接里 /item/ 后的数字）: ").strip()
    if not product_id:
        print("❌ 未提供商品 ID")
        return 1
    product_id = product_id.split("?")[0].rstrip("/").split("/")[-1]

    print(f"== 联调信息 ==")
    print(f"App Key      : {app_key[:6]}...")
    print(f"API 路径     : {ALIEXPRESS_API_PATH}")
    print(f"timestamp    : {_timestamp()}  (格式: yyyy-MM-dd HH:mm:ss GMT+8)")
    print(f"商品 ID      : {product_id}")
    print()

    t0 = time.time()
    try:
        result = request_product_detail(app_key, app_secret, product_id)
        print(f"== 响应（{time.time()-t0:.1f}s）==")
        print(result)
        # 淘宝网关错误结构：{"error_response": {"code": ..., "msg": ..., "sub_code": ...}}
        err = result.get("error_response") or {}
        if err:
            print()
            print(f"❌ 网关返回错误: code={err.get('code')} msg={err.get('msg')}")
            sub = err.get("sub_code") or ""
            if "sign" in sub.lower() or "sign" in str(err.get("msg", "")).lower():
                print("   → 签名相关问题。请确认 .env 里 App Secret 正确；")
                print("     签名规则: hmac-sha256(api_path + 参数ASCII排序拼接)，结果大写 hex，")
                print("     timestamp 为 yyyy-MM-dd HH:mm:ss（GMT+8）。")
            if "timestamp" in sub.lower() or "expire" in sub.lower():
                print("   → timestamp 超时/格式问题（本机时钟需正确，GMT+8）")
            return 2
        resp = result.get("aliexpress_affiliate_productdetail_get_response") or {}
        if resp:
            print("✅ 联调成功：网关接受了请求（返回业务数据）")
            return 0
        print("⚠️ 响应结构未识别（非错误也非业务数据），请人工判断")
        return 3
    except Exception as e:
        print(f"❌ 请求异常: {type(e).__name__}: {e}")
        return 4

if __name__ == "__main__":
    sys.exit(main())
