"""卡牌 / 角色 / 卡池模型。

实测到的字段结构（国服 master data）
------------------------------------
**角色** ``gameCharacters.json``（26 个）::

    {"id": 1, "firstName": "星乃", "givenName": "一歌",
     "firstNameEnglish": "HOSHINO", "givenNameEnglish": "ICHIKA",
     "unit": "light_sound", "gender": "female", ...}

注意：**没有 ``name`` 字段**，名字要拼 ``firstName + givenName``。
国服的 ``firstName``/``givenName`` 是**中文**（日服是日文）。

**卡牌** ``cards.json``（国服 1345 张）::

    {"id": 1, "characterId": 1, "cardRarityType": "rarity_1",
     "attr": "cool", "skillId": 1,
     "cardSkillName": "身边的小花", "prefix": "外表帅气且为朋友着想",
     "assetbundleName": "res001_no001", "releaseAt": 1704092400000}

``prefix`` 是卡名前缀（中文），``releaseAt`` 是**毫秒**时间戳。

**卡池** ``gachas.json``（国服 45 个）::

    {"id": 16, "gachaType": "beginner", "name": "新手應援起跑衝刺招募",
     "startAt": 1607065200000, "endAt": ...}

国服的卡池名是**繁中**（「應援」「衝刺」），这是官方的翻译，不是我们转换的。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from .models import ASSET_REGIONS, parse_dt, region_tz

#: 稀有度代码 -> 显示文本
RARITY_NAMES: dict[str, str] = {
    "rarity_1": "★1",
    "rarity_2": "★2",
    "rarity_3": "★3",
    "rarity_4": "★4",
    "rarity_birthday": "生日",
}

#: 属性代码 -> 中文名
ATTR_NAMES: dict[str, str] = {
    "cool": "Cool（蓝）",
    "cute": "Cute（粉）",
    "happy": "Happy（橙）",
    "pure": "Pure（绿）",
    "mysterious": "Mysterious（紫）",
}

#: 组合（unit）代码 -> 中文名
UNIT_NAMES: dict[str, str] = {
    "light_sound": "Leo/need",
    "idol": "MORE MORE JUMP!",
    "street": "Vivid BAD SQUAD",
    "theme_park": "Wonderlands×Showtime",
    "school_refusal": "25时，在Nightcord。",
    "piapro": "VIRTUAL SINGER",
    "none": "—",
}

#: 卡池类型 -> 中文名
#:
#: 实测取值（国服 45 个 / 日服 1046 个）：
#: cn = ``ceil``(20) / ``normal``(12) / ``beginner``(5) / ``sureturn``(4) / ``subeginner``(4)
#: jp = ``ceil``(772) / ``normal``(248) / ``gift``(18) / ``beginner``(8)
#:
#: 其余取值是从社区资料补的，**没有实测过**，留作兜底。
GACHA_TYPE_NAMES: dict[str, str] = {
    "beginner": "新手池",
    "subeginner": "新手池（续）",
    "normal": "常驻池",
    "ceil": "天井池",
    "sureturn": "确定池",
    "gift": "赠送池",
    "gacha_select": "自选池",
    "movie": "剧场版联动",
    "bug": "复刻池",
    "birthday": "生日池",
}


@dataclass
class Character:
    """角色。"""

    id: int
    first_name: str
    given_name: str
    region: str
    unit: str = "none"
    first_name_en: str = ""
    given_name_en: str = ""
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def name(self) -> str:
        """中文全名。"""
        return f"{self.first_name}{self.given_name}".strip()

    @property
    def name_en(self) -> str:
        """罗马字全名。"""
        return f"{self.given_name_en} {self.first_name_en}".strip()

    @property
    def unit_label(self) -> str:
        return UNIT_NAMES.get(self.unit, self.unit)

    @classmethod
    def from_master(cls, raw: dict, region: str) -> Character:
        return cls(
            id=int(raw.get("id", 0)),
            first_name=str(raw.get("firstName", "") or ""),
            given_name=str(raw.get("givenName", "") or ""),
            region=region,
            unit=str(raw.get("unit", "none") or "none"),
            first_name_en=str(raw.get("firstNameEnglish", "") or ""),
            given_name_en=str(raw.get("givenNameEnglish", "") or ""),
            raw=raw,
        )


@dataclass
class Card:
    """卡牌。"""

    id: int
    character_id: int
    name: str
    region: str
    rarity: str = ""
    attr: str = ""
    skill_name: str = ""
    assetbundle_name: str = ""
    released_at: datetime | None = None
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def rarity_label(self) -> str:
        return RARITY_NAMES.get(self.rarity, self.rarity or "—")

    @property
    def attr_label(self) -> str:
        return ATTR_NAMES.get(self.attr, self.attr or "—")

    @property
    def released_text(self) -> str:
        if not self.released_at:
            return "未知"
        return self.released_at.astimezone(region_tz(self.region)).strftime("%Y-%m-%d")

    @property
    def rarity_stars(self) -> int:
        """稀有度数字（★4 -> 4），用于排序。"""
        if self.rarity == "rarity_birthday":
            return 4
        tail = self.rarity.rsplit("_", 1)[-1]
        return int(tail) if tail.isdigit() else 0

    # ★3 / ★4 才有「特训后」卡面。★1 / ★2 没有特训机制；
    # 生日卡（rarity_birthday）实测也没有 card_after_training.webp（全 404），
    # 所以别把它算进来，否则每次都要白探一次。
    @property
    def has_after_training(self) -> bool:
        """这张卡有没有特训后卡面。"""
        return self.rarity in ("rarity_3", "rarity_4")

    def card_art_url(self, *, after_training: bool = False) -> str:
        """卡面图（卡绘）地址。

        实测规则（2026-10-27 验证，40 张抽样命中 38 张）::

            https://storage.sekai.best/sekai-{asset_region}-assets
                /character/member/{assetbundleName}/card_normal.webp
            https://storage.sekai.best/sekai-{asset_region}-assets
                /character/member/{assetbundleName}/card_after_training.webp

        **两个容易踩的坑**（都实测撞过）：

        1. 目录名里**不能带 ``_rip``**。``{assetbundleName}_rip`` 是
           sekai-viewer 自己私有镜像的写法，官方 CDN 上带 ``_rip`` 一律 404。
        2. 资源库前缀是 ``sekai-jp-assets`` / ``sekai-cn-assets``，
           **不是** ``sekai-assets``（后者也 404）。

        实测体积：WebP 平均 193 KB（77~424 KB），PNG 是 3.8 MB 级别，
        所以默认取 WebP，PNG 只在兜底时用。

        :param after_training: 取特训后卡面（★1/★2 没有，会 404）
        """
        if not self.assetbundle_name:
            return ""
        asset_region = ASSET_REGIONS.get(self.region, "jp")
        suffix = "after_training" if after_training else "normal"
        return (
            f"https://storage.sekai.best/sekai-{asset_region}-assets"
            f"/character/member/{self.assetbundle_name}/card_{suffix}.webp"
        )

    def card_art_png(self, *, after_training: bool = False) -> str:
        """卡面图 PNG 地址（WebP 发不出去时兜底，体积大很多）。"""
        url = self.card_art_url(after_training=after_training)
        return url[:-5] + ".png" if url.endswith(".webp") else url

    def card_cutout_url(self, *, after_training: bool = False) -> str:
        """角色立绘（去掉背景的透明图，体积约 35~40 KB）。

        路径在 ``character/member_cutout/`` 下，文件名是 ``normal.webp``
        而不是 ``card_normal.webp``，别和卡面搞混。
        """
        if not self.assetbundle_name:
            return ""
        asset_region = ASSET_REGIONS.get(self.region, "jp")
        suffix = "after_training" if after_training else "normal"
        return (
            f"https://storage.sekai.best/sekai-{asset_region}-assets"
            f"/character/member_cutout/{self.assetbundle_name}/{suffix}.webp"
        )

    @classmethod
    def from_master(cls, raw: dict, region: str) -> Card:
        return cls(
            id=int(raw.get("id", 0)),
            character_id=int(raw.get("characterId", 0) or 0),
            # prefix 才是卡名前缀；没有时退回技能名
            name=str(raw.get("prefix", "") or raw.get("cardSkillName", "") or ""),
            region=region,
            rarity=str(raw.get("cardRarityType", "") or ""),
            attr=str(raw.get("attr", "") or ""),
            skill_name=str(raw.get("cardSkillName", "") or ""),
            assetbundle_name=str(raw.get("assetbundleName", "") or ""),
            released_at=parse_dt(raw.get("releaseAt"), region),
            raw=raw,
        )


@dataclass
class Gacha:
    """卡池。"""

    id: int
    name: str
    region: str
    gacha_type: str = ""
    start_at: datetime | None = None
    end_at: datetime | None = None
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def type_label(self) -> str:
        return GACHA_TYPE_NAMES.get(self.gacha_type, self.gacha_type or "—")

    def status(self, now: datetime | None = None) -> str:
        """``upcoming`` / ``running`` / ``ended``。"""
        now = now or datetime.now(timezone.utc)
        if self.start_at and now < self.start_at:
            return "upcoming"
        if self.end_at and now >= self.end_at:
            return "ended"
        return "running"

    @property
    def is_running(self) -> bool:
        return self.status() == "running"

    def remaining_text(self, now: datetime | None = None) -> str:
        from .models import humanize_delta

        if not self.end_at:
            return "未知"
        now = now or datetime.now(timezone.utc)
        left = self.end_at - now
        if left.total_seconds() <= 0:
            return "已结束"
        return humanize_delta(left)

    def time_range_text(self) -> str:
        """``10-21 15:00 ~ 10-29 20:59`` 这种紧凑写法。"""
        tz = region_tz(self.region)
        start = self.start_at.astimezone(tz).strftime("%m-%d %H:%M") if self.start_at else "?"
        end = self.end_at.astimezone(tz).strftime("%m-%d %H:%M") if self.end_at else "?"
        return f"{start} ~ {end}"

    @classmethod
    def from_master(cls, raw: dict, region: str) -> Gacha:
        return cls(
            id=int(raw.get("id", 0)),
            name=str(raw.get("name", "") or ""),
            region=region,
            gacha_type=str(raw.get("gachaType", "") or ""),
            start_at=parse_dt(raw.get("startAt"), region),
            end_at=parse_dt(raw.get("endAt"), region),
            raw=raw,
        )