# -*- coding: utf-8 -*-
"""一次性清理脚本（数据血缘修复，v1.0.4）：

1. 删除旧格式孤儿缓存（cache_key=''）：新代码（fetch_year 带 mode_key）永不读取，
   占空间且无血缘，清掉让数据库回到"每行可追溯"状态。
2. 删除测试特征行：tests/test_data_clean.py 夹具数据（6.88e8 美元 + 1000kg 净重）
   曾落入生产缓存，会被真实查询读取（德国 2024 年 8525 总进口），必须清除。
3. 其余 quality='valid' 但 source='' 的行标为 suspicious（来源不可考）。

用法: python scripts/clean_legacy_cache.py [--dry-run]
"""
import sqlite3
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database import DB_PATH  # noqa: E402

DRY = "--dry-run" in sys.argv


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    total = conn.execute("SELECT COUNT(*) FROM trade_cache").fetchone()[0]
    print(f"清理前缓存行数: {total}")

    # 1. 旧格式孤儿（cache_key=''）
    orphans = conn.execute("SELECT COUNT(*) FROM trade_cache WHERE cache_key=''").fetchone()[0]
    # 2. 测试特征行（夹具 6.88e8 + 1000kg，JSON 内匹配）
    test_rows2 = conn.execute(
        "SELECT COUNT(*) FROM trade_cache WHERE cmd_code='8525' AND partner_code='0' "
        "AND period='2024' AND flow_code='M' AND reporter_code='276' "
        "AND data_json LIKE '%688000000%'").fetchone()[0]
    print(f"  旧格式孤儿: {orphans} 行 | 测试特征行(精确键匹配): {test_rows2}")

    if DRY:
        print("[dry-run] 不执行写入")
        conn.close()
        return

    conn.execute("DELETE FROM trade_cache WHERE cache_key=''")
    conn.execute(
        "DELETE FROM trade_cache WHERE cmd_code='8525' AND partner_code='0' "
        "AND period='2024' AND flow_code='M' AND reporter_code='276' "
        "AND data_json LIKE '%688000000%'")
    # 3. valid 但无血缘的行降级为 suspicious（防"不可考数据当可信"）
    cur = conn.execute(
        "UPDATE trade_cache SET quality='suspicious', validation_reason="
        "'历史缓存无血缘记录，来源不可考' "
        "WHERE quality='valid' AND (source='' OR source IS NULL)")
    conn.commit()
    after = conn.execute("SELECT COUNT(*) FROM trade_cache").fetchone()[0]
    print(f"清理完成: {total} → {after} 行；{cur.rowcount} 行降级为 suspicious")
    conn.close()


if __name__ == "__main__":
    main()
