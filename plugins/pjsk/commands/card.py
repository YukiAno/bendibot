"""卡牌 / 卡池 / 角色指令。

::

    查卡 一歌              某个角色的卡（按稀有度从高到低）
    查卡 一歌 4            只要 ★4
    查卡 侦探              按卡名前缀搜索
    查卡池                 当前正在开的卡池 + 剩余时间
    查角色                 全部角色
    （区服写法同上，如「查卡池 日服」）

参数解析与歌曲指令一致：**最后一个词能识别成区服就当区服**。
"""

from __future__ import annotations

from nonebot import logger, on_command
from nonebot.adapters.onebot.v11 import Message, MessageSegment
from nonebot.params import CommandArg

from ..config import cfg, region_label, resolve_region
from ..services import card as card_service

_USAGE_CARD = (
    "用法：查卡 <角色名或卡名> [区服]\n"
    "例：查卡 一歌      → 一歌的全部卡\n"
    "    查卡 一歌 4    → 只要 ★4\n"
    "    查卡 侦探       → 按卡名搜索\n"
    "想看卡面用「卡面 <行首的 #ID>」"
)

_USAGE_CARD_ART = (
    "用法：卡面 <角色名 / 卡名 / 卡ID> [花前|花后] [区服]\n"
    "例：卡面 949       → 花前花后各一张（觉醒前后）\n"
    "    卡面 949 花前  → 只看觉醒前\n"
    "    卡面 949 花后  → 只看觉醒后\n"
    "（「特训前/后」「觉醒前/后」也能用；「查卡面」是别名）"
)

#: 版本词表：``(词, 想要哪个版本)``。
#:
#: **顺序是关键，长的必须排前面。** 如果「特训」排在「特训前」前面，
#: ``"卡面 949 特训前".replace("特训", " ")`` 会变成「949  前」，
#: **剩一个「前」混进关键词**，查出来就是"没找到 949 前"。
#:
#: 主名用玩家黑话：觉醒俗称「开花」，所以「花前」= 觉醒前、「花后」= 觉醒后。
_VERSION_WORDS: tuple[tuple[str, str], ...] = (
    # 主名
    ("花前", "before"),
    ("花后", "after"),
    # 别名（游戏内正式说法）
    ("特训前", "before"),
    ("特训后", "after"),
    ("觉醒前", "before"),
    ("觉醒后", "after"),
    ("after_training", "after"),  # 必须在 after 前面
    ("after", "after"),
    ("before", "before"),
    # 只说「特训」「觉醒」时按觉醒后理解（大家通常指那张）
    ("特训", "after"),
    ("觉醒", "after"),
)


def _split_args(text: str) -> tuple[str, str]:
    """拆「关键词 + 可选区服」。"""
    parts = text.split()
    if len(parts) >= 2:
        region = resolve_region(parts[-1])
        if region:
            return " ".join(parts[:-1]).strip(), region
    return text.strip(), cfg.default_region


def _split_version(text: str) -> tuple[str, str]:
    """从关键词里剥掉版本标记。

    :return: ``(剩余关键词, "both"|"before"|"after")``。**没写版本就是 both**
        —— 默认一次发觉醒前后两张。
    """
    for word, want in _VERSION_WORDS:
        if word in text:
            return text.replace(word, " ").strip(), want
    return text, "both"


# ---------------------------------------------------------------- 查卡

card_matcher = on_command("查卡", aliases={"查卡牌", "卡牌"}, priority=5, block=True)


