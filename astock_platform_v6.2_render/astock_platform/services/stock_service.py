"""
股票基础服务：元信息、大股东、实时行情测试、货币政策
"""
import json
import os
from datetime import datetime
from typing import List

import pandas as pd

from config.settings import (
    FETCH_TIMEOUT, CACHE_DIR, INDUSTRY_CHAIN,
    green, yellow, dim, bold, cyan, get_industry_short_name,
)
from core.timeout import run_with_timeout
from data_sources.sina import fetch_sina_realtime, fetch_sina_sector_spot


# ─────────────────────────────────────────────
#  辅助
# ─────────────────────────────────────────────

def extract_json_list(data, *keys):
    """递归在 dict 中找第一个值为 list 的 key（供其他模块 import）"""
    if not isinstance(data, dict):
        return None
    for key in keys:
        if key in data and isinstance(data[key], list):
            return data[key]
        if key in data and isinstance(data[key], dict):
            sub = extract_json_list(data[key], *keys)
            if sub is not None:
                return sub
    return None


def score_relevance(text: str, keywords: List[str]) -> int:
    """关键词相关度评分（3 字以上命中 2 分，2 字命中 1 分）"""
    return sum(
        (2 if len(kw) >= 3 else 1)
        for kw in keywords
        if kw and kw in text
    )


def score_news_relevance(title: str, company: str,
                          industry: str, keywords: List[str]) -> float:
    """返回 0-1 相关度分数"""
    score = 0.0
    if company and company in title:
        score += 0.4
    elif company and len(company) >= 2 and company[:2] in title:
        score += 0.2
    if industry and industry in title:
        score += 0.3
    elif industry and len(industry) >= 2 and industry[:2] in title:
        score += 0.15
    kw_hits = sum(1 for kw in keywords if kw and kw in title)
    score += min(0.3, kw_hits * 0.1)
    noise = ["美联储", "伊朗", "特朗普", "美元", "美股", "比特币", "原油期货"]
    score -= sum(0.2 for nk in noise if nk in title)
    return max(0.0, min(1.0, score))


# ─────────────────────────────────────────────
#  股票元信息
# ─────────────────────────────────────────────

def get_stock_meta(ak, symbol: str) -> dict:
    """通过东方财富 akshare 接口获取股票元信息"""
    meta = {"name": "", "industry": "", "concepts": [], "keywords": []}
    df, err = run_with_timeout(ak.stock_individual_info_em, FETCH_TIMEOUT, args=(symbol,))
    if not err and df is not None and not df.empty:
        try:
            info = dict(zip(df.iloc[:, 0], df.iloc[:, 1]))
            meta["name"]     = str(info.get("股票简称", "")).strip()
            meta["industry"] = str(info.get("行业", "")).strip()
        except Exception:
            pass

    base_kws = [symbol]
    name, ind = meta["name"], meta["industry"]
    if name:
        base_kws.append(name)
        core = name
        for suffix in ["股份有限公司", "有限公司", "股份", "集团", "控股",
                       "科技", "传媒", "文化", "影视"]:
            core = core.replace(suffix, "")
        if len(core.strip()) >= 2:
            base_kws.append(core.strip())
    if ind:
        base_kws.append(ind)
        for key, chain in INDUSTRY_CHAIN.items():
            if key in ind or ind in key:
                base_kws.extend(chain["upstream"][:2])
                base_kws.extend(chain["downstream"][:2])
                break
    meta["keywords"] = list(dict.fromkeys(kw for kw in base_kws if kw and len(kw) >= 2))
    return meta


