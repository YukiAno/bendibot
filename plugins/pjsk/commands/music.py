"""歌曲与谱面指令。

::

    查歌曲 千本樱            搜歌（支持原名/别名/读音，模糊匹配）
    查歌曲 ロキ 日服         指定区服
    查谱面 千本樱            看各难度等级与物量
    查谱面 千本樱 日服
    加别名 38 千本樱         给歌曲加中文别名（超级用户）

参数解析约定：**最后一个词如果能识别成区服，就当作区服**，其余部分当关键词。
所以「查歌曲 千本樱 日服」和「查歌曲 日服 千本樱」都能正确工作。
"""

from __future__ import annotations

from nonebot import logger, on_command
from nonebot.adapters.onebot.v11 import Message, MessageSegment
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER

from ..config import REGION_NAMES, cfg, region_label, resolve_region
from ..services import music as music_service

# ---------------------------------------------------------------- 搜歌

search_matcher = on_command(
    "查歌曲", aliases={"歌曲", "搜歌", "找歌"}, priority=5, block=True
)


def _split_args(text: str) -> tuple[str, str]:
    """拆「关键词 + 可选区服」。

    返回 ``(关键词, 区服代码)``。
    """
    parts = text.split()
    if len(parts) >= 2:
        region = resolve_region(parts[-1])
        if region:
            return " ".join(parts[:-1]).strip(), region
    return text.strip(), cfg.default_region


@search_matcher.handle()
async def handle_search(args: Message = CommandArg()) -> None:
    keyword, region = _split_args(args.extract_plain_text())

    if not keyword:
        await search_matcher.finish(
            "要搜什么歌？\n"
            "用法：查歌曲 千本樱\n"
            "（可以写曲名、中文别名或日文原名；最后加区服可指定，如：查歌曲 ロキ 日服）"
        )
        return

    try:
        hits = await music_service.search(region, keyword, limit=cfg.max_result)
    except ValueError as exc:
        await search_matcher.finish(str(exc))
        return
    except Exception as exc:
        logger.exception("[pjsk] 搜歌异常: {}", exc)
        await search_matcher.finish("搜歌出错了，请稍后再试。")
        return

    if not hits:
        names = "、".join(REGION_NAMES.values())
        await search_matcher.finish(
            f"在{region_label(region)}没找到「{keyword}」。\n"
            f"提示：歌名大多是日文，可以试试中文别名，或用 /加别名 自己加。\n"
            f"（可指定区服：{names}）"
        )
        return

    lines = [f"{region_label(region)} 搜「{keyword}」找到 {len(hits)} 首："]
    for music in hits:
        aliases = music_service.aliases_of(music.id)
        alias_text = f"（{'/'.join(aliases[:2])}）" if aliases else ""
        lines.append(f"• {music.title}{alias_text}  ID:{music.id}")
    lines.append("")
    lines.append("看谱面：查谱面 <歌名>")

    await search_matcher.finish("\n".join(lines))


# ---------------------------------------------------------------- 查谱面

chart_matcher = on_command(
    "查谱面", aliases={"谱面", "查定数", "定数"}, priority=5, block=True
)


@chart_matcher.handle()
async def handle_chart(args: Message = CommandArg()) -> None:
    keyword, region = _split_args(args.extract_plain_text())

    if not keyword:
        await chart_matcher.finish(
            "要查哪首歌的谱面？\n用法：查谱面 千本樱\n（可加区服：查谱面 千本樱 日服）"
        )
        return

    try:
        music = await music_service.find_one(region, keyword)
        charts = (
            await music_service.get_charts(region, music.id)
            if music is not None
            else None
        )
    except ValueError as exc:
        await chart_matcher.finish(str(exc))
        return
    except Exception as exc:
        logger.exception("[pjsk] 查谱面异常: {}", exc)
        await chart_matcher.finish("查谱面出错了，请稍后再试。")
        return

    if music is None or charts is None:
        await chart_matcher.finish(f"在{region_label(region)}没找到「{keyword}」。")
        return

    await chart_matcher.finish(music_service.charts_text(music, charts))


# ---------------------------------------------------------------- 查曲绘

jacket_matcher = on_command(
    "查曲绘", aliases={"曲绘", "歌曲封面", "查封面"}, priority=5, block=True
)


@jacket_matcher.handle()
async def handle_jacket(args: Message = CommandArg()) -> None:
    keyword, region = _split_args(args.extract_plain_text())

    if not keyword:
        await jacket_matcher.finish(
            "要查哪首歌的曲绘？\n用法：查曲绘 千本樱\n（可加区服：查曲绘 千本樱 日服）"
        )
        return

    try:
        hits = await music_service.search(region, keyword, limit=3)
    except ValueError as exc:
        await jacket_matcher.finish(str(exc))
        return
    except Exception as exc:
        logger.exception("[pjsk] 查曲绘异常: {}", exc)
        await jacket_matcher.finish("查曲绘出错了，请稍后再试。")
        return

    if not hits:
        await jacket_matcher.finish(f"在{region_label(region)}没找到「{keyword}」。")
        return

    music = hits[0]
    url = music.jacket_url()
    if not url:
        await jacket_matcher.finish(f"【{music.title}】没有曲绘数据。")
        return

    caption = f"【{music.title}】{region_label(region)}"
    if len(hits) > 1:
        others = "、".join(m.title for m in hits[1:])
        caption += f"\n（还有：{others}）"

    # WebP 优先（体积只有 PNG 的五分之一），发送失败再退回 PNG。
    # 必须用 send() 而不是 finish()：finish() 抛的 FinishedException
    # 会被这里的 except Exception 误判成"发送失败"，导致重复发图。
    try:
        await jacket_matcher.send(MessageSegment.image(url) + "\n" + caption)
    except Exception:
        logger.warning("[pjsk] WebP 曲绘发送失败，退回 PNG")
        await jacket_matcher.send(MessageSegment.image(music.jacket_png) + "\n" + caption)

    await jacket_matcher.finish()


# ---------------------------------------------------------------- 加别名

alias_matcher = on_command("加别名", priority=5, permission=SUPERUSER, block=True)


@alias_matcher.handle()
async def handle_alias(args: Message = CommandArg()) -> None:
    parts = args.extract_plain_text().split()
    if len(parts) < 2:
        await alias_matcher.finish(
            "用法：加别名 <歌曲ID> <别名>\n"
            "例：加别名 38 千本樱\n"
            "（歌曲ID 可以用「查歌曲」看到）"
        )
        return

    try:
        music_id = int(parts[0])
    except ValueError:
        await alias_matcher.finish(f"「{parts[0]}」不是有效的歌曲ID（要填数字）。")
        return

    alias = " ".join(parts[1:]).strip()
    added = music_service.add_alias(music_id, alias)

    if added:
        await alias_matcher.finish(f"已给歌曲 {music_id} 加上别名「{alias}」。")
    else:
        await alias_matcher.finish(f"歌曲 {music_id} 已经有别名「{alias}」了。")