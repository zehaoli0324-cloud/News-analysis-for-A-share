"""
竞品对比分析服务
用于对比目标公司与竞品的各项指标
"""
from typing import List, Dict
from datetime import datetime


class CompetitorAnalyzer:
    """竞品分析器"""
    
    def __init__(self, target_symbol: str, target_name: str, competitors: List[str]):
        """
        初始化竞品分析器
        
        Args:
            target_symbol: 目标股票代码
            target_name: 目标公司名称
            competitors: 竞品公司名称列表
        """
        self.target_symbol = target_symbol
        self.target_name = target_name
        self.competitors = competitors
    
    def analyze_news_sentiment(self, target_news: List[Dict], 
                                competitor_news: Dict[str, List[Dict]]) -> Dict:
        """
        分析新闻情绪对比
        
        Returns:
            {
                'target': {'positive': X, 'neutral': Y, 'negative': Z},
                'competitors': {
                    '万达电影': {'positive': X, 'neutral': Y, 'negative': Z},
                    ...
                },
                'comparison': '目标公司情绪优于/劣于竞品'
            }
        """
        result = {
            'target': self._count_sentiment(target_news),
            'competitors': {},
            'comparison': ''
        }
        
        for comp_name, news_list in competitor_news.items():
            result['competitors'][comp_name] = self._count_sentiment(news_list)
        
        # 生成对比结论
        result['comparison'] = self._generate_sentiment_comparison(
            result['target'], result['competitors']
        )
        
        return result
    
    def _count_sentiment(self, news_list: List[Dict]) -> Dict:
        """统计新闻情绪（简化版，实际可用NLP模型）"""
        positive_keywords = [
            '增长', '上涨', '利好', '突破', '创新', '获奖', '合作', '扩张',
            '盈利', '超预期', '新高', '大涨', '涨停', '净利润增', '营收增',
            '票房', '爆款', '热映', '满座', '签约', '中标', '获批', '定档',
            '翻倍', '连板', '封板', '涨幅', '新签', '中签', '拿下', '高增',
        ]
        negative_keywords = [
            '下跌', '亏损', '裁员', '诉讼', '违规', '退市', '暴跌', '风险',
            '减持', '质押', '降级', '预警', '撤档', '停映', '下调',
            '净利润降', '营收降', '失败', '处罚', '立案', '被告',
            '跌停', '盘中跌停', '触及跌停', '大跌', '跌幅', '净卖出',
            '低迷', '萎缩', '亏损', '爆雷', '暴雷', '崩盘', '闪崩',
        ]
        
        positive = 0
        negative = 0
        neutral = 0
        
        for news in news_list:
            title = news.get('title', '') + news.get('description', '')
            pos_count = sum(1 for kw in positive_keywords if kw in title)
            neg_count = sum(1 for kw in negative_keywords if kw in title)
            
            if pos_count > neg_count:
                positive += 1
            elif neg_count > pos_count:
                negative += 1
            else:
                neutral += 1
        
        total = len(news_list) if news_list else 1
        return {
            'positive': positive,
            'neutral': neutral,
            'negative': negative,
            'total': len(news_list),
            'positive_ratio': f"{positive/total*100:.1f}%",
            'negative_ratio': f"{negative/total*100:.1f}%"
        }
    
    def _generate_sentiment_comparison(self, target_sentiment: Dict, 
                                        comp_sentiments: Dict) -> str:
        """生成情绪对比结论"""
        if not comp_sentiments:
            return "无竞品数据，无法对比"
        
        # 计算竞品平均情绪
        avg_positive = sum(
            s['positive'] for s in comp_sentiments.values()
        ) / len(comp_sentiments)
        avg_negative = sum(
            s['negative'] for s in comp_sentiments.values()
        ) / len(comp_sentiments)
        
        target_pos = target_sentiment['positive']
        target_neg = target_sentiment['negative']
        
        if target_pos > avg_positive and target_neg < avg_negative:
            return f"✅ {self.target_name}情绪优于行业平均水平（正面新闻更多）"
        elif target_pos < avg_positive and target_neg > avg_negative:
            return f"❌ {self.target_name}情绪劣于行业平均水平（负面新闻更多）"
        else:
            return f"⚖️ {self.target_name}情绪与行业平均水平相当"
    
    def analyze_price_performance(self, target_change: float,
                                   competitor_changes: Dict[str, float]) -> Dict:
        """
        分析股价表现对比
        
        Args:
            target_change: 目标公司涨跌幅
            competitor_changes: 竞品涨跌幅字典 {'万达电影': -2.5, ...}
            
        Returns:
            {
                'target': X.X%,
                'competitors': {...},
                'ranking': '行业排名X/Y',
                'conclusion': '表现优于/劣于行业'
            }
        """
        result = {
            'target': f"{target_change:.2f}%",
            'competitors': competitor_changes,
            'ranking': '',
            'conclusion': ''
        }
        
        # 计算排名
        all_changes = list(competitor_changes.values()) + [target_change]
        all_changes_sorted = sorted(all_changes, reverse=True)
        
        try:
            rank = all_changes_sorted.index(target_change) + 1
            total = len(all_changes)
            result['ranking'] = f"行业排名 {rank}/{total}"
            
            # 生成结论
            avg_change = sum(all_changes) / len(all_changes)
            if target_change > avg_change:
                result['conclusion'] = f"✅ 表现优于行业平均（{target_change:.2f}% vs 平均{avg_change:.2f}%）"
            elif target_change < avg_change:
                result['conclusion'] = f"❌ 表现劣于行业平均（{target_change:.2f}% vs 平均{avg_change:.2f}%）"
            else:
                result['conclusion'] = f"⚖️ 表现与行业平均相当（{target_change:.2f}%）"
        except:
            result['ranking'] = "无法计算排名"
            result['conclusion'] = "数据不足"
        
        return result
    
    def generate_comparison_report(self, target_data: Dict, 
                                    competitor_data: Dict) -> str:
        """
        生成对比分析报告
        
        Args:
            target_data: 目标公司数据
            competitor_data: 竞品数据字典
            
        Returns:
            对比分析报告文本
        """
        lines = []
        lines.append(f"\n{'='*60}")
        lines.append(f"📊 {self.target_name} vs 行业竞品对比分析")
        lines.append(f"{'='*60}")
        
        # 1. 新闻情绪对比
        lines.append("\n一、新闻情绪对比")
        lines.append(f"{'─'*60}")
        
        if 'news_sentiment' in target_data:
            target_sent = target_data['news_sentiment']
            lines.append(f"\n【{self.target_name}】")
            lines.append(f"  新闻总数：{target_sent['total']}条")
            lines.append(f"  正面：{target_sent['positive']}条 ({target_sent['positive_ratio']})")
            lines.append(f"  负面：{target_sent['negative']}条 ({target_sent['negative_ratio']})")
            lines.append(f"  中性：{target_sent['neutral']}条")
        
        if 'competitor_news_sentiment' in competitor_data:
            lines.append(f"\n【竞品对比】")
            for comp_name, sentiment in competitor_data['competitor_news_sentiment'].items():
                lines.append(f"\n  {comp_name}:")
                lines.append(f"    新闻数：{sentiment['total']}条")
                lines.append(f"    正面：{sentiment['positive_ratio']} | 负面：{sentiment['negative_ratio']}")
        
        # 注：股价对比已移除（接口返回数据全为0，无实际参考价值）
        # 仅保留新闻情绪对比，有实质内容
        lines.append(f"\n{'─'*60}")
        lines.append("\n二、综合结论")
        lines.append(f"{'─'*60}")
        if 'sentiment_comparison' in target_data:
            lines.append(f"\n{target_data['sentiment_comparison']}")
        lines.append(f"\n  ⚠️ 竞品当日股价数据暂不可用（接口限制），请人工查询同行涨跌对比")
        
        lines.append(f"\n{'='*60}")
        
        return "\n".join(lines)


