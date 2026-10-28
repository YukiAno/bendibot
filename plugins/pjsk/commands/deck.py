"""组卡指令 —— 理论最优卡组推荐。

::

    组卡 千本樱                默认难度 Master，套用当前活动加成
    组卡 千本樱 ex             指定难度（支持 ex/ma/hd 等缩写）
    组卡 千本樱 ma 30          指定难度 + 指定活动 ID
    组卡 千本樱 无活动         不要活动加成，纯得分最优
    组卡 千本樱 日服           指定区服
    组卡 千本樱 按综合力       换优化目标（默认按打歌得分）

五个区服都支持。原理：加载**本区服的 masterdata**，
配上**日服的 `music_metas`**（里面是游戏机制系数，不含区服信息，
引擎源码明确写 region "does not change engine math"）。
详见 ``services/deck.py`` 的模块 docstring。

.. warning::
   结果是**假设全卡满破满级**的理论最优，**不是**按玩家实际持有的卡算的。
"""

from __future__ import annotations

from nonebot import logger, on_command
from nonebot.adapters.onebot.v11 import Message
from nonebot.params import CommandArg

from ..config import REGION_NAMES, cfg, region_label, resolve_region
from ..models import DIFFICULTY_ALIASES
from ..services import deck as deck_service
from ..services import music as music_service

deck_matcher = on_command(
    "组卡", aliases={"推荐卡组", "查卡组", "卡组推荐"}, priority=5, block=True
)

#: 明确表示"不要活动加成"的词
_NO_EVENT_WORDS = {"无活动", "不带活动", "无加成", "不带加成", "无event", "纯打歌"}

#: 优化目标的中文写法 → 引擎的 target 值
_TARGET_WORDS: dict[str, str] = {
    "按综合力": "power",
    "综合力": "power",
    "按得分": "score",
    "按打歌得分": "score",
    "按技能": "skill",
    "技能": "skill",
    "按加成": "bonus",
    "加成": "bonus",
    "mysekai": "mysekai",
}


def _parse(text: str) -> tuple[str, str, str, int | None, bool, str]:
    """解析参数。

    返回 ``(歌名, 区服, 难度, 活动ID, 是否要活动加成, 优化目标)``。
    """
    parts = text.split()
    keyword_parts: list[str] = []
    region = cfg.default_region
    difficulty = "master"
    event_id: int | None = None
    want_event = True
    target = "score"
    difficulty_set = False
    target_set = False
    region_set = False

    for raw in parts:
        token = raw.strip()
        if not token:
            continue

        # 区服（只认一次，避免把歌名里的词当区服）
        if not region_set:
            resolved = resolve_region(token)
            if resolved:
                region = resolved
                region_set = True
                continue

        if token in _NO_EVENT_WORDS:
            want_event = False
            continue

        # 活动 ID：纯数字
        if token.isdigit():
            event_id = int(token)
            continue

        # 优化目标
        if not target_set and token in _TARGET_WORDS:
            target = _TARGET_WORDS[token]
            target_set = True
            continue

        # 难度：只在还没设过时生效
        if not difficulty_set and token.lower() in DIFFICULTY_ALIASES:
            difficulty = DIFFICULTY_ALIASES[token.lower()]
            difficulty_set = True
            continue

        keyword_parts.append(token)

    keyword = " ".join(keyword_parts).strip()
    # 清了点参数后歌名为空，但整串其实是个歌名的情形（例如「无活动」其实是歌名）
    if not keyword and text.strip() and len(parts) == 1:
        keyword = text.strip()

    return keyword, region, difficulty, event_id, want_event, target


def _usage(region: str) -> str:
    names = "、".join(REGION_NAMES.values())
    return (
        "用法：组卡 <歌名> [区服] [难度] [活动ID] [优化目标]\n"
        "\n"
        "例：\n"
        "  组卡 千本樱              默认 Master，套用当前活动加成\n"
        "  组卡 千本樱 ex           指定难度（ex / ma / hd / nm / ez / ap）\n"
        "  组卡 千本樱 ma 30        指定活动 ID\n"
        "  组卡 千本樱 无活动       不要活动加成\n"
        "  组卡 千本樱 日服         指定区服\n"
        "  组卡 千本樱 按综合力     换优化目标（默认按打歌得分）\n"
        "\n"
        f"区服可写：{names}（不写用默认：{region_label(region)}）\n"
        "\n"
        "⚠️ 结果是**假设全卡满破满级**的理论最优，不是你实际能组的卡组。"
    )


@deck_matcher.handle()
async def handle_deck(args: Message = CommandArg()) -> None:
    text = args.extract_plain_text()

    keyword, region, difficulty, event_id, want_event, target = _parse(text)

    if not keyword:
        await deck_matcher.finish(_usage(cfg.default_region))
        return

    # 当前活动（用户没指定、也没说"不要活动"时自动套用）
    auto_event = False
    if want_event and event_id is None:
        try:
            event_id = deck_service.current_event_id(region)
            auto_event = event_id is not None
        except Exception:  # noqa: BLE001
            event_id = None
    elif not want_event:
        event_id = None

    try:
        music = await music_service.find_one(region, keyword)
        result = (
            await deck_service.recommend(
                music_id=music.id,
                region=region,
                difficulty=difficulty,
                limit=3,
                event_id=event_id,
                target=target,
            )
            if music is not None
            else None
        )
    except deck_service.DeckError as exc:
        await deck_matcher.finish(str(exc))
        return
    except Exception as exc:  # noqa: BLE001
        logger.exception("[pjsk] 组卡异常: {}", exc)
        await deck_matcher.finish("组卡出错了，请稍后再试。")
        return

    if music is None or result is None:
        await deck_matcher.finish(
            f"在{region_label(region)}没找到「{keyword}」。\n"
            "提示：歌名大多是日文，可以试试中文别名，或用 /加别名 自己加。"
        )
        return

    if auto_event:
        result.note = f"已自动套用当前活动 {event_id} 的加成"

    try:
        card_names = deck_service.load_card_names(region)
    except Exception:  # noqa: BLE001
        card_names = {}

    output = deck_service.render_decks(
        result,
        music=music,
        card_names=card_names,
        region_label=region_label(region),
    )
    if auto_event:
        output += f"\n※ 已自动套用当前活动 {event_id} 的加成（写「无活动」可关掉）。"
    await deck_matcher.finish(output)