"""
新浪数据源：关键词新闻、实时行情、板块行情、微博热搜
"""
import re
import concurrent.futures
from datetime import datetime
from typing import List, Optional

from config.settings import dim, green, yellow
from core.request import robust_request
from models.data_model import NewsItem

# 噪声过滤词（全局宏观噪声）
_NOISE = [
    "美联储", "伊朗", "特朗普", "美元", "美股", "纳斯达克",
    "英国", "欧洲", "日本", "韩国", "印度", "俄罗斯",
    "OpenAI", "谷歌", "微软", "苹果", "特斯拉", "SpaceX",
    "比特币", "原油期货", "黄金价格",
]


def fetch_sina_keyword_news(keyword: str, limit: int = 8,
                             must_contain: Optional[List[str]] = None) -> List[NewsItem]:
    """
    新浪滚动新闻按关键词搜索。
    返回 List[NewsItem]（type 固定为 "news"，调用方可覆盖）。
    """
    url = (
        f"https://feed.mix.sina.com.cn/api/roll/get"
        f"?pageid=153&lid=2509&k={keyword}&num={min(limit * 3, 30)}&page=1"
    )
    results: List[NewsItem] = []
    try:
        resp = robust_request(url, timeout=8)
        data = resp.json()
        items = (
            data.get("result", {}).get("data", [])
            if isinstance(data, dict) else []
        )
        for item in items:
            title = str(item.get("title", "")).strip()
            if not title:
                continue
            if must_contain and not any(mc in title for mc in must_contain if mc):
                continue
            if any(nw in title for nw in _NOISE):
                continue
            # 时间解析
            dt = None
            raw_time = str(item.get("ctime", item.get("mtime", ""))).strip()
            if raw_time.isdigit() and len(raw_time) == 10:
                dt = datetime.fromtimestamp(int(raw_time))
            results.append(NewsItem(
                source=f"新浪·{keyword}",
                title=title,
                time=dt,
                url=str(item.get("url", "")),
                type="news",
                relevance=1.0,
            ))
            if len(results) >= limit:
                break
    except Exception as e:
        print(dim(f"   新浪新闻请求失败({keyword}): {str(e)[:50]}"))
    return results


def fetch_sina_keyword_news_as_dict(keyword: str, limit: int = 8,
                                    must_contain: Optional[List[str]] = None) -> List[dict]:
    """返回 dict 列表（向后兼容）"""
    from models.data_model import news_item_to_dict
    return [news_item_to_dict(n) for n in fetch_sina_keyword_news(keyword, limit, must_contain)]


def fetch_multi_keyword_news(keywords: List[str], limit_per_keyword: int = 3,
                              must_contain: Optional[List[str]] = None,
                              timeout: int = 8) -> List[dict]:
    """对多个关键词并发搜索新浪新闻，合并去重，返回 dict 列表。"""
    from core.request import set_concurrent_mode
    all_news: List[dict] = []
    seen: set = set()
    if not keywords:
        return all_news

    def _fetch(kw):
        set_concurrent_mode(True)
        return fetch_sina_keyword_news_as_dict(kw, limit_per_keyword, must_contain)

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=min(10, len(keywords))
    ) as executor:
        future_to_kw = {executor.submit(_fetch, kw): kw for kw in keywords if kw}
        for future in concurrent.futures.as_completed(future_to_kw, timeout=timeout + 2):
            try:
                for item in future.result(timeout=timeout):
                    key = item["title"][:30]
                    if key not in seen:
                        seen.add(key)
                        all_news.append(item)
            except Exception:
                continue
    return all_news


def fetch_news_concurrent(keywords: List[str], limit_per_keyword: int = 4,
                           must_contain: Optional[List[str]] = None,
                           industry_keywords: Optional[List[str]] = None,
                           timeout: int = 8) -> List[dict]:
    """
    并发搜索多关键词，宽松相关性过滤，返回 dict 列表。
    """
    from core.request import set_concurrent_mode
    if not keywords:
        return []
    all_news: List[dict] = []
    seen: set = set()

    def _fetch(kw):
        set_concurrent_mode(True)
        # 优先用 search.sina.com.cn（稳定），fallback 到 feed.mix
        results = fetch_sina_search_news(kw, limit=limit_per_keyword)
        if not results:
            results = fetch_sina_keyword_news_as_dict(kw, limit_per_keyword)
        return results

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=min(8, len(keywords))
    ) as executor:
        future_to_kw = {executor.submit(_fetch, kw): kw for kw in keywords}
        for future in concurrent.futures.as_completed(future_to_kw, timeout=timeout + 2):
            try:
                for item in future.result(timeout=timeout):
                    title = item.get("title", "")
                    if not title:
                        continue
                    # 如果没有过滤条件则全部保留，否则按条件过滤
                    if must_contain or industry_keywords:
                        relevant = (
                            (must_contain and any(mc in title for mc in must_contain if mc))
                            or (industry_keywords and any(ik in title for ik in industry_keywords if ik))
                        )
                        if not relevant:
                            continue
                    key = title[:40]
                    if key not in seen:
                        seen.add(key)
                        all_news.append(item)
            except Exception:
                continue

    all_news.sort(key=lambda x: x.get("time", ""), reverse=True)
    return all_news


