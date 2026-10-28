"""实效计算 —— 协力（多人演出）卡组**实际生效**的分数提升倍率。

公式
----
::

    实效 = 队长技能% + (其余 4 张技能%之和 ÷ 5)

也就是其余 4 张各按 **20%** 生效。

三个独立来源互相印证
--------------------
* **萌娘百科**：「协力技能的得分倍率和单人演出不同，为队长的技能得分倍率
  加上编队中其他四人的技能得分倍率的 20%」
* **Yahoo!知恵袋 最佳答案**（带完整推导）：
  编成 140/110/140/100/80 → 140＋22＋28＋20＋16＝226%，
  并给出简化式「リーダースキル＋(残り4枚のスキルの合計÷5)」
* **切游网（国服攻略）**：队长 80%、其余 80/100/100/80 →
  80+(80+100+100+80)×0.2＝152%

要点
----
* **第一位是队长**，享受 100% 生效；后四位各 20%
* **回血卡 / 判定强化卡没有分数提升值**，当作 0 输入即可
* **没有公式上的上限**（社区里说的「最大 270% 左右」是支援编成的现实水平，不是封顶）

这个功能**完全是本地计算**：不联网、不需要账号、不需要抓包，国服日服通用。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

#: 非队长位置的生效比例（1/5）
OTHER_RATIO = 0.2

#: 合理上限，用于拦截明显的误输入（例如把综合力当技能值填进来）
MAX_SKILL = 300.0

#: 一个队最多几张卡
TEAM_SIZE = 5


@dataclass
class EffectResult:
    """实效计算结果。"""

    #: 队长技能值
    leader: float
    #: 其余位置的技能值
    others: list[float]
    #: 其余位置合计
    others_sum: float
    #: 其余位置实际贡献（others_sum × 0.2）
    others_bonus: float
    #: 实效总计
    total: float

    def detail_text(self) -> str:
        """逐项推导，方便群友核对。"""
        lines = [
            f"队长：{_fmt(self.leader)}%",
            "其余 4 位（各按 20% 生效）：",
        ]
        for i, value in enumerate(self.others, start=2):
            lines.append(f"  第{i}位 {_fmt(value)}% → {_fmt(value * OTHER_RATIO)}%")
        if len(self.others) < TEAM_SIZE - 1:
            lines.append(f"  （只填了 {len(self.others)} 位，空的按 0 算）")

        lines.append("")
        lines.append(
            f"实效 = {_fmt(self.leader)} + {_fmt(self.others_sum)} ÷ 5 "
            f"= {_fmt(self.leader)} + {_fmt(self.others_bonus)} "
            f"= {_fmt(self.total)}%"
        )
        return "\n".join(lines)

    def short_text(self) -> str:
        """一行式结果。"""
        return f"实效 {_fmt(self.total)}%"


def _fmt(value: float) -> str:
    """去掉多余的小数位：整数显示整数。"""
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def parse_skills(numbers: list[float]) -> list[float]:
    """校验并整理输入的技能值。

    :raises ValueError: 数量、范围不合法（消息可直接给用户看）
    """
    if not numbers:
        raise ValueError("至少要给 1 个技能值。第一位是队长，后面 4 位是队友。")
    if len(numbers) > TEAM_SIZE:
        raise ValueError(f"最多 {TEAM_SIZE} 个值（一个队伍 5 张卡），你给了 {len(numbers)} 个。")

    for value in numbers:
        if value < 0:
            raise ValueError("技能值不能是负数。")
        if value > MAX_SKILL:
            raise ValueError(
                f"技能值 {_fmt(value)}% 超出合理范围（技能加分一般 80~150%，"
                f"上限按 {_fmt(MAX_SKILL)}% 算）。"
                "这里要填**技能加分百分比**，不是综合力、也不是活动点数哦。"
            )
    return list(numbers)


def calc(values: list[float]) -> EffectResult:
    """计算实效。

    :param values: 技能值列表，**第一个是队长**
    :raises ValueError: 输入不合法
    """
    skills = parse_skills(values)
    leader = skills[0]
    others = skills[1:]

    # 不足 4 位时，空位按 0 处理（便于只填主队核心成员的情况）
    others_sum = math.fsum(others)
    others_bonus = others_sum / (1 / OTHER_RATIO)  # 即 ÷ 5
    total = leader + others_bonus

    return EffectResult(
        leader=leader,
        others=others,
        others_sum=others_sum,
        others_bonus=others_bonus,
        total=total,
    )


def calc_text(numbers: list[float], *, detail: bool = True) -> str:
    """一次算完并渲染成文本。"""
    result = calc(numbers)
    if detail:
        return result.detail_text()
    return result.short_text()