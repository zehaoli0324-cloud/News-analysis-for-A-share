"""
腾讯数据源：个股历史行情、板块新闻
"""
import time
from datetime import datetime
from typing import List

from config.settings import dim
from core.request import robust_request


def get_market_prefix(symbol: str) -> str:
    """根据股票代码返回市场前缀"""
    symbol = str(symbol).strip()
    if symbol.startswith(("688", "689")):
        return "sh"
    if symbol.startswith(("8", "4")):
        return "bj"
    if symbol.startswith(("30", "301")):
        return "sz"
    if symbol.startswith("6"):
        return "sh"
    if symbol.startswith(("0", "2", "3")):
        return "sz"
    return "sz"


def fetch_tencent_spot(symbol: str, name: str = "") -> dict:
    """腾讯接口获取个股最近5日收盘价"""
    if not symbol.startswith(("sz", "sh")):
        prefix = get_market_prefix(symbol)
        tx_symbol = prefix + symbol
    else:
        tx_symbol = symbol

    url = (
        f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
        f"?param={tx_symbol},day,,,5"
    )
    try:
        resp = robust_request(url, timeout=8)
        data = resp.json()
        if data.get("code") == 0:
            node = data["data"].get(tx_symbol, {}).get("day", [])
            if node and len(node) > 0:
                last = node[-1]
                if isinstance(last, (list, tuple)) and len(last) >= 6:
                    prev = node[-2] if len(node) >= 2 else last
                    close = float(last[2])
                    prev_close = float(prev[2])
                    chg_pct = (
                        round((close - prev_close) / prev_close * 100, 2)
                        if prev_close else 0
                    )
                    return {
                        "date":   last[0],
                        "price":  str(close),
                        "change": f"{chg_pct:+.2f}",
                        "open":   str(last[1]),
                        "high":   str(last[3]),
                        "low":    str(last[4]),
                        "volume": str(last[5]),
                    }
    except Exception:
        pass
    return {}


def fetch_tencent_sector_news(industry: str, search_terms: List[str],
                               add_item_func, seen: set) -> List[dict]:
    """腾讯新闻搜索板块新闻（供 fetch_sector_news_strong 调用）"""
    source_news: List[dict] = []
    for kw in search_terms[:4]:
        try:
            resp = robust_request(
                "https://r.inews.qq.com/getrecommend",
                params={
                    "chlid": "news_news_finance",
                    "devid": f"cursor-{int(time.time())}",
                    "k": kw,
                    "count": 20,
                },
                timeout=8,
            )
            data = resp.json()
            from services.stock_service import extract_json_list
            candidates = extract_json_list(data, "list", "news", "items") or []
            for item in candidates:
                add_item_func(
                    source_news,
                    f"腾讯财经·{kw}",
                    str(item.get("title", item.get("sTitle", ""))),
                    str(item.get("time", item.get("publish_time", ""))),
                    str(item.get("url", item.get("vurl", ""))),
                )
        except Exception:
            continue
    return source_news
