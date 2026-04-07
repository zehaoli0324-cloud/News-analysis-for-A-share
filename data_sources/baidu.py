"""
百度 & 门户数据源：百度新闻搜索、网易/腾讯财经首页、宏观事件
"""
import re
import time
from datetime import datetime
from typing import List

from config.settings import dim, green, yellow
from core.request import robust_request


def fetch_baidu_news(keyword: str, limit: int = 10) -> List[dict]:
    """百度新闻搜索（移动版接口）"""
    # 尝试不同的百度新闻接口
    urls = [
        "https://m.baidu.com/s",
        "https://www.baidu.com/s",
    ]
    
    for url in urls:
        params = {"tn": "news", "word": keyword, "pn": 0, "rn": limit, "ie": "utf-8"}
        headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36",
            "Referer": "https://m.baidu.com/",
        }
        try:
            resp = robust_request(url, params=params, headers=headers, timeout=8)
            
            # 尝试不同的正则表达式模式
            patterns = [
                (r'<h3 class="c-title".*?<a[^>]*>(.*?)</a>', r'<a[^>]+href="(https?://[^"]+)"[^>]*>'),
                (r'<h3 class="t".*?<a[^>]*>(.*?)</a>', r'<a[^>]+href="(https?://[^"]+)"[^>]*>'),
            ]
            
            for title_pattern, link_pattern in patterns:
                titles = re.findall(title_pattern, resp.text, re.DOTALL)
                links = re.findall(link_pattern, resp.text)
                clean_titles = [re.sub(r"<[^>]+>", "", t).strip() for t in titles]
                results = []
                for title, link in zip(clean_titles, links):
                    if title and len(title) > 5:
                        results.append({
                            "source": f"百度新闻·{keyword}",
                            "title": title,
                            "time": "",
                            "url": link,
                        })
                if results:
                    return results[:limit]
        except Exception as e:
            print(dim(f"  百度新闻搜索失败({keyword}): {str(e)[:30]}"))
    return []


def fetch_baidu_sector_news(industry: str, search_terms: List[str],
                             add_item_func, seen: set) -> List[dict]:
    """百度财经板块新闻（供 fetch_sector_news_strong 调用）"""
    source_news: List[dict] = []
    for kw in search_terms[:4]:
        try:
            resp = robust_request(
                "https://finance.pae.baidu.com/selfselect/news",
                params={"query": kw, "pn": 0, "rn": 20, "ut": "", "type": "news"},
                timeout=8,
            )
            from data_sources.eastmoney import extract_json_list
            data = resp.json()
            candidates = extract_json_list(data, "Result", "list") or []
            for item in candidates:
                add_item_func(
                    source_news,
                    f"百度财经·{kw}",
                    str(item.get("title", item.get("news_title", ""))),
                    str(item.get("time", item.get("publish_time", ""))),
                    str(item.get("url", item.get("news_url", ""))),
                )
        except Exception:
            continue
    return source_news


def fetch_baidu_news_sector(industry: str, search_terms: List[str],
                             add_item_func, seen: set) -> List[dict]:
    """百度新闻搜索（板块关键词，供 fetch_sector_news_strong 调用）"""
    source_news: List[dict] = []
    for kw in search_terms[:3]:
        for item in fetch_baidu_news(kw, limit=5):
            add_item_func(
                source_news,
                f"百度新闻·{kw}",
                item["title"],
                item.get("time", ""),
                item.get("url", ""),
            )
    return source_news


def fetch_portal_news(limit: int = 10) -> List[dict]:
    """从网易财经和腾讯财经首页抓取新闻标题（替代已失效的财联社接口）"""
    news: List[dict] = []
    seen: set = set()

    # 网易财经
    try:
        resp = robust_request("https://money.163.com/", timeout=8)
        titles = re.findall(r'<a[^>]*href="[^"]*"[^>]*>([^<]{10,})</a>', resp.text)
        for title in titles:
            title = title.strip()
            if len(title) > 10 and "财经" not in title and title not in seen:
                seen.add(title)
                news.append({
                    "source":       "网易财经",
                    "title":        title,
                    "time":         datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "url":          "",
                    "relevance":    0.5,
                    "relevance_ai": 0,
                })
                if len(news) >= limit // 2:
                    break
        print(green(f"  ✓ 网易财经新闻 {len(news)} 条"))
    except Exception as e:
        print(dim(f"  网易财经抓取失败: {e}"))

    # 腾讯财经（短暂延迟）
    time.sleep(0.3)
    try:
        resp = robust_request("https://finance.qq.com/", timeout=8)
        titles = re.findall(r'<a[^>]*href="[^"]*"[^>]*>([^<]{10,})</a>', resp.text)
        count_before = len(news)
        for title in titles:
            title = title.strip()
            if len(title) > 10 and title not in seen:
                seen.add(title)
                news.append({
                    "source":       "腾讯财经",
                    "title":        title,
                    "time":         datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "url":          "",
                    "relevance":    0.5,
                    "relevance_ai": 0,
                })
                if len(news) >= limit:
                    break
        print(green(f"  ✓ 腾讯财经新闻 {len(news) - count_before} 条"))
    except Exception as e:
        print(dim(f"  腾讯财经抓取失败: {e}"))

    return news[:limit]


def fetch_macro_calendar(ak) -> List[dict]:
    """宏观事件（接口已失效，返回空）"""
    print(yellow("  ✗ 宏观日历接口当前不可用，已跳过"))
    return []
