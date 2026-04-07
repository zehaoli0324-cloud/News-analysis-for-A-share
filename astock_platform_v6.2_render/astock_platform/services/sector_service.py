"""
板块数据服务：即时行情、资金流向、概念、行业新闻、产业链新闻、异动挖掘
BUG FIX 汇总：
  1. fetch_sector_data 返回值绝不为 None（改为空 dict 兜底）
  2. sector_data.get("sector_news") 的元素先 ensure_dict 再访问
  3. 行业名长代码（R87...）通过 get_industry_short_name 映射
  4. fund_flow_hist 接口失败时静默跳过，不 crash
  5. stock_board_industry_spot_em 空 DataFrame 处理
"""
import concurrent.futures
import xml.etree.ElementTree as ET
from typing import List, Optional

import pandas as pd

from config.settings import (
    FETCH_TIMEOUT, SECTOR_TIMEOUT, ENABLE_SLOW_APIS, INDUSTRY_CHAIN,
    green, yellow, dim, cyan, get_industry_short_name,
)
from core.timeout import run_with_timeout, run_concurrent_tasks, run_concurrent_tasks_with_progress
from data_sources.sina import (
    fetch_sina_keyword_news_as_dict,
    fetch_multi_keyword_news,
    fetch_hotsearch_for_industry,
    fetch_sina_sector_spot,
)
from data_sources.eastmoney import fetch_eastmoney_industry_news, fetch_eastmoney_sector_news
from data_sources.baidu import fetch_baidu_sector_news, fetch_baidu_news_sector, fetch_baidu_news
from models.data_model import ensure_dict


# ─────────────────────────────────────────────
#  辅助
# ─────────────────────────────────────────────

def is_sector_relevant(title: str, industry: str) -> bool:
    if not title or not industry:
        return False
    if industry in title:
        return True
    for i in range(len(industry) - 1):
        if industry[i:i+2] in title:
            return True
    return False


def _fetch_10jqka_sector(industry: str, search_terms: List[str],
                          add_item_func, seen: set) -> List[dict]:
    """同花顺板块新闻"""
    from core.request import robust_request
    from data_sources.eastmoney import extract_json_list
    source_news: List[dict] = []
    for kw in search_terms[:4]:
        try:
            resp = robust_request(
                "https://news.10jqka.com.cn/tapp/news/push/stock/",
                params={"page": 1, "tag": kw, "track": "website", "pagesize": 20},
                timeout=8,
            )
            data = resp.json()
            candidates = extract_json_list(data, "list", "data", "result") or []
            for item in candidates:
                add_item_func(
                    source_news,
                    f"同花顺·{kw}",
                    str(item.get("title", item.get("newsTitle", ""))),
                    str(item.get("time", item.get("publish_time", ""))),
                    str(item.get("url", item.get("newsUrl", ""))),
                )
        except Exception:
            continue
    return source_news


def _fetch_sina_sector(industry: str, search_terms: List[str],
                        add_item_func, seen: set) -> List[dict]:
    """新浪板块新闻（并发版，避免串行 sleep 叠加）"""
    import concurrent.futures as cf
    source_news: List[dict] = []

    def _one(kw):
        return fetch_sina_keyword_news_as_dict(kw, limit=5)

    with cf.ThreadPoolExecutor(max_workers=min(4, len(search_terms))) as ex:
        futures = {ex.submit(_one, kw): kw for kw in search_terms[:6]}
        for future in cf.as_completed(futures, timeout=10):
            kw = futures[future]
            try:
                for item in future.result():
                    add_item_func(
                        source_news,
                        f"新浪财经·{kw}",
                        item.get("title", ""),
                        item.get("time", ""),
                        item.get("url", ""),
                    )
            except Exception:
                continue
    return source_news


