"""活动服务 —— 「查活动」指令和 AI 工具共用同一份逻辑。

覆盖的功能
----------
* 当前活动 + **剩余时间**（群友最关心的）
* 下一期活动预告（**国服特有**：国服 master data 含未开始的活动）
* 最近几期活动列表

区服能力差异
------------
activity 数据 5 个区服都有，所以这个功能**全服可用**。
取不到数据源时不会抛异常，而是返回一句人话提示。
"""

from __future__ import annotations

from nonebot import logger

from ..config import cfg, region_label
from ..models import Event
from ..providers import Capability, find_provider, unsupported_hint
from ..render import render_event, render_event_report


class PjskError(Exception):
    """PJSK 查询失败（网络、数据源缺失等），消息可直接发给用户。"""


async def _master(region: str):
    """取静态数据源；不可用则抛 :class:`PjskError`。"""
    provider = find_provider(Capability.EVENT, region)
    if provider is None:
        raise PjskError(unsupported_hint(Capability.EVENT, region))
    return provider


async def get_current(region: str) -> Event | None:
    """当前进行中的活动。"""
    provider = await _master(region)
    try:
        return await provider.get_current_event(region)
    except Exception as exc:
        logger.warning("[pjsk] 查 {} 当前活动失败: {}", region, exc)
        raise PjskError(f"查询{region_label(region)}活动失败，请稍后再试。") from exc


async def get_upcoming(region: str) -> Event | None:
    """下一期活动（未开始）。"""
    provider = await _master(region)
    try:
        return await provider.get_upcoming_event(region)
    except Exception as exc:
        logger.debug("[pjsk] 查 {} 下期活动失败: {}", region, exc)
        return None


async def get_recent(region: str, limit: int = 5) -> list[Event]:
    """最近几期活动。"""
    provider = await _master(region)
    try:
        return await provider.get_recent_events(region, limit)
    except Exception as exc:
        logger.warning("[pjsk] 查 {} 活动列表失败: {}", region, exc)
        raise PjskError(f"查询{region_label(region)}活动列表失败，请稍后再试。") from exc


async def report(region: str, *, with_upcoming: bool = True) -> str:
    """渲染「查活动」的完整回复文本。

    :param region: 区服代码（``cn`` / ``jp`` …）
    :param with_upcoming: 是否附上下期预告
    """
    current = await get_current(region)
    upcoming = await get_upcoming(region) if with_upcoming else None
    return render_event_report(region, current, upcoming)


async def one_line(region: str) -> str:
    """一行式摘要（给 AI 工具用的短文本，省 token）。"""
    current = await get_current(region)
    if current is None:
        return f"{region_label(region)}当前没有进行中的活动。"
    return render_event(current, compact=True)