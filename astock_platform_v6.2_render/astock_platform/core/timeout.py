"""
跨平台超时 & 并发工具

改动：
  1. 任务失败时区分"网络超时"和"其他错误"，超时只打一行简短提示
  2. run_concurrent_tasks_with_progress 的 desc=None 时完全不显示进度条
     （用于抑制嵌套进度条）
"""
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

from config.settings import yellow, dim

try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False


def _short_err(e) -> str:
    """把网络错误压缩成一行简短描述，隐藏 IP/端口噪音"""
    s = str(e)
    lower = s.lower()
    if "timed out" in lower or "timeout" in lower:
        return "网络超时"
    if "connection" in lower or "proxy" in lower or "ssl" in lower:
        return "连接失败"
    if "max retries" in lower:
        return "重试耗尽"
    # 其他：截断到 60 字
    return s[:60]


def run_with_timeout(func, timeout_sec: float, args=(), kwargs=None):
    """
    在子线程中执行 func，超时则返回 (None, TimeoutError)。
    返回 (result, error)
    """
    if kwargs is None:
        kwargs = {}
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(func, *args, **kwargs)
            try:
                result = future.result(timeout=timeout_sec)
                return result, None
            except FutureTimeoutError:
                return None, TimeoutError(f"超时 >{timeout_sec}s")
    except Exception as e:
        try:
            result = func(*args, **kwargs)
            return result, None
        except Exception as e2:
            return None, e2


def run_concurrent_tasks(task_dict: dict, max_workers: int = 5,
                         timeout_per_task: float = None) -> dict:
    """
    并发执行多个任务，返回 {任务名: 结果} 字典。
    task_dict: {"名称": (func, args, kwargs)}
    """
    from core.request import set_concurrent_mode

    results = {}

    def _run(func, args, kwargs):
        set_concurrent_mode(True)
        try:
            return func(*args, **kwargs)
        finally:
            set_concurrent_mode(False)

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_name = {
            executor.submit(_run, func, args, kwargs): name
            for name, (func, args, kwargs) in task_dict.items()
        }
        for future in concurrent.futures.as_completed(future_to_name):
            name = future_to_name[future]
            try:
                results[name] = future.result(timeout=timeout_per_task)
            except concurrent.futures.TimeoutError:
                print(yellow(f"  ✗ [{name}] 超时，跳过"))
                results[name] = None
            except Exception as e:
                print(yellow(f"  ✗ [{name}] {_short_err(e)}"))
                results[name] = None
    return results


def run_concurrent_tasks_with_progress(task_dict: dict, max_workers: int = 5,
                                       timeout_per_task: float = None,
                                       desc: str = "采集数据",
                                       on_complete=None) -> dict:
    """
    带 tqdm 进度条的并发任务执行。
    desc=None 时完全静默，不显示进度条（用于抑制嵌套进度条）。
    on_complete(name, result, ok): 每个任务完成时回调，ok=True表示成功。
    """
    from core.request import set_concurrent_mode

    results = {}

    def _run(func, args, kwargs):
        set_concurrent_mode(True)
        try:
            return func(*args, **kwargs)
        finally:
            set_concurrent_mode(False)

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_name = {
            executor.submit(_run, func, args, kwargs): name
            for name, (func, args, kwargs) in task_dict.items()
        }
        show_bar = HAS_TQDM and desc is not None
        pbar = tqdm(total=len(future_to_name), desc=desc,
                    unit="任务", ncols=90) if show_bar else None

        for future in concurrent.futures.as_completed(future_to_name):
            name = future_to_name[future]
            ok = True
            try:
                results[name] = future.result(timeout=timeout_per_task)
            except concurrent.futures.TimeoutError:
                results[name] = None
                ok = False
                if not show_bar:
                    print(yellow(f"  ✗ [{name}] 超时，跳过"))
            except Exception as e:
                results[name] = None
                ok = False
                if not show_bar:
                    print(yellow(f"  ✗ [{name}] {_short_err(e)}"))
            if pbar:
                pbar.update(1)
            # 每个任务完成时立即回调
            if on_complete:
                try:
                    on_complete(name, results[name], ok)
                except Exception:
                    pass

        if pbar:
            pbar.close()
    return results
