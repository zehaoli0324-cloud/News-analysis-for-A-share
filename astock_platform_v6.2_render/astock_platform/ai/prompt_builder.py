"""
Prompt 构建：system prompt 和分析 prompt
v3.0 重构 —— 修复竞品数据缺失处理、重写 system prompt 以驱动高质量分析
"""
from typing import List
from datetime import datetime

from config.settings import INDUSTRY_CHAIN
from services.stock_service import parse_longhubang_from_news


# ══════════════════════════════════════════════════════
#   SYSTEM PROMPT
# ══════════════════════════════════════════════════════

def make_system(symbol: str, meta: dict) -> str:
    name     = meta.get("name", "") or symbol
    industry = meta.get("industry", "未知行业")
    keywords = meta.get("keywords", [])
    upstream, downstream = [], []
    for key, chain in INDUSTRY_CHAIN.items():
        if key in industry or industry in key:
            upstream   = chain["upstream"]
            downstream = chain["downstream"]
            break

    return f"""你是A股短线交易员，不是研究员。

你每天面对不完整的信息做决策。目标不是解释所有信息，而是在信息不完备时找到胜率和盈亏比更优的方向。

【人格】
有经验、有判断、有犹豫——但必须表达倾向。你不把决策压力推给读者，你说清楚"我更偏向哪一边，以及为什么"。允许判断错误，但不允许没有判断。

【写作风格——最重要的约束】
全部段落形式，每段200-400字，数字融入句子。
语言有犹豫感：用"更像是"、"目前还看不到"、"真正的问题不在于...而在于"、"问题在于"这类表达。

绝对禁止的结构痕迹：
不能写"核心依据有三：一是...二是...三是..."
不能写"其一...其二...其三..."
不能写"A→B→C"这样单独列出的因果链

正确做法：把多个理由压进一句话或一段话里：
把"成交量收缩、龙虎榜无机构、板块弱势"这三点融成一句："更关键的问题不在于反弹本身，而在于这轮反弹缺乏资金支撑——无论是龙虎榜没有机构席位，还是成交量持续收缩，都更像是存量资金的自救行为，而不是新资金入场。"
因果链也融进叙事："这轮反弹本质上是从板块情绪修复开始，再传导到超跌个股，最后叠加票房数据形成短线催化，但这个链条最不稳的一环仍然是票房与公司盈利之间的弱关联。"

【关于推断数据——关键】
推断的数据不要用【推断】标注，直接换说法或删掉：
不要写：净利润【推断】约X亿
要写：若按历史季节性推算，净利润大约在X亿区间
宁可少一个数字，也不要让读者对数据可信度产生疑虑。

【方向性表达——关键升级】
不能只说"观望"，要表达真实倾向：
不要写：观望，等待成交量放大
要写：当前更接近反弹中继偏弱结构，向下风险略大于向上空间，策略上更偏向减仓而不是等待加仓机会。
如果分析出来是偏空但不做空，就直接说偏空但不做空，不要用"观望"掩盖真实判断。

【禁止行为】
不能只做总结，不能只说"观望"没有倾向，不能把风险全部推给读者（禁止"建议投资者自行判断"）。
禁止"必然"、"一定"等过度确定性表达。

【铁律】
每条数据注明来源和具体日期。横向对比必须出现（同行今日涨跌、板块表现）。有效期在开头注明。

【输出结构（九章）】

# {name}（{symbol}）短期投资简报
**评级**：[回避/观望/做多]  **置信度**：[高/中/低]  **有效期**：3个交易日  **日期**：{datetime.now().strftime('%Y-%m-%d')}

---

## 一、核心结论

3-4句自然语言，说清楚今天发生了什么、核心驱动是什么、你当前更偏向哪一侧。
必须表达方向性，不能中性总结。

---

## 二、今日事件复盘

先2-3句介绍公司做什么（给不了解的人看），再按时间线描述今日事件，每个事实融入解释。
必须有具体股价、成交额，以及行业整体今日涨跌对比。至少600字，2-3段。

---

## 三、为什么这样走

把因果链融进叙述，不要单独列出。区分行业共性（β）和个股特有（α）。
最后说这段逻辑里最脆弱的一环——如果它被证伪，结论怎么变。

---

## 四、公司基本面与估值

先介绍商业模式，再给PE估算（写计算过程）和同行横向对比，数字融入句子。
能确认的数据才写，推断的换说法（"若按历史推算..."），不用【推断】标注。

---

## 五、资金行为解读

龙虎榜数据后，重点解释这些资金为什么这么做。
结合成交量：放量跌和缩量跌含义完全不同，要说清楚。
无法确定时，说"更可能是X，但如果Y成立，结论会反转"。

---

## 六、多空分歧

认真陈述空方论点，认真找多方反驳，但把多个理由压进自然叙述，不要平行列举。
最后：最关键的变量是什么，一旦改变，判断会翻转。

---

## 七、交易员判断（核心）

第一句必须钉死当前市场位置，例如：
"当前更接近下跌趋势中的弱反弹中段，而不是反转初期。"
这是坐标系，决定后续所有判断的前提。

然后一段话说明为什么偏向这一侧，把资金行为、情绪位置、结构强弱融进去，不分条。
结尾一句：如果判断错了，最可能错在哪里。这不是免责，是说明你知道自己的盲点。

参考风格（必须对标这个水准）：
"虽然今天出现反弹，但整体更接近下跌趋势中的弱反弹，而不是新一轮行情的起点。问题不在于涨没涨，而在于这轮上涨缺乏资金支撑——成交量明显收缩，龙虎榜也没有机构入场，更像是被套资金的自救行为。在这种结构下，继续向上的空间并不大，反而一旦情绪转弱，回落会比较快。如果判断错误，最大的可能是五一档票房超预期带动板块重新获得资金关注，但在看到这种信号之前，提前下注的性价比并不高。"

---

## 八、投资建议

评级加2-3句交易员语言，必须有方向性，不能只说"等信号"。
3个行为性信号说明什么时候会改变判断（例如"股价在X附近连续两日放量承接，而不是冲高回落"）。

---

## 九、人工核查清单

3-5项当前新闻无法获取的关键信息，用复选框列出，每条说明为什么重要。

---

【标的信息】
代码：{symbol}，名称：{name}，行业：{industry}
产业链：上游[{' / '.join(upstream) or '未知'}] → {name} → 下游[{' / '.join(downstream) or '未知'}]
关键词：{' / '.join(keywords[:10])}

【数据优先级】
公告（最高权重·A级）：逐条引用，若无则开头说明并降置信度。
龙虎榜：优先解释资金动机，不只描述方向。
个股新闻（东方财富）：高权重，含"业绩""公告""诉讼"的条目优先。
GNews舆情：中权重，过滤噪音。
板块/产业链：判断β方向。

写完整，九章全部输出，最后一行写（报告完）。"""


