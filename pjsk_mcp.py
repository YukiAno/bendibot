"""PJSK MCP 服务器 —— 让 AI（DeepSeek）能自己查 PJSK 数据。

这是什么
--------
``nonebot-plugin-llmchat`` 的工具**只能来自 MCP 服务器**（它的代码里工具名是
``mcp__{服务器名}__{工具名}``，没有直接注册 Python 函数的接口）。所以想让
AI 在聊天里自己查「日服榜线多少」「下期活动什么时候开」，就得提供一个
MCP 服务器进程。

本文件就是那个进程：通过 **stdio** 传输，供 llmchat 按需拉起。

怎么启用
--------
在 ``.env`` 里加一行（**改完要重启机器人**）::

    LLMCHAT__MCP_SERVERS={"pjsk":{"command":"...python.exe","args":["pjsk_mcp.py"],"friendly_name":"PJSK 数据"}}

``tools.py`` 里的 ``ensure_mcp_config()`` 会帮你写好这一行。

两个踩过的坑（都已处理，别改回去）
----------------------------------
1. **工具必须是 ``async def``，不能是同步函数里调 ``asyncio.run``**。
   FastMCP 调用工具时本身就在事件循环里，``asyncio.run`` 会直接抛
   ``RuntimeError: asyncio.run() cannot be called from a running event loop``。
2. **stdout 必须只输出 JSON-RPC，一行日志都不能有**。
   stdio 传输的协议流就是 stdout；如果这里调 ``nonebot.init()``，它会往
   stdout 打 ``[SUCCESS] NoneBot is initializing...``，客户端立刻报
   ``Invalid JSON: trailing characters``。所以本进程**不初始化 NoneBot**
   （``from nonebot import logger`` 本身不需要 init），并且把 loguru 的
   输出全部改道 stderr。

设计原则
--------
* **工具返回给人看的纯文本**，不是 JSON —— AI 直接复述即可，省 token。
* **每个工具都先判区服支持**，不支持就返回一句中文说明，而不是抛异常。
* 工具数量刻意控制在 **8 个**：太多会挤占上下文，也会让 AI 挑错工具。
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

# 让本进程能 import 到 plugins.pjsk
_BASE_DIR = Path(__file__).resolve().parent
if str(_BASE_DIR) not in sys.path:
    sys.path.insert(0, str(_BASE_DIR))

# 必须在导入 plugins.pjsk 之前设置：告诉插件「现在是 MCP 进程，别注册指令」
os.environ["PJSK_MCP_MODE"] = "1"
os.environ.setdefault("LOG_LEVEL", "WARNING")

# 日志一律走 stderr，绝不能污染 stdout 的 JSON-RPC 流
from loguru import logger  # noqa: E402

logger.remove()
logger.add(sys.stderr, level="WARNING", format="{time:HH:mm:ss} | {level} | {message}")

from mcp.server.fastmcp import FastMCP  # noqa: E402

from plugins.pjsk.config import REGION_NAMES  # noqa: E402
from plugins.pjsk.providers import all_providers  # noqa: E402
from plugins.pjsk.services import border as border_service  # noqa: E402
from plugins.pjsk.services import card as card_service  # noqa: E402
from plugins.pjsk.services import deck as deck_service  # noqa: E402
from plugins.pjsk.services import effect as effect_service  # noqa: E402
from plugins.pjsk.services import event as event_service  # noqa: E402
from plugins.pjsk.services import music as music_service  # noqa: E402

mcp = FastMCP("pjsk")

#: 给 AI 看的区服说明
_REGION_HINT = (
    " 区服参数：cn=国服（默认），jp=日服，tw=台服，kr=韩服，en=国际服；"
    "也接受中文写法如「日服」。"
)


def _region_name(region: str) -> str:
    return REGION_NAMES.get(region, region)


def _numbers(text: str) -> list[float]:
    """从「140 110, 140 / 100、80」这类字符串里抠出数字。"""
    return [float(m) for m in re.findall(r"\d+(?:\.\d+)?", text or "")]


def _text_result(value) -> str:
    """把 MCP 返回的内容统一成字符串。"""
    return str(value)


# ---------------------------------------------------------------- 工具


@mcp.tool(
    description=(
        "查询 Project SEKAI（世界计划/啤酒烧烤）当前正在进行的活动、剩余时间、"
        "进度，以及下一期活动预告（国服支持预告）。"
        "当群友问「现在什么活动」「活动还剩多久」「下期活动什么时候」时用这个。"
        + _REGION_HINT
    )
)
async def get_event(region: str = "cn") -> str:
    """查当前活动与下一期活动。"""
    try:
        return await event_service.report(region)
    except Exception as exc:
        return f"查活动失败：{exc}"


@mcp.tool(
    description=(
        "查询 Project SEKAI 活动榜线（某一名次档位的当前分数线）。"
        "**重要：国服没有公开榜线数据源**，只有日服/台服/韩服/国际服可用。"
        "当群友问「1000 名多少分」「榜线多少」「分数线」时用这个。"
        + _REGION_HINT
    )
)
async def get_border(region: str = "jp", rank: int = 1000) -> str:
    """查某个档位的榜线分数。"""
    try:
        border = await border_service.border_of(region, rank)
        if border is None:
            return f"{_region_name(region)}没有 {rank} 名这个档位。"
        return (
            f"{_region_name(region)} 第{border.event_id or '?'}期 "
            f"{rank} 名分数线：{border.score:,}"
            f"（更新于 {border.updated_text()}）"
        )
    except border_service.BorderError as exc:
        return str(exc)
    except Exception as exc:
        return f"查榜线失败：{exc}"


@mcp.tool(
    description=(
        "查询 Project SEKAI 榜线预测（活动结束时的最终分数线预测）。"
        "**目前只有日服有预测数据。**"
        "当群友问「最后会到多少分」「预测榜线」「能保住吗」时用这个。"
    )
)
async def get_forecast(region: str = "jp") -> str:
    """查榜线预测。"""
    try:
        return await border_service.forecast_report(region)
    except border_service.BorderError as exc:
        return str(exc)
    except Exception as exc:
        return f"查预测失败：{exc}"


@mcp.tool(
    description=(
        "搜索 Project SEKAI 歌曲，返回曲名、作曲、各难度等级、物量（音符数）"
        "以及曲绘（封面图）地址。"
        "**歌名大多是日文**（如 ロキ、テオ、初音ミクの消失），中文名要靠别名匹配"
        "（如 千本樱、炉心融解、初音未来的消失）。"
        "当群友问「某首歌几级」「查谱面」「物量多少」「定数多少」「曲绘长什么样」"
        "时用这个。"
    )
)
async def search_music(keyword: str, region: str = "cn") -> str:
    """搜歌并给出各难度等级。"""
    try:
        hits = await music_service.search(region, keyword, limit=3)
        if not hits:
            return f"在{_region_name(region)}没找到和「{keyword}」相关的歌。"
        blocks = []
        for music in hits:
            charts = await music_service.get_charts(region, music.id)
            block = music_service.charts_text(music, charts)
            jacket = music.jacket_url()
            if jacket:
                block += f"\n曲绘：{jacket}"
            blocks.append(block)
        return "\n\n".join(blocks)
    except Exception as exc:
        return f"搜歌失败：{exc}"


@mcp.tool(
    description=(
        "查询 Project SEKAI 卡牌、卡池或角色。"
        "keyword 可以是角色名（如 一歌、初音未来、镜音铃）、卡名前缀（如 侦探），"
        "**留空则只返回当前正在开的卡池**。"
        "当群友问「现在什么池」「某角色有哪些卡」「查卡」时用这个。"
        + _REGION_HINT
    )
)
async def get_card(keyword: str = "", region: str = "cn") -> str:
    """按角色名/卡名查卡；关键词留空则返回当前卡池。"""
    try:
        name = _region_name(region)
        if not keyword.strip():
            gachas = await card_service.current_gachas(region)
            if not gachas:
                return f"{name}当前没有正在开的卡池。"
            return f"【{name}】" + card_service.render_gacha_list(gachas, limit=5)

        chars = {c.id: c for c in await card_service.all_characters(region)}
        char = await card_service.find_character(region, keyword)
        if char is not None:
            cards = await card_service.cards_of_character(region, char)
            title = f"【{char.name}】共 {len(cards)} 张卡（{char.unit_label}）"
            return card_service.render_card_list(cards, chars, title=title, limit=8)

        cards = await card_service.search_cards(region, keyword, limit=5)
        if not cards:
            return f"在{name}没找到和「{keyword}」相关的角色或卡。"
        return card_service.render_card_list(
            cards, chars, title=f"搜「{keyword}」：", limit=5
        )
    except Exception as exc:
        return f"查卡失败：{exc}"


@mcp.tool(
    description=(
        "计算 Project SEKAI 卡组「实效」值（也叫综合力加成、倍率）。"
        "公式：实效 = 队长技能% + 其余 4 张之和 ÷ 5。"
        "skills 传入 1~5 个数字的字符串，**队长放第一个**"
        "（只传 1 个就只算队长加成）；非队长位置每张按 20% 生效，"
        "回复类/判定类卡可以填 0。"
        "当群友问「这组实效多少」「帮算实效」「倍率多少」时用这个。"
    )
)
async def calc_effect(skills: str) -> str:
    """按「140 110 140 100 80」这样的数字串算实效。"""
    try:
        numbers = _numbers(skills)
        if not numbers:
            return (
                "没读到数字。请按「队长 队友1 队友2 队友3 队友4」的顺序给，"
                "例如：140 110 140 100 80"
            )
        return effect_service.calc_text(numbers)
    except ValueError as exc:
        return str(exc)
    except Exception as exc:
        return f"算实效失败：{exc}"


@mcp.tool(
    description=(
        "推荐 Project SEKAI 的**理论最优卡组**（组卡）。"
        "keyword 传歌名（支持中文别名，如「红虾」「千本樱」）；"
        "region 传区服（cn 国服默认 / jp / tw / kr / en，**五个区服都支持**）；"
        "difficulty 传难度（easy/normal/hard/expert/master/append，默认 master）；"
        "event_id 传活动 ID（0 = 自动用当前活动；-1 = 不要活动加成）；"
        "target 传优化目标（score 打歌得分默认 / power 综合力 / skill 技能 / bonus 活动加成）。"
        "★重要★ 这是**假设全卡满破满级**的理论最优，不是按玩家实际持有的卡算的。"
        "当群友问「这期活动用什么卡组」「这首歌怎么配队」「帮我组卡」时用这个。"
        "如果对方想要基于自己账号的组卡，要说明需要他先用 Haruki 工具箱提供数据。"
    )
)
async def recommend_deck(
    keyword: str,
    region: str = "cn",
    difficulty: str = "master",
    event_id: int = 0,
    target: str = "score",
) -> str:
    """按歌名推荐理论最优卡组。"""
    try:
        if region not in deck_service.MASTERDATA_BASES:
            return (
                f"组卡不支持「{region}」。可用区服："
                + "、".join(deck_service.MASTERDATA_BASES)
            )

        music = await music_service.find_one(region, keyword)
        if music is None:
            return (
                f"在{_region_name(region)}没找到「{keyword}」。"
                "可以试试中文别名，或用 /加别名 添加。"
            )

        chosen: int | None
        if event_id == -1:
            chosen = None
        elif event_id > 0:
            chosen = event_id
        else:
            chosen = deck_service.current_event_id(region)

        result = await deck_service.recommend(
            music_id=music.id,
            region=region,
            difficulty=difficulty,
            limit=3,
            event_id=chosen,
            target=target,
        )
        names = deck_service.load_card_names(region)
        lines = [deck_service.render_brief(result), "", "卡组明细："]
        for deck in result.decks:
            cards = "、".join(
                names.get(c.card_id, f"#{c.card_id}") for c in deck.cards
            )
            extra = f"，活动加成{deck.event_bonus_rate:.0f}%" if deck.event_bonus_rate else ""
            lines.append(
                f"第{deck.rank}名 得分{deck.live_score:,} 综合力{deck.total_power:,}"
                f"{extra}：{cards}"
            )
        if chosen:
            lines.append(f"（按活动 {chosen} 的加成算）")
        lines.append("")
        lines.append(
            "※ 全卡满破满级的理论最优，非玩家实际持有。"
            f"区服：{_region_name(region)}。"
        )
        return "\n".join(lines)
    except deck_service.DeckError as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001
        return f"组卡失败：{exc}"


@mcp.tool(
    description=(
        "列出机器人当前支持的 PJSK 数据源与能力，说明哪些区服能查榜线。"
        "当群友问「国服能不能查榜线」「都支持什么功能」「数据哪来的」时用这个。"
    )
)
async def list_sources() -> str:
    """列出数据源与支持情况。"""
    lines = ["可用数据源："]
    for provider in all_providers():
        lines.append(f"• {provider.name}：{provider.desc}")
        lines.append(f"  区服：{provider.region_text()}")
        lines.append(f"  能力：{provider.capability_text()}")
    lines.append("")
    lines.append("重要：**国服没有公开榜线数据源**，榜线只能查日服/台服/韩服/国际服；")
    lines.append("榜线预测目前只有日服。其余功能（活动/歌曲/谱面/卡牌/卡池/角色）国服都有。")
    lines.append("")
    lines.append("组卡（推荐最优卡组）**五个区服都支持**（国服也可以）：")
    lines.append("  做法是加载本区服的 masterdata，配上日服的 music_metas。")
    lines.append("  music_metas 里是游戏机制系数（技能槽权重、音符数），本就不分区服；")
    lines.append("  引擎源码明确写 region \"does not change engine math\"。")
    lines.append("  给出的是「假设全卡满破满级」的理论最优，不是按玩家实际持有的卡算的。")
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()