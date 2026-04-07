"""
结构化日志：给每个采集模块打标签，输出 latency + status。
"""
import logging
import time
from functools import wraps
from typing import Callable

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[
        logging.FileHandler("stock_analyzer.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)

_logger = logging.getLogger("stock_analyzer")


def timed_fetch(module_name: str):
    """
    装饰器：自动记录函数执行时间、成功/失败状态。
    用法：
        @timed_fetch("eastmoney_news")
        def fetch_xxx(...):
            ...
    """
    def decorator(func: Callable):
        @wraps(func)
        def wrapper(*args, **kwargs):
            t0 = time.time()
            try:
                result = func(*args, **kwargs)
                latency = time.time() - t0
                count = len(result) if isinstance(result, (list, dict)) else "?"
                _logger.info(
                    f"[{module_name}] status=success count={count} latency={latency:.2f}s"
                )
                return result
            except Exception as e:
                latency = time.time() - t0
                _logger.error(
                    f"[{module_name}] status=fail error={str(e)[:100]} latency={latency:.2f}s"
                )
                raise
        return wrapper
    return decorator


def log_collection_summary(results: dict):
    """打印采集结果汇总表（替代分散的 print）"""
    print("\n  ┌─ 采集结果汇总 " + "─" * 44)
    rows = []
    for name, val in results.items():
        if val is None:
            rows.append((name, "✗ 空", ""))
        elif isinstance(val, list):
            rows.append((name, f"✓ {len(val)}条" if val else "✗ 0条", ""))
        elif isinstance(val, dict):
            rows.append((name, "✓ 有数据" if val else "✗ 空dict", ""))
        else:
            rows.append((name, "✓", ""))
    for name, status, note in rows:
        print(f"  │  {name:<22} {status}")
    print("  └" + "─" * 57)