def get_stock_meta_baostock(symbol: str) -> dict:
    """
    使用 baostock 获取股票名称和行业。
    BUG FIX: 将 baostock 返回的长行业代码（如 R87...）映射为短名。
    """
    meta = {"name": "", "industry": "", "keywords": []}
    bs = None
    try:
        import baostock as bs
        lg = bs.login()
        if lg.error_code != "0":
            print(yellow(f"baostock登录失败: {lg.error_msg}"))
            return meta

        code = f"sh.{symbol}" if symbol.startswith("6") else f"sz.{symbol}"
        rs = bs.query_stock_basic(code=code)
        if rs.error_code == "0":
            while rs.next():
                row = rs.get_row_data()
                if row:
                    meta["name"] = row[1]

        rs_ind = bs.query_stock_industry(code=code)
        if rs_ind.error_code == "0":
            while rs_ind.next():
                row = rs_ind.get_row_data()
                if len(row) > 3:
                    raw_industry = row[3]
                    # BUG FIX: 将长行业名映射为可用于接口查询的短名
                    meta["industry"] = get_industry_short_name(raw_industry)

        if not meta["industry"]:
            meta["industry"] = "未知"

        if meta["name"]:
            meta["keywords"] = [symbol, meta["name"]]
            core = meta["name"]
            for suffix in ["股份有限公司", "有限公司", "股份", "集团", "控股",
                           "科技", "传媒", "文化", "影视"]:
                core = core.replace(suffix, "")
            if len(core.strip()) >= 2:
                meta["keywords"].append(core.strip())
            if meta["industry"] and meta["industry"] != "未知":
                meta["keywords"].append(meta["industry"])

        # 根据行业补充细分词
        industry = meta["industry"]
        # 补全行业识别：公司名含行业词但行业字段为"未知"时尝试推断
        if meta["industry"] == "未知":
            for ind_key in INDUSTRY_CHAIN:
                if ind_key in meta.get("name", ""):
                    meta["industry"] = ind_key
                    industry = ind_key
                    break
        # 从 INDUSTRY_CHAIN 动态补充上下游关键词（每侧取前3个），替代所有硬编码行业词
        for key, chain in INDUSTRY_CHAIN.items():
            if key in industry or industry in key:
                meta["keywords"].extend(chain["upstream"][:3])
                meta["keywords"].extend(chain["downstream"][:3])
                break
        meta["keywords"] = list(dict.fromkeys(meta["keywords"]))

    except Exception as e:
        print(yellow(f"baostock获取元信息失败: {e}"))
    finally:
        if bs:
            try:
                bs.logout()
            except Exception:
                pass
    return meta


# ─────────────────────────────────────────────
#  大股东 & 股东户数
# ─────────────────────────────────────────────

def fetch_holder_news(ak, symbol: str) -> List[dict]:
    """大股东持股（主要股东 → 流通股东 备用）"""
    results = []
    df, err = run_with_timeout(
        lambda: ak.stock_main_stock_holder(stock=symbol), FETCH_TIMEOUT
    )
    if not err and df is not None and not df.empty:
        for _, row in df.head(5).iterrows():
            holder = str(row.get("股东名称", row.get("holder_name", "")))
            ratio  = str(row.get("持股比例", row.get("hold_ratio", "")))
            change = str(row.get("增减", row.get("change", "")))
            if holder and holder != "nan":
                results.append({"source": "大股东", "holder": holder,
                                 "ratio": ratio, "change": change})
    if not results:
        df2, err2 = run_with_timeout(
            ak.stock_circulate_stock_holder, FETCH_TIMEOUT, args=(symbol,)
        )
        if not err2 and df2 is not None and not df2.empty:
            for _, row in df2.head(5).iterrows():
                holder = str(row.get("股东名称", ""))
                ratio  = str(row.get("持股比例", ""))
                change = str(row.get("增减股数", ""))
                if holder and holder != "nan":
                    results.append({"source": "流通股东", "holder": holder,
                                     "ratio": ratio, "change": change})
    if results:
        print(green(f"  ✓ 大股东信息 {len(results)} 条"))
    else:
        print(yellow("  ✗ 大股东：接口不可用"))
    return results


def fetch_holder_news_baostock(symbol: str) -> List[dict]:
    """baostock 十大股东（接口已失效）"""
    print(dim("  （baostock 十大股东接口已失效，跳过）"))
    return []


