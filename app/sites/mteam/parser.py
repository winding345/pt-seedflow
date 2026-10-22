"""m-team 返回数据的解析：JSON → Candidate。"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from ...scoring import Candidate

DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# 这些促销没有明确的结束时间，按"长期有效"处理
RULE_BASED_DISCOUNTS = {"PERCENT_70", "PERCENT_50", "_2X", "_2X_PERCENT_50"}


def parse_ts(value: Optional[str]) -> Optional[int]:
    """站点返回的是本地时间字符串，转成 epoch 秒。"""
    if not value:
        return None
    try:
        return int(datetime.strptime(value, DATE_FORMAT).timestamp())
    except ValueError:
        return None


def to_candidate(item: Dict[str, Any]) -> Candidate:
    status = item.get("status") or {}
    discount = status.get("discount") or "NORMAL"
    discount_end = parse_ts(status.get("discountEndTime"))

    # 规则型促销不返回 discountEndTime，此时不要伪造一个到期时间
    rule = status.get("promotionRule") or {}
    if rule.get("discount"):
        discount = rule["discount"]
        discount_end = None

    try:
        category = int(item.get("category"))
    except (TypeError, ValueError):
        category = None

    return Candidate(
        tid=str(item.get("id")),
        name=str(item.get("name") or ""),
        size=int(item.get("size") or 0),
        discount=str(discount),
        discount_end=discount_end,
        seeders=int(status.get("seeders") or 0),
        leechers=int(status.get("leechers") or 0),
        created=parse_ts(item.get("createdDate")),
        category=category,
        page_url="https://kp.m-team.cc/detail/%s" % item.get("id"),
    )


def parse_candidates(payload: Dict[str, Any]) -> List[Candidate]:
    items = ((payload.get("data") or {}).get("data")) or []
    return [to_candidate(item) for item in items]
