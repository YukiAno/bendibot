"""切换「完全静默模式」—— 风控恢复期用。

**为什么需要它**
----------------
账号被 QQ 风控之后，最稳的恢复方式是**先让账号静养**。
但有时候你又希望 bot 保持在线（比如不想反复重启、想看看能不能正常连上），
只是**一条消息都别发出去**。

``SEND_GUARD_SILENT=true`` 就是这个状态：
bot 照常在线、照常收消息、照常跑逻辑，但**所有出站消息被闸门拦住**。

**安全**
--------
只做一件事：把 ``.env`` 里 ``SEND_GUARD_SILENT=`` 这一行改掉。
改之前先备份成 ``.env.bak``；**其它内容原样保留**，
特别是含 API 密钥的那一行 —— 本脚本从头到尾不读它的内容。

用法
----
::

    venv\\Scripts\\python.exe toggle_silent.py          # 看当前状态
    venv\\Scripts\\python.exe toggle_silent.py on       # 打开静默
    venv\\Scripts\\python.exe toggle_silent.py off      # 关闭静默
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
ENV = BASE / ".env"
BACKUP = BASE / ".env.bak"

_KEY = "SEND_GUARD_SILENT"


def read_state() -> str | None:
    """返回当前值（没配过返回 None）。不打印其它任何行。"""
    if not ENV.exists():
        return None
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith(f"{_KEY}="):
            return line.split("=", 1)[1].strip()
    return None


def write_state(value: str) -> None:
    """改这一行并写回（其余内容一字不动）。

    .. warning::
       **两个必须小心的细节，都会把 .env 改坏：**

       1. **换行风格** —— 原文件如果是 CRLF，用默认方式读写会变成 LF。
          所以 ``read_text`` / ``write_text`` 都传 ``newline=""``（不转换）。
       2. **正则别吃掉 ``\\r``** —— 在 CRLF 文件里，``.*$`` 会把行尾的
          ``\\r`` 一起匹配进去，替换后那个 ``\\r`` 就丢了。
          所以用 ``[^\\r\\n]*`` 明确排除。

       ``.env`` 被改坏的后果是密钥读不出来、机器人直接起不来，
       所以这里宁可啰嗦一点。
    """
    # ⚠️ 这里必须用 open(newline="")，不能用 Path.read_text ——
    # read_text() 没有 newline 参数（Python 3.13 才加），
    # 用它会直接 TypeError。
    with ENV.open("r", encoding="utf-8", newline="") as fh:
        original = fh.read()
    nl = "\r\n" if "\r\n" in original else "\n"

    if re.search(rf"^{_KEY}\s*=", original, flags=re.MULTILINE):
        text = re.sub(
            rf"^{_KEY}\s*=[^\r\n]*",
            f"{_KEY}={value}",
            original,
            count=1,
            flags=re.MULTILINE,
        )
    else:
        text = original
        if not text.endswith(("\n", "\r")):
            text += nl
        text += f"{_KEY}={value}{nl}"

    with BACKUP.open("w", encoding="utf-8", newline="") as fh:
        fh.write(original)
    with ENV.open("w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def show() -> None:
    state = read_state()
    print("=" * 56)
    print("  发送闸门状态")
    print("=" * 56)
    if state is None:
        print("  SEND_GUARD_SILENT 未配置（默认 = false，正常发送）")
        return
    if state.lower() in {"true", "1", "yes", "on"}:
        print("  ★ 完全静默：已开启 ★")
        print()
        print("  bot 在线，但一条消息都不会发出去。")
        print("  适合账号被风控后的「静养」阶段。")
    else:
        print("  完全静默：已关闭（正常发送）")
        print()
        print("  发送闸门仍然生效：每群 10 条/分，超限拦截，")
        print("  被拦 10 次则熔断静默 10 分钟。")


def main() -> None:
    action = sys.argv[1].strip().lower() if len(sys.argv) > 1 else ""

    if action in {"on", "true", "1", "开"}:
        write_state("true")
        print("已打开完全静默模式。")
        print("（原 .env 已备份为 .env.bak）")
        print()
        print("★ 改完要重启机器人才生效。")
        show()
    elif action in {"off", "false", "0", "关"}:
        write_state("false")
        print("已关闭完全静默模式，恢复正常发送。")
        print("（发送闸门仍然生效，超频依旧会被拦）")
        print()
        print("★ 改完要重启机器人才生效。")
        show()
    else:
        show()
        print()
        print("用法：")
        print("  toggle_silent.py on    打开完全静默（不发任何消息）")
        print("  toggle_silent.py off   关闭，恢复正常发送")
        print("  toggle_silent.py       只看当前状态")


if __name__ == "__main__":
    main()