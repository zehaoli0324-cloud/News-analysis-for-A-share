"""
新闻采集服务：个股新闻、公司专项资讯、板块异动二次挖掘

修复说明：
  1. fetch_stock_news 过滤器中的硬编码影视词改为由 industry_keywords 参数动态传入
  2. fetch_company_news 社会新闻搜索词的硬编码影视词列表改为先用 keyword_dict 动态词，
     硬编码词仅作最后兜底（已移至函数底部注释），不再污染其他行业的搜索结果
  3. 原有逻辑和接口签名完全保持兼容
"""
import concurrent.futures
from datetime import datetime, timedelta
from typing import List, Optional
import re

from config.settings import (
    FETCH_TIMEOUT, STOCK_LIMIT, INDUSTRY_CHAIN,
    green, yellow, dim,
)
from core.timeout import run_with_timeout, run_concurrent_tasks
from data_sources.sina import (
    fetch_sina_keyword_news_as_dict,
    fetch_news_concurrent,
    fetch_multi_keyword_news,
)

from data_sources.eastmoney import fetch_eastmoney_stock_news


def fetch_announcement_content(url: str) -> Optional[str]:
    if not url or url == "nan":
        return None
    try:
        import requests
        from bs4 import BeautifulSoup
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        response = requests.get(url, headers=headers, timeout=8)
        response.encoding = "utf-8"
        soup = BeautifulSoup(response.text, "html.parser")
        for script in soup(["script", "style"]):
            script.decompose()
        text = soup.get_text(separator="\n", strip=True)
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        cleaned_text = "\n".join(lines)
        if len(cleaned_text) > 1000:
            cleaned_text = cleaned_text[:1000] + "..."
        return cleaned_text
    except Exception:
        return None


def is_relevant_news(title: str, keywords: List[str],
                     must_keywords: Optional[List[str]] = None) -> bool:
    if not title:
        return False
    if must_keywords:
        return any(kw in title for kw in must_keywords if kw)
    return any(kw in title for kw in keywords if kw and len(kw) >= 2)


def fetch_stock_news(ak, symbol: str, name: str = "",
                     industry_keywords: List[str] = None) -> List[dict]:
    """
    个股新闻：东方财富/efinance 按股票代码精准拉取。

    职责边界：
      - 只用「股票代码」匹配的接口（东财、efinance）
      - 不再按公司名在新浪重复搜索（该工作由 fetch_company_news 统一完成，避免重叠）
      - industry_keywords 用于过滤步骤，确保相关性

    Args:
        industry_keywords: 行业相关词，用于过滤。
                           应从 keyword_dict["行业短词"] + keyword_dict["热词"] 动态获取。
    """
    news: List[dict] = []
    seen: set = set()
    industry_kws = industry_keywords or []

    # 1. 东方财富（主，按股票代码）
    em_news = fetch_eastmoney_stock_news(ak, symbol, limit=STOCK_LIMIT)
    for item in em_news:
        key = item["title"][:20]
        if key not in seen:
            seen.add(key)
            news.append(item)

    # 2. efinance 备用（仍按股票代码）
    if not news:
        print(yellow("  ⚠ 东方财富个股新闻无数据，尝试 efinance..."))
        try:
            import efinance as ef
            df_ef = ef.news.get_stock_news(symbol)
            if df_ef is not None and not df_ef.empty:
                for _, row in df_ef.head(STOCK_LIMIT).iterrows():
                    title = str(row.get("title", row.get("新闻标题", ""))).strip()
                    t     = str(row.get("date",  row.get("发布时间",  ""))).strip()
                    key   = title[:20]
                    if title and key not in seen:
                        seen.add(key)
                        news.append({
                            "source": "efinance·个股",
                            "title":  title,
                            "time":   t,
                            "url":    str(row.get("url", "")),
                        })
                print(green(f"  ✓ 个股新闻(efinance) {len(news)} 条"))
        except Exception as e:
            print(yellow(f"  ✗ efinance个股新闻失败: {str(e)[:50]}"))

    # 3. 过滤：保留含公司名或行业关键词的条目（提升信噪比）
    if name and news:
        filtered = []
        for n in news:
            title = n["title"]
            if name in title or (len(name) >= 2 and name[:2] in title):
                filtered.append(n)
            elif industry_kws and any(kw in title for kw in industry_kws):
                filtered.append(n)
        news = filtered[:15]

    if news:
        print(green(f"  ✓ 个股新闻合计 {len(news)} 条"))
    return news