@card_matcher.handle()
async def handle_card(args: Message = CommandArg()) -> None:
    keyword, region = _split_args(args.extract_plain_text())
    if not keyword:
        await card_matcher.finish(_USAGE_CARD)
        return

    # 「一歌 4」这种：末尾单独的数字当稀有度
    rarity: int | None = None
    parts = keyword.split()
    if len(parts) >= 2 and parts[-1].isdigit():
        value = int(parts[-1])
        if 1 <= value <= 4:
            rarity = value
            keyword = " ".join(parts[:-1]).strip()

    try:
        chars = {c.id: c for c in await card_service.all_characters(region)}

        # 先当角色名找
        char = await card_service.find_character(region, keyword)
        if char is not None:
            cards = await card_service.cards_of_character(region, char, rarity=rarity)
            title = f"【{char.name}】的卡"
            if rarity:
                title += f"（★{rarity}）"
            title += f" 共 {len(cards)} 张"
            result = card_service.render_card_list(
                cards,
                chars,
                title=title,
                limit=cfg.card_list_limit,
                # 标题里已经写了是谁，行内不用再重复一遍角色名
                show_owner=False,
            )
        else:
            # 再按卡名搜
            cards = await card_service.search_cards(region, keyword, limit=cfg.max_result)
            if not cards:
                result = f"在{region_label(region)}没找到「{keyword}」相关的角色或卡。"
            else:
                result = card_service.render_card_list(
                    cards,
                    chars,
                    title=f"{region_label(region)} 搜「{keyword}」：",
                    limit=cfg.max_result,
                )
    except ValueError as exc:
        await card_matcher.finish(str(exc))
        return
    except Exception as exc:
        logger.exception("[pjsk] 查卡异常: {}", exc)
        await card_matcher.finish("查卡出错了，请稍后再试。")
        return

    await card_matcher.finish(result)


# ---------------------------------------------------------------- 卡面

cardart_matcher = on_command(
    "卡面", aliases={"查卡面", "查卡绘", "卡绘", "看卡面"}, priority=5, block=True
)


@cardart_matcher.handle()
async def handle_card_art(args: Message = CommandArg()) -> None:
    """发卡面图 —— **默认花前 + 花后各一张，合成一条消息发**。

    卡面图地址规则见 :meth:`Card.card_art_url`。因为实测约 5% 的卡在资源库
    里压根没有图（新卡没同步 / 本来就没这版立绘），所以发之前先探一次
    （两张并发探，用户只等一次往返）。

    想看单张就加「特训前」／「特训后」。
    """
    keyword, region = _split_args(args.extract_plain_text())
    keyword, want = _split_version(keyword)
    if not keyword:
        await cardart_matcher.finish(_USAGE_CARD_ART)
        return

    # 「一歌 4」这种：末尾单独的数字当稀有度。
    # 整体就是一个数字时不在这里处理 —— 那是卡 ID，交给 find_card。
    rarity: int | None = None
    parts = keyword.split()
    if len(parts) >= 2 and parts[-1].isdigit():
        value = int(parts[-1])
        if 1 <= value <= 4:
            rarity = value
            keyword = " ".join(parts[:-1]).strip()

    try:
        cards = await card_service.find_card(region, keyword, rarity=rarity)
    except ValueError as exc:
        await cardart_matcher.finish(str(exc))
        return
    except Exception as exc:
        logger.exception("[pjsk] 卡面异常: {}", exc)
        await cardart_matcher.finish("卡面出错了，请稍后再试。")
        return

    if not cards:
        shown = keyword + (f" ★{rarity}" if rarity else "")
        await cardart_matcher.finish(
            f"在{region_label(region)}没找到「{shown}」相关的卡。"
        )
        return

    card = cards[0]
    try:
        chars = {c.id: c for c in await card_service.all_characters(region)}
    except Exception as exc:
        logger.warning("[pjsk] 卡面取角色表失败: {}", exc)
        chars = {}
    char = chars.get(card.character_id)
    who = char.name if char else f"角色{card.character_id}"

    try:
        arts = await card_service.pick_art_urls(card, want=want)
    except Exception as exc:
        logger.warning("[pjsk] 探测卡面图失败: {}", exc)
        arts = []

    if not arts:
        await cardart_matcher.finish(
            f"【{who}】{card.name}（#{card.id}）的卡面图取不到。\n"
            "资源库里这张卡还没同步图片，换一张试试？"
        )
        return

    has_before = any(not flag for _, flag in arts)
    has_after = any(flag for _, flag in arts)

    lines = [f"【{who}】{card.name}"]
    lines.append(f"{card.rarity_label}  属性：{card.attr_label}")
    lines.append(f"实装：{card.released_text}  卡 ID：{card.id}")
    # 明确告诉用户**现在发的是哪几张、怎么单独看某一张** ——
    # 否则只知道图出来了，不知道怎么切。「花前/花后」是主名，
    # 后面括号里带上游戏里的正式说法，免得看不懂黑话。
    if has_before and has_after:
        lines.append("花前 + 花后（觉醒前 + 觉醒后）")
        lines.append(f"只看一张：卡面 {card.id} 花前 / 卡面 {card.id} 花后")
    elif has_after:
        lines.append(f"花后（觉醒后）　看花前：卡面 {card.id} 花前")
    elif card.has_after_training:
        lines.append(f"花前（觉醒前）　看花后：卡面 {card.id} 花后")
    else:
        lines.append("花前（觉醒前）　★1 / ★2 / 生日卡没有花后")
    if len(cards) > 1:
        lines.append(
            f"（符合条件的共 {len(cards)} 张，这是第一张；"
            f"想看别的用「卡面 <卡ID>」）"
        )
    caption = "\n".join(lines)

    segments = [MessageSegment.image(url) for url, _ in arts]

    # 必须 send()，不能 finish()：finish 抛的 FinishedException 会被下面的
    # except Exception 当成"发送失败"，结果同一组图发两遍。
    # 先试「一条消息带多张图」，万一协议端不支持就退回逐张发。
    try:
        await cardart_matcher.send(Message(segments) + "\n" + caption)
    except Exception as exc:
        logger.warning("[pjsk] 多图合并发送失败，改为逐张发: {}", exc)
        sent = False
        for seg in segments:
            try:
                await cardart_matcher.send(seg)
                sent = True
            except Exception as inner:
                logger.warning("[pjsk] 单张发送也失败: {}", inner)
        if sent:
            await cardart_matcher.send(caption)

    await cardart_matcher.finish()


