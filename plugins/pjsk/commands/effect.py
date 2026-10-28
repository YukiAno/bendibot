"""实效计算指令。

用法::

    实效 100 200 300 400 500     第一位是队长，后四位是队友
    实效 140 110 140 100 80
    倍率 140 110 140 100 80      别名

**纯本地计算**，不联网。国服日服通用。
公式与来源见 ``services/effect.py`` 的文档。
"""

from __future__ import annotations

import re

from nonebot import on_command, logger
from nonebot.adapters.onebot.v11 import Message
from nonebot.params import CommandArg

from ..services import effect as effect_service

matcher = on_command(
    "实效", aliases={"pjsk实效", "倍率"}, priority=5, block=True
)

#: 抓取文本里的数字（支持小数）
_NUM_RE = re.compile(r"\d+(?:\.\d+)?")

_USAGE = (
    "用法：实效 100 200 300 400 500\n"
    "（第一位是队长，享受 100% 生效；后面 4 位各按 20% 生效）"
)


@matcher.handle()
async def handle_effect(args: Message = CommandArg()) -> None:
    text = args.extract_plain_text()
    numbers = [float(m) for m in _NUM_RE.findall(text)]

    if not numbers:
        await matcher.finish("没看到技能值。\n" + _USAGE)
        return

    try:
        result = effect_service.calc_text(numbers)
    except ValueError as exc:
        await matcher.finish(f"{exc}\n{_USAGE}")
        return
    except Exception as exc:  # 兜底
        logger.exception("[pjsk] 实效计算异常: {}", exc)
        await matcher.finish("计算出错了，请检查输入。")
        return

    await matcher.finish(result)