def fetch_sina_realtime(symbol: str) -> dict:
    """从新浪获取个股实时行情"""
    prefix = "sh" if symbol.startswith("6") else "sz"
    code = prefix + symbol
    url = f"https://hq.sinajs.cn/list={code}"
    try:
        resp = robust_request(url, timeout=5,
                              headers={"Referer": "https://finance.sina.com.cn"})
        match = re.search(r'="([^"]+)"', resp.text)
        if match:
            parts = match.group(1).split(",")
            if len(parts) >= 9:
                yesterday_close = float(parts[2]) if parts[2] else 0
                current_price   = float(parts[3]) if parts[3] else 0
                chg_pct = (
                    round((current_price - yesterday_close) / yesterday_close * 100, 2)
                    if yesterday_close else 0
                )
                return {
                    "date":   datetime.now().strftime("%Y-%m-%d"),
                    "price":  parts[3],
                    "change": f"{chg_pct:+.2f}",
                    "open":   parts[1],
                    "high":   parts[4],
                    "low":    parts[5],
                    "volume": parts[8],
                }
    except Exception:
        pass
    return {}


def fetch_sina_sector_spot(industry: str) -> dict:
    """从新浪获取行业板块实时行情"""
    import json
    try:
        url = (
            "http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php"
            "/Market_Center.getHQNodeData"
        )
        params = {
            "page": 1, "num": 100, "sort": "changepercent",
            "asc": 0, "node": "hs_industry", "_s_r_a": "page",
        }
        resp = robust_request(url, params=params, timeout=8)
        text = resp.text.strip()
        if text.startswith("(") and text.endswith(")"):
            text = text[1:-1]
        data = json.loads(text)
        for item in data:
            name = item.get("name", "")
            if industry in name or name in industry:
                return {
                    "涨跌幅":   item.get("changepercent", "0"),
                    "成交额":   item.get("turnover", "0"),
                    "换手率":   item.get("turnoverratio", "0"),
                    "上涨家数": item.get("upcount", "0"),
                    "下跌家数": item.get("downcount", "0"),
                }
    except Exception as e:
        print(dim(f"  新浪板块行情失败：{str(e)[:50]}"))
    return {}


def fetch_weibo_hotsearch(limit: int = 10) -> List[dict]:
    """获取微博热搜榜"""
    url = "https://weibo.com/ajax/side/hotSearch"
    try:
        resp = robust_request(url, timeout=8, headers={"Referer": "https://weibo.com/"})
        data = resp.json()
        hot_list = data.get("data", {}).get("realtime", [])
        return [
            {
                "source": "微博热搜",
                "title": item.get("word", ""),
                "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "url": f"https://s.weibo.com/weibo?q={item.get('word', '')}",
            }
            for item in hot_list[:limit]
            if item.get("word")
        ]
    except Exception as e:
        print(dim(f"  微博热搜获取失败: {str(e)[:50]}"))
    return []


def fetch_hotsearch_for_industry(industry: str, keywords: List[str]) -> List[dict]:
    """过滤出与行业相关的微博热搜"""
    hot_news = fetch_weibo_hotsearch(20)
    return [h for h in hot_news if any(kw in h["title"] for kw in keywords[:5])]


def fetch_sina_search_news(keyword: str, limit: int = 8) -> List[dict]:
    """
    新浪财经搜索（search.sina.com.cn） — 按标题搜索，比 feed.mix 更稳定。
    用于产业链关键词搜索。
    """
    try:
        from bs4 import BeautifulSoup
        resp = robust_request(
            "https://search.sina.com.cn/news",
            params={"q": keyword, "range": "title", "c": "news",
                    "sort": "time", "num": str(limit * 2)},
            timeout=10,
        )
        soup = BeautifulSoup(resp.text, "html.parser")
        results = []
        for item in soup.select(".box-result, .result-item, li.news-item")[:limit]:
            title_tag = item.select_one("h2 a, h3 a, .news-title a, a")
            time_tag  = item.select_one(".fgray_time, .time, .date, span.time")
            if not title_tag:
                continue
            title = title_tag.get_text(strip=True)
            url   = title_tag.get("href", "")
            t     = time_tag.get_text(strip=True) if time_tag else ""
            src_tag = item.select_one(".fgray_time span, .source, .from")
            source = src_tag.get_text(strip=True) if src_tag else "新浪财经"
            if title and len(title) >= 5:
                results.append({
                    "title":  title,
                    "url":    url,
                    "time":   t,
                    "source": source,
                })
        return results[:limit]
    except Exception as e:
        print(dim(f"   新浪搜索失败({keyword}): {str(e)[:50]}"))
        return []
