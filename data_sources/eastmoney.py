"""
东方财富数据源：个股新闻、行业新闻、板块 JSONP
"""
import json
import time
import random
from typing import List

from config.settings import dim, green, yellow, FETCH_TIMEOUT, STOCK_LIMIT
from core.request import robust_request
from core.timeout import run_with_timeout
from data_sources.baidu import fetch_baidu_news


def extract_json_list(data, *keys):
    """递归在 dict 中找第一个值为 list 的 key"""
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


def fetch_eastmoney_stock_news(ak, symbol: str, limit: int = None) -> List[dict]:
    """
    东方财富个股新闻（akshare 接口）。
    返回 dict 列表，字段：source / title / time / url
    """
    if limit is None:
        limit = STOCK_LIMIT
    news: List[dict] = []
    df, err = run_with_timeout(ak.stock_news_em, FETCH_TIMEOUT, args=(symbol,))
    if not err and df is not None and not df.empty:
        for _, row in df.head(limit).iterrows():
            title = str(row.get("新闻标题", "")).strip()
            if title:
                news.append({
                    "source": "东方财富·个股",
                    "title":  title,
                    "time":   str(row.get("发布时间", "")).strip(),
                    "url":    str(row.get("新闻链接", "")),
                })
        if news:
            print(green(f"  ✓ 个股新闻(东方财富) {len(news)} 条"))
    else:
        print(yellow(f"  ✗ 东方财富个股新闻失败：{str(err)[:60]}"))
    return news


def fetch_eastmoney_industry_news(ak, industry: str, limit: int = 15) -> List[dict]:
    """
    东方财富行业新闻 + 非股票相关行业新闻。
    BUG FIX: 行业名如含 'R87...' 长代码，先提取中文核心词。
    """
    from config.settings import get_industry_short_name
    short = get_industry_short_name(industry)

    news_list: List[dict] = []
    seen: set = set()

    # 行业核心词列表 - 更丰富
    industry_cores = list({short, short[:2]})
    if len(short) >= 4:
        industry_cores.append(short[2:])
    # 扩展行业相关词库
    if "影视" in short or "院线" in short:
        industry_cores.extend(["电影", "票房", "院线", "影视", "上映", "排片", "观影", "档期", "爆款", "流媒体", "内容", "文娱"])
    elif "传媒" in short:
        industry_cores.extend(["传媒", "广告", "游戏", "短视频", "直播", "内容", "文娱"])
    elif "半导体" in short:
        industry_cores.extend(["芯片", "晶圆", "光刻", "封装", "产能", "算力", "AI芯片"])
    elif "新能源" in short:
        industry_cores.extend(["锂电", "光伏", "储能", "电动车", "电池", "充电"])
    else:
        # 通用行业扩展词
        industry_cores.extend(["行业", "产业", "市场", "公司", "企业"])

    # 1. 优先 akshare 接口（股票相关新闻）
    try:
        df, err = run_with_timeout(ak.stock_news_em, FETCH_TIMEOUT, args=(short,))
        if not err and df is not None and not df.empty:
            for _, row in df.head(limit).iterrows():
                title = str(row.get("新闻标题", "")).strip()
                if not title:
                    continue
                key = title[:40]
                if key not in seen:
                    seen.add(key)
                    news_list.append({
                        "source": "东方财富行业新闻",
                        "title":  title,
                        "time":   str(row.get("发布时间", "")),
                        "url":    str(row.get("新闻链接", "")),
                    })
    except Exception as e:
        print(yellow(f"  东方财富行业新闻失败: {str(e)[:60]}"))

    # 2. 新浪关键词（股票+非股票行业新闻）
    from data_sources.sina import fetch_sina_keyword_news_as_dict
    for kw in industry_cores[:6]:
        for item in fetch_sina_keyword_news_as_dict(kw, limit=6):
            title = item["title"]
            key = title[:40]
            if key not in seen:
                seen.add(key)
                news_list.append({
                    "source": f"新浪·{kw}",
                    "title":  title,
                    "time":   item.get("time", ""),
                    "url":    item.get("url", ""),
                })

    # 3. 百度新闻补充（非股票行业新闻，fetch_baidu_news 已在模块顶部导入）
    for kw in industry_cores[:4]:
        for item in fetch_baidu_news(kw, limit=4):
            title = item.get("title", "")
            key = title[:40]
            if key not in seen:
                seen.add(key)
                news_list.append({
                    "source": item.get("source", f"百度·{kw}"),
                    "title":  title,
                    "time":   item.get("time", ""),
                    "url":    item.get("url", ""),
                })

    if news_list:
        print(green(f"  ✓ 行业新闻 {len(news_list)} 条（多源混合）"))
    else:
        print(yellow(f"  ✗ 行业新闻：所有数据源均无返回"))

    return news_list[:limit]


def fetch_eastmoney_sector_news(industry: str, search_terms: List[str],
                                 add_item_func, seen: set) -> List[dict]:
    """东方财富 JSONP 板块新闻（供 fetch_sector_news_strong 调用）"""
    source_news: List[dict] = []
    for kw in search_terms[:4]:
        callback = f"jQuery1124{random.randint(100000, 999999)}"
        param = {
            "uid": "",
            "keyword": kw,
            "type": ["cmsArticle"],
            "clientType": "web",
            "marketType": "",
            "pageIndex": 1,
            "pageSize": 20,
        }
        url = "https://search-api-web.eastmoney.com/search/jsonp"
        try:
            resp = robust_request(
                url,
                params={
                    "cb": callback,
                    "param": json.dumps(param, ensure_ascii=False),
                    "_": int(time.time() * 1000),
                },
                timeout=6,
            )
            txt = resp.text.strip()
            start, end = txt.find("("), txt.rfind(")")
            obj = json.loads(txt[start + 1:end]) if (start != -1 and end > start) else {}
            pages = extract_json_list(obj, "cmsArticle", "result") or []
            for item in pages:
                add_item_func(
                    source_news,
                    f"东方财富·{kw}",
                    str(item.get("title", "")),
                    str(item.get("showTime", item.get("date", ""))),
                    str(item.get("url", "")),
                )
        except Exception:
            continue
    return source_news
