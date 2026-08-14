# -*- coding: utf-8 -*-
"""导出模块测试：CSV（BOM/空数据/并集字段）+ 趋势图（PNG 头，不校验字形）"""
import io

import pytest

from export import build_csv, build_trend_chart


class TestBuildCsv:
    def test_bom_prefix(self):
        data = build_csv([{"a": 1}])
        raw = data.getvalue()
        assert raw.startswith(b"\xef\xbb\xbf"), "缺少 UTF-8 BOM（Excel 中文会乱码）"

    def test_empty_rows_returns_header_only_csv(self):
        """空数据返回带表头的空文件（标准 CSV），不再是 '暂无数据' 文本"""
        data = build_csv([]).getvalue().decode("utf-8-sig")
        assert data == "\r\n" or data == "\n"  # 仅表头行

    def test_union_fields_preserved(self):
        data = build_csv([{"a": 1}, {"b": 2}, {"a": 3, "c": 4}]).getvalue().decode("utf-8-sig")
        lines = [l for l in data.splitlines() if l]
        assert lines[0] == "a,b,c"  # 并集字段且顺序稳定
        assert lines[1] == "1,,"
        assert lines[2] == ",2,"
        assert lines[3] == "3,,4"

    def test_non_dict_rows_skipped(self):
        data = build_csv(["垃圾行", {"a": 1}]).getvalue().decode("utf-8-sig")
        assert "垃圾行" not in data


class TestBuildTrendChart:
    def test_returns_png_bytes(self):
        buf = build_trend_chart({"2020": {"value": 1.0, "weight": 0.5},
                                 "2021": {"value": 2.0, "weight": 1.0}})
        assert buf.getvalue().startswith(b"\x89PNG")

    def test_empty_trend_no_crash(self):
        buf = build_trend_chart({})
        assert buf is not None

    def test_zero_weight_no_crash(self):
        buf = build_trend_chart({"2020": {"value": 0.0, "weight": 0.0}})
        assert buf.getvalue().startswith(b"\x89PNG")
