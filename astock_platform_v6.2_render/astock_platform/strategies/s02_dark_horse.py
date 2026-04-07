"""
strategies/s02_dark_horse.py — 低位黑马启动策略

逻辑：
  - 股价处于近60日低位区（距高点回调>20%）
  - 今日出现放量突破（量比>2x）
  - 涨幅适中（2~7%），换手率适中（3~12%）
  - 适合寻找长期横盘后突然放量启动的"黑马"
"""

from strategy_registry import BaseStrategy
import screener_core
import numpy as np
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed


class DarkHorseStrategy(BaseStrategy):

    META = {
        "id":          "dark_horse",
        "name":        "低位黑马启动",
        "description": "长期低位横盘 + 放量突破，寻找刚启动的黑马股",
        "tags":        ["突破", "放量", "短线", "黑马"],
        "author":      "",
        "version":     "1.0",
    }

    def run(self, snapshot_df, hs300_chg: float, actual_date: str, log) -> list:
        log("🔎 低位黑马筛选：涨幅2-7% + 量比>2 + 换手3-12%...")

        # 预筛条件
        pre = []
        for _, row in snapshot_df.iterrows():
            pct  = row.get("pct_chg",  np.nan)
            vr   = row.get("vol_ratio", np.nan)
            to   = row.get("turnover",  np.nan)
            if pd.isna(pct) or not (2.0 <= pct <= 7.0): continue
            if pd.notna(vr) and vr < 2.0: continue
            if pd.notna(to) and not (3.0 <= to <= 12.0): continue
            pre.append(row.to_dict())

        log(f"  预筛通过 {len(pre)} 只，拉取K线验证低位条件...")

        # 并发拉K线
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
            if hist is None or len(hist) < 30:
                continue

            close = hist["close"]
            high  = hist["high"]
            vol   = hist["volume"]

            # 距60日高点回调>20%（低位确认）
            h60 = high.iloc[-min(60, len(high)):-1].max()
            if h60 <= 0: continue
            dist = (h60 - close.iloc[-1]) / h60 * 100
            if dist < 20: continue   # 必须是低位

            # 今日量比>2（相对近20日均量）
            avg20 = vol.iloc[-21:-1].mean() if len(vol) >= 21 else vol[:-1].mean()
            if avg20 <= 0 or vol.iloc[-1] < avg20 * 2.0: continue

            # 近10日有缩量迹象（横盘特征）
            avg10_prev = vol.iloc[-11:-1].mean() if len(vol) >= 11 else avg20
            shrink_days = int((vol.iloc[-10:-1] < avg20 * 0.8).sum()) if len(vol) >= 10 else 0
            if shrink_days < 3: continue  # 之前要有缩量横盘

            hits = [f"距高点{dist:.1f}%低位", f"今日放量{vol.iloc[-1]/avg20:.1f}x", f"前期缩量{shrink_days}日"]
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

        candidates.sort(key=lambda x: x["stage1_score"], reverse=True)
        log(f"✅ 低位黑马筛选 {len(candidates)} 只通过")
        return candidates[:50]
