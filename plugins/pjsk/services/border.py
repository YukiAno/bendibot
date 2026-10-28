"""榜线服务 —— 当前分数线与榜线预测。

能力说明（很重要的区服差异）
----------------------------
========================  ======  ======  ======  ======  ======
功能                       国服    日服    台服    国际服  韩服
========================  ======  ======  ======  ======  ======
当前榜线（各档位分数）      ❌      ✅      ✅      ✅      ✅
榜线预测                    ❌      ✅      ❌      ❌      ❌
========================  ======  ======  ======  ======  ======

* **国服没有榜线**：``api.sekai.best`` 明确拒绝 ``region=cn``
  （``region must be one of jp, tw, en, kr``）。这是数据源限制，不是实现问题。
  查询时会返回一句「国服暂不支持」的提示，不会报错。
* **预测只有日服**：33Kit 只服务日服。其余三个预测源
  （SekaRun / SnowyBot / 本地自算）实测暂时不可用，详见 ``providers/``。
"""

from __future__ import annotations

from datetime import datetime, timezone

from nonebot import logger

from ..config import cfg, region_label
from ..models import BorderLine, humanize_delta
from ..providers import Capability, find_provider, supported_regions, unsupported_hint
from ..providers.sekai_best import COMMON_RANKS, SekaiBestProvider

#: 「查榜线」默认展示的档位（太多会刷屏）
DEFAULT_RANKS: tuple[int, ...] = (100, 500, 1000, 2000, 5000, 10000, 50000, 100000)


class BorderError(Exception):
    """榜线查询失败，消息可直接发给用户。"""


# ---------------------------------------------------------------- 当前榜线


async def current_borders(region: str, *, force: bool = False) -> list[BorderLine]:
    """当前活动全部档位的分数线。

    :raises BorderError: 该区服没有榜线数据源
    """
    provider = find_provider(Capability.BORDER, region)
    if provider is None:
        raise BorderError(unsupported_hint(Capability.BORDER, region))

    try:
        return await provider.get_borders(region, force=force)
    except Exception as exc:
        logger.warning("[pjsk] 查 {} 榜线失败: {}", region, exc)
        raise BorderError(f"查询{region_label(region)}榜线失败，请稍后再试。") from exc


async def border_of(region: str, rank: int) -> BorderLine | None:
    """指定档位的分数线。"""
    for border in await current_borders(region):
        if border.rank == rank:
            return border
    return None


# ---------------------------------------------------------------- 预测


async def forecast(region: str) -> dict[int, int]:
    """榜线预测：``{档位: 预测最终分数}``。

    :raises BorderError: 该区服没有预测源
    """
    provider = find_provider(Capability.FORECAST, region)
    if provider is None:
        raise BorderError(unsupported_hint(Capability.FORECAST, region))
    try:
        return await provider.get_forecast(region)
    except ValueError as exc:
        raise BorderError(str(exc)) from exc
    except Exception as exc:
        logger.warning("[pjsk] 查 {} 榜线预测失败: {}", region, exc)
        raise BorderError(f"查询{region_label(region)}榜线预测失败，请稍后再试。") from exc


async def forecast_report(region: str) -> str:
    """渲染榜线预测。"""
    predictions = await forecast(region)
    if not predictions:
        return f"{region_label(region)}暂时没有预测数据。"

    provider = find_provider(Capability.FORECAST, region)
    source = provider.desc if provider else ""

    lines = [f"【{region_label(region)}】榜线预测", f"数据源：{source}", ""]
    for rank in DEFAULT_RANKS:
        if rank in predictions:
            lines.append(f"• {rank:>6,} 名  最终约 {predictions[rank]:,}")

    # 补充其它有的档位（只列几个大的）
    extra = [r for r in sorted(predictions) if r not in DEFAULT_RANKS]
    if extra:
        show = [r for r in extra if r >= 10][:6]
        if show:
            lines.append("")
            lines.append("其它档位：")
            for rank in show:
                lines.append(f"• {rank:>6,} 名  最终约 {predictions[rank]:,}")

    lines.append("")
    lines.append("（预测值会随活动进行不断修正，仅供参考）")
    return "\n".join(lines)


# ---------------------------------------------------------------- 渲染


def render_borders(
    borders: list[BorderLine],
    region: str,
    *,
    ranks: tuple[int, ...] = DEFAULT_RANKS,
    rank_lo: int | None = None,
    rank_hi: int | None = None,
) -> str:
    """渲染当前榜线。

    :param ranks: 想看的档位（只显示数据里有的）
    :param rank_lo: 只看这个名次之后的（``查榜线 1000+``）
    :param rank_hi: 只看这个名次之前的（``查榜线 100-``）
    """
    if not borders:
        return f"没查到{region_label(region)}的榜线数据。"

    mapping = {b.rank: b for b in borders}

    if rank_lo is not None or rank_hi is not None:
        selected = [
            b
            for b in borders
            if (rank_lo is None or b.rank >= rank_lo)
            and (rank_hi is None or b.rank <= rank_hi)
        ]
        selected.sort(key=lambda b: b.rank)
        # 名次范围太宽时抽样，避免刷屏
        if len(selected) > cfg.max_result:
            step = max(1, len(selected) // cfg.max_result)
            selected = selected[::step][: cfg.max_result]
    else:
        selected = [mapping[r] for r in ranks if r in mapping]
        if not selected:
            selected = sorted(borders, key=lambda b: b.rank)[: cfg.max_result]

    updated = borders[0].updated_text() if borders else "未知"
    event_id = borders[0].event_id if borders else None

    lines = [f"【{region_label(region)}】当前榜线"]
    if event_id:
        lines.append(f"活动：第{event_id}期   更新：{updated}")
    lines.append("")

    for border in selected:
        lines.append(f"• {border.rank:>7,} 名   {border.score:>12,}")

    lines.append("")
    lines.append("想看具体名次：查榜线 50     看范围：查榜线 1000+")
    return "\n".join(lines)


def render_border_detail(border: BorderLine, region: str) -> str:
    """单个档位的详情。"""
    return (
        f"【{region_label(region)}】第{border.event_id or '?'}期\n"
        f"{border.rank:,} 名：{border.score:,}\n"
        f"更新：{border.updated_text()}"
    )


def supported_border_regions() -> list[str]:
    """哪些区服能查榜线。"""
    return supported_regions(Capability.BORDER)


def border_speed_text(borders: list[BorderLine], hours: float) -> str:
    """用当前榜线估算速度（粗略）。

    .. note::
       真正准确的时速需要用历史数据算差值，这里只做「从活动开始到现在的均值」，
       所以调用方要自己决定是否展示。
    """
    if not borders or hours <= 0:
        return "数据不足"
    # 取 1000 名做代表
    target = next((b for b in borders if b.rank == 1000), borders[0])
    per_hour = target.score / hours
    return f"约 {per_hour:,.0f} 分/小时（均值估算）"