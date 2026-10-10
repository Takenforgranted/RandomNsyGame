# -*- coding: utf-8 -*-
"""
模拟 OneBot v11 服务端 —— 不装 QQ 也能调试机器人

它假装自己是 NapCat，监听 WebSocket 等 bot.py 连上来，
然后在终端里充当「群友」：你输入一句话，机器人怎么回复就实时打印出来。

用法：
  # 1) 终端 A：启动假服务器（默认 tcp://127.0.0.1:3001）
  python mock_onebot_server.py

  # 2) 终端 B：启动机器人（config.json 的 onebot_ws_url 保持默认即可）
  python bot.py

  # 3) 回到终端 A 输入消息（模拟群聊，默认群号 88888，你自己 QQ 号 20001）
  > 猜声优
  > 1 2 xd
  > A

  非交互自测（一条条跑完就退出）：
  python mock_onebot_server.py --script "猜声优|1 2|A|B"
"""
import argparse
import asyncio
import base64
import json
import os
import uuid

import websockets

SELF_ID = 10001
GROUP_ID = 88888
USER_ID = 20001
DUMP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mock_dump")

CLIENT = {"ws": None, "seq": 0}


def describe_segments(message):
    """把消息段渲染成人类可读文本，图片落盘到 mock_dump/ 便于肉眼检查"""
    if isinstance(message, str):
        return message, 0
    texts, images = [], 0
    for seg in message or []:
        if not isinstance(seg, dict):
            continue
        stype = seg.get("type")
        data = seg.get("data") or {}
        if stype == "text":
            texts.append(str(data.get("text", "")))
        elif stype == "image":
            images += 1
            file_val = str(data.get("file", ""))
            os.makedirs(DUMP_DIR, exist_ok=True)
            name = f"img_{uuid.uuid4().hex[:8]}"
            try:
                if file_val.startswith("base64://"):
                    raw = base64.b64decode(file_val[len("base64://"):])
                    path = os.path.join(DUMP_DIR, name + ".jpg")
                    with open(path, "wb") as f:
                        f.write(raw)
                    texts.append(f"[图片 {len(raw) // 1024}KB -> mock_dump/{name}.jpg]")
                else:
                    texts.append(f"[图片 {file_val[:60]}]")
            except Exception as e:
                texts.append(f"[图片解析失败 {e}]")
    return "\n".join(texts), images


async def handle_action(action, params, echo):
    ws = CLIENT["ws"]
    if action in ("send_group_msg", "send_private_msg"):
        CLIENT["seq"] += 1
        who = "群" if action == "send_group_msg" else "私聊"
        text, images = describe_segments(params.get("message"))
        print(f"\n🤖 机器人 -> {who}：\n{text}")
        if images:
            print(f"   （含 {images} 张图片）")
        print("> ", end="", flush=True)
    elif action == "get_login_info":
        await ws.send(json.dumps({"status": "ok", "retcode": 0, "echo": echo,
                                  "data": {"user_id": SELF_ID, "nickname": "猜声优机器人"}}))
        return
    else:
        print(f"\n[假服务器] 收到未处理动作：{action} {params}\n> ", end="", flush=True)

    await ws.send(json.dumps({"status": "ok", "retcode": 0, "echo": echo,
                              "data": {"message_id": CLIENT["seq"]}}))


async def push_message(text, at=True, message_type="group"):
    """假装有人发了一条消息"""
    message = []
    if at and message_type == "group":
        message.append({"type": "at", "data": {"qq": str(SELF_ID)}})
        message.append({"type": "text", "data": {"text": " " + text}})
    else:
        message.append({"type": "text", "data": {"text": text}})

    event = {
        "time": int(asyncio.get_event_loop().time()),
        "self_id": SELF_ID,
        "post_type": "message",
        "message_type": message_type,
        "sub_type": "normal",
        "message_id": CLIENT["seq"] + 1000,
        "user_id": USER_ID,
        "raw_message": text,
        "font": 0,
        "sender": {"user_id": USER_ID, "nickname": "测试群友"},
        "message": message,
    }
    if message_type == "group":
        event["group_id"] = GROUP_ID

    print(f"\n👤 你 -> 机器人：{text}")
    await CLIENT["ws"].send(json.dumps(event))
    await asyncio.sleep(1.2)


async def handler(ws):
    CLIENT["ws"] = ws
    peer = getattr(ws, "remote_address", "?")
    print(f"[假服务器] 机器人已连接：{peer}")
    await ws.send(json.dumps({
        "time": 0, "self_id": SELF_ID, "post_type": "meta_event",
        "meta_event_type": "lifecycle", "sub_type": "connect",
    }))
    try:
        async for raw in ws:
            try:
                data = json.loads(raw)
            except Exception:
                continue
            if "action" in data:
                asyncio.create_task(handle_action(
                    data["action"], data.get("params") or {}, data.get("echo")))
    except websockets.ConnectionClosed:
        pass
    finally:
        print("[假服务器] 机器人断开连接")


async def input_loop():
    """交互模式：从终端读入模拟消息"""
    print("""
=================== 模拟群聊 ===================
直接输入内容回车 = 你在群里 @机器人 说话
输入 /n 内容     = 不 @ 机器人直接说话
输入 /p 内容     = 私聊机器人
quit 退出
===============================================
""")
    loop = asyncio.get_running_loop()
    # 等机器人连上来
    for _ in range(60):
        if CLIENT["ws"] is not None:
            break
        await asyncio.sleep(0.5)
    if CLIENT["ws"] is None:
        print("⚠️ 机器人还没连上来，先在另一个终端运行 python bot.py")
        return

    while True:
        line = (await loop.run_in_executor(None, input, ""))
        line = line.strip()
        if not line:
            continue
        if line.lower() in ("quit", "exit", "q"):
            break
        if line.startswith("/n "):
            await push_message(line[3:], at=False)
        elif line.startswith("/p "):
            await push_message(line[3:], message_type="private")
        else:
            await push_message(line)
        await asyncio.sleep(0.3)


async def script_loop(script, wait):
    for _ in range(60):
        if CLIENT["ws"] is not None:
            break
        await asyncio.sleep(0.5)
    if CLIENT["ws"] is None:
        print("⚠️ 机器人没有在 60 秒内连上来")
        return
    await asyncio.sleep(wait)
    for item in script.split("|"):
        item = item.strip()
        if not item:
            continue
        if item == "<n>":
            await push_message(" ", at=False)
        else:
            await push_message(item)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=3001)
    ap.add_argument("--script", default="", help="非交互：用 | 分隔的模拟消息")
    ap.add_argument("--wait", type=float, default=1.0, help="脚本开始前等待秒数")
    args = ap.parse_args()

    async with websockets.serve(handler, args.host, args.port,
                               max_size=32 * 1024 * 1024):
        print(f"[假服务器] 正在监听 ws://{args.host}:{args.port}")
        print(f"[假服务器] 请让 config.json 的 onebot_ws_url 指向这里")
        if args.script:
            await script_loop(args.script, args.wait)
            await asyncio.sleep(2.0)
        else:
            await input_loop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n已退出")