def fetch_top_shareholders(ak, symbol: str) -> dict:
    """
    十大流通股东，多接口依次尝试。
    列名（经过源码确认）：
      stock_circulate_stock_holder: 截止日期/公告日期/编号/股东名称/持股数量/占流通股比例/股本性质
      stock_main_stock_holder:      股东名称/持股数量/持股比例
    """
    result = {"has_data": False, "top10": [], "summary": ""}

    # 接口1：stock_circulate_stock_holder（新浪源，列名已确认）
    try:
        df, err = run_with_timeout(
            ak.stock_circulate_stock_holder, FETCH_TIMEOUT, args=(symbol,)
        )
        if not err and df is not None and not df.empty:
            holders = []
            for _, row in df.head(10).iterrows():
                name  = str(row.get("股东名称", "")).strip()
                ratio = str(row.get("占流通股比例", "")).strip()
                if name and name not in ("nan", ""):
                    holders.append(f"{name}({ratio}%)")
            if holders:
                result.update({"has_data": True, "top10": holders,
                               "summary": f"十大流通股东：{' / '.join(holders[:3])}"})
                print(green(f"  ✓ 十大流通股东 {len(holders)} 条"))
                return result
    except Exception as e:
        print(dim(f"  （流通股东接口失败：{str(e)[:50]}）"))

    # 接口2：stock_main_stock_holder（列名：股东名称/持股数量/持股比例）
    try:
        df2, err2 = run_with_timeout(
            lambda: ak.stock_main_stock_holder(stock=symbol), FETCH_TIMEOUT
        )
        if not err2 and df2 is not None and not df2.empty:
            holders = []
            for _, row in df2.head(10).iterrows():
                name  = str(row.get("股东名称", "")).strip()
                ratio = str(row.get("持股比例", "")).strip()
                if name and name not in ("nan", ""):
                    holders.append(f"{name}({ratio})")
            if holders:
                result.update({"has_data": True, "top10": holders,
                               "summary": f"主要股东：{' / '.join(holders[:3])}"})
                print(green(f"  ✓ 主要股东 {len(holders)} 条"))
                return result
    except Exception as e:
        print(dim(f"  （主要股东接口失败：{str(e)[:50]}）"))

    # 接口3：stock_zh_a_gdhs_detail_em（东方财富，列名：代码/名称/股东户数-本次 等）
    # 注意：此接口返回的是股东户数趋势，不是持股名单
    try:
        func = getattr(ak, "stock_zh_a_gdhs_detail_em", None)
        if func:
            df3, err3 = run_with_timeout(func, FETCH_TIMEOUT, kwargs={"symbol": symbol})
            if not err3 and df3 is not None and not df3.empty:
                last = df3.sort_values("股东户数统计截止日", ascending=False).iloc[0]
                holders_num = last.get("股东户数-本次", "")
                result.update({"has_data": True,
                               "summary": f"股东户数：{holders_num}（截至{last.get('股东户数统计截止日','')}）"})
                print(green(f"  ✓ 股东户数: {result['summary']}"))
                return result
    except Exception as e:
        print(dim(f"  （股东户数接口失败：{str(e)[:50]}）"))

    print(yellow("  ✗ 十大股东：所有接口均不可用"))
    return result


# ─────────────────────────────────────────────
#  龙虎榜
# ─────────────────────────────────────────────

