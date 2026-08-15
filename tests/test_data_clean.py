# -*- coding: utf-8 -*-
"""数据聚合回归测试（正确逻辑：去重 + customsCode=C00(总计) + motCode=0(全部运输)）

背景：UN Comtrade 数据按 customsCode(C00=总计=C03+C04+…) 和 motCode(0=全部运输)
拆分多条且有成对重复。正确总额 = C00 且 mot=0 的唯一记录。
此前"sum 所有行"(55亿) 和 "mot=0 优先"(27.5亿) 均错，真实值 6.88 亿（实测验证）。
"""
from unittest import mock

import trade
from trade import fetch_year


def _mock_resp(data_rows):
    m = mock.MagicMock()
    m.status_code = 200
    m.json.return_value = {"data": data_rows}
    return m


def _mk(value, mot=0, customs="C00", reporter=276, partner=156):
    return {"reporterCode": reporter, "partnerCode": partner, "cmdCode": "8525",
            "period": "2024", "motCode": mot, "mosCode": 0, "customsCode": customs,
            "primaryValue": value, "refYear": 2024, "netWgt": 1000.0}


class TestFetchYearAggregation:
    def test_dedup_repeated_rows(self, tmp_db):
        """同一条(C00+mot=0)重复 3 次 → 去重为 1 条"""
        rows = [_mk(1e8)] * 3
        with mock.patch.object(trade.requests, "get", return_value=_mock_resp(rows)):
            out = fetch_year("8525", "156", "2024", "德国", "X")
        assert len(out) == 1
        assert sum(r["primaryValue"] for r in out) == 1e8

    def test_mot_split_takes_total(self, tmp_db):
        """C00 下 mot=0(总计5e8) + mot 拆分行(3e8+2e8) → 只取 mot=0"""
        rows = [_mk(5e8, mot=0), _mk(3e8, mot=1000), _mk(2e8, mot=2100)]
        with mock.patch.object(trade.requests, "get", return_value=_mock_resp(rows)):
            out = fetch_year("8525", "156", "2024", "德国", "X")
        assert len(out) == 1
        assert out[0]["motCode"] == 0
        assert sum(r["primaryValue"] for r in out) == 5e8

    def test_customs_split_takes_total(self, tmp_db):
        """mot=0 下 C00(6.88) + C03(6.37) + C04(0.51) → 只取 C00（不重复计子项）"""
        rows = [_mk(6.88e8, customs="C00"), _mk(6.37e8, customs="C03"), _mk(0.51e8, customs="C04")]
        with mock.patch.object(trade.requests, "get", return_value=_mock_resp(rows)):
            out = fetch_year("8525", "156", "2024", "德国", "X")
        assert len(out) == 1
        assert out[0]["customsCode"] == "C00"
        assert abs(sum(r["primaryValue"] for r in out) - 6.88e8) < 0.01e8

    def test_real_dirty_case_germany(self, tmp_db):
        """真实德国案例：22 条成对重复 + mot/customs 拆分 → 正确聚合 6.88 亿"""
        combos = [
            ("C00", 0, 6.8838e8), ("C00", 1000, 2.6868e8), ("C00", 2100, 4.1964e8),
            ("C03", 0, 6.3713e8), ("C03", 1000, 2.6644e8), ("C03", 2100, 3.7061e8),
            ("C04", 0, 0.5126e8), ("C04", 1000, 0.0223e8), ("C04", 2100, 0.4902e8),
        ]
        rows = []
        for customs, mot, v in combos:
            rows.append(_mk(v, mot=mot, customs=customs))
            rows.append(_mk(v, mot=mot, customs=customs))  # 成对重复
        with mock.patch.object(trade.requests, "get", return_value=_mock_resp(rows)):
            out = fetch_year("8525", "156", "2024", "德国", "X")
        total = sum(r["primaryValue"] for r in out)
        assert abs(total / 1e8 - 6.88) < 0.05, f"德国正确聚合应 6.88 亿，实际 {total/1e8}"

    def test_no_c00_fallback_to_unique(self, tmp_db):
        """无 C00 行时兜底取去重后的全部（不崩溃、不重复）"""
        rows = [_mk(6.37e8, customs="C03"), _mk(0.51e8, customs="C04"), _mk(6.37e8, customs="C03")]
        with mock.patch.object(trade.requests, "get", return_value=_mock_resp(rows)):
            out = fetch_year("8525", "156", "2024", "德国", "X")
        assert len(out) == 2

    def test_clean_single_untouched(self, tmp_db):
        """干净单条（C00+mot=0）原样保留"""
        rows = [_mk(5.94e8)]
        with mock.patch.object(trade.requests, "get", return_value=_mock_resp(rows)):
            out = fetch_year("8525", "156", "2024", "德国", "X")
        assert len(out) == 1
        assert sum(r["primaryValue"] for r in out) == 5.94e8

    def test_share_math_consistency(self, tmp_db):
        """份额数学自洽：出口 6.88 亿 / 总进口 22.11 亿 = 31.1% < 100%"""
        exp = [_mk(6.88e8)] * 4  # 脏数据重复
        imp = [_mk(22.11e8)]
        with mock.patch.object(trade.requests, "get",
                               side_effect=[_mock_resp(exp), _mock_resp(imp)]):
            export_rows = fetch_year("8525", "156", "2024", "德国", "X")
            import_rows = fetch_year("8525", "0", "2024", "中国", "M")
        export_v = sum(r["primaryValue"] for r in export_rows)
        import_v = sum(r["primaryValue"] for r in import_rows)
        share = export_v / import_v * 100
        assert 30 < share < 32, f"德国份额应约 31%，实际 {share:.1f}%"
