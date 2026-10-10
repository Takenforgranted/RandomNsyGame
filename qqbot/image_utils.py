# -*- coding: utf-8 -*-
"""
图片处理：缩放白底填充 + 转成可发送的 CQ 图片码

两种发送方式（配置 image_send_mode 决定）：
  - "base64"：把图片编码进消息里。不依赖 QQ 后端与图片文件在同一台机器，
              最稳、跨机器可用（默认）。代价是消息体较大。
  - "file"  ：只发送本地文件路径（file://）。速度快、无体积开销，
              但要求 OneBot 实现（NapCat / LLOneBot 等）与本项目在同一台机器上。
"""
import base64
import os
import threading
import urllib.parse
from io import BytesIO

from PIL import Image

# 已处理图片缓存：{(abs_path, size, quality): bytes}，避免同一张图重复缩放
_CACHE = {}
_CACHE_LOCK = threading.Lock()
_CACHE_MAX = 300


def resize_and_pad(img_path, size, bg="white"):
    """
    等比缩放到 size×size 内并白底填充为正方形，返回 PIL.Image。
    与桌面版 / 网页版表现一致；图片损坏时返回浅灰占位图，不抛异常。
    """
    try:
        with open(img_path, 'rb') as f:
            img = Image.open(f).convert("RGB")
        img.thumbnail((size, size), Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", (size, size), bg)
        offset = ((size - img.width) // 2, (size - img.height) // 2)
        canvas.paste(img, offset)
        return canvas
    except Exception:
        return Image.new("RGB", (size, size), "#f0f0f0")


def encode_image_bytes(img_path, size=400, quality=88, fmt="JPEG"):
    """缩放 → 编码为字节，带缓存。失败返回 None"""
    key = (os.path.abspath(img_path), size, quality, fmt)
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
    if cached is not None:
        return cached

    try:
        img = resize_and_pad(img_path, size)
        buf = BytesIO()
        img.save(buf, format=fmt, quality=quality)
        data = buf.getvalue()
    except Exception:
        return None

    with _CACHE_LOCK:
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.clear()
        _CACHE[key] = data
    return data


def to_cq_image(img_path, size=400, mode="base64", quality=88):
    """
    生成 CQ 图片码字符串。失败返回 None。
      mode="base64" -> [CQ:image,file=base64://...]
      mode="file"   -> [CQ:image,file=file:///E:/a/b.jpg]
    """
    if mode == "file":
        if not os.path.isfile(img_path):
            return None
        abs_path = os.path.abspath(img_path).replace("\\", "/")
        if not abs_path.startswith("/"):
            abs_path = "/" + abs_path
        # file:/// + URL 编码（中文路径、空格必须编码）
        return "[CQ:image,file=file://{}]".format(
            urllib.parse.quote(abs_path, safe="/:")
        )

    data = encode_image_bytes(img_path, size=size, quality=quality)
    if not data:
        return None
    return "[CQ:image,file=base64://{}]".format(
        base64.b64encode(data).decode("ascii")
    )


def to_image_segment(img_path, size=400, mode="base64", quality=88):
    """生成 OneBot v11 数组格式的消息段；失败返回 None"""
    if mode == "file":
        cq = to_cq_image(img_path, mode="file")
        if not cq:
            return None
        abs_path = os.path.abspath(img_path).replace("\\", "/")
        return {"type": "image", "data": {"file": "file:///{}".format(abs_path)}}

    data = encode_image_bytes(img_path, size=size, quality=quality)
    if not data:
        return None
    return {
        "type": "image",
        "data": {"file": "base64://" + base64.b64encode(data).decode("ascii")},
    }