def fetch_lhb_data(symbol: str, name: str = "") -> dict:
    """
    龙虎榜数据（含席位信息）。
    """
    result = {
        "has_lhb": False, 
        "date": "", 
        "reason": "", 
        "net_amount": "", 
        "conclusion": "",
        "details": [],  # 龙虎榜明细（含席位）
        "seats": []     # 席位分析
    }
    
    # 常见游资席位知识库
    SEAT_KNOWLEDGE = {
        "华泰证券股份有限公司深圳益田路": {
            "style": "顶级游资",
            "behavior": "擅长做连板龙头，持股周期短，快进快出",
            "signal": "积极信号，往往有3-5个涨停预期"
        },
        "中信证券股份有限公司上海溧阳路": {
            "style": "孙哥席位",
            "behavior": "擅长做热点题材，喜欢打板",
            "signal": "积极信号，热点持续性强"
        },
        "中国银河证券股份有限公司绍兴": {
            "style": "赵老哥席位",
            "behavior": "擅长做妖股，连板能力强",
            "signal": "强烈信号，妖股潜力大"
        },
        "兴业证券股份有限公司福州湖东路": {
            "style": "作手新一",
            "behavior": "擅长做趋势股和题材股",
            "signal": "积极信号，趋势向好"
        },
        "国泰君安证券股份有限公司南京太平南路": {
            "style": "著名刺客",
            "behavior": "擅长做首板和二板",
            "signal": "中性偏积极，关注后续换手"
        },
        "招商证券股份有限公司深圳蛇口工业三路": {
            "style": "欢乐海岸",
            "behavior": "擅长做妖股，锁仓能力强",
            "signal": "强烈信号，妖股确立"
        },
        "东方财富证券股份有限公司拉萨团结路": {
            "style": "散户大本营",
            "behavior": "追涨杀跌，波动大",
            "signal": "谨慎信号，注意短期波动"
        },
        "东方财富证券股份有限公司拉萨东环路": {
            "style": "散户大本营",
            "behavior": "追涨杀跌，波动大",
            "signal": "谨慎信号，注意短期波动"
        },
        "机构专用": {
            "style": "机构席位",
            "behavior": "价值投资，持股周期长",
            "signal": "积极信号，基本面认可"
        },
        "深股通专用": {
            "style": "北向资金",
            "behavior": "价值投资，注重基本面",
            "signal": "积极信号，外资看好"
        },
        "沪股通专用": {
            "style": "北向资金",
            "behavior": "价值投资，注重基本面",
            "signal": "积极信号，外资看好"
        }
    }
    
    try:
        import akshare as _ak
        from datetime import datetime, timedelta

        # 接口1：近一月统计（最轻量）
        df = None
        for period in ("近一月", "近三月"):
            try:
                df_tmp, err = run_with_timeout(
                    _ak.stock_lhb_stock_statistic_em, FETCH_TIMEOUT,
                    kwargs={"symbol": period}
                )
                if not err and df_tmp is not None and not df_tmp.empty:
                    # 列名探测：通常含"代码"或第一列是代码
                    cols = list(df_tmp.columns)
                    code_col = next(
                        (c for c in cols if "代码" in c or "stock" in c.lower()), cols[0]
                    )
                    mask = df_tmp[code_col].astype(str).str.contains(symbol, na=False)
                    matched = df_tmp[mask]
                    if not matched.empty:
                        df = matched
                        break
            except Exception:
                continue

        # 接口2：获取龙虎榜详细数据（含席位）- 使用stock_lhb_stock_detail_em
        df_detail = None
        end_date   = datetime.today()
        
        # 尝试获取最近5个交易日的龙虎榜明细
        for days_back in range(0, 5):
            d = (end_date - timedelta(days=days_back)).strftime("%Y%m%d")
            try:
                # 获取买入席位
                df_buy, err_buy = run_with_timeout(
                    _ak.stock_lhb_stock_detail_em, FETCH_TIMEOUT,
                    kwargs={"symbol": symbol, "date": d, "flag": "买入"}
                )
                if not err_buy and df_buy is not None and not df_buy.empty:
                    df_buy["_flag"] = "买入"
                    df_buy["_date"] = d
                    df_detail = df_buy
                
                # 获取卖出席位
                df_sell, err_sell = run_with_timeout(
                    _ak.stock_lhb_stock_detail_em, FETCH_TIMEOUT,
                    kwargs={"symbol": symbol, "date": d, "flag": "卖出"}
                )
                if not err_sell and df_sell is not None and not df_sell.empty:
                    df_sell["_flag"] = "卖出"
                    df_sell["_date"] = d
                    if df_detail is None:
                        df_detail = df_sell
                    else:
                        df_detail = pd.concat([df_detail, df_sell], ignore_index=True)
                
                if df_detail is not None and not df_detail.empty:
                    result["date"] = d
                    break
            except Exception as e:
                continue

        # 接口3：近期全市场明细（按日期+代码搜）
        if df is None or df.empty:
            for days_back in range(0, 5):
                d = (end_date - timedelta(days=days_back)).strftime("%Y%m%d")
                for flag in ("买入", "卖出"):
                    try:
                        df_tmp2, err2 = run_with_timeout(
                            _ak.stock_lhb_stock_detail_em, FETCH_TIMEOUT,
                            kwargs={"symbol": symbol, "date": d, "flag": flag}
                        )
                        if not err2 and df_tmp2 is not None and not df_tmp2.empty:
                            df_tmp2["_flag"] = flag
                            df_tmp2["_date"] = d
                            df = df_tmp2 if df is None else pd.concat([df, df_tmp2])
                    except Exception:
                        continue
                if df is not None and not df.empty:
                    break

        # 处理龙虎榜数据
        if df is not None and not df.empty or df_detail is not None and not df_detail.empty:
            result["has_lhb"] = True
            
            # 优先使用详细数据
            main_df = df_detail if (df_detail is not None and not df_detail.empty) else df
            
            if main_df is not None and not main_df.empty:
                cols = list(main_df.columns)
                # 调试：打印列名
                print(dim(f"  （龙虎榜数据列：{', '.join(cols[:10])}）"))
                
                net_col    = next((c for c in cols if "净买入" in c or "净额" in c), None)
                reason_col = next((c for c in cols if "上榜原因" in c or "原因" in c), None)
                date_col   = next((c for c in cols if "日期" in c or "时间" in c), None)
                
                if not result["date"] and date_col:
                    result["date"] = str(main_df.iloc[0][date_col])
                
                if reason_col:
                    result["reason"] = str(main_df.iloc[0][reason_col])
                
                # 解析席位信息 - 根据akshare stock_lhb_stock_detail_em返回的列名
                # 典型列名："序号", "交易营业部名称", "买入金额(万)", "卖出金额(万)", "净额(万)", "类型"
                seat_col = next((c for c in cols if "营业部" in c or "席位" in c), None)
                buy_col = next((c for c in cols if "买入" in c and "万" in c), None)
                sell_col = next((c for c in cols if "卖出" in c and "万" in c), None)
                net_col_seat = next((c for c in cols if "净额" in c), None)
                type_col = next((c for c in cols if "类型" in c), None)
                
                print(dim(f"  （席位列：{seat_col or '无'} | 买入列：{buy_col or '无'} | 卖出列：{sell_col or '无'}）"))
                
                if seat_col:
                    for idx, row in main_df.iterrows():
                        detail = {}
                        seat_name = str(row.get(seat_col, "")).strip()
                        if seat_name and seat_name != "nan":
                            detail["seat"] = seat_name
                            
                            # 买入金额
                            if buy_col:
                                buy_val = row.get(buy_col, "")
                                if buy_val and str(buy_val) not in ["nan", "", "None"]:
                                    detail["buy"] = f"{buy_val}万"
                            
                            # 卖出金额
                            if sell_col:
                                sell_val = row.get(sell_col, "")
                                if sell_val and str(sell_val) not in ["nan", "", "None"]:
                                    detail["sell"] = f"{sell_val}万"
                            
                            # 净额
                            if net_col_seat:
                                net_val = row.get(net_col_seat, "")
                                if net_val and str(net_val) not in ["nan", "", "None"]:
                                    detail["net"] = f"{net_val}万"
                            
                            # 类型（机构/游资等）
                            if type_col:
                                type_val = str(row.get(type_col, "")).strip()
                                if type_val and type_val not in ["nan", "", "None"]:
                                    detail["type"] = type_val
                            
                            # 匹配席位知识库
                            for known_seat, info in SEAT_KNOWLEDGE.items():
                                if known_seat in seat_name:
                                    detail["style"] = info["style"]
                                    detail["behavior"] = info["behavior"]
                                    detail["signal"] = info["signal"]
                                    break
                            
                            # 如果没有匹配到知识库，根据类型判断
                            if "style" not in detail:
                                if "机构" in seat_name or (detail.get("type") == "机构"):
                                    detail["style"] = "机构席位"
                                    detail["behavior"] = "价值投资，持股周期长"
                                    detail["signal"] = "积极信号，基本面认可"
                                elif "拉萨" in seat_name:
                                    detail["style"] = "散户大本营"
                                    detail["behavior"] = "追涨杀跌，波动大"
                                    detail["signal"] = "谨慎信号，注意短期波动"
                            
                            if detail:
                                result["details"].append(detail)
                else:
                    # 如果没有明确的席位列，打印调试信息
                    print(dim("  （未找到席位列，打印数据样例...）"))
                    print(dim(f"  （数据列：{', '.join(cols)}）"))
                    print(dim(f"  （前2行数据：\n{main_df.head(2).to_string()}）"))
                
                # 计算净额（从席位买入卖出计算）
                total_buy = 0.0
                total_sell = 0.0
                if buy_col and sell_col:
                    # 分别计算总买入和总卖出
                    for _, row in main_df.iterrows():
                        buy_val = row.get(buy_col, 0)
                        sell_val = row.get(sell_col, 0)
                        try:
                            if buy_val:
                                total_buy += float(str(buy_val).replace(",", ""))
                            if sell_val:
                                total_sell += float(str(sell_val).replace(",", ""))
                        except:
                            pass
                
                # 如果有单列净额，优先使用
                if net_col:
                    total_net = pd.to_numeric(main_df[net_col], errors="coerce").sum()
                else:
                    total_net = total_buy - total_sell
                
                if total_net != 0 or total_buy > 0 or total_sell > 0:
                    direction = "净买入" if total_net > 0 else "净卖出"
                    # akshare net_col返回的是元，需除以10000转为万元
                    # buy_col/sell_col列名含"万"则已经是万元单位，无需转换
                    if net_col and not buy_col and not sell_col:
                        # 仅从净额列汇总，单位为元，转万元
                        net_wan = abs(total_net) / 10000
                    else:
                        # 有买入/卖出列（列名含"万"），单位已是万元
                        net_wan = abs(total_net)
                    result["net_amount"] = f"{net_wan:.1f}万元"
                    result["conclusion"] = f"近期上榜{len(main_df)}次，{direction}{net_wan:.1f}万元"
                else:
                    result["conclusion"] = f"近期上榜{len(main_df)}次"
                
                print(green(f"  ✓ 龙虎榜：{result['conclusion']}"))
                if result["details"]:
                    print(dim(f"  ✓ 龙虎榜席位：{len(result['details'])} 个"))
                    # 显示前3个席位
                    for i, d in enumerate(result["details"][:3], 1):
                        print(dim(f"    {i}. {d.get('seat', '未知')[:20]}... | 买:{d.get('buy', 'N/A')} | 卖:{d.get('sell', 'N/A')} | 类型:{d.get('style', '未知')}"))
                else:
                    print(yellow(f"  ⚠ 龙虎榜无席位详情（数据列可能不匹配）"))
        else:
            # 从个股新闻标题中有 "龙虎榜" 字样，提示存在但接口无数据
            if name:
                print(dim(f"  （龙虎榜：接口无数据，请关注个股新闻中的龙虎榜标题）"))
            else:
                print(dim("  （龙虎榜：近期未上榜）"))

    except AttributeError as e:
        print(dim(f"  （龙虎榜接口不存在于当前版本：{str(e)[:50]}）"))
    except Exception as e:
        print(yellow(f"  ✗ 龙虎榜获取失败: {str(e)[:80]}"))
    
    return result


