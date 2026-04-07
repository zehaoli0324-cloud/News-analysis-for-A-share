"""
strategy_registry.py — 策略插件注册中心

新增策略只需：
  1. 在 strategies/ 目录下新建一个 .py 文件
  2. 继承 BaseStrategy，实现 META 和 run() 即可
  3. 无需修改任何其他文件，启动时自动发现注册

策略文件最小模板（复制到 strategies/my_strategy.py 即可）：
─────────────────────────────────────────────────────
from strategy_registry import BaseStrategy
import screener_core

class MyStrategy(BaseStrategy):
    META = {
        "id":          "my_strategy",       # 唯一ID，英文，不能重复
        "name":        "我的策略",           # 前端展示名称
        "description": "一句话描述策略逻辑", # 前端副标题
        "tags":        ["价值", "中线"],     # 可选标签
        "author":      "作者名",
        "version":     "1.0",
    }

    def run(self, snapshot_df, hs300_chg, actual_date, log):
        # snapshot_df: 全市场行情 DataFrame（来自 screener_core.get_spot_data）
        # hs300_chg:   当日沪深300涨跌幅 float
        # actual_date: 实际交易日字符串 "YYYYMMDD"
        # log:         callable，用于写运行日志 log("消息内容")
        # 返回：list[dict]，每个 dict 至少包含：
        #   code, name, price, pct_chg, vol_ratio, turnover,
        #   circ_cap_yi, stage1_score, stage1_hits, _hist(K线df)
        candidates = screener_core.screen_stage1(snapshot_df, hs300_chg, actual_date)
        return candidates
─────────────────────────────────────────────────────
"""

import importlib, importlib.util, pathlib, sys
from abc import ABC, abstractmethod

# ── 策略基类 ────────────────────────────────────────────────────
class BaseStrategy(ABC):
    """
    所有选股策略必须继承此类。
    子类需定义：
      - META: dict  (id / name / description / tags / author / version)
      - run(self, snapshot_df, hs300_chg, actual_date, log) -> list[dict]
    """

    META: dict = {
        "id":          "base",
        "name":        "基础策略",
        "description": "未设置描述",
        "tags":        [],
        "author":      "",
        "version":     "1.0",
    }

    @abstractmethod
    def run(self, snapshot_df, hs300_chg: float, actual_date: str, log) -> list:
        """
        执行选股逻辑。

        参数
        ────
        snapshot_df : pd.DataFrame
            全市场行情快照，来自 screener_core.get_spot_data()
            列：code, name, price, pct_chg, turnover, vol_ratio, circ_cap_yi, ...
        hs300_chg   : float
            沪深300当日涨跌幅
        actual_date : str
            实际交易日，格式 "YYYYMMDD"
        log         : callable(str)
            日志回调，调用 log("消息") 即可将信息显示到前端运行日志

        返回
        ────
        list[dict]，每个 dict 至少含：
            code          str   股票代码（6位）
            name          str   股票名称
            price         float 现价
            pct_chg       float 涨幅%
            vol_ratio     float 量比
            turnover      float 换手率%
            circ_cap_yi   float 流通市值（亿元）
            stage1_score  int   量价评分（可自定义评分逻辑，没有就填 0）
            stage1_hits   list  命中条件列表
            _hist         df|None  K线 DataFrame（供资金流评分用，可以为 None）
        """
        ...

    @classmethod
    def meta(cls) -> dict:
        """返回策略元信息"""
        return {
            "id":          cls.META.get("id", cls.__name__),
            "name":        cls.META.get("name", cls.__name__),
            "description": cls.META.get("description", ""),
            "tags":        cls.META.get("tags", []),
            "author":      cls.META.get("author", ""),
            "version":     cls.META.get("version", "1.0"),
        }


# ── 注册表 ──────────────────────────────────────────────────────
_registry: dict[str, type] = {}   # id -> 策略类
_loaded    = False

def _discover():
    """扫描 strategies/ 目录，自动导入所有 .py 文件并注册 BaseStrategy 子类"""
    global _loaded
    if _loaded:
        return
    _loaded = True

    strat_dir = pathlib.Path(__file__).parent / "strategies"
    strat_dir.mkdir(exist_ok=True)

    for py_file in sorted(strat_dir.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        module_name = f"strategies.{py_file.stem}"
        try:
            if module_name in sys.modules:
                mod = sys.modules[module_name]
            else:
                spec = importlib.util.spec_from_file_location(module_name, py_file)
                mod  = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = mod
                spec.loader.exec_module(mod)

            for attr_name in dir(mod):
                cls = getattr(mod, attr_name)
                if (isinstance(cls, type)
                        and issubclass(cls, BaseStrategy)
                        and cls is not BaseStrategy):
                    sid = cls.META.get("id", cls.__name__)
                    _registry[sid] = cls
        except Exception as e:
            print(f"[strategy_registry] ⚠️ 加载 {py_file.name} 失败：{e}")


def list_strategies() -> list[dict]:
    """返回所有已注册策略的元信息列表（按 id 排序）"""
    _discover()
    return [cls.meta() for cls in _registry.values()]


def get_strategy(strategy_id: str) -> "BaseStrategy":
    """根据 ID 实例化并返回策略对象，找不到则抛 KeyError"""
    _discover()
    if strategy_id not in _registry:
        raise KeyError(f"策略 '{strategy_id}' 未注册，可用：{list(_registry.keys())}")
    return _registry[strategy_id]()


def reload_strategies():
    """强制重新扫描（开发时热重载用）"""
    global _loaded
    _loaded = False
    _registry.clear()
    # 清除已加载的模块缓存
    to_del = [k for k in sys.modules if k.startswith("strategies.")]
    for k in to_del:
        del sys.modules[k]
    _discover()
