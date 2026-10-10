# -*- coding: utf-8 -*-
"""配置加载：默认值 + 可选覆盖（config.json 或环境变量）"""
import json
import os

# 默认配置（不改 config.json 也能直接跑）
DEFAULTS = {
    # ---- 连接 ----
    # NapCat / LLOneBot 的「正向 WebSocket」地址；Lagrange 默认 8080
    "onebot_ws_url": "ws://127.0.0.1:3001",
    "access_token": "",
    # websockets 15+ 会自动读取 HTTP_PROXY/HTTPS_PROXY 环境变量，
    # 开着代理时连本机 NapCat 会被塞进代理并报 502，所以默认关闭。
    "use_system_proxy": False,

    # ---- 素材 ----
    # 相对本文件上一级（项目根）的 assets 目录
    "assets_dir": "../assets",

    # ---- 发送 ----
    # "base64"：图片编码进消息，跨机器最稳（默认）
    # "file"  ：只发本地路径，快且省流量，但要求 OneBot 与本项目同机
    "image_send_mode": "base64",
    "image_size": 400,
    # 连发多张图片时的间隔（秒），避免触发风控
    "send_interval": 0.4,

    # ---- 交互 ----
    # 群里是否必须 @ 机器人才能触发指令（对局中的作答不受此限制）
    "require_at_in_group": True,
    # 对局进行中，是否允许直接回复 A/B/C/D 作答（无需 @）
    "allow_plain_answer": True,
    # 是否响应私聊
    "enable_private_chat": True,
    # 是否响应群聊消息
    "enable_group_chat": True,
    # 除 @ 之外还可用的指令前缀
    "command_prefixes": ["#", "/", "！", "!", "。"],

    # ---- 会话 ----
    # 无活动多久（秒）自动回收对局
    "session_ttl": 3600,
    # 同时进行的对局上限，防止刷屏
    "max_sessions": 100,
    # 同时处理的消息条数上限（超出则排队），防止被刷屏时任务无限膨胀
    "max_concurrent_handlers": 64,

    # ---- 调试 ----
    "log_level": "INFO",
}

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


def load_config(path=None):
    """读取 config.json 并覆盖默认值；文件不存在就用默认值"""
    cfg = dict(DEFAULTS)
    path = path or CONFIG_PATH
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                user_cfg = json.load(f)
            if isinstance(user_cfg, dict):
                cfg.update(user_cfg)
        except Exception as e:
            print(f"[配置] config.json 解析失败，改用默认配置：{e}")

    # 环境变量兜底（方便容器部署）
    env_map = {
        "ONEBOT_WS_URL": "onebot_ws_url",
        "ONEBOT_ACCESS_TOKEN": "access_token",
        "QQBOT_IMAGE_MODE": "image_send_mode",
    }
    for env_key, cfg_key in env_map.items():
        val = os.environ.get(env_key)
        if val:
            cfg[cfg_key] = val
    return cfg


def resolve_assets_dir(cfg):
    """把 assets_dir 解析成绝对路径（相对本文件所在目录）"""
    raw = cfg.get("assets_dir") or DEFAULTS["assets_dir"]
    if os.path.isabs(raw):
        return os.path.normpath(raw)
    base = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(base, raw))