def parse_longhubang_from_news(news_list: List[dict]) -> List[dict]:
    """从个股新闻标题中解析龙虎榜结构化信息"""
    import re
    results = []
    keywords = ["龙虎榜", "登上龙虎榜", "换手率", "机构席位", "净买入", "净卖出", "上榜"]
    for news in news_list:
        title = news.get("title", "")
        if not any(kw in title for kw in keywords):
            continue
        net_match = (
            re.search(r"净买入[：:]?\s*([\d\.]+)\s*万元", title)
            or re.search(r"净卖出[：:]?\s*([\d\.]+)\s*万元", title)
            or re.search(r"买入[：:]?\s*([\d\.]+)\s*万元", title)
        )
        net_value  = float(net_match.group(1)) if net_match else 0.0
        direction  = "买入" if "净买入" in title or ("买入" in title and "净卖出" not in title) else "卖出"
        reason_m   = re.search(r"(换手率|振幅|偏离值|异常波动|机构出逃|游资炒作)", title)
        reason     = reason_m.group(1) if reason_m else "龙虎榜异动"
        date_m     = re.search(r"\d{4}[-/]\d{2}[-/]\d{2}", news.get("time", ""))
        std_date   = date_m.group(0).replace("/", "-") if date_m else ""
        results.append({
            "date":     std_date,
            "stock":    "",
            "net_buy":  net_value if direction == "买入" else -net_value,
            "reason":   reason,
            "source":   news.get("source", ""),
            "title":    title,
        })
    return results


