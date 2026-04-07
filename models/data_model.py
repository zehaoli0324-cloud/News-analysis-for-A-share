"""
统一数据结构模型
"""
from dataclasses import dataclass, field
from typing import List, Optional
from datetime import datetime


@dataclass
class NewsItem:
    """统一新闻/资讯数据模型"""
    source: str                          # 来源
    title: str                           # 标题
    time: Optional[datetime]             # 时间（标准化为 datetime 对象）
    url: str = ""                        # 链接
    type: str = "news"                   # 类型：stock/industry/social/announcement/competitor/macro
    relevance: float = 1.0               # 相关度（0-1）
    extra: dict = field(default_factory=dict)  # 扩展字段


def news_item_to_dict(ni: "NewsItem") -> dict:
    """NewsItem → 旧格式 dict，兼容 export_excel 等函数"""
    d = {
        "source":    ni.source,
        "title":     ni.title,
        "url":       ni.url,
        "relevance": ni.relevance,
        "type":      ni.type,
    }
    if ni.time:
        d["time"] = ni.time.strftime("%Y-%m-%d %H:%M")
    else:
        d["time"] = ""
    d.update(ni.extra)
    return d


def dict_to_news_item(item: dict, news_type: str = "news",
                      default_source: str = "未知来源") -> "NewsItem":
    """旧格式 dict → NewsItem"""
    dt = None
    time_str = str(item.get("time", "")).strip()
    if time_str and time_str != "nan":
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(time_str, fmt)
                break
            except ValueError:
                continue
    return NewsItem(
        source=item.get("source", default_source),
        title=item.get("title", ""),
        time=dt,
        url=item.get("url", ""),
        type=item.get("type", news_type),
        relevance=float(item.get("relevance", 1.0)),
        extra={k: v for k, v in item.items()
               if k not in ("source", "title", "time", "url", "relevance", "type")},
    )


def ensure_dict(item) -> dict:
    """
    兼容层：如果传入的是 NewsItem，转成 dict；如果已是 dict，直接返回。
    解决 'NewsItem' object is not subscriptable 问题。
    """
    if isinstance(item, NewsItem):
        return news_item_to_dict(item)
    return item
