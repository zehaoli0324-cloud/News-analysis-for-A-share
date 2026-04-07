"""
通用健壮 HTTP 请求（随机 UA、重试、连接池、代理支持）
"""
import random
import time
import threading

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config.settings import REQUEST_RETRIES, REQUEST_BACKOFF, ENABLE_PROXY, PROXIES

# 并发场景标志：True 时大幅压缩随机延迟
_CONCURRENT_MODE = threading.local()


def set_concurrent_mode(enabled: bool):
    """在并发任务入口调用，减少每次请求的随机 sleep"""
    _CONCURRENT_MODE.enabled = enabled


def _get_sleep_range():
    """并发模式 0.05~0.2s，串行模式 0.5~1.5s"""
    if getattr(_CONCURRENT_MODE, "enabled", False):
        return 0.05, 0.2
    return 0.5, 1.5

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/119.0",
]

_GLOBAL_SESSION: requests.Session = None


def _get_session() -> requests.Session:
    global _GLOBAL_SESSION
    if _GLOBAL_SESSION is None:
        _GLOBAL_SESSION = requests.Session()
        adapter = HTTPAdapter(
            pool_connections=50,
            pool_maxsize=50,
            max_retries=Retry(
                total=REQUEST_RETRIES,
                backoff_factor=REQUEST_BACKOFF,
                status_forcelist=[500, 502, 503, 504, 429],
                allowed_methods=["HEAD", "GET", "OPTIONS"],
            ),
        )
        _GLOBAL_SESSION.mount("http://", adapter)
        _GLOBAL_SESSION.mount("https://", adapter)
    return _GLOBAL_SESSION


def robust_request(
    url: str,
    method: str = "GET",
    params=None,
    headers=None,
    timeout: int = 10,
    retries: int = None,
    backoff: float = None,
    allow_redirects: bool = True,
):
    """
    增强版请求：随机延迟 + 重试 + 真实 UA + 全局 Session + 连接池。
    返回 requests.Response，失败则抛异常。
    """
    if retries is None:
        retries = REQUEST_RETRIES
    if backoff is None:
        backoff = REQUEST_BACKOFF

    session = _get_session()

    req_headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
        "Cache-Control": "no-cache",
    }
    if headers:
        req_headers.update(headers)

    lo, hi = _get_sleep_range()
    time.sleep(random.uniform(lo, hi))

    # 部分站点 SSL 问题，临时关闭验证
    verify_ssl = not any(
        domain in url for domain in ("cls.cn", "sina.com.cn", "eastmoney.com")
    )

    for attempt in range(retries + 1):
        try:
            resp = session.request(
                method=method,
                url=url,
                params=params,
                headers=req_headers,
                timeout=timeout,
                proxies=PROXIES if ENABLE_PROXY else None,
                allow_redirects=allow_redirects,
                verify=verify_ssl,
            )
            resp.raise_for_status()
            return resp
        except requests.exceptions.RequestException as e:
            if attempt == retries:
                raise
            wait = backoff * (2 ** attempt) + random.uniform(0, 1)
            time.sleep(wait)
    return None  # never reached
