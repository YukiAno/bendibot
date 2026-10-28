# -*- coding: utf-8 -*-
"""
bendibot 状态检查工具
双击同目录的「检查状态.bat」运行，或直接运行本文件。

本脚本不会读取或显示任何密钥内容，只判断是否已填写。
"""

import re
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
ENV_FILE = BASE / ".env"

OK = "[OK]"
BAD = "[!!]"
WARN = "[?]"


def run(cmd):
    """执行命令并按 GBK 解码输出（Windows 控制台程序多为 GBK）"""
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="gbk",
            errors="replace",
            timeout=30,
        )
        return r.stdout or ""
    except Exception as e:
        return ""


def netstat_lines():
    return run(["netstat", "-ano"]).splitlines()


def procs_of(name):
    out = run(["tasklist", "/FI", "IMAGENAME eq " + name, "/NH"])
    return [ln for ln in out.splitlines() if name.lower() in ln.lower()]


def main():
    print("=" * 56)
    print("            bendibot 状态检查")
    print("=" * 56)

    lines = netstat_lines()
    listening = [l for l in lines if ":8080" in l and "LISTENING" in l]
    established = [l for l in lines if ":8080" in l and "ESTABLISHED" in l]

    # ---------- 1. 机器人进程 ----------
    print()
    print("[1] NoneBot2 机器人（端口 8080）")
    if listening:
        print("    状态: 正在运行  " + OK)
    else:
        print("    状态: 未运行  " + BAD)
        print("    → 请双击「启动bot.bat」启动机器人")

    # ---------- 2. NapCat 连接 ----------
    print()
    print("[2] NapCat 连接状态")
    if established:
        print("    状态: 已连接  " + OK + "（{} 条连接）".format(len(established)))
    elif listening:
        print("    状态: 未连接  " + BAD)
        print("    → 机器人已在运行，但 NapCat 没连上来")
        print("    → 检查 NapCat 窗口是否还开着，没开就去双击 napcat.bat")
    else:
        print("    状态: 无法判断（机器人未运行）")

    # ---------- 3. NapCat 进程 ----------
    print()
    print("[3] NapCat 进程")
    boot = procs_of("NapCatWinBootMain.exe")
    qq = procs_of("QQ.exe")

    if boot or qq:
        print("    NapCatWinBootMain.exe : {} 个".format(len(boot)))
        print("    QQ.exe                : {} 个".format(len(qq)))
        print("    状态: NapCat 正在运行  " + OK)
        if len(qq) > 1:
            print("    提示: 这 {} 个 QQ 进程里包含你日常使用的 QQ（可通过路径区分）".format(len(qq)))
    elif established:
        print("    （进程列表读取受限，但连接已建立 → NapCat 正在运行）  " + OK)
    else:
        print("    无法判断（进程列表读取受限）  " + WARN)
        print("    → 打开任务管理器，看有没有 NapCatWinBootMain.exe")
        print("    → 若没有，双击 napcat\\OneKey\\NapCat.52230.Shell\\napcat.bat")

    # ---------- 4. 配置文件 ----------
    print()
    print("[4] 配置文件检查")

    # 密钥是否已填（不显示内容）
    if ENV_FILE.exists():
        raw = ENV_FILE.read_text(encoding="utf-8", errors="replace")
        if "sk-替换成你的密钥" in raw:
            print("    DeepSeek 密钥 : 未填写  " + BAD)
            print("    → 用记事本打开 .env，替换 sk-替换成你的密钥")
        elif re.search(r"sk-[A-Za-z0-9_\-]{20,}", raw):
            print("    DeepSeek 密钥 : 已填写  " + OK + "（内容未显示）")
        else:
            print("    DeepSeek 密钥 : 状态不明  " + WARN)

        m = re.search(r'"model_name"\s*:\s*"([^"]+)"', raw)
        if m:
            model = m.group(1)
            mark = OK if model in ("deepseek-flash", "deepseek-v4-pro") else WARN
            print("    模型          : {}  {}".format(model, mark))

        m = re.search(r"(?m)^\s*LLMCHAT__DEFAULT_PRESET\s*=\s*(\S+)", raw)
        if m:
            preset = m.group(1)
            if preset.lower() in ("off", ""):
                print("    默认预设      : off  " + BAD + "（插件关闭，群里发「API预设 deepseek」开启）")
            else:
                print("    默认预设      : {}  {}".format(preset, OK))

        if re.search(r"(?m)^\s*SUPERUSERS\s*=", raw):
            print("    超级用户      : 已设置  " + OK)
        else:
            print("    超级用户      : 未设置  " + WARN + "（管理指令可能不可用）")
    else:
        print("    .env 文件不存在  " + BAD)

    # 反向 WS 配置
    cfg = (
        BASE
        / "napcat" / "OneKey" / "NapCat.52230.Shell" / "versions"
        / "9.9.33-52230" / "resources" / "app" / "napcat" / "config"
    )
    found = list(cfg.glob("onebot11_*.json")) if cfg.exists() else []
    if found:
        txt = found[0].read_text(encoding="utf-8", errors="replace")
        if "ws://127.0.0.1:8080/onebot/v11/ws" in txt:
            print("    反向WS配置    : 已配置  " + OK)
        else:
            print("    反向WS配置    : 未配置  " + BAD)
    else:
        print("    反向WS配置    : 找不到配置文件  " + WARN)

    # ---------- 结论 ----------
    print()
    print("=" * 56)
    if listening and established:
        print("  结论：一切正常，机器人已在线。")
        print("  直接把机器人 QQ 拉进群，@ 它就能聊天。")
    elif listening:
        print("  结论：机器人已启动，但 NapCat 未连接。")
        print("  请启动 NapCat（或重启它）。")
    else:
        print("  结论：机器人没在运行。")
        print("  双击「启动bot.bat」即可。")
    print("=" * 56)
    print()
    print("常见问题：")
    print("  · 回复「Insufficient Balance」→ DeepSeek 账户余额不足，去充值")
    print("  · 回复「服务暂时不可用」    → 多半也是余额或密钥问题")
    print("  · 群里 @ 它没反应          → 看「启动bot.bat」窗口的报错")
    print()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("检查过程出错:", e)
    input("按回车键关闭...")