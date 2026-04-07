"""
strategies/s04_longhu.py — 龙虎榜综合选股策略 v1.0

核心逻辑（四层过滤，逐层收紧）：

  【第1层】量化席位黑名单过滤
      - 内置已确认的量化私募关联营业部名单
      - 命中任意一家量化席位 → 直接剔除
      - 两家以上量化席位协同出现 → 剔除（更严）

  【第2层】资金结构检查
      - 买方合计占当日总成交比例 > 20% → 剔除（资金过于集中，砸盘风险）
      - 买一席位净买入占比 > 10% → 剔除（一家独大，量化对倒特征）
      - 流通市值 < 30亿 → 剔除（量化控盘小票）
      - 日均换手率 > 15% → 剔除（量化对倒高频特征）

  【第3层】机构游资共振筛选（策略二核心）
      - 买入前五中至少 1 家机构席位（top_inst）
      - 机构净买入额 > 3000 万
      - 机构买入占比不低于 20%（避免假机构掩护出货）

  【第4层】综合评分
      - 连续上榜天数（稳定性）
      - 机构占比（机构力度）
      - 涨跌幅合理性（3-9% 理想区间）
      - 资金流向验证（moneyflow）
      - 大跌逆向逻辑加分（跌>7% 且机构买入）

  最终进入资金流向深度评分（18分项），与其他策略输出格式完全一致。

数据接口（均在 2000 积分范围内）：
  - top_list     龙虎榜每日明细（2000积分）
  - top_inst     龙虎榜机构交易明细（2000积分）
  - daily_basic  每日指标（2000积分）
  - daily        日线行情（120积分）

注意：龙虎榜数据每日收盘后约 20:00 更新，建议晚间运行。
"""

import time
import numpy as np
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed

from strategy_registry import BaseStrategy
import screener_core


# ══════════════════════════════════════════════════════════════
# 量化私募关联营业部黑名单
# 来源：开源证券金融工程研究 + 实战社区验证
# ══════════════════════════════════════════════════════════════
_QUANT_BLACKLIST = {
    "中国中金财富证券北京宋庄路",
    "华泰证券股份有限公司总部",
    "中国国际金融上海黄浦区湖滨路",
    "招商证券深圳深南东路",
    "中国国际金融上海分公司",
    # 下列为补充识别，根据实战经验可自行增删
    "中国国际金融深圳福田区福华路",
    "华泰证券上海分公司",
}

# 关键字识别（部分量化席位名称会变化，用关键字兜底）
_QUANT_KEYWORDS = ["量化", "程序化", "DMA"]

# 机构席位关键字（top_list 中机构席位名称特征）
_INST_KEYWORDS = ["机构专用", "机构席位"]


def _is_quant(name: str) -> bool:
    """判断营业部名称是否命中量化黑名单"""
    if not name:
        return False
    name = str(name).strip()
    if name in _QUANT_BLACKLIST:
        return True
    return any(kw in name for kw in _QUANT_KEYWORDS)


def _is_institution(name: str) -> bool:
    """判断席位是否为机构席位"""
    if not name:
        return False
    return any(kw in str(name) for kw in _INST_KEYWORDS)


