"""活动指令 —— 「查活动」。

用法::

    查活动              查默认区服（.env 的 PJSK_DEFAULT_REGION）
    查活动 日服         查日服
    查活动 cn           也可以写代码
    活动                别名，同上

回复内容：活动名、状态、开始/结束时间（区服本地时间）、**剩余时间**、进度条，
以及（国服可以）下一期预告。
"""

from __future__ import annotations

from nonebot import logger, on_command
from nonebot.adapters.onebot.v11 import Message, MessageEvent
from nonebot.params import CommandArg

from ..config import REGION_NAMES, cfg, region_label, resolve_region
from ..services import event as event_service
from ..services.event import PjskError

matcher = on_command("查活动", aliases={"活动"}, priority=5, block=True)


def _parse_region(text: str) -> str:
    """解析区服参数。

    空 -> 默认区服；能识别 -> 对应区服；识别不了 -> 抛 :class:`PjskError`。
    """
    text = text.strip()
    if not text:
        return cfg.default_region

    region = resolve_region(text)
    if region is None:
        names = "、".join(REGION_NAMES.values())
        raise PjskError(
            f"不认识的区服「{text}」。可以写：{names}（或不写，默认"
            f"{region_label(cfg.default_region)}）"
        )
    return region


@matcher.handle()
async def handle_event(event: MessageEvent, args: Message = CommandArg()) -> None:
    try:
        region = _parse_region(args.extract_plain_text())
    except PjskError as exc:
        await matcher.finish(str(exc))
        return

    try:
        text = await event_service.report(region)
    except PjskError as exc:
        await matcher.finish(str(exc))
        return
    except Exception as exc:  # 兜底，绝不给群里抛栈
        logger.exception("[pjsk] 查活动异常: {}", exc)
        await matcher.finish("查询活动失败，请稍后再试。")
        return

    await matcher.finish(text)