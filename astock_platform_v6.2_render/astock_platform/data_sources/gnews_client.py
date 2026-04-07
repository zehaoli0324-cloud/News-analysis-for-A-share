"""
多维度资讯采集客户端  v2.5
使用gnews库进行新闻搜索
"""
from typing import List, Dict, Optional
import concurrent.futures
import time

try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False

try:
    from gnews import GNews
    HAS_GNEWS = True
except ImportError:
    HAS_GNEWS = False

from core.request import robust_request


# ─────────────────────────────────────────────
#  基础搜索函数 - 使用gnews库
# ─────────────────────────────────────────────

def _gnews_search(keyword: str, limit: int = 10, language: str = 'zh', country: str = 'CN') -> List[Dict]:
    """使用gnews库搜索新闻"""
    if not keyword or not keyword.strip():
        return []
    
    results = []
    
    # 尝试gnews搜索
    if HAS_GNEWS:
        try:
            # 为政策和监管关键词使用英文搜索，可能会获得更多结果
            if any(kw in keyword for kw in ['政策', '监管', '新规', '扶持']):
                # 尝试英文搜索
                try:
                    google_news_en = GNews(language='en', country='US', max_results=limit)
                    news_list_en = google_news_en.get_news(keyword)
                    for article in news_list_en:
                        # 尝试获取文章内容
                        content = ""
                        try:
                            from gnews import Article
                            article_obj = Article(article.get('url', ''))
                            article_obj.download()
                            article_obj.parse()
                            content = article_obj.text
                        except Exception:
                            pass
                        
                        results.append({
                            "title": article.get('title', ''),
                            "url": article.get('url', ''),
                            "time": article.get('published date', ''),
                            "source": article.get('publisher', {}).get('title', 'Google News (EN)'),
                            "type": "gnews",
                            "content": content,
                        })
                except Exception:
                    pass
            
            # 中文搜索
            google_news = GNews(language=language, country=country, max_results=limit)
            news_list = google_news.get_news(keyword)
            
            for article in news_list:
                # 尝试获取文章内容
                content = ""
                try:
                    from gnews import Article
                    article_obj = Article(article.get('url', ''))
                    article_obj.download()
                    article_obj.parse()
                    content = article_obj.text
                except Exception:
                    pass
                
                results.append({
                    "title": article.get('title', ''),
                    "url": article.get('url', ''),
                    "time": article.get('published date', ''),
                    "source": article.get('publisher', {}).get('title', 'Google News'),
                    "type": "gnews",
                    "content": content,
                })
        except Exception as e:
            pass  # 静默处理错误，避免刷屏
    
    # 如果还是没有结果，尝试新浪搜索
    if not results:
        try:
            sina_news = _sina_search(keyword, limit=limit)
            results.extend(sina_news)
        except Exception as e:
            pass  # 静默处理错误，避免刷屏
    
    return results


def _sina_search(keyword: str, limit: int = 8) -> List[Dict]:
    """新浪财经关键词搜索（备用）"""
    if not keyword or not keyword.strip():
        return []
    try:
        params = {"q": keyword, "range": "title", "c": "news", "sort": "time", "num": str(limit)}
        resp = robust_request(
            "https://search.sina.com.cn/news",
            params=params,
            timeout=8,
        )
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        results = []
        
        for item in soup.select(".box-result")[:limit]:
            title_tag = item.select_one("h2 a")
            time_tag  = item.select_one(".fgray_time")
            if not title_tag:
                continue
            title = title_tag.get_text(strip=True)
            url   = title_tag.get("href", "")
            t     = time_tag.get_text(strip=True) if time_tag else ""
            src_tag = item.select_one(".fgray_time span")
            source = src_tag.get_text(strip=True) if src_tag else "新浪财经"
            
            # 尝试获取文章内容
            content = ""
            try:
                if url:
                    article_resp = robust_request(url, timeout=5)
                    article_soup = BeautifulSoup(article_resp.text, "html.parser")
                    # 提取正文内容
                    content_tags = article_soup.select(".article-content, .content, .article-body, .main-content")
                    if content_tags:
                        content = " ".join([tag.get_text(strip=True) for tag in content_tags])
                    else:
                        # 尝试提取所有p标签内容
                        p_tags = article_soup.select("p")
                        content = " ".join([tag.get_text(strip=True) for tag in p_tags[:20]])  # 只取前20个p标签
            except Exception:
                pass
            
            if title and len(title) >= 5:
                results.append({
                    "title":  title,
                    "url":    url,
                    "time":   t,
                    "source": source,
                    "type":   "sina_search",
                    "content": content,
                })
        
        return results
    except Exception as e:
        print(f"  新浪搜索失败({keyword}): {str(e)[:30]}")
        return []


