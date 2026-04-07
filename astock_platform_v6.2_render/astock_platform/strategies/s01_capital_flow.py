"""
strategies/s01_capital_flow.py — 资金流向主力建仓策略（原版）

逻辑：
  - 涨幅 1~9.5%，放量，量比达标 → K线量价评分（8条件）
  - 取前50只进行资金流向深度评分
  - 适合寻找主力持续建仓、资金持续流入的个股
"""

from strategy_registry import BaseStrategy
import screener_core


class CapitalFlowStrategy(BaseStrategy):

    META = {
        "id":          "capital_flow",
        "name":        "主力资金建仓",
        "description": "量价8条件预筛 → 资金流向18分项深度评分，寻找主力持续建仓标的",
        "tags":        ["资金流向", "主力", "中线", "经典"],
        "author":      "原版",
        "version":     "4.0",
    }

    def run(self, snapshot_df, hs300_chg: float, actual_date: str, log) -> list:
        log("📈 量价评分（并发拉K线，约需1-3分钟）...")
        all_candidates = screener_core.screen_stage1(
            snapshot_df, hs300_chg, actual_date, ff_workers=8
        )
        log(f"✅ 量价筛选 {len(all_candidates)} 只通过")
        candidates = all_candidates[:screener_core.STAGE1_TOPN]
        if candidates:
            log(f"🎯 前 {len(candidates)} 只进入资金流向验证")
        return candidates