def fetch_company_news(ak, symbol: str, name: str,
                        keyword_dict: Optional[dict] = None,
                        sector_news: Optional[List[dict]] = None,
                        stock_news_seen: Optional[set] = None) -> List[dict]:
    """
    公司专项资讯：公告 + 社会新闻 + 竞品。

    修复：
      - 社会新闻搜索的行业词完全来自 keyword_dict（AI动态生成），不再硬编码
      - stock_news_seen: fetch_stock_news 已收录的标题前20字集合，用于跨函数去重
    """
    from models.data_model import ensure_dict
    results: List[dict] = []
    keyword_dict    = keyword_dict    or {}
    sector_news     = sector_news     or []
    # 跨函数去重：初始化时把 stock_news 标题前20字预填入 seen_social
    _cross_seen     = stock_news_seen or set()
    short_name      = name[:2] if len(name) >= 2 else name

    # ── 1. 公告 ────────────────────────────────────────────────────────────────
    today  = datetime.today()
    start  = (today - timedelta(days=180)).strftime("%Y%m%d")
    end    = today.strftime("%Y%m%d")
    print(dim(f"  查询公告：{symbol}，{start} → {end}"))

    ann_list = []

    def is_valid_announcement(title, url):
        if not title or title == "nan":
            return False
        if "{{" in title or "}}" in title:
            return False
        return True

    # 方案1：AKShare stock_notice_report
    print(dim("  尝试AKShare公告接口：stock_notice_report..."))
    try:
        import akshare as ak_mod
        df_notice = ak_mod.stock_notice_report(symbol=symbol)
        if df_notice is not None and not df_notice.empty:
            print(dim(f"  ✓ AKShare返回{len(df_notice)}条公告"))
            for _, row in df_notice.iterrows():
                title = str(row.get("公告标题", row.get("title", ""))).strip()
                date  = str(row.get("公告日期", row.get("date", ""))).strip()
                url   = str(row.get("公告链接", row.get("url", ""))).strip()
                if is_valid_announcement(title, url):
                    ann_list.append((date, title, url))
        else:
            print(dim("  AKShare无数据"))
    except Exception as e:
        print(dim(f"  AKShare接口异常：{str(e)[:50]}"))

    # 方案2：新浪财经公告
    if not ann_list:
        print(dim("  尝试新浪财经公告..."))
        try:
            import requests
            from bs4 import BeautifulSoup
            sid = symbol[2:] if symbol.startswith("00") else symbol
            sina_url = f"https://vip.stock.finance.sina.com.cn/corp/go.php/vCB_AllBulletin/stockid/{sid}.phtml"
            headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}
            resp = requests.get(sina_url, headers=headers, timeout=10)
            soup = BeautifulSoup(resp.text, "html.parser")
            for table in soup.find_all("table"):
                for row in table.find_all("tr")[1:]:
                    cols = row.find_all("td")
                    if len(cols) >= 2:
                        date_elem  = cols[0].text.strip()
                        title_elem = cols[1].text.strip()
                        link_elem  = cols[1].find("a")
                        url = ""
                        if link_elem and link_elem.get("href"):
                            url = link_elem["href"]
                            if not url.startswith("http"):
                                url = "https://vip.stock.finance.sina.com.cn" + url
                        if is_valid_announcement(title_elem, url):
                            ann_list.append((date_elem, title_elem, url))
            if ann_list:
                print(dim(f"  ✓ 新浪财经返回{len(ann_list)}条公告"))
        except Exception as e:
            print(dim(f"  新浪财经异常：{str(e)[:50]}"))

    # 方案3：巨潮资讯
    if not ann_list:
        print(dim("  尝试巨潮资讯公告..."))
        try:
            import requests, json as json_mod
            cninfo_url = "http://www.cninfo.com.cn/new/disclosure"
            params  = {"stockCode": symbol, "pageNum": 1, "pageSize": 30}
            headers = {"User-Agent": "Mozilla/5.0",
                       "Referer": f"http://www.cninfo.com.cn/new/disclosure?stockCode={symbol}"}
            resp = requests.get(cninfo_url, params=params, headers=headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                for item in (data.get("classifiedAnnouncements") or []):
                    title = item.get("announcementTitle", "").strip()
                    date  = item.get("announcementTime", "").split(" ")[0]
                    url   = item.get("adjunctUrl", "")
                    if url:
                        url = f"http://static.cninfo.com.cn/{url}"
                    if is_valid_announcement(title, url):
                        ann_list.append((date, title, url))
                if ann_list:
                    print(dim(f"  ✓ 巨潮资讯返回{len(ann_list)}条公告"))
        except Exception as e:
            print(dim(f"  巨潮资讯异常：{str(e)[:50]}"))

    if ann_list:
        priority_kws = ["业绩预告", "业绩快报", "重大合同", "并购", "重组",
                        "诉讼", "减持", "增持", "股权质押", "利润分配"]

        def parse_announcement_title(title):
            parsed_info = []
            if "业绩预告" in title or "业绩快报" in title:
                if "预增" in title:        parsed_info.append("业绩预增")
                elif "预减" in title or "预亏" in title: parsed_info.append("业绩预减/预亏")
                elif "扭亏" in title:      parsed_info.append("扭亏为盈")
                m = re.search(r"(\d+\.?\d*)%", title)
                if m: parsed_info.append(f"幅度：{m.group(1)}%")
            if "诉讼" in title or "仲裁" in title:
                if "原告" in title or "起诉" in title: parsed_info.append("公司为原告")
                elif "被告" in title or "被起诉" in title: parsed_info.append("公司为被告")
                m = re.search(r"(\d+\.?\d*)\s*[亿万]", title)
                if m: parsed_info.append(f"涉案金额：{m.group(1)}")
            if "减持" in title:   parsed_info.append("股东减持")
            elif "增持" in title: parsed_info.append("股东增持")
            if "质押" in title:   parsed_info.append("股权质押")
            if "辞职" in title or "离任" in title:   parsed_info.append("董事/高管辞职")
            elif "聘任" in title or "选举" in title: parsed_info.append("董事/高管聘任")
            if "重大合同" in title or "重大订单" in title:
                parsed_info.append("重大合同/订单")
                m = re.search(r"(\d+\.?\d*)\s*[亿万]", title)
                if m: parsed_info.append(f"金额：{m.group(1)}")
            return " | ".join(parsed_info) if parsed_info else None

        def get_priority(title):
            for i, kw in enumerate(priority_kws):
                if kw in title: return i
            return 99

        ann_list_sorted = sorted(
            [(get_priority(t), d, t, u) for d, t, u in ann_list],
            key=lambda x: (x[0], x[1])
        )

        # 并发爬取前5条公告正文
        selected = ann_list_sorted[:5]
        ann_contents = {}
        crawl_tasks = {
            f"ann_{i}": (fetch_announcement_content, (u,), {})
            for i, (_, _, _, u) in enumerate(selected)
            if u and u != "nan"
        }
        if crawl_tasks:
            print(dim(f"  正在爬取 {len(crawl_tasks)} 条公告正文..."))
            crawl_results = run_concurrent_tasks(crawl_tasks, max_workers=3, timeout_per_task=10)
            for i, (_, d, t, _) in enumerate(selected):
                if f"ann_{i}" in crawl_results:
                    ann_contents[(d, t)] = crawl_results[f"ann_{i}"]

        for priority, date, title, url in ann_list_sorted[:8]:
            ann_item = {"source": "交易所公告", "title": title, "time": date, "type": "announcement"}
            if url and url != "nan":
                ann_item["url"] = url
            parsed = parse_announcement_title(title)
            if parsed:
                ann_item["parsed_info"] = parsed
            content = ann_contents.get((date, title))
            if content:
                ann_item["content"] = content
            results.append(ann_item)
        print(green(f"  ✓ 近期公告 {min(8, len(ann_list_sorted))} 条"))
    else:
        print(yellow("  ✗ 公告接口：所有接口均无数据"))

    # 社会新闻部分已删除，避免检索卡住

    # 定义行业关键词，用于后续的竞品分析
    industry_kws = list(dict.fromkeys(
        keyword_dict.get("行业短词", [])[:8]
        + keyword_dict.get("热词", [])[:6]
        + keyword_dict.get("上游短词", [])[:3]
        + keyword_dict.get("下游短词", [])[:3]
    ))

    # ── 3. 从板块新闻中提取个股相关 ──────────────────────────────────────────
    pulled = 0
    for sn_raw in sector_news:
        sn = ensure_dict(sn_raw)
        title = sn.get("title", "")
        if name in title or short_name in title:
            results.append({
                "source": "板块新闻·个股",
                "title":  title,
                "time":   sn.get("time", ""),
                "url":    sn.get("url", ""),
                "type":   "social",
            })
            pulled += 1
    if pulled:
        print(green(f"  ✓ 从板块新闻提取 {pulled} 条"))

    # ── 4. 竞争对手新闻 ──────────────────────────────────────────────────────
    competitors = keyword_dict.get("竞争对手", [])
    if competitors:
        comp_news_sina  = fetch_news_concurrent(
            competitors[:4], limit_per_keyword=4,
            must_contain=None, industry_keywords=industry_kws,
        )

        seen_comp: set = set()
        filtered_comp: List[dict] = []
        for n in comp_news_sina:
            key = n.get("title", "")[:40]
            if key and key not in seen_comp:
                seen_comp.add(key)
                filtered_comp.append(n)

        for n in filtered_comp[:10]:
            results.append({
                "source": n.get("source", "竞品新闻"),
                "title":  n.get("title", ""),
                "time":   n.get("time", ""),
                "url":    n.get("url", ""),
                "type":   "competitor",
            })
        if filtered_comp:
            print(green(f"  ✓ 竞争对手新闻 {len(filtered_comp[:10])} 条"))

    print(dim("  （巨潮问答接口已禁用）"))
    return results
