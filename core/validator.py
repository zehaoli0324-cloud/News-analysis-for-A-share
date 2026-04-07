"""
数据质量校验层：在数据进入 AI prompt 之前过滤垃圾字段。
"""
from typing import List, Dict, Any


def validate_holder(holder: dict) -> bool:
    """股东条目有效性：必须有名称且名称不等于公司本身（数据抓错了）"""
    name = str(holder.get("holder", "")).strip()
    ratio = str(holder.get("ratio", "")).strip()
    return (
        bool(name)
        and name not in ("nan", "", "-")
        and name != holder.get("_company", "")   # 防止公司名=股东名
        and ratio not in ("nan", "", "-", "持股：")
    )


def validate_fund_flow(ff: dict) -> bool:
    """资金流向有效性：净流入不能为空字符串"""
    inflow = str(ff.get("inflow", "")).strip()
    return bool(inflow) and inflow not in ("nan", "", "-")


def validate_news_item(item: dict, min_title_len: int = 5) -> bool:
    """新闻条目有效性"""
    title = str(item.get("title", "")).strip()
    return len(title) >= min_title_len and title not in ("nan", "")


def clean_holders(holders: List[dict], company_name: str = "") -> List[dict]:
    """
    清洗股东列表：
    - 过滤名称=公司名的条目（接口返回错误数据）
    - 过滤持股比例为空的条目
    """
    result = []
    for h in holders:
        h["_company"] = company_name
        if validate_holder(h):
            h.pop("_company", None)
            result.append(h)
    return result


def clean_fund_flow(ff: dict) -> dict:
    """清洗资金流向：去掉空字段"""
    return {k: v for k, v in ff.items()
            if str(v).strip() not in ("", "nan", "-", "%", "0.0%")}


def summarize_data_quality(collected: Dict[str, Any]) -> str:
    """生成数据质量摘要行，供日志和 AI prompt 使用"""
    lines = []
    for name, val in collected.items():
        if val is None:
            lines.append(f"✗ {name}:空")
        elif isinstance(val, list):
            lines.append(f"{'✓' if val else '✗'} {name}:{len(val)}条")
        elif isinstance(val, dict):
            lines.append(f"{'✓' if val else '✗'} {name}:{'有' if val else '空'}")
        else:
            lines.append(f"✓ {name}:有")
    return " | ".join(lines)
