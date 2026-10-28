"""歌曲与谱面服务。

为什么需要「别名」
------------------
实测：**master data 里的歌名全是日文或罗马字，国服也一样**。例如
``ロキ``、``テオ``、``初音ミクの消失``、``Tell Your World``。
群里的人却常常用中文称呼（「千本樱」「炉心融解」），直接搜是搜不到的。

所以本模块支持三层匹配：

1. **原名**（``title``）—— 日文/罗马字，模糊包含
2. **读音**（``pronunciation``）—— master data 自带
3. **别名** —— 存在 ``data/pjsk_music_alias.json``，**可以用记事本直接改**，
   格式是 ``{"歌曲ID": ["别名1", "别名2"]}``

匹配优先级：完全相等 > 开头匹配 > 包含匹配。这样搜「ロキ」不会先蹦出
「ロキノキョク」之类。

别名文件不存在时会自动创建，并写入一批常见曲目的中文别名作为种子。

谱面数据
--------
``musicDifficulties.json`` 每条记录是::

    {"musicId": 1, "musicDifficulty": "easy", "playLevel": 5, "totalNoteCount": 220}

难度代码固定是 ``easy/normal/hard/expert/master/append`` 六种。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from nonebot import logger

from ..config import cfg
from ..models import DIFFICULTY_ALIASES, ChartInfo, Music
from ..providers import Capability, find_provider, unsupported_hint

#: 机器人根目录（plugins/pjsk/services/music.py -> 上溯 4 层）
_BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent

#: 别名文件
ALIAS_PATH = _BASE_DIR / "data" / "pjsk_music_alias.json"

#: 种子别名表版本。改内容时把这个数字 +1，插件会自动覆盖旧种子
#: （只覆盖「和上一版种子完全一致」的文件，用户自己加过的别名不会丢）
SEED_VERSION = 3

#: 首次生成时写入的种子别名（歌曲ID -> 别名列表）
#:
#: **ID 全部经过 master data 实测校准**，不是猜的。对应关系：
#:
#: ======  ===================================  =========================
#: ID      日文原名                              中文别名
#: ======  ===================================  =========================
#: 1       Tell Your World                      世界版 / TYW
#: 2       ロキ                                   Roki
#: 3       テオ                                   Teo
#: 6       ヒバナ -Reloaded-                      火花
#: 49      初音ミクの消失                          初音未来的消失
#: **74**  **独りんぼエンヴィー**                  **红虾 / 虾**（社区昵称）
#: 110     千本桜                                千本樱（**仅日服有**）
#: 131     初音ミクの激唱                          初音未来的激唱
#: 141     群青讃歌                                群青赞歌
#: 179     炉心融解                                炉心融解
#: 186     初音天地開闢神話                        初音天地开辟神话
#: 540     虚無さん                                虚无先生
#: 610     Polar Star                            北极星
#: ======  ===================================  =========================
#:
#: 注意：**别名是跨区服共用的**。国服还没上的歌（如 110 千本桜）在国服搜不到，
#: 这是正常的。
_SEED_ALIASES: dict[str, list[str]] = {
    "1": ["世界版", "TYW", "Tell Your World"],
    "2": ["Roki"],
    "3": ["Teo"],
    "6": ["火花", "ヒバナ"],
    "8": ["时光机", "タイムマシン"],
    "49": ["初音未来的消失", "消失"],
    "62": ["jsg", "Jackpot Sad Girl"],
    "74": ["红虾", "虾"],
    "104": ["紫十", "10th", "桑德利永"],
    "110": ["千本樱"],
    "131": ["初音未来的激唱", "激唱"],
    "141": ["群青赞歌"],
    "179": ["炉心融解"],
    "186": ["初音天地开辟神话"],
    "337": ["METEOR"],
    "388": ["激唱", "初音未来的激唱"],
    "540": ["虚无先生", "虚无"],
    "610": ["北极星", "Polar Star"],
}

#: 历史种子用过的歌曲 ID（用于升级时分清「插件写的」和「用户自己加的」）
_LEGACY_SEED_KEYS: dict[int, set[str]] = {
    # v1 的种子（ID 是猜的，已在 v2 修正）
    1: {"1", "2", "3", "8", "38", "51", "54", "60", "74", "104", "186", "610"},
    # v2 的种子（v3 只是补了 74 红虾）
    2: {
        "1", "2", "3", "6", "8", "49", "62", "104",
        "110", "131", "141", "179", "186", "337", "388", "540", "610",
    },
}

_ALIAS_CACHE: dict[str, list[str]] | None = None


def _migrate(data: dict, stored_version: int) -> dict:
    """把旧版本别名表升级到当前种子版本。

    规则：**插件自己写的种子会被新版覆盖，用户手加的别名一定保留**。
    这样以后修正种子 ID 时，不需要让用户手动清理。
    """
    if stored_version >= SEED_VERSION:
        return data

    # 收集所有历史种子用过的 key
    legacy_keys: set[str] = set()
    for version in range(stored_version, SEED_VERSION):
        legacy_keys |= _LEGACY_SEED_KEYS.get(version, set())

    # 不在历史种子里 -> 是用户自己加的，要保留
    user_entries = {
        key: value for key, value in data.items() if key not in legacy_keys and value
    }

    merged: dict[str, list[str]] = {k: list(v) for k, v in _SEED_ALIASES.items()}
    for key, values in user_entries.items():
        existing = merged.get(key, [])
        # 去重合并
        merged[key] = list(dict.fromkeys(existing + list(values)))

    logger.info(
        "[pjsk] 歌曲别名表已从 v%s 升级到 v%s（保留 %d 条自定义）",
        stored_version,
        SEED_VERSION,
        len(user_entries),
    )
    return merged


# ---------------------------------------------------------------- 别名


def _parse_entries(raw: dict) -> dict[str, list[str]]:
    """把文件里的原始结构整理成 ``{id: [别名]}``。

    兼容三种写法：``{"1": "别名"}`` / ``{"1": ["a","b"]}`` /
    ``{"1": {"aliases": [...]}}``。下划线开头的键（如 ``_seed_version``）跳过。
    """
    cleaned: dict[str, list[str]] = {}
    for key, value in raw.items():
        if str(key).startswith("_"):
            continue
        if isinstance(value, str):
            cleaned[str(key)] = [value]
        elif isinstance(value, list):
            cleaned[str(key)] = [str(v) for v in value if v]
        elif isinstance(value, dict):
            items = value.get("aliases") or value.get("alias") or []
            cleaned[str(key)] = [str(v) for v in items if v]
    return cleaned


def _write_aliases(data: dict[str, list[str]]) -> None:
    """写别名文件（带种子版本号）。"""
    payload = {"_seed_version": SEED_VERSION}
    payload.update(data)
    try:
        ALIAS_PATH.parent.mkdir(parents=True, exist_ok=True)
        ALIAS_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as exc:
        logger.warning("[pjsk] 写别名文件失败: {}", exc)


def load_aliases(*, reload: bool = False) -> dict[str, list[str]]:
    """读别名表（进程内缓存，``reload=True`` 强制重读）。

    文件不存在会按种子创建；文件版本旧会自动升级（保留用户自定义的别名）。
    """
    global _ALIAS_CACHE
    if _ALIAS_CACHE is not None and not reload:
        return _ALIAS_CACHE

    if not ALIAS_PATH.exists():
        _write_aliases(_SEED_ALIASES)
        logger.info("[pjsk] 已创建歌曲别名文件 {}", ALIAS_PATH)
        _ALIAS_CACHE = {k: list(v) for k, v in _SEED_ALIASES.items()}
        return _ALIAS_CACHE

    try:
        raw = json.loads(ALIAS_PATH.read_text(encoding="utf-8"))
        stored_version = int(raw.pop("_seed_version", 1) or 1)
        cleaned = _parse_entries(raw)
        migrated = _migrate(cleaned, stored_version)
        if stored_version < SEED_VERSION:
            _write_aliases(migrated)
        _ALIAS_CACHE = migrated
    except Exception as exc:
        logger.warning("[pjsk] 读取别名文件失败，改用内置种子: {}", exc)
        _ALIAS_CACHE = {k: list(v) for k, v in _SEED_ALIASES.items()}

    return _ALIAS_CACHE


def aliases_of(music_id: int) -> list[str]:
    """某首歌的别名列表。"""
    return load_aliases().get(str(music_id), [])


def add_alias(music_id: int, alias: str) -> bool:
    """给歌曲加别名并写盘。已存在则不重复加。"""
    data = {k: list(v) for k, v in load_aliases().items()}
    key = str(music_id)
    items = list(data.get(key, []))
    if alias in items:
        return False
    items.append(alias)
    data[key] = items

    global _ALIAS_CACHE
    _ALIAS_CACHE = data
    _write_aliases(data)
    return True


# ---------------------------------------------------------------- 匹配


def normalize(text: str) -> str:
    """归一化（转发到 :mod:`..textutil`，保留此名以兼容旧调用）。"""
    from ..textutil import normalize as _norm

    return _norm(text)


def _match_score(music: Music, keyword: str, aliases: dict[str, list[str]]) -> int:
    """匹配打分；0 表示不匹配，分数越高越相关。

    候选顺序即权重顺序：**原名 > 别名 > 读音**。
    """
    from ..textutil import match_score

    candidates: list[str] = [music.title]
    candidates.extend(aliases.get(str(music.id), []))
    candidates.append(music.pronunciation)
    return match_score(keyword, *candidates)


# ---------------------------------------------------------------- 取数


async def _provider(region: str):
    provider = find_provider(Capability.MUSIC, region)
    if provider is None:
        raise ValueError(unsupported_hint(Capability.MUSIC, region))
    return provider


async def all_musics(region: str) -> list[Music]:
    """该区服全部歌曲。"""
    provider = await _provider(region)
    return await provider.get_musics(region)


async def search(region: str, keyword: str, *, limit: int = 10) -> list[Music]:
    """按关键词搜索歌曲，按相关度排序。

    :param keyword: 曲名 / 别名 / 读音，支持模糊
    """
    musics = await all_musics(region)
    aliases = load_aliases()

    scored: list[tuple[int, Music]] = []
    for music in musics:
        score = _match_score(music, keyword, aliases)
        if score > 0:
            scored.append((score, music))

    scored.sort(key=lambda pair: (-pair[0], pair[1].id))
    return [m for _, m in scored[:limit]]


async def find_one(region: str, keyword: str) -> Music | None:
    """找一首歌（用于「查谱面」这类需要唯一命中的场景）。"""
    hits = await search(region, keyword, limit=1)
    return hits[0] if hits else None


async def get_charts(region: str, music_id: int) -> list[ChartInfo]:
    """某首歌的各难度谱面，按难度顺序返回。"""
    provider = find_provider(Capability.CHART, region) or await _provider(region)
    raw_items = await provider.get_music_difficulties(region)

    order = ["easy", "normal", "hard", "expert", "master", "append"]
    charts: list[ChartInfo] = []
    for item in raw_items:
        if not isinstance(item, dict) or item.get("musicId") != music_id:
            continue
        charts.append(ChartInfo.from_master(item, region))

    charts.sort(key=lambda c: order.index(c.difficulty) if c.difficulty in order else 99)
    return charts


def resolve_difficulty(text: str | None) -> str | None:
    """把用户输入的难度写法转成标准代码。"""
    if not text:
        return None
    return DIFFICULTY_ALIASES.get(text.strip().lower()) or DIFFICULTY_ALIASES.get(
        text.strip()
    )


# ---------------------------------------------------------------- 渲染辅助


def summary_line(music: Music, charts: list[ChartInfo] | None = None) -> str:
    """一行式歌曲摘要。"""
    parts = [music.title]
    if charts:
        levels = " ".join(
            f"{c.difficulty_label}{c.level}" for c in charts if c.level
        )
        if levels:
            parts.append(f"[{levels}]")
    return " ".join(parts)


def charts_text(music: Music, charts: list[ChartInfo]) -> str:
    """渲染一首歌的谱面详情。"""
    lines = [f"【{music.title}】"]
    if music.composer:
        lines.append(f"作曲：{music.composer}")
    lines.append(f"类型：{music.unit_label}  发行：{music.published_text}")
    lines.append("")
    if not charts:
        lines.append("没有查到谱面数据。")
        return "\n".join(lines)

    for chart in charts:
        if not chart.level:
            continue
        lines.append(
            f"• {chart.difficulty_label:<7} 等级 {chart.level:<3} 物量 {chart.note_count}"
        )
    return "\n".join(lines)