def _dedup(news_list: List[Dict]) -> List[Dict]:
    seen, out = set(), []
    for n in news_list:
        key = n.get("title", "")
        if key and key not in seen:
            seen.add(key)
            out.append(n)
    return out


def _filter_low_value_news(news_list: List[Dict]) -> List[Dict]:
    """过滤掉低价值的新闻，如百度百科、百度文库等"""
    low_value_sources = [
        "百度百科", "百度文库", "维基百科", "互动百科",
        "360百科", "搜狗百科", "知乎百科", "百度知道",
        "词典", "字典", "百科", "文库", "知道"
    ]
    
    low_value_keywords = [
        "- 百度百科", "- 百度文库", "(中国普通高等学校本科专业)",
        "法律知识", "需求分析", "解析:", "产业现状",
        "技术支持", "售后服务", "保障及承诺",
        "是什么意思", "什么是", "如何", "怎样",
        "艺术形式与技术手段", "观众权益", "观众需求",
        "金逸国际电影城 - 百度百科", "观众权益法律知识", "观众需求分析"
    ]
    
    high_value_keywords = [
        "政策", "监管", "法规", "措施", "影响",
        "投资", "价值", "机会", "风险", "前景",
        "上涨", "下跌", "涨停", "跌停", "涨幅", "跌幅",
        "业绩", "财报", "利润", "营收", "亏损",
        "合作", "收购", "并购", "重组", "上市",
        "新品", "创新", "技术", "突破", "发展",
        "市场", "行业", "板块", "龙头", "竞争",
        "上游", "下游", "产业链", "供应商", "渠道",
        "消费者", "影院", "内容", "企业", "扶持",
        "制片厂", "影视基地", "广告主", "电影院"
    ]
    
    filtered = []
    for news in news_list:
        source = news.get("source", "")
        if any(low_source in source for low_source in low_value_sources):
            continue
        
        title = news.get("title", "")
        
        if "百度百科" in title or "百度文库" in title or "百度知道" in title:
            continue
        
        if any(low_keyword in title for low_keyword in low_value_keywords):
            continue
        
        if len(title) < 10:
            continue
        
        has_high_value = any(high_keyword in title for high_keyword in high_value_keywords)
        if not has_high_value:
            chain_keywords = ["上游", "下游", "产业链", "供应商", "渠道", "消费者", "影院", "内容", "企业", "扶持", "制片厂", "影视基地", "广告主", "电影院"]
            has_chain_keyword = any(chain_keyword in title for chain_keyword in chain_keywords)
            if not has_chain_keyword:
                continue
        
        filtered.append(news)
    
    return filtered


# ─────────────────────────────────────────────
#  主入口
# ─────────────────────────────────────────────

