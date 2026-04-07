"""
简单内存缓存（TTL）
"""
from functools import wraps
from datetime import datetime, timedelta

_cache_store: dict = {}


def cache(ttl_seconds: int = 300):
    """TTL 内存缓存装饰器"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            key = f"{func.__name__}:{args!r}:{sorted(kwargs.items())!r}"
            now = datetime.now()
            if key in _cache_store:
                value, expire = _cache_store[key]
                if now < expire:
                    return value
            result = func(*args, **kwargs)
            _cache_store[key] = (result, now + timedelta(seconds=ttl_seconds))
            return result
        return wrapper
    return decorator


def clear_cache():
    _cache_store.clear()