def enrich_keywords_with_ai(client, meta: dict) -> dict:
    """用 GLM 扩展关键词：生成 20-30 个 2-4 字词语"""
    import json, re
    from config.settings import MODEL, green, yellow, dim

    name     = meta.get("name", "")
    industry = meta.get("industry", "")
    symbol   = meta["keywords"][0] if meta["keywords"] else ""
    base_kws = meta.get("keywords", [])

    if not name or not industry:
        return meta

    prompt = f"""你是A股行业专家，精通所有行业的关键词挖掘。请为股票【{symbol} {name}】（行业：{industry}）生成一组用于新闻搜索的精准关键词。

【核心要求】
1. 每个词2-4字，优先使用2-3字，必要时可用4字。
2. 总数25-35个，分6类，每类4-6个。
3. 关键词必须能体现该公司的**业务实质**和**行业特性**。
4. 竞争对手名称必须使用最常见的公开名称，例如：
   - 万达电影（而非"万达影业"）
   - 华谊兄弟（而非"华谊"）
   - 博纳影业（而非"博纳"）
5. 示例参考（仅作格式说明，非分析约束）：公司简称：[公司简称]、[缩写]；竞争对手：[竞品A]、[竞品B]；行业短词：[行业核心词]；上游：[上游词]；下游：[下游词]；热词：[热点词]

请根据【{industry}】行业特点生成类似精准关键词。

只输出JSON，不要其他内容：
{{"keywords": {{"公司简称": [], "竞争对手": [], "行业短词": [], "上游短词": [], "下游短词": [], "热词": []}}}}

禁止：5字以上的词、英文、重复。"""

    try:
        resp = client.chat.completions.create(
            model=MODEL, max_tokens=1000, stream=False,
            messages=[
                {"role": "system", "content": "你是A股行业专家，只输出JSON。"},
                {"role": "user",   "content": prompt},
            ],
        )
        raw    = re.sub(r"```json\s*|```\s*", "",
                        resp.choices[0].message.content.strip()).strip()
        data   = json.loads(raw)
        kw_dict = data.get("keywords", {})
        ai_kws  = [
            w.strip() for words in kw_dict.values()
            for w in words
            if w.strip() and 2 <= len(w.strip()) <= 4
        ]
        merged = list(dict.fromkeys(base_kws + ai_kws))
        meta["keywords"]    = merged
        meta["keyword_dict"] = kw_dict
        print(green(f"  ✓ AI关键词扩展：共 {len(merged)} 个"))
        for cat, words in kw_dict.items():
            if words:
                print(dim(f"    [{cat}] {' / '.join(words[:6])}"))
    except Exception as e:
        print(yellow(f"  ⚠ AI关键词扩展失败，使用基础词：{str(e)[:50]}"))
        default_keywords = {
            "公司简称": [name, name[:2]],
            "竞争对手": [],
            "行业短词": [industry, industry[:2]],
            "上游短词": [],
            "下游短词": [],
            "热词": []
        }
        industry_competitors = {
            "传媒": ["华谊兄弟", "光线传媒", "万达电影", "横店影视"],
            "医药": ["恒瑞医药", "复星医药", "药明康德", "长春高新"],
            "科技": ["腾讯控股", "阿里巴巴", "百度", "京东"],
            "房地产": ["万科A", "保利发展", "招商蛇口", "金地集团"],
            "银行": ["工商银行", "建设银行", "招商银行", "中国银行"],
            "汽车": ["比亚迪", "长城汽车", "吉利汽车", "长安汽车"],
            "半导体": ["中芯国际", "韦尔股份", "北方华创", "兆易创新"],
            "新能源": ["宁德时代", "比亚迪", "隆基绿能", "阳光电源"],
            "食品饮料": ["贵州茅台", "五粮液", "伊利股份", "海天味业"],
            "钢铁": ["宝钢股份", "鞍钢股份", "太钢不锈", "马钢股份"],
            "化工": ["万华化学", "恒力石化", "荣盛石化", "桐昆股份"],
            "电力": ["华能国际", "大唐发电", "国电电力", "华电国际"],
            "农业": ["牧原股份", "温氏股份", "新希望", "隆平高科"],
            "军工": ["中航沈飞", "航天彩虹", "中兵红箭", "中航西飞"],
            "人工智能": ["科大讯飞", "海康威视", "旷视科技", "商汤科技"],
            "云计算": ["阿里云", "腾讯云", "华为云", "百度云"],
        }
        if industry in industry_competitors:
            default_keywords["竞争对手"] = industry_competitors[industry]
        meta["keyword_dict"] = default_keywords
        default_kws = [w for words in default_keywords.values() for w in words if w]
        merged = list(dict.fromkeys(base_kws + default_kws))
        meta["keywords"] = merged
        print(dim(f"  使用默认关键词：{', '.join(default_kws[:6])}"))

    return meta