def fetch_gnews_comprehensive(
    symbol: str,
    name: str,
    short_name: str,
    sector: str,
    competitors: List[str],
    upstream: List[str],
    downstream: List[str],
    policy_keywords: List[str],
    business_keywords: Optional[List[str]] = None,
    max_workers: int = 6,
    max_searches: int = 50,  # 限制最大搜索次数
    max_results_per_search: int = 8,  # 限制每次搜索的结果数
    progress_callback=None  # 进度回调函数：callback(done, total, pct, cat, query, count)
) -> Dict:
    """
    并发获取多维资讯（公司舆情、板块舆情、产业链、竞品、政策）
    使用gnews库进行搜索
    """
    
    # 存储结果的数据结构
    company_sentiment = {"by_name": [], "by_short": [], "by_code": []}
    sector_sentiment = []
    chain = {"upstream": [], "downstream": [], "policy": []}
    comp_raw = {c: [] for c in (competitors or [])}
    policy_raw = []
    business_raw = []
    
    # 构建搜索任务列表
    tasks = []

    # 公司舆情：8条
    company_search_terms = [
        name, short_name, symbol,
        f"{name} 新闻", f"{short_name} 新闻",
        f"{name} 业绩", f"{name} 涨跌", f"{short_name} 股价",
    ]
    for term in company_search_terms:
        if term:
            tasks.append(("company_sentiment", "by_name", term))

    # 板块舆情：5条
    sector_suffixes = [
        f"{sector} 新闻", f"{sector} 行业",
        f"{sector} 板块", f"{sector} 政策", f"{sector} 资金",
    ]
    for suffix in sector_suffixes:
        tasks.append(("sector_sentiment", "_", suffix))

    # 产业链 - 上游：最多5个关键词，每个2条 = 10条
    for kw in (upstream or [])[:5]:
        if kw:
            tasks.append(("industry_chain", "upstream", kw))
            tasks.append(("industry_chain", "upstream", f"{kw} {sector}"))

    # 产业链 - 下游：最多5个关键词，每个2条 = 10条
    for kw in (downstream or [])[:5]:
        if kw:
            tasks.append(("industry_chain", "downstream", kw))
            tasks.append(("industry_chain", "downstream", f"{kw} {sector}"))

    # 竞品：最多4家，每家1条 = 4条
    for comp in (competitors or [])[:4]:
        if comp:
            tasks.append(("competitor", comp, comp))
            tasks.append(("competitor", comp, f"{comp} 新闻"))

    # 政策：来自参数的+通用政策词 = ~8条
    for kw in (policy_keywords or [])[:5]:
        if kw:
            tasks.append(("policy", "_", kw))
    for term in [f"{sector} 政策", f"{sector} 监管", f"{sector} 新规"]:
        tasks.append(("policy", "_", term))

    # 商业/业务关键词：最多4条
    for kw in (business_keywords or [])[:4]:
        if kw:
            tasks.append(("business", "_", kw))

    # 去重（同一query不搜两次）
    seen_queries = set()
    deduped_tasks = []
    for t in tasks:
        if t[2] not in seen_queries:
            seen_queries.add(t[2])
            deduped_tasks.append(t)
    tasks = deduped_tasks[:max_searches]
    
    # 并发执行搜索任务
    _gnews_completed = [0]  # mutable counter

    def _worker(task):
        category, sub_key, query = task
        try:
            results = _gnews_search(query, limit=max_results_per_search)
            _gnews_completed[0] += 1
            pct = int(_gnews_completed[0] / len(tasks) * 100)
            cat_zh = {"company_sentiment":"公司","sector_sentiment":"板块","industry_chain":"产业链","competitor":"竞品","policy":"政策","business":"业务"}
            cat_name = cat_zh.get(category, category)
            q_safe = query[:24].replace("|","")
            # 构造进度字符串
            prog_line = f"__GNEWS_PROGRESS__|{_gnews_completed[0]}|{len(tasks)}|{pct}|{cat_name}|{q_safe}|{len(results)}"
            print(prog_line)  # 保留print供terminal显示
            if progress_callback:
                try:
                    progress_callback(_gnews_completed[0], len(tasks), pct, cat_name, q_safe, len(results))
                except Exception:
                    pass
            return (category, sub_key, results)
        except Exception as e:
            _gnews_completed[0] += 1
            pct = int(_gnews_completed[0] / len(tasks) * 100)
            q_safe = query[:24].replace("|","")
            prog_line = f"__GNEWS_PROGRESS__|{_gnews_completed[0]}|{len(tasks)}|{pct}|失败|{q_safe}|0"
            print(prog_line)
            if progress_callback:
                try:
                    progress_callback(_gnews_completed[0], len(tasks), pct, "失败", q_safe, 0)
                except Exception:
                    pass
            return (category, sub_key, [])
    
    total = len(tasks)
    if HAS_TQDM:
        pbar = tqdm(total=total, desc="多维资讯搜索", unit="次")
    
    completed = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_task = {executor.submit(_worker, t): t for t in tasks}
        for future in concurrent.futures.as_completed(future_to_task):
            category, sub_key, results = future.result()
            
            if category == "company_sentiment":
                company_sentiment[sub_key].extend(results)
            elif category == "sector_sentiment":
                sector_sentiment.extend(results)
            elif category == "industry_chain":
                chain[sub_key].extend(results)
            elif category == "competitor":
                comp_raw[sub_key].extend(results)
            elif category == "policy":
                policy_raw.extend(results)
            elif category == "business":
                business_raw.extend(results)
            
            completed += 1
            if HAS_TQDM:
                pbar.update(1)
    
    if HAS_TQDM:
        pbar.close()
    
    total_results = sum(len(v) for v in company_sentiment.values()) + len(sector_sentiment) + len(chain['upstream']) + len(chain['downstream']) + len(policy_raw) + len(business_raw)
    print(f"  多维搜索完成：{completed} 次查询，共 {total_results} 条结果")
    
    # 去重和过滤
    for k in company_sentiment:
        company_sentiment[k] = _filter_low_value_news(_dedup(company_sentiment[k]))
    sector_sentiment = _filter_low_value_news(_dedup(sector_sentiment))
    chain["upstream"] = _filter_low_value_news(_dedup(chain["upstream"]))
    chain["downstream"] = _filter_low_value_news(_dedup(chain["downstream"]))
    chain["policy"] = _filter_low_value_news(_dedup(policy_raw))
    for comp in comp_raw:
        comp_raw[comp] = _filter_low_value_news(_dedup(comp_raw[comp]))
    business = _filter_low_value_news(_dedup(business_raw))
    
    return {
        "company_sentiment": company_sentiment,
        "sector_sentiment": sector_sentiment,
        "industry_chain": chain,
        "competitors": comp_raw,
        "policy": chain["policy"],
        "business": business
    }


# 便捷函数
fetch_gnews = fetch_gnews_comprehensive