def fetch_competitor_data(competitor_symbols: List[str]) -> Dict[str, Dict]:
    """
    获取竞品数据（股价、新闻等）
    
    Args:
        competitor_symbols: 竞品股票代码列表，如 ['002739', '603103']
        
    Returns:
        {
            '002739': {'name': '万达电影', 'change': -2.5, 'news': [...]},
            '603103': {'name': '横店影视', 'change': -1.8, 'news': [...]},
        }
    """
    # 这里需要接入实际的数据源
    # 简化版：返回空数据，实际使用时需要接入AKShare或其他数据源
    return {}


def analyze_competitors(target_symbol: str, target_name: str,
                        target_change: float, target_news: List[Dict],
                        competitor_names: List[str],
                        competitor_data: Dict[str, Dict]) -> str:
    """
    便捷的竞品分析函数

    Returns:
        对比分析报告文本
    """
    analyzer = CompetitorAnalyzer(target_symbol, target_name, competitor_names)
    
    # 分析新闻情绪
    comp_news = {}
    comp_price_changes = {}
    
    for comp_name, data in competitor_data.items():
        comp_news[comp_name] = data.get('news', [])
        comp_price_changes[comp_name] = data.get('change', 0)
    
    sentiment_analysis = analyzer.analyze_news_sentiment(target_news, comp_news)
    price_analysis = analyzer.analyze_price_performance(target_change, comp_price_changes)
    
    # 生成报告
    target_data = {
        'news_sentiment': sentiment_analysis['target'],
        'price_change': target_change,
        'sentiment_comparison': sentiment_analysis['comparison'],
        'price_comparison': price_analysis['conclusion']
    }
    
    competitor_analysis = {
        'competitor_news_sentiment': sentiment_analysis['competitors'],
        'competitor_price_changes': comp_price_changes
    }
    
    report = analyzer.generate_comparison_report(target_data, competitor_analysis)
    
    # 增加更多分析维度
    additional_analysis = []
    additional_analysis.append("\n四、行业地位分析")
    additional_analysis.append("─" * 60)
    
    # 分析目标公司在行业中的地位
    if competitor_names:
        additional_analysis.append(f"\n【{target_name}】在行业中的主要竞争对手：")
        for i, comp in enumerate(competitor_names[:3], 1):
            additional_analysis.append(f"  {i}. {comp}")
    
    # 增加投资建议
    additional_analysis.append("\n五、投资建议")
    additional_analysis.append("─" * 60)
    
    # 基于情绪和价格表现生成投资建议
    if sentiment_analysis['comparison'].startswith("✅") and price_analysis['conclusion'].startswith("✅"):
        additional_analysis.append("\n📈 投资建议：考虑关注")
        additional_analysis.append("  - 情绪面和股价表现均优于行业平均")
        additional_analysis.append("  - 建议进一步研究公司基本面和财务数据")
    elif sentiment_analysis['comparison'].startswith("❌") or price_analysis['conclusion'].startswith("❌"):
        additional_analysis.append("\n📉 投资建议：谨慎观望")
        additional_analysis.append("  - 情绪面或股价表现劣于行业平均")
        additional_analysis.append("  - 建议等待更多正面信号")
    else:
        additional_analysis.append("\n➖ 投资建议：中性")
        additional_analysis.append("  - 情绪面和股价表现与行业平均相当")
        additional_analysis.append("  - 建议关注行业整体趋势")
    
    # 增加风险提示
    additional_analysis.append("\n六、风险提示")
    additional_analysis.append("─" * 60)
    additional_analysis.append("\n⚠️ 风险因素：")
    additional_analysis.append("  - 行业政策变化风险")
    additional_analysis.append("  - 市场竞争加剧风险")
    additional_analysis.append("  - 宏观经济环境变化风险")
    additional_analysis.append("  - 公司基本面变化风险")
    
    additional_analysis.append("\n" + "=" * 60)
    
    # 合并报告
    full_report = report + "\n".join(additional_analysis)
    
    return full_report