# ---------------------------------------------------------------- 查卡池

gacha_matcher = on_command("查卡池", aliases={"卡池", "查池"}, priority=5, block=True)


@gacha_matcher.handle()
async def handle_gacha(args: Message = CommandArg()) -> None:
    text = args.extract_plain_text().strip()
    region = resolve_region(text) or cfg.default_region

    try:
        gachas = await card_service.current_gachas(region)
        if gachas:
            result = f"【{region_label(region)}】" + card_service.render_gacha_list(gachas)
        else:
            # 没有正在开的，就退回显示最近的
            recent = await card_service.recent_gachas(region, limit=5)
            if not recent:
                result = f"没查到{region_label(region)}的卡池数据。"
            else:
                result = "当前没有正在开的卡池。最近的卡池：\n\n"
                result += card_service.render_gacha_list(recent)
    except ValueError as exc:
        await gacha_matcher.finish(str(exc))
        return
    except Exception as exc:
        logger.exception("[pjsk] 查卡池异常: {}", exc)
        await gacha_matcher.finish("查卡池出错了，请稍后再试。")
        return

    await gacha_matcher.finish(result)


# ---------------------------------------------------------------- 查角色

char_matcher = on_command("查角色", aliases={"角色列表"}, priority=5, block=True)


@char_matcher.handle()
async def handle_chars(args: Message = CommandArg()) -> None:
    text = args.extract_plain_text().strip()
    region = resolve_region(text) or cfg.default_region

    try:
        chars = await card_service.all_characters(region)
        result = f"【{region_label(region)}】" + card_service.render_character_list(chars)
    except ValueError as exc:
        await char_matcher.finish(str(exc))
        return
    except Exception as exc:
        logger.exception("[pjsk] 查角色异常: {}", exc)
        await char_matcher.finish("查角色出错了，请稍后再试。")
        return

    await char_matcher.finish(result)