def fetch_sector_news_strong(industry: str, keywords: Optional[List[str]] = None) -> List[dict]:
    """
    强力板块新闻多源并发爬取，返回 dict 列表。
    BUG FIX: industry 先映射为短名再做匹配。
    """
    short = get_industry_short_name(industry)
    keywords = keywords or []
    seen: set = set()
    max_limit = 15

    terms = [short, short[:2]]
    if len(short) > 2:
        terms.append(short[2:])
    terms.extend(
        [kw for kw in keywords if isinstance(kw, str) and 2 <= len(kw) <= 3][:6]
    )
    search_terms = list(dict.fromkeys(t.strip() for t in terms if t and len(t.strip()) >= 2))

    def normalize(news_list):
        def _time_key(x):
            ts = str(x.get("time", ""))
            return "".join(ch for ch in ts if ch.isdigit())
        news_list.sort(key=_time_key, reverse=True)
        return news_list[:max_limit]

    def add_item(target: list, source: str, title: str, t: str = "", url: str = ""):
        title = (title or "").strip()
        if not title or not is_sector_relevant(title, short):
            return
        key = title[:20]
        if key in seen:
            return
        seen.add(key)
        target.append({"source": source, "title": title,
                        "time": (t or "").strip(), "url": (url or "").strip(),
                        "relevance": 1})

    sources = {
        "eastmoney": lambda: fetch_eastmoney_sector_news(short, search_terms, add_item, seen),
        "10jqka":    lambda: _fetch_10jqka_sector(short, search_terms, add_item, seen),
        "sina":      lambda: _fetch_sina_sector(short, search_terms, add_item, seen),
        "baidu":     lambda: fetch_baidu_sector_news(short, search_terms, add_item, seen),
        "baidu_news": lambda: fetch_baidu_news_sector(short, search_terms, add_item, seen),
    }
    all_news: List[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(sources)) as executor:
        futures = {executor.submit(func): name for name, func in sources.items()}
        for future in concurrent.futures.as_completed(futures):
            try:
                result = future.result(timeout=10)
                if result:
                    all_news.extend(result)
            except Exception:
                continue

    if all_news:
        return normalize(all_news)

    # RSS 兜底
    source_news: List[dict] = []
    rss_sources = [
        ("证券时报", "http://www.stcn.com/rss/stcn_all.xml"),
        ("中国证券网", "http://www.cnstock.com/v_news/rss_list.xml"),
    ]
    for sname, rss_url in rss_sources:
        try:
            from core.request import robust_request
            resp = robust_request(rss_url, timeout=8)
            text = resp.text
            try:
                root = ET.fromstring(text)
                for item in root.findall(".//item"):
                    title = (item.findtext("title") or "").strip()
                    link  = (item.findtext("link") or "").strip()
                    pub   = (item.findtext("pubDate") or "").strip()
                    add_item(source_news, f"{sname}·RSS", title, pub, link)
            except Exception:
                import re
                for title in re.findall(r"<title><!\[CDATA\[(.*?)\]\]></title>", text):
                    add_item(source_news, f"{sname}·RSS", title, "", "")
        except Exception:
            continue
    return normalize(source_news) if source_news else []


# ─────────────────────────────────────────────
#  板块异动二次挖掘
# ─────────────────────────────────────────────