# ─────────────────────────────────────────────
#  货币政策
# ─────────────────────────────────────────────

def fetch_monetary_policy(ak, force_refresh: bool = False) -> dict:
    """货币政策（从静态配置文件读取，原接口已失效）"""
    config_file = os.path.join(CACHE_DIR, "monetary_config.json")
    default_config = {
        "lpr":     [{"date": "2026-03-20", "1y": "3.45", "5y": "4.20"},
                    {"date": "2026-02-20", "1y": "3.45", "5y": "4.20"},
                    {"date": "2026-01-20", "1y": "3.45", "5y": "4.20"}],
        "rrr":     [{"date": "2025-09-15", "large": "9.50", "medium": "7.50"},
                    {"date": "2025-03-15", "large": "9.75", "medium": "7.75"}],
        "summary": "LPR: 1年期3.45%, 5年期4.20% | 存款准备金率: 大型9.50%, 中小型7.50%",
        "updated": datetime.now().strftime("%Y-%m-%d"),
    }
    
    if force_refresh:
        print(yellow("\n  ⚠ 货币政策接口已失效，请手动更新配置文件："))
        print(dim(f"    编辑 {config_file}，修改 LPR 和准备金率数据"))
        input("    按 Enter 继续使用现有配置...")
    
    try:
        if not os.path.exists(config_file):
            print(dim(f"  货币政策配置文件不存在，创建默认配置..."))
            with open(config_file, "w", encoding="utf-8") as f:
                json.dump(default_config, f, ensure_ascii=False, indent=2)
            print(green(f"  ✓ 已创建默认货币政策配置文件"))
        
        with open(config_file, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                raise ValueError("配置文件为空")
            result = json.loads(content)
        print(green(f"  ✓ 货币政策数据（更新于 {result.get('updated', '未知')}）"))
        return result
    except Exception as e:
        print(yellow(f"  ⚠ 货币政策配置读取失败: {e}，使用默认值"))
        try:
            with open(config_file, "w", encoding="utf-8") as f:
                json.dump(default_config, f, ensure_ascii=False, indent=2)
        except:
            pass
        return default_config


# ─────────────────────────────────────────────
#  接口可用性测试
# ─────────────────────────────────────────────

def test_critical_apis(ak):
    """
    启动时快速测试关键接口（≤10s）。
    区分三种状态：
      ✓ 接口可用
      ~ 接口存在但网络超时（运行时重试可能成功）
      ✗ 接口不存在（需升级 akshare）
    """
    import inspect as _ins
    print(bold("\n🔧 测试关键接口可用性..."))

    # 1. 个股信息（最稳定，必须通过）
    _, err = run_with_timeout(ak.stock_individual_info_em, 6, args=("000001",))
    if not err:
        print("  个股信息接口: ✓")
    else:
        err_s = str(err).lower()
        if "proxy" in err_s or "timeout" in err_s or "connection" in err_s:
            print(f"  个股信息接口: ~ 网络超时（{str(err)[:40]}）")
        else:
            print(f"  个股信息接口: ✗ {str(err)[:40]}")

    # 2. 板块行情 — stock_board_industry_spot_em
    #    只验证函数存在 + 能发起请求（不要求一定返回数据）
    spot_func = getattr(ak, "stock_board_industry_spot_em", None)
    if spot_func is None:
        print("  东方财富板块行情: ✗（akshare 无此接口，请升级）")
    else:
        # 用默认参数 '小金属' 测试，3s 内能响应即算可用
        df, err = run_with_timeout(spot_func, 5, kwargs={"symbol": "小金属"})
        if not err and df is not None and not df.empty:
            print("  东方财富板块行情: ✓")
        else:
            err_s = str(err).lower() if err else ""
            if "proxy" in err_s or "timeout" in err_s or "connection" in err_s or "curl" in err_s:
                # 网络问题，不是接口问题——用备用链仍可获取数据
                print("  东方财富板块行情: ~ 接口存在，当前网络超时（运行时将用备用链）")
            else:
                print(f"  东方财富板块行情: ✗ {str(err)[:50]}")

    # 3. 新浪个股实时行情
    real = fetch_sina_realtime("002905")
    print("  新浪个股行情:", "✓" if real else "✗")
