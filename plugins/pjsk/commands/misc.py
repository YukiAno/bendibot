"""杂项指令 —— 帮助、数据源、缓存管理。

::

    pjsk帮助            看有哪些 PJSK 指令
    pjsk数据源          看当前注册了哪些数据源、各支持什么区服
    pjsk清缓存          清掉本地缓存（超级用户），下次查询重新拉
"""

from __future__ import annotations

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Message
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER

from ..cache import clear_cache
from ..config import cfg, region_label
from ..providers import all_providers, supported_capabilities
from ..providers.base import CAPABILITY_NAMES, Capability
from ..render import render_provider_list

# ---------------------------------------------------------------- 帮助

help_matcher = on_command("pjsk帮助", aliases={"pjsk指令"}, priority=5, block=True)

_HELP_TEXT = """【PJSK 插件指令】

【活动】
  查活动 [区服]        当期活动 + 剩余时间 + 下期预告

【歌曲】
  查歌曲 <关键词> [区服]   按曲名搜索（支持模糊）
  查谱面 <关键词> [难度]   看各难度等级与物量
  查曲绘 <关键词> [区服]   直接发歌曲封面图
  加别名 <别名> <歌名>     给歌加个中文别名（例：加别名 红虾 独りんぼエンヴィー）

【卡牌与角色】
  查卡 <关键词> [区服]     搜索卡牌
  查卡池 [区服]           看卡池
  查角色 <关键词> [区服]   搜索角色

【计算】
  实效 <5个数字>          算协力卡组实效，例：实效 100 200 300 400 500
  组卡 <歌名> [区服] [难度] [活动ID] [优化目标]
                          推荐理论最优卡组（五个区服都支持）
                          例：组卡 千本樱 ex  /  组卡 千本樱 无活动
                              组卡 千本樱 按综合力

【榜线】
  查榜线 [档位] [区服]     实时排名分数（国服没有）
  查预测 [区服]           榜线预测（仅日服）

【其他】
  pjsk数据源           看数据源与各自支持的区服
  pjsk清缓存           清缓存（仅超级用户）
  pjsk帮助             本帮助

区服可写：国服 / 日服 / 台服 / 韩服 / 国际服，不写用默认。
默认区服：{default}
数据缓存目录：{cachedir}
""".strip()


@help_matcher.handle()
async def handle_help(args: Message = CommandArg()) -> None:
    text = _HELP_TEXT.format(
        default=region_label(cfg.default_region),
        cachedir=cfg.cache_dir,
    )
    await help_matcher.finish(text)


# ---------------------------------------------------------------- 数据源

prov_matcher = on_command("pjsk数据源", aliases={"pjsk源"}, priority=5, block=True)


@prov_matcher.handle()
async def handle_providers() -> None:
    lines = [render_provider_list(all_providers())]
    lines.append("")
    lines.append("各区服支持情况：")
    for region in ("cn", "jp", "tw", "kr", "en"):
        items = supported_capabilities(region)
        names = "、".join(_cap_name(c) for c in items) or "无"
        lines.append(f"• {region_label(region)}：{names}")

    await prov_matcher.finish("\n".join(lines))


def _cap_name(capability: Capability) -> str:
    return CAPABILITY_NAMES.get(capability, capability.value)


# ---------------------------------------------------------------- 清缓存

clear_matcher = on_command(
    "pjsk清缓存", aliases={"pjsk清cache"}, priority=5, permission=SUPERUSER, block=True
)


@clear_matcher.handle()
async def handle_clear(args: Message = CommandArg()) -> None:
    arg = args.extract_plain_text().strip()

    if arg:
        # 指定 key 时清单条，方便调试
        removed = clear_cache(arg)
        await clear_matcher.finish(f"已清除缓存「{arg}」（{removed} 条）。")

    removed = clear_cache()
    await clear_matcher.finish(f"已清空 PJSK 缓存（{removed} 条）。下次查询会重新拉取。")