# ══════════════════════════════════════════════════════
#   BUILD PROMPT（送给 AI 的数据上下文）
# ══════════════════════════════════════════════════════

def build_prompt(symbol, meta, stock_news, macro_news, caixin_news,
                 macro_events, holder_news, company_news, sector_data,
                 monetary=None, movement_news=None, spot_info=None, lhb_data=None) -> str:
    """构建分析 prompt：把采集到的所有数据结构化喂给 AI"""
    from models.data_model import ensure_dict

    name      = meta.get("name", "") or symbol
    industry  = meta.get("industry", "未知行业")
    keywords  = meta.get("keywords", [])
    now_date  = datetime.now().strftime("%Y-%m-%d")

    stock_news    = [ensure_dict(n) for n in (stock_news   or [])]
    macro_news    = [ensure_dict(n) for n in (macro_news   or [])]
    caixin_news   = [ensure_dict(n) for n in (caixin_news  or [])]
    holder_news   = [ensure_dict(n) for n in (holder_news  or [])]
    company_news  = [ensure_dict(n) for n in (company_news or [])]
    movement_news = [ensure_dict(n) for n in movement_news or []]
    lhb_data      = lhb_data or {}
    sector_data   = sector_data or {}

    upstream, downstream = [], []
    for key, chain in INDUSTRY_CHAIN.items():
        if key in industry:
            upstream   = chain["upstream"]
            downstream = chain["downstream"]
            break

    spot             = sector_data.get("spot", {})
    ff               = sector_data.get("fund_flow", {})
    sector_news_list = sector_data.get("sector_news", [])
    chain_news       = sector_data.get("chain_news", [])
    social_news      = [n for n in company_news if n.get('type') == 'social']
    formal_anns      = [n for n in company_news if n.get('type') != 'social']

    lines = []

    # ── 头部 ────────────────────────────────────────
    lines.append(f"{'='*70}")
    lines.append(f"分析标的：{name}（{symbol}）| {industry} | {now_date}")
    lines.append(f"{'='*70}")
    lines.append("")

    # ── 实时行情 ────────────────────────────────────
    lines.append("【实时行情】")
    if spot_info:
        price  = spot_info.get("price", spot_info.get("最新价", "?"))
        change = spot_info.get("change", spot_info.get("涨跌幅", "?"))
        lines.append(f"  当前价格：{price}  今日涨跌：{change}%")
    if spot:
        chg = spot.get("涨跌幅", spot.get("今日涨跌幅", "?"))
        vol = spot.get("成交额", "?")
        lines.append(f"  {industry}板块今日：{chg}%  成交额：{vol}")
    lines.append("")

    # ── 龙虎榜（预计算席位分布，防止AI捏造） ──────────
    lines.append("【龙虎榜数据】")
    if lhb_data.get("has_lhb"):
        lines.append(f"  {lhb_data.get('conclusion', '')}")
        details = lhb_data.get("details", [])

        inst_seats   = [d for d in details if "机构" in str(d.get("style","")) or "机构" in str(d.get("type",""))]
        retail_seats = [d for d in details if "散户" in str(d.get("style","")) or "拉萨" in str(d.get("seat",""))]
        other_seats  = [d for d in details if d not in inst_seats and d not in retail_seats]
        has_buy_data  = any(d.get("buy") not in (None, "N/A", "") for d in details)
        has_sell_data = any(d.get("sell") not in (None, "N/A", "") for d in details)

        lines.append(f"  ⚠️ 席位类型统计（Python预算，AI必须与此一致不得修改）：")
        lines.append(f"    - 机构席位：{len(inst_seats)}个（{'有机构出现' if inst_seats else '本次无机构席位'}）")
        lines.append(f"    - 散户席位（拉萨系等）：{len(retail_seats)}个")
        lines.append(f"    - 其他/未知席位：{len(other_seats)}个")
        if not has_buy_data and not has_sell_data:
            lines.append(f"    - ⚠️ 买卖金额数据：全部为N/A（接口本次未返回明细）")
            lines.append("    - ⚠️ 禁止写【机构卖出明显】【机构买入】等判断——金额数据缺失，无法确认方向")

        if details:
            lines.append(f"  席位原始数据：")
            for d in details:
                seat  = d.get("seat", "未知")
                buy   = d.get("buy", "N/A")
                sell  = d.get("sell", "N/A")
                style = d.get("style", "未知")
                lines.append(f"    · [{style}] {seat} | 买:{buy} 卖:{sell}")
    else:
        lines.append("  无龙虎榜数据")
    lines.append("")

    # ── 大股东 ──────────────────────────────────────
    lines.append("【十大流通股东】")
    if holder_news:
        for i, h_raw in enumerate(holder_news[:10], 1):
            h = ensure_dict(h_raw)
            lines.append(f"  {i}. {h.get('holder','')}  持股{h.get('ratio','')}%  {h.get('change','')}")
    else:
        lines.append("  无数据")
    lines.append("")

    # ── 公司公告（最高权重，AI必须优先分析） ────────────────
    lines.append(f"【公司公告·最高权重·A级数据】（{len(formal_anns)}条）")
    if formal_anns:
        lines.append(f"  ⚠️ 以下公告是最可靠的A级数据，AI必须逐条引用，不得跳过：")
        for i, n in enumerate(formal_anns[:15], 1):
            t   = n.get('time', '')[:16]
            src = n.get('source', '')
            url = n.get('url', '')
            lines.append(f"  {i}. [{t}] {n.get('title','')}  [{src}]")
            if url and 'google.com/rss' not in url:
                lines.append(f"     链接：{url}")
    else:
        lines.append("  ⚠️ 本次未采集到正式公告（巨潮接口受限）")
        lines.append("  → AI须在报告第一章明确说明公告数据缺失，并将置信度降为低/中")
    lines.append("")

    # ── 个股新闻 ────────────────────────────────────
    lines.append(f"【个股新闻·东方财富】（{len(stock_news)}条）")
    for n in stock_news[:10]:
        t   = n.get('time', n.get('date', ''))[:16]
        src = n.get('source', '')
        lines.append(f"  [{t}] {n.get('title','')}  [{src}]")
    lines.append("")

    # ── 社会舆情（Python层预过滤噪音） ────────────────
    # 过滤掉明显不相关的噪音：金融罚款、海外政策、无关行业
    NOISE_KEYWORDS = [
        "农村商业银行", "农村信用", "被罚", "支付结算", "反洗钱", "金融统计",
        "奥巴马", "特朗普", "拜登", "泰国大麻", "化妆品法规", "交通法规",
        "实验动物", "干电池", "新能源车", "光伏", "储能", "生物医药",
        "军队院校", "五角大楼", "韩国化妆品"
    ]
    filtered_social = [
        n for n in social_news
        if not any(kw in n.get('title','') for kw in NOISE_KEYWORDS)
    ]
    noise_count = len(social_news) - len(filtered_social)

    lines.append(f"【社会舆情·GNews搜索】（原{len(social_news)}条，过滤无关噪音{noise_count}条后剩{len(filtered_social)}条）")
    for i, n in enumerate(filtered_social, 1):
        t   = n.get('time', '')[:16]
        src = n.get('source', '')
        lines.append(f"  {i:>2}. [{t}] {n.get('title','')}  [{src}]")
    lines.append("")

    # ── 板块/行业新闻 ───────────────────────────────
    lines.append(f"【{industry}板块新闻】（{len(sector_news_list)}条）")
    for n in sector_news_list[:15]:
        t   = n.get('time', n.get('date', ''))[:16]
        lines.append(f"  [{t}] {n.get('title','')}")
    lines.append("")

    # ── 板块异动 ────────────────────────────────────
    if movement_news:
        lines.append(f"【板块异动线索】（{len(movement_news)}条）")
        for n in movement_news[:10]:
            t = n.get('time', n.get('date', ''))[:16]
            lines.append(f"  [{t}] {n.get('title','')}")
        lines.append("")

    # ── 产业链新闻 ──────────────────────────────────
    if chain_news:
        upstream_news   = [n for n in chain_news if n.get('chain') == 'upstream']
        downstream_news = [n for n in chain_news if n.get('chain') == 'downstream']
        policy_news_c   = [n for n in chain_news if n.get('chain') == 'policy']

        lines.append(f"【产业链新闻】上游{len(upstream_news)}条 / 下游{len(downstream_news)}条 / 政策{len(policy_news_c)}条")

        if downstream_news:
            # 过滤百科/文库/无关内容
            DOWN_NOISE = ["百度百科", "百度文库", "wikipedia", "知乎专栏基础知识",
                          "观众权益", "汽油价格", "世界杯门票", "燃油附加费"]
            valid_down = [n for n in downstream_news
                          if not any(kw in n.get('title','') + n.get('url','') for kw in DOWN_NOISE)
                          and n.get('time','')]  # 过滤无日期的（通常是百科）
            lines.append(f"  ▶ 下游需求（票房/档期/观影，{len(valid_down)}条有效）：")
            for n in valid_down[:12]:
                t = n.get('time', '')[:10]
                lines.append(f"    [{t}] {n.get('title','')}")

        if upstream_news:
            UP_NOISE = ["百度百科", "百度文库", "wikipedia", "剧本杀", "张国立",
                        "杨磊", "刘恺", "庞麦郎", "可口可乐", "英伟达对Anthropic",
                        "美战争部长", "韩国电影行业", "澳大利亚流媒体"]
            valid_up = [n for n in upstream_news
                        if not any(kw in n.get('title','') + n.get('url','') for kw in UP_NOISE)
                        and n.get('time','')]
            lines.append(f"  ▶ 上游供给（内容投资/制作成本，{len(valid_up)}条有效）：")
            for n in valid_up[:8]:
                t = n.get('time', '')[:10]
                lines.append(f"    [{t}] {n.get('title','')}")

        if policy_news_c:
            POL_NOISE = ["韩国化妆品", "军队招生", "南非", "马来西亚", "泰国大麻",
                         "爱尔兰监管", "美国移民政策", "奥巴马减排", "交通法规",
                         "医药行业", "储能行业", "新能源车", "光伏", "助贷",
                         "实验动物", "干电池", "黄金珠宝", "北京市实验"]
            valid_pol = [n for n in policy_news_c
                         if not any(kw in n.get('title','') for kw in POL_NOISE)
                         and n.get('time','')]
            lines.append(f"  ▶ 政策环境（影视/传媒监管相关，{len(valid_pol)}条有效）：")
            for n in valid_pol[:8]:
                t = n.get('time', '')[:10]
                lines.append(f"    [{t}] {n.get('title','')}")
    else:
        lines.append("【产业链新闻】无数据")
    lines.append("")

    # ── 竞品对比（修复：检测数据是否真实有效） ──────
    comp_report      = sector_data.get('competitor_report', '')
    competitor_data  = sector_data.get('competitor_data', {})

    all_zero_price   = True
    has_real_comp_data = False
    if competitor_data:
        for comp_name, cdata in competitor_data.items():
            if cdata.get('change', 0) != 0:
                all_zero_price = False
            if cdata.get('news'):
                has_real_comp_data = True

    lines.append("【竞品对比数据】")
    if not competitor_data and not comp_report:
        lines.append("  ⚠️ 无竞品数据，跳过横向对比。")
    elif all_zero_price and not has_real_comp_data:
        lines.append("  ⚠️ 竞品股价接口返回全为0（数据缺失）。")
        lines.append("  → 请AI在报告中**省略竞品价格横向对比**，不得捏造对比结论。")
        if comp_report:
            lines.append("  竞品新闻情绪（仅供参考）：")
            lines.append(comp_report)
    else:
        lines.append("  竞品数据有效：")
        lines.append(comp_report)
    lines.append("")

    # ── 宏观/货币政策 ───────────────────────────────
    lines.append("【宏观与货币政策】")
    monetary = monetary or {}
    lpr_data = monetary.get("lpr", [])
    if lpr_data:
        lpr1 = lpr_data[0].get("1年期", "")
        lpr5 = lpr_data[0].get("5年期", "")
        lines.append(f"  LPR 1年期：{lpr1}  5年期：{lpr5}  大型行存准率：9.5%")
    if macro_news:
        lines.append(f"  宏观快讯（{len(macro_news)}条）：")
        for n in macro_news[:5]:
            t = n.get('time', n.get('date',''))[:16]
            lines.append(f"    [{t}] {n.get('title','')}")
    lines.append("")

    # ── 数据完整度 ──────────────────────────────────
    has_announcement = len(formal_anns) > 0
    has_social       = len(social_news) > 0
    has_lhb          = lhb_data.get("has_lhb", False)
    has_sector       = len(sector_news_list) > 0
    has_holders      = len(holder_news) > 0
    has_chain        = bool(chain_news)
    has_spot         = bool(spot)
    has_realtime     = bool(spot_info)
    data_score       = sum([has_announcement, has_social, has_lhb,
                            has_sector, has_holders, has_chain, has_spot, has_realtime])

    lines.append(f"【数据完整度】{data_score}/8")
    missing_items = [k for k, v in {
        "公司公告": has_announcement, "社会舆情": has_social,
        "龙虎榜":   has_lhb,         "行业新闻": has_sector,
        "大股东":   has_holders,      "产业链":   has_chain,
        "板块行情": has_spot,         "实时行情": has_realtime,
    }.items() if not v]
    if missing_items:
        lines.append(f"  缺失项：{' / '.join(missing_items)}")
    lines.append("")

    # ── 给AI的最终指令 ──────────────────────────────
    lines.append("="*70)
    lines.append("【给AI的分析指令】")
    lines.append(f"请基于以上所有数据，按 system prompt 的报告结构，对 {name}（{symbol}）输出完整投资简报。")
    lines.append("")
    lines.append("重点提示：")
    lines.append(f"1. 今日该股涨跌幅显著，请重点分析【今日异动解读】章节，找出真正原因。")
    lines.append(f"2. 社会舆情条数较多但质量参差，请自行筛选与{name}直接相关的新闻，过滤噪音。")
    lines.append(f"3. 龙虎榜数据是重要的资金信号，请量化解读（席位数量、买卖金额、类型分布）。")
    if all_zero_price:
        lines.append(f"4. ⚠️ 竞品当日股价数据全为0（接口问题），【禁止】在报告中进行竞品价格横向对比，直接省略该内容。")
    else:
        lines.append(f"4. 竞品数据有效，请在行业景气度章节适当引用做横向对比。")
    lines.append(f"5. 若某项关键数据缺失，用【推断】标注并推理，不得留空。")
    lines.append(f"6. 每章都用段落写（200-400字），不要大量分条列举，数字要融入句子中说明含义。")
    lines.append("⚠️ 段落式叙述，完整输出九个章节，第七章必须包含带方向性的交易员判断（不能中性），最后一行写（报告完）。")
    lines.append("")

    # ── 后续核查清单 ────────────────────────────────
    _comps      = meta.get("keyword_dict", {}).get("竞争对手", [])
    _downstream = meta.get("keyword_dict", {}).get("下游短词", [])
    lines.append("【人工核查清单（AI在报告末尾附上）】")
    lines.append(f"- [ ] 访问巨潮资讯，搜索【{symbol}】，查看近期业绩预告/快报/重大诉讼")
    lines.append(f"- [ ] 在东方财富APP龙虎榜查看机构席位具体买卖金额（本次数据中buy/sell显示N/A）")
    if _comps:
        lines.append(f"- [ ] 查看{'/'.join(_comps[:3])}今日实际涨跌幅，判断是否行业普跌")
    lines.append(f"- [ ] 通过天眼查查询{name}大股东股权质押情况")
    if _downstream:
        lines.append(f"- [ ] 查询灯塔/猫眼等专业平台获取{name}近期影院排片率和上座率数据")

    return "\n".join(lines)