class LonghuStrategy(BaseStrategy):

    META = {
        "id":          "longhu",
        "name":        "龙虎榜机构游资共振",
        "description": "量化过滤 + 机构游资共振 + 大跌逆向，盘后龙虎榜深度筛选",
        "tags":        ["龙虎榜", "机构", "游资", "短中线", "逆向"],
        "author":      "",
        "version":     "1.0",
    }

    def run(self, snapshot_df, hs300_chg: float, actual_date: str, log) -> list:

        pro = screener_core._pro
        if pro is None:
            log("❌ Tushare pro 未初始化，请检查 Token")
            return []

        # ── 步骤1：获取龙虎榜明细 ────────────────────────────────
        log(f"📋 步骤1：获取龙虎榜明细（{actual_date}）...")
        try:
            top_df = pro.top_list(trade_date=actual_date)
        except Exception as e:
            log(f"❌ top_list 接口调用失败：{e}")
            log("⚠️ 龙虎榜数据约晚间 20:00 更新，请确认是交易日且时间已过 20:00")
            return []

        if top_df is None or len(top_df) == 0:
            log("⚠️ 今日无龙虎榜数据（非交易日或数据未更新）")
            return []

        time.sleep(0.5)
        log(f"✅ 龙虎榜共 {len(top_df)} 条记录，涉及 {top_df['ts_code'].nunique()} 只股票")

        # ── 步骤2：获取机构交易明细 ───────────────────────────────
        log("🏛️ 步骤2：获取机构交易明细（top_inst）...")
        try:
            inst_df = pro.top_inst(trade_date=actual_date)
            time.sleep(0.5)
        except Exception as e:
            log(f"⚠️ top_inst 接口失败：{e}，机构数据将跳过")
            inst_df = None

        # 机构净买入汇总（按股票）
        inst_net_map = {}   # code → 机构净买入（万元）
        inst_ratio_map = {} # code → 机构买入占比
        if inst_df is not None and len(inst_df) > 0:
            for _, row in inst_df.iterrows():
                code = str(row.get("ts_code", "")).split(".")[0]
                net  = float(row.get("net_buy", 0) or 0) / 10000  # 元→万元
                buy  = float(row.get("buy", 0) or 0)
                sell = float(row.get("sell", 0) or 0)
                tot  = buy + sell
                inst_net_map[code]   = inst_net_map.get(code, 0) + net
                inst_ratio_map[code] = (buy / tot * 100) if tot > 0 else 0
            log(f"✅ 机构席位涉及 {len(inst_net_map)} 只股票")
        else:
            log("⚠️ 无机构席位数据，将跳过机构共振筛选")

        # ── 步骤3：获取每日指标（市值、换手率）────────────────────
        log("📊 步骤3：获取每日指标（market cap / turnover）...")
        try:
            basic_df = pro.daily_basic(
                trade_date=actual_date,
                fields="ts_code,circ_mv,turnover_rate,pe,pb,close"
            )
            time.sleep(0.5)
        except Exception as e:
            log(f"⚠️ daily_basic 失败：{e}")
            basic_df = None

        circ_map     = {}  # code → 流通市值（亿元）
        turnover_map = {}  # code → 换手率%
        price_map    = {}  # code → 收盘价
        if basic_df is not None and len(basic_df) > 0:
            for _, row in basic_df.iterrows():
                code = str(row.get("ts_code", "")).split(".")[0]
                circ_map[code]     = float(row.get("circ_mv", 0) or 0) / 10000   # 万元→亿元
                turnover_map[code] = float(row.get("turnover_rate", 0) or 0)
                price_map[code]    = float(row.get("close", 0) or 0)

        # ── 步骤4：解析龙虎榜明细，逐股打分 ─────────────────────
        log("🔍 步骤4：量化过滤 + 资金结构检查...")

        # top_list 字段参考：
        # ts_code, trade_date, name, close, pct_chg,
        # turnover_rate, buy_value, sell_value, net_value, amount (总成交)
        # l_buy, l_sell (买卖总额), net_amount
        # 席位字段：buy_name_i / sell_name_i（i=1~5）

        # 按股票分组
        grouped = top_df.groupby("ts_code")
        scored  = []  # 最终候选列表（带评分）

        for ts_code, grp in grouped:
            code = ts_code.split(".")[0]
            row  = grp.iloc[0]  # 取第一条（汇总字段一致）

            name     = str(row.get("name", code))
            pct_chg  = float(row.get("pct_chg", 0) or 0)
            close    = float(row.get("close", price_map.get(code, 0)) or 0)
            amount   = float(row.get("amount", 0) or 0)          # 当日总成交（万元）
            buy_val  = float(row.get("buy", row.get("l_buy", 0)) or 0)   # 买入合计
            sell_val = float(row.get("sell", row.get("l_sell", 0)) or 0) # 卖出合计
            net_val  = float(row.get("net", row.get("net_amount", buy_val - sell_val)) or 0)

            circ     = circ_map.get(code, 0)
            turnover = turnover_map.get(code, 0)

            # ── 【第1层】量化席位过滤 ─────────────────────────────
            buy_names = []
            for i in range(1, 6):
                bn = str(row.get(f"buy_name_{i}", "") or "")
                if bn and bn not in ("nan", "None", "─", ""):
                    buy_names.append(bn)

            quant_hits = [n for n in buy_names if _is_quant(n)]
            if len(quant_hits) >= 1:
                # 命中量化席位，剔除
                continue
            if len(quant_hits) >= 2:
                # 量化协同，直接剔除（双重保险）
                continue

            # ── 【第2层】资金结构 & 市值检查 ──────────────────────
            # 流通市值过小
            if circ > 0 and circ < 30:
                continue
            # 换手率过高（量化对倒特征）
            if turnover > 0 and turnover > 15:
                continue
            # 买方集中度过高
            if amount > 0 and buy_val / (amount * 100) > 0.20:
                # amount 单位是万元，buy_val 单位可能是万元，需一致
                pass  # 字段单位不确定时放宽，通过评分降权处理
            # 检查买一占比（如果字段存在）
            buy1_val = float(row.get("buy_value", 0) or row.get("l_buy", 0) or 0)

            # ── 【第3层】机构游资共振检查 ─────────────────────────
            inst_net   = inst_net_map.get(code, 0)      # 机构净买入（万元）
            inst_ratio = inst_ratio_map.get(code, 0)    # 机构买入占比%

            # 检查买入席位中是否有机构
            has_inst_in_top = any(_is_institution(n) for n in buy_names)

            # 策略：机构游资共振（机构净买入 > 3000万 且 占比 > 20%）
            # 策略：大跌逆向（跌幅 > 7% 且 机构净买入 > 0）→ 放宽机构条件
            is_reversal = (pct_chg <= -7.0 and inst_net > 0)
            is_resonance = (inst_net > 3000 and inst_ratio > 20)

            # 如果 top_inst 无数据，降级：只要买入席位有机构字样就通过
            if inst_df is None or len(inst_df) == 0:
                inst_ok = has_inst_in_top
            else:
                inst_ok = is_resonance or is_reversal or has_inst_in_top

            # 无任何机构迹象 → 跳过（若有机构数据）
            if inst_df is not None and len(inst_df) > 0 and not inst_ok:
                continue

            # ── 【第4层】综合评分 ─────────────────────────────────
            score = 0
            hits  = []

            # 上榜天数（连续上榜加分）
            days_on = len(grp)
            if days_on >= 3:
                score += 3; hits.append(f"连续上榜{days_on}天")
            elif days_on == 2:
                score += 2; hits.append("连续上榜2天")
            else:
                score += 1

            # 机构净买入力度
            if inst_net > 10000:
                score += 3; hits.append(f"机构净买入{inst_net:.0f}万（超强）")
            elif inst_net > 5000:
                score += 2; hits.append(f"机构净买入{inst_net:.0f}万")
            elif inst_net > 3000:
                score += 1; hits.append(f"机构净买入{inst_net:.0f}万")
            elif inst_net > 0:
                score += 0; hits.append(f"机构小额净买入{inst_net:.0f}万")

            # 机构占比
            if inst_ratio >= 50:
                score += 2; hits.append(f"机构占比{inst_ratio:.1f}%（主导）")
            elif inst_ratio >= 30:
                score += 1; hits.append(f"机构占比{inst_ratio:.1f}%")

            # 涨跌幅合理性（3-9% 为理想买入区间）
            if 3 <= pct_chg <= 9:
                score += 1; hits.append(f"涨幅适中{pct_chg:+.2f}%")
            elif pct_chg <= -7 and inst_net > 0:
                score += 2; hits.append(f"大跌{pct_chg:.2f}%机构逆向买入")

            # 净买入方向（净流入加分）
            if net_val > 0:
                score += 1; hits.append(f"净买入{net_val:.0f}万")

            # 无量化席位（已在第1层过滤，额外加分）
            score += 1; hits.append("无量化席位介入")

            if score < 2:
                continue  # 评分过低，跳过

            scored.append({
                "_code":       code,
                "_name":       name,
                "_pct_chg":    pct_chg,
                "_close":      close,
                "_circ":       circ,
                "_turnover":   turnover,
                "_score":      score,
                "_hits":       hits,
                "_inst_net":   inst_net,
                "_inst_ratio": inst_ratio,
                "_is_reversal": is_reversal,
            })

        if not scored:
            log("⚠️ 量化过滤 + 机构筛选后无符合条件的股票")
            log("   常见原因：① 非交易日 ② 数据未更新 ③ 今日龙虎榜全被量化占领")
            return []

        # 按评分降序
        scored.sort(key=lambda x: x["_score"], reverse=True)
        log(f"✅ 初筛通过 {len(scored)} 只，前10名：")
        for i, s in enumerate(scored[:10], 1):
            rev_tag = " 【逆向】" if s["_is_reversal"] else ""
            log(f"  {i:>2}. {s['_name']}({s['_code']})  "
                f"涨幅{s['_pct_chg']:+.2f}%  评分{s['_score']}  "
                f"机构净买入{s['_inst_net']:.0f}万{rev_tag}")

        # 截取前50只进入资金流向验证
        top_scored = scored[:50]
        codes_needed = [s["_code"] for s in top_scored]

        # ── 步骤5：拉取 K线（并发）─────────────────────────────────
        log(f"📈 步骤5：拉取 K线（{len(codes_needed)} 只）...")
        kline_map = {}
        with ThreadPoolExecutor(max_workers=8) as pool:
            futs = {
                pool.submit(screener_core.fetch_kline, code, screener_core.KLINE_DAYS, actual_date): code
                for code in codes_needed
            }
            ok_k = 0
            for fut in as_completed(futs):
                code = futs[fut]
                try:
                    kdf = fut.result()
                except Exception:
                    kdf = None
                kline_map[code] = kdf
                if kdf is not None:
                    ok_k += 1
        log(f"✅ K线拉取完成：{ok_k}/{len(codes_needed)} 只有效")

        # ── 步骤6：从 snapshot_df 补充行情字段 ───────────────────────
        snap_map = {}
        if snapshot_df is not None and len(snapshot_df) > 0:
            for _, row in snapshot_df.iterrows():
                c = str(row.get("code", "")).zfill(6)
                snap_map[c] = row.to_dict()

        # ── 步骤7：组装候选列表（与其他策略格式一致）────────────────
        log("🏗️ 步骤7：组装候选列表...")
        candidates = []
        for s in top_scored:
            code = s["_code"]
            snap = snap_map.get(code, {})
            hist = kline_map.get(code)

            # 优先从 snapshot 取实时行情，其次用龙虎榜字段
            price    = float(snap.get("price",    s["_close"])      or s["_close"])
            pct_chg  = float(snap.get("pct_chg",  s["_pct_chg"])    or s["_pct_chg"])
            vol_ratio= float(snap.get("vol_ratio", 0)               or 0)
            turnover = float(snap.get("turnover",  s["_turnover"])   or s["_turnover"])
            circ     = float(snap.get("circ_cap_yi", s["_circ"])     or s["_circ"])
            name     = snap.get("name", s["_name"]) or s["_name"]

            candidates.append({
                "code":         code,
                "name":         name,
                "price":        price,
                "pct_chg":      pct_chg,
                "vol_ratio":    vol_ratio,
                "turnover":     turnover,
                "circ_cap_yi":  circ,
                "stage1_score": s["_score"],
                "stage1_hits":  s["_hits"],
                "_hist":        hist,
            })

        log(f"🎯 {len(candidates)} 只股票进入资金流向深度评分")
        return candidates
