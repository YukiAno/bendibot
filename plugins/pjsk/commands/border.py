"""榜线指令。

::

    查榜线              默认几个常用档位（日服/台服/国际服/韩服）
    查榜线 1000         只看 1000 名
    查榜线 1000+        1000 名及以后
    查榜线 100-         100 名以内
    查榜线 日服          指定区服
    查预测              榜线预测（目前只有日服）
    查预测 日服

**国服查榜线会得到一句友好提示**（数据源不支持），不会报错。
"""

from __future__ import annotations

import re

from nonebot import logger, on_command
from nonebot.adapters.onebot.v11 import Message
from nonebot.params import CommandArg

from ..config import cfg, region_label, resolve_region
from ..services import border as border_service
from ..services.border import BorderError

#: 「1000+」「100-」这类范围写法
_RANGE_RE = re.compile(r"^([\d,，]+)\s*([+＋\-－])$")
#: 纯数字档位
_NUM_RE = re.compile(r"^[\d,，]+$")


def _to_int(text: str) -> int | None:
    try:
        return int(text.replace(",", "").replace("，", ""))
    except ValueError:
        return None


def _parse(text: str) -> tuple[str, int | None, int | None, int | None]:
    """解析参数。

    :return: ``(区服, 单档位, rank_lo, rank_hi)``
    """
    region = cfg.default_region
    rank: int | None = None
    rank_lo: int | None = None
    rank_hi: int | None = None

    for token in text.split():
        token = token.strip()
        if not token:
            continue

        # 区服
        found = resolve_region(token)
        if found:
            region = found
            continue

        # 范围：1000+ / 100-
        match = _RANGE_RE.match(token)
        if match:
            value = _to_int(match.group(1))
            if value is not None:
                if match.group(2) in "+＋":
                    rank_lo = value
                else:
                    rank_hi = value
            continue

        # 单档位
        if _NUM_RE.match(token):
            value = _to_int(token)
            if value is not None:
                rank = value

    return region, rank, rank_lo, rank_hi


# ---------------------------------------------------------------- 查榜线

border_matcher = on_command(
    "查榜线", aliases={"榜线", "分数线"}, priority=5, block=True
)

_USAGE = (
    "用法：查榜线 [档位] [区服]\n"
    "例：查榜线          常用档位\n"
    "    查榜线 1000     只看 1000 名\n"
    "    查榜线 1000+    1000 名以后\n"
    "    查榜线 100-     100 名以内\n"
    "    查榜线 日服      指定区服\n"
    "（国服暂不支持榜线查询）"
)


@border_matcher.handle()
async def handle_border(args: Message = CommandArg()) -> None:
    region, rank, rank_lo, rank_hi = _parse(args.extract_plain_text())

    # 同 forecast：finish() 必须放在 try 外面，否则 FinishedException
    # 会被 except Exception 捕获，造成「发完结果又发一条出错提示」。
    try:
        if rank is not None:
            border = await border_service.border_of(region, rank)
            if border is None:
                result = (
                    f"{region_label(region)}没有 {rank:,} 名这个档位。\n"
                    "试试：查榜线 1000   或   查榜线 1000+"
                )
            else:
                result = border_service.render_border_detail(border, region)
        else:
            borders = await border_service.current_borders(region)
            result = border_service.render_borders(
                borders, region, rank_lo=rank_lo, rank_hi=rank_hi
            )
    except BorderError as exc:
        await border_matcher.finish(f"{exc}\n{_USAGE}")
        return
    except Exception as exc:
        logger.exception("[pjsk] 查榜线异常: {}", exc)
        await border_matcher.finish("查榜线出错了，请稍后再试。")
        return

    await border_matcher.finish(result)


# ---------------------------------------------------------------- 查预测

forecast_matcher = on_command(
    "查预测", aliases={"榜线预测", "预测榜线"}, priority=5, block=True
)


@forecast_matcher.handle()
async def handle_forecast(args: Message = CommandArg()) -> None:
    text = args.extract_plain_text().strip()
    region = resolve_region(text) or cfg.default_region

    # 注意：绝对不能把 finish() 放在 try 里 ——
    # finish() 内部会抛 FinishedException 来中断处理，而它继承 Exception，
    # 会被下面的 except Exception 捕获，导致「发完结果又发一条出错提示」。
    try:
        report = await border_service.forecast_report(region)
    except BorderError as exc:
        await forecast_matcher.finish(
            f"{exc}\n目前只有日服有榜线预测（数据源限制）。"
        )
        return
    except Exception as exc:
        logger.exception("[pjsk] 查预测异常: {}", exc)
        await forecast_matcher.finish("查预测出错了，请稍后再试。")
        return

    await forecast_matcher.finish(report)