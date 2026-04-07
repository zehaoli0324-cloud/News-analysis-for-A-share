"""
全局配置 & 行业链映射
"""
import os
from dataclasses import dataclass, field
from typing import Optional

# ── 智谱 API ──────────────────────────────────
ZHIPU_BASE_URL = "https://api.siliconflow.cn/v1"

# ── 模型 & 生成 ───────────────────────────────
MODEL            = os.getenv("SILICONFLOW_MODEL", "deepseek-ai/DeepSeek-V3")
MAX_TOKENS       = 8192
API_TIMEOUT      = 120   # 免费版限速，超时适当放宽
API_RETRIES      = 2

# ── 采集 ──────────────────────────────────────
FETCH_TIMEOUT    = 12
SECTOR_TIMEOUT   = 15
REQUEST_RETRIES  = 3
REQUEST_BACKOFF  = 2.0
ENABLE_SLOW_APIS = os.getenv("ENABLE_SLOW_APIS", "true").lower() == "true"
ENABLE_LHB       = True
ENABLE_GDFX      = True
ENABLE_INDUSTRY_POLICY = True
ENABLE_PROXY     = os.getenv("ENABLE_PROXY", "false").lower() == "true"
PROXIES: Optional[dict] = None  # {"http": "...", "https": "..."}

# ── 新闻条数限制 ──────────────────────────────
MACRO_LIMIT      = 10
STOCK_LIMIT      = 8
CAIXIN_LIMIT     = 30
MIN_ANALYSIS_LEN = 50

# ── 缓存 ──────────────────────────────────────
CACHE_DIR = "cache"
os.makedirs(CACHE_DIR, exist_ok=True)

# ── 颜色工具 ──────────────────────────────────
def c(text, code):  return f"\033[{code}m{text}\033[0m"
def green(s):       return c(s, 92)
def red(s):         return c(s, 91)
def yellow(s):      return c(s, 93)
def cyan(s):        return c(s, 96)
def bold(s):        return c(s, 1)
def dim(s):         return c(s, 2)

# ── 行业上下游映射 ────────────────────────────
INDUSTRY_CHAIN = {
    "银行":     {"upstream": ["央行", "存款准备金", "利率"],         "downstream": ["房地产", "制造业贷款", "消费信贷"]},
    "证券":     {"upstream": ["IPO", "再融资", "监管政策"],           "downstream": ["基金", "理财", "投资者情绪"]},
    "保险":     {"upstream": ["监管", "偿付能力"],                    "downstream": ["医疗", "养老", "车险"]},
    "房地产":   {"upstream": ["土地拍卖", "钢铁", "水泥", "建材"],    "downstream": ["家电", "家居", "物业"]},
    "医药":     {"upstream": ["原料药", "CRO", "集采"],               "downstream": ["医院", "药店", "医疗器械"]},
    "半导体":   {"upstream": ["光刻机", "EDA", "硅片", "稀土"],       "downstream": ["手机", "服务器", "汽车电子"]},
    "新能源":   {"upstream": ["锂矿", "碳酸锂", "正极材料"],          "downstream": ["储能", "充电桩", "电动车"]},
    "汽车":     {"upstream": ["钢铁", "铝", "芯片", "锂电池"],        "downstream": ["汽车零部件", "经销商", "保险"]},
    "消费电子": {"upstream": ["芯片", "面板", "内存"],                "downstream": ["零售", "电商", "售后"]},
    "煤炭":     {"upstream": ["采矿设备", "运输"],                    "downstream": ["电力", "钢铁", "化工"]},
    "钢铁":     {"upstream": ["铁矿石", "煤炭", "废钢"],              "downstream": ["建筑", "汽车", "机械"]},
    "化工":     {"upstream": ["原油", "天然气", "煤炭"],              "downstream": ["农药", "涂料", "新材料"]},
    "电力":     {"upstream": ["煤炭", "天然气", "核电燃料"],          "downstream": ["工业用电", "居民用电"]},
    "食品饮料": {"upstream": ["粮食", "糖", "包装材料"],              "downstream": ["超市", "餐饮", "电商"]},
    "农业":     {"upstream": ["化肥", "农药", "种子"],                "downstream": ["食品加工", "养殖"]},
    "军工":     {"upstream": ["稀土", "钛合金", "碳纤维"],            "downstream": ["航空", "船舶", "卫星"]},
    "传媒":     {"upstream": ["电影制作成本", "影片供应", "版权价格", "内容投资", "版权", "内容制作"],
                 "downstream": ["电影票房", "观影人次", "票价", "影院上座率", "假期档期", "广告", "游戏", "流媒体"]},
    "影视":     {"upstream": ["电影制作成本", "影片供应", "版权价格", "内容投资", "版权", "票房", "院线", "内容制作"],
                 "downstream": ["电影票房", "观影人次", "票价", "影院上座率", "假期档期", "流媒体", "衍生品", "广告", "短视频"]},
    "院线":     {"upstream": ["电影制作成本", "影片供应", "版权价格", "内容投资", "版权", "票房", "内容制作", "发行"],
                 "downstream": ["电影票房", "观影人次", "票价", "影院上座率", "假期档期", "流媒体", "衍生品", "广告"]},
    "影视院线": {"upstream": ["电影制作成本", "影片供应", "版权价格", "内容投资", "版权", "票房", "内容制作", "发行"],
                 "downstream": ["电影票房", "观影人次", "票价", "影院上座率", "假期档期", "流媒体", "衍生品", "广告", "短视频"]},
    "人工智能": {"upstream": ["GPU", "算力", "数据"],                 "downstream": ["云计算", "自动驾驶", "机器人"]},
    "云计算":   {"upstream": ["服务器", "带宽", "芯片"],              "downstream": ["SaaS", "企业软件", "电商"]},
}


def get_industry_short_name(industry: str) -> str:
    """
    把 baostock 返回的长行业名（如 'R87广播、电视、电影和录音制作业'）
    映射为短名（如 '影视院线'），方便后续接口匹配。
    如果找不到映射则返回原值。
    """
    mapping = {
        "广播": "传媒", "电视": "传媒", "电影": "影视院线", "录音": "传媒",
        "影视": "影视院线", "院线": "影视院线",
        "银行": "银行", "证券": "证券", "保险": "保险",
        "房地产": "房地产", "医药": "医药", "半导体": "半导体",
        "新能源": "新能源", "汽车": "汽车", "消费电子": "消费电子",
        "煤炭": "煤炭", "钢铁": "钢铁", "化工": "化工",
        "电力": "电力", "食品饮料": "食品饮料", "农业": "农业",
        "军工": "军工", "人工智能": "人工智能", "云计算": "云计算",
    }
    for key, short in mapping.items():
        if key in industry:
            return short
    # 尝试用前2字匹配 INDUSTRY_CHAIN
    short2 = industry[:2]
    for k in INDUSTRY_CHAIN:
        if k in industry or industry in k or short2 in k:
            return k
    return industry
