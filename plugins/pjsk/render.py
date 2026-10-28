"""输出渲染 —— 把数据模型变成 QQ 里看得懂的中文。

单独一层的理由
--------------
同样的数据要喂给两种消费者：**群友**（要好看的文案）和 **AI**（要结构清晰、
信息完整、别太啰嗦）。渲染层集中处理排版，想换风格（比如以后改成出图片）
只改这里，services 不用动。

约定
----
* 不用 Markdown 表格（QQ 不渲染），用「• 键：值」这种一行一项的写法
* 时间用区服本地时间，并同时给「还剩多久」——这是群友最关心的
* 条数受 ``cfg.max_result`` 限制，避免刷屏
"""

from __future__ import annotations

from datetime import datetime, timezone

from .config import region_label
from .models import Event, Music

# ---------------------------------------------------------------- 小工具


def _bar(ratio: float | None, width: int = 12) -> str:
    """进度条，例如 ``██████░░░░░░ 52%``。"""
    if ratio is None:
        return ""
    ratio = max(0.0, min(1.0, ratio))
    filled = int(round(ratio * width))
    return "█" * filled + "░" * (width - filled) + f" {ratio * 100:.0f}%"


def _status_tag(event: Event, now: datetime | None = None) -> str:
    """状态标记。"""
    status = event.status(now)
    return {
        "running": "进行中",
        "upcoming": "未开始",
        "ended": "已结束",
    }.get(status, "未知")


# ---------------------------------------------------------------- 活动


def render_event(event: Event, *, now: datetime | None = None, compact: bool = False) -> str:
    """渲染单个活动的详情。"""
    region = region_label(event.region)
    lines: list[str] = []

    if compact:
        lines.append(f"{event.name}（{region}）")
        lines.append(f"剩余 {event.remaining_text(now)}")
        return "\n".join(lines)

    lines.append(f"【{region} · 第{event.id}期】{event.name}")
    lines.append(f"状态：{_status_tag(event, now)}")
    lines.append(f"开始：{event.start_text()}")
    lines.append(f"结束：{event.end_text()}")

    remaining = event.remaining_text(now)
    if remaining not in ("已结束", "未知"):
        lines.append(f"剩余：{remaining}")

    progress = event.progress(now)
    if progress is not None:
        lines.append(f"进度：{_bar(progress)}")

    return "\n".join(lines)


def render_event_list(events: list[Event], *, limit: int = 5) -> str:
    """渲染活动列表（一行一期）。"""
    if not events:
        return "没有查到活动数据。"

    now = datetime.now(timezone.utc)
    lines: list[str] = []
    for event in events[:limit]:
        tag = _status_tag(event, now)
        if tag == "进行中":
            tail = f"剩余 {event.remaining_text(now)}"
        else:
            tail = event.start_text()
        lines.append(f"• 第{event.id}期 {event.name}（{tag}·{tail}）")
    return "\n".join(lines)


def render_event_report(
    region: str,
    current: Event | None,
    upcoming: Event | None = None,
    *,
    upcoming_limit: int | None = None,
) -> str:
    """渲染「查活动」的完整回复。"""
    label = region_label(region)
    now = datetime.now(timezone.utc)
    parts: list[str] = []

    if current is not None:
        parts.append(render_event(current, now=now))
    else:
        parts.append(f"【{label}】当前没有进行中的活动。")

    if upcoming is not None and upcoming is not current:
        start = upcoming.start_text()
        parts.append(f"下一期：第{upcoming.id}期 {upcoming.name}\n开始：{start}")

    return "\n\n".join(parts)


# ---------------------------------------------------------------- 歌曲


def render_music(music: Music, *, difficulty: str | None = None) -> str:
    """渲染单首歌。"""
    region = region_label(music.region)
    lines = [f"【{region}】{music.title}"]
    if music.composer:
        lines.append(f"作曲：{music.composer}")
    if music.lyricist and music.lyricist != music.composer:
        lines.append(f"作词：{music.lyricist}")
    if music.arranger:
        lines.append(f"编曲：{music.arranger}")
    if music.categories:
        lines.append(f"类型：{music.unit_label}")
    if difficulty:
        lines.append(f"难度：{difficulty}")
    lines.append(f"曲 ID：{music.id}")
    return "\n".join(lines)


def render_music_list(items: list[tuple[Music, str | None]], *, limit: int = 10) -> str:
    """渲染歌曲搜索结果。``items`` 是 ``(歌曲, 难度文本)`` 列表。"""
    if not items:
        return "没找到符合条件的歌曲。"
    lines: list[str] = []
    for music, diff in items[:limit]:
        tail = f"  {diff}" if diff else ""
        lines.append(f"• {music.title}{tail}")
    if len(items) > limit:
        lines.append(f"（共 {len(items)} 首，只显示前 {limit} 首）")
    return "\n".join(lines)


# ---------------------------------------------------------------- 通用


def render_provider_list(providers: list) -> str:
    """渲染数据源列表（``/pjsk数据源``）。"""
    if not providers:
        return "没有已注册的数据源。"
    lines = ["PJSK 数据源："]
    for p in providers:
        lines.append(
            f"• {p.name}\n"
            f"  区服：{p.region_text()}\n"
            f"  能力：{p.capability_text()}\n"
            f"  说明：{p.desc or '—'}"
        )
    return "\n".join(lines)