"""
strategies/s03_volume_breakout.py — 均线多头放量突破策略

逻辑：
  - 价格同时站上 MA5/MA10/MA20/MA60（四线多头排列）
  - 今日放量突破（量比>1.8x），换手率3-15%
  - 涨幅1.5~9%，跑赢大盘
  - 适合趋势行情中寻找强势突破个股
"""

from strategy_registry import BaseStrategy
import screener_core
import numpy as np
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed


class VolumeBreakoutStrategy(BaseStrategy):

    META = {
        "id":          "volume_breakout",
        "name":        "均线多头放量突破",
        "description": "MA5/10/20/60 四线多头 + 放量，趋势行情中寻找强势突破股",
        "tags":        ["趋势", "突破", "均线", "中短线"],
        "author":      "",
        "version":     "1.0",
    }

    def run(self, snapshot_df, hs300_chg: float, actual_date: str, log) -> list:
        log("📊 均线多头放量突破筛选：涨幅1.5-9% + 量比>1.8 + 跑赢大盘...")

        pre = []
        for _, row in snapshot_df.iterrows():
            pct  = row.get("pct_chg",  np.nan)
            vr   = row.get("vol_ratio", np.nan)
            to   = row.get("turnover",  np.nan)
            if pd.isna(pct) or not (1.5 <= pct <= 9.0): continue
            if pd.notna(vr)  and vr < 1.8: continue
            if pd.notna(to)  and not (3.0 <= to <= 15.0): continue
            if pct <= hs300_chg + 0.5: continue  # 必须跑赢大盘
            pre.append(row.to_dict())

        log(f"  预筛通过 {len(pre)} 只，拉取K线验证均线形态...")

        kline_map = {}
        with ThreadPoolExecutor(max_workers=8) as pool:
            futs = {pool.submit(screener_core.fetch_kline, r["code"], 90, actual_date): r["code"]
                    for r in pre}
            for fut in as_completed(futs):
                code, kdf = futs[fut], None
                try: kdf = fut.result()
                except: pass
                kline_map[code] = kdf

        candidates = []
        for row in pre:
            code = str(row.get("code", "")).zfill(6)
            hist = kline_map.get(code)
            if hist is None or len(hist) < 65: continue

            close = hist["close"]
            vol   = hist["volume"]

            # 四线多头排列
            ma5  = close.rolling(5).mean().iloc[-1]
            ma10 = close.rolling(10).mean().iloc[-1]
            ma20 = close.rolling(20).mean().iloc[-1]
            ma60 = close.rolling(60).mean().iloc[-1]
            last = close.iloc[-1]
            if not (pd.notna(ma5) and pd.notna(ma10) and pd.notna(ma20) and pd.notna(ma60)):
                continue
            if not (last > ma5 > ma10 > ma20 > ma60):
                continue

            # 均线斜率向上（趋势加速）
            ma20_5d_ago = close.rolling(20).mean().iloc[-6]
            if pd.isna(ma20_5d_ago) or ma20 <= ma20_5d_ago: continue

            # 放量突破
            avg20 = vol.iloc[-21:-1].mean()
            if avg20 <= 0 or vol.iloc[-1] < avg20 * 1.8: continue

            vr_actual = vol.iloc[-1] / avg20
            hits = [
                f"四线多头(MA5>{ma5:.2f}>MA10>{ma10:.2f}>MA20>{ma20:.2f}>MA60>{ma60:.2f})",
                f"放量{vr_actual:.1f}x",
                f"MA20向上斜率",
            ]
            candidates.append({
                "code":         code,
                "name":         str(row.get("name", code)),
                "price":        float(row.get("price", 0) or 0),
                "pct_chg":      float(row.get("pct_chg", 0)),
                "vol_ratio":    float(row.get("vol_ratio", 0) or 0),
                "turnover":     float(row["turnover"]) if pd.notna(row.get("turnover")) else np.nan,
                "circ_cap_yi":  float(row.get("circ_cap_yi", 0) or 0),
                "stage1_score": len(hits),
                "stage1_hits":  hits,
                "_hist":        hist,
            })

        # 按涨幅+量比综合排序
        candidates.sort(key=lambda x: (x["stage1_score"], x["pct_chg"]), reverse=True)
        log(f"✅ 均线多头突破筛选 {len(candidates)} 只通过")
        return candidates[:50]