def fetch_sector_movement_reason(ak, industry: str, sector_data: dict,
                                  hist_volatility: float = 2.0) -> List[dict]:
    """
    板块异动动态阈值搜索。
    BUG FIX: sector_data 可能为 None → 默认空 dict。
    """
    sector_data = sector_data or {}
    spot = sector_data.get("spot", {})
    chg_str = spot.get("涨跌幅", "0")
    try:
        chg = float(str(chg_str).replace("%", "").replace("+", ""))
    except Exception:
        chg = 0.0

    threshold = max(1.0, hist_volatility * 0.4)  # 降低触发阈值
    if abs(chg) < threshold:
        # 即使没有达到阈值，也尝试搜索少量驱动原因
        print(dim(f"  板块涨跌{abs(chg):.1f}%，尝试挖掘潜在驱动因素..."))
    else:
        direction = "大涨" if chg > 0 else "大跌"
        short = get_industry_short_name(industry)
        print(cyan(f"\n  📊 {short}板块{direction}{abs(chg):.1f}%，触发异动原因挖掘..."))

    short = get_industry_short_name(industry)
    # 扩展搜索关键词
    base_terms = [f"{short}{'大涨' if chg > 0 else '大跌'}", f"{short}原因", f"{short}最新", f"{short}行情"]
    extra_terms = (
        ["机构买入", "北向流入", "利好", "政策"] if chg > 0 else ["机构卖出", "北向流出", "利空", "监管"]
    )
    search_terms = base_terms + extra_terms
    all_news = fetch_multi_keyword_news(search_terms, limit_per_keyword=5, timeout=8)

    # 领涨/领跌个股
    try:
        df_stocks = ak.stock_board_industry_cons_em(symbol=short)
        if df_stocks is not None and not df_stocks.empty:
            df_stocks["涨跌幅"] = pd.to_numeric(df_stocks["涨跌幅"], errors="coerce")
            top = (
                df_stocks.nlargest(1, "涨跌幅").iloc[0]["名称"]
                if chg > 0 else
                df_stocks.nsmallest(1, "涨跌幅").iloc[0]["名称"]
            )
            all_news.extend(fetch_sina_keyword_news_as_dict(top, limit=5))
            # 再加1个领涨/领跌股
            try:
                top2 = (
                    df_stocks.nlargest(2, "涨跌幅").iloc[1]["名称"]
                    if chg > 0 else
                    df_stocks.nsmallest(2, "涨跌幅").iloc[1]["名称"]
                )
                if top2 != top:
                    all_news.extend(fetch_sina_keyword_news_as_dict(top2, limit=3))
            except Exception:
                pass
    except Exception:
        pass

    return all_news[:15]


# ─────────────────────────────────────────────
#  板块综合数据
# ─────────────────────────────────────────────

def fetch_sector_data(ak, industry: str, keywords: Optional[List[str]] = None,
                       keyword_dict: Optional[dict] = None) -> dict:
    """
    板块综合数据并发采集。
    BUG FIX:
      - 始终返回非 None dict（任何异常都有兜底）
      - 行业名映射为短名后再传给 akshare
      - spot 空 DataFrame 静默处理
      - fund_flow_hist 失败静默跳过
    """
    if not industry:
        print(yellow("  ✗ 板块数据：未知行业，跳过"))
        return {"industry": "", "spot": {}, "fund_flow": {}, "fund_history": [],
                "concepts": [], "sector_news": [], "leader_news": [], "chain_news": []}

    short = get_industry_short_name(industry)
    keywords    = keywords    or []
    keyword_dict = keyword_dict or {}

    data = {
        "industry":     short,
        "spot":         {},
        "fund_flow":    {},
        "fund_history": [],
        "concepts":     [],
        "sector_news":  [],
        "leader_news":  [],
        "chain_news":   [],
    }

    subtasks = {}
    # 板块行情（AKShare spot接口不稳定，已禁用，改用新浪轻量接口）
    # subtasks["spot"] = (ak.stock_board_industry_spot_em, (short,), {})  # 禁用：超时频繁
    # 资金流排行已禁用（接口不可达）

    subtasks["sector_news"] = (fetch_eastmoney_industry_news, (ak, short, 15), {})
    # 微博热搜已禁用（与股票分析相关性低，且接口不稳定）
    # subtasks["weibo_hot"] = (fetch_hotsearch_for_industry, (short, keywords or []), {})

    sub_results = run_concurrent_tasks_with_progress(
        subtasks, max_workers=3, timeout_per_task=8, desc=None
    )

    # ── 1. 即时行情（仅用新浪轻量接口，AKShare接口超时已禁用）──
    sina_spot = fetch_sina_sector_spot(short)
    if sina_spot:
        data["spot"] = sina_spot
        print(green(f"  ✓ {short}板块今日：涨跌幅={sina_spot.get('涨跌幅', '?')}%"))
    else:
        print(dim("  （板块行情：接口无数据，跳过）"))

    # ── 2. 资金流排行：已禁用（接口不可达）────────────────────

    # ── 3. 概念板块 ────────────────────────────────
    df_concept = sub_results.get("concepts")
    if df_concept is not None and not df_concept.empty:
        try:
            name_col = next(
                (c for c in df_concept.columns if "板块名称" in c or "名称" in c), None
            )
            if name_col:
                kws = [short]
                if len(short) >= 2:
                    kws.append(short[:2])
                if len(short) >= 4:
                    kws.append(short[2:])
                for key, chain in INDUSTRY_CHAIN.items():
                    if key in short or short in key:
                        kws.extend(chain.get("upstream",   [])[:2])
                        kws.extend(chain.get("downstream", [])[:2])
                        break
                kws = list(dict.fromkeys([kw for kw in kws if kw]))

                matched = pd.DataFrame()
                if short in df_concept[name_col].values:
                    matched = df_concept[df_concept[name_col] == short]
                if matched.empty:
                    for kw in kws:
                        tmp = df_concept[df_concept[name_col].str.contains(kw, na=False, case=False)]
                        if not tmp.empty:
                            matched = pd.concat([matched, tmp])
                if not matched.empty:
                    matched = matched.drop_duplicates(subset=[name_col])
                    data["concepts"] = matched[name_col].tolist()

            if data["concepts"]:
                print(green(f"  ✓ 相关概念 {len(data['concepts'])} 个：{' / '.join(data['concepts'][:6])}"))
            else:
                print(yellow("  ⚠ 未找到相关概念板块"))
        except Exception as e:
            print(yellow(f"  ⚠ 概念板块解析失败：{str(e)[:50]}"))
    else:
        print(dim("  （概念板块：无数据）"))

    # ── 5. 行业新闻 ────────────────────────────────
    sector_news_list = sub_results.get("sector_news") or []
    data["sector_news"] = sector_news_list
    if sector_news_list:
        print(green(f"  ✓ 行业新闻 {len(sector_news_list)} 条"))
    else:
        print(yellow("  ✗ 行业新闻：所有数据源均无返回"))

    # ── 6. 微博热搜：已禁用（与股票分析相关性低）─────────────

    # ── 7. 产业链新闻：已禁用（由GNews模块提供更高质量的产业链数据） ──────────
    # 注：GNews在main.py中单独调用，质量更好，这里不再重复调取
    if False:  # 禁用早期产业链调取
        upstream   = keyword_dict.get("上游短词", [])[:5]   # 增加到5个
        downstream = keyword_dict.get("下游短词", [])[:5]   # 增加到5个
        if not upstream or not downstream:
            for key, chain in INDUSTRY_CHAIN.items():
                if key in short or short in key:
                    upstream   = chain.get("upstream", [])[:5]
                    downstream = chain.get("downstream", [])[:5]
                    break
        policy_kws    = ["行业政策", "监管", "新规", "扶持"]  # 增加政策词
        all_chain_kws = list(dict.fromkeys(upstream + downstream + policy_kws))[:10]  # 增加到10个
    
        if all_chain_kws:
            print(dim(f"  产业链关键词（共{len(all_chain_kws)}个）：{all_chain_kws}"))
    
            # 并发任务：新浪搜索（search.sina.com.cn，稳定）+ 百度
            from data_sources.sina import fetch_sina_search_news
            chain_tasks = {}
            for kw in all_chain_kws:
                chain_tasks[f"sina_{kw}"]  = (fetch_sina_search_news,      (kw,), {"limit": 6})
            for kw in all_chain_kws[:5]:
                chain_tasks[f"baidu_{kw}"] = (fetch_baidu_news, (kw,), {"limit": 4})
    
            chain_results = run_concurrent_tasks(
                chain_tasks, max_workers=10, timeout_per_task=10
            )
    
            # 放宽噪声过滤：只过滤最明显的国际噪声
            _CHAIN_NOISE = [
                "美联储", "特朗普", "美国关税", "美股", "纳斯达克",
                "乌克兰", "俄罗斯", "比特币", "原油期货", "美元指数",
            ]
            # 放宽白名单：只要有行业相关就保留
            _CHAIN_ALLOW = list(dict.fromkeys(
                [short, short[:2]] + upstream + downstream
                + keyword_dict.get("公司简称", [])[:3]
                + keyword_dict.get("竞争对手", [])[:3]
                + ["A股", "股市", "上市", "板块", "行业", "中国", "国内"]
            ))
            
            # 传媒/影视/院线行业必须包含的词（避免匹配到无关行业）
            media_must_words = ["影视", "电影", "院线", "传媒", "影院", "票房", "观影", "影片"]
            is_media_industry = any(x in short for x in ["影视", "院线", "传媒"])
    
            seen_chain: set = set()
            for kw in all_chain_kws:
                cat = ("upstream" if kw in upstream else
                       "downstream" if kw in downstream else "policy")
                
                # 处理新浪结果
                for item in (chain_results.get(f"sina_{kw}") or []):
                    title = item.get("title", "")
                    key   = title[:30]  # 增加key长度减少误判
                    if not title or len(title) < 6 or key in seen_chain:  # 降低标题长度要求
                        continue
                    if any(noise in title for noise in _CHAIN_NOISE):
                        continue
                    # 不再强制要求标题含行业词：search.sina 搜索结果标题本就相关
                    if kw in title or any(allow in title for allow in _CHAIN_ALLOW if allow):
                        seen_chain.add(key)
                        data["chain_news"].append({
                            "source":  f"新浪·{kw}",
                            "keyword": kw,
                            "title":   title,
                            "time":    item.get("time", ""),
                            "chain":   cat,
                        })
                
                # 处理百度结果
                for item in (chain_results.get(f"baidu_{kw}") or []):
                    title = item.get("title", "")
                    key   = title[:30]
                    if not title or len(title) < 6 or key in seen_chain:
                        continue
                    if any(noise in title for noise in _CHAIN_NOISE):
                        continue
                    if kw in title or any(allow in title for allow in _CHAIN_ALLOW if allow):
                        seen_chain.add(key)
                        data["chain_news"].append({
                            "source":  f"百度·{kw}",
                            "keyword": kw,
                            "title":   title,
                            "time":    item.get("time", ""),
                            "chain":   cat,
                        })
            
            # 如果还是没有，直接用行业词放宽搜索不做严格过滤
            if not data["chain_news"]:
                print(dim(f"  产业链新闻降级：放宽过滤条件..."))
                for kw in all_chain_kws[:5]:
                    cat = ("upstream" if kw in upstream else
                           "downstream" if kw in downstream else "policy")
                    for item in (chain_results.get(f"sina_{kw}") or []):
                        title = item.get("title", "")
                        key = title[:30]
                        if not title or key in seen_chain:
                            continue
                        if any(noise in title for noise in _CHAIN_NOISE):
                            continue
                        seen_chain.add(key)
                        data["chain_news"].append({
                            "source":  f"新浪·{kw}",
                            "keyword": kw,
                            "title":   title,
                            "time":    item.get("time", ""),
                            "chain":   cat,
                        })
                    if len(data["chain_news"]) >= 10:
                        break
            
            if data["chain_news"]:
                pc = sum(1 for n in data["chain_news"] if n["chain"] == "policy")
                uc = sum(1 for n in data["chain_news"] if n["chain"] == "upstream")
                dc = sum(1 for n in data["chain_news"] if n["chain"] == "downstream")
                print(green(f"  ✓ 产业链新闻 {len(data['chain_news'])} 条"
                            f"（政策{pc}/上游{uc}/下游{dc}）"))
            else:
                print(yellow("  ⚠ 产业链新闻：所有数据源均无相关内容"))
        else:
            print(yellow("  ⚠ 未找到产业链关键词"))
    
    # ── 8. 板块异动 ────────────────────────────────
    movement_news = fetch_sector_movement_reason(ak, short, data)
    if movement_news:
        data["sector_news"].extend(movement_news)
        print(green(f"  ✓ 板块异动线索 {len(movement_news)} 条"))

    return data   # 始终返回 dict，绝不是 None
