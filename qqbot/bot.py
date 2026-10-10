# -*- coding: utf-8 -*-
"""
趣味猜女声优器 —— QQ 聊天机器人

基于 OneBot v11 正向 WebSocket 协议，兼容 NapCat / LLOneBot / Lagrange 等实现。
玩法与桌面版 (main.py) / 网页版 (web_app.py) 完全一致，只是把交互搬到了 QQ 里。

交互流程：
    @机器人 猜声优          -> 开一局，先展示企划列表
    回复 1 3（或 1 2 xd）   -> 选择企划（可多选，xd 开启挑战模式）
    机器人出题               -> 看名选图（发 4 张图 A/B/C/D）或 看图猜人（发 1 张图 + 4 个名字）
    回复 A/B/C/D（或 1234）  -> 作答，答对答错有不同回应
    12 题结束                -> 结算分数与用时

运行： python bot.py

并发模型（多群 / 多群友同时 @ 的场景，详见 README 第六节）：
    每条消息都被派发成一个独立协程并发处理，因此显式做了这些约束 ——
      · 玩家级串行锁：一个玩家的一轮交互（判题→计分→出下一题）不被另一条消息插队
      · 一次性作答令牌：收到消息时在任何 await 之前消费，连点不会把答案算到下一题
      · 群级发送锁（可重入）：图片连发作为整体发出，同群内不与他人消息交错
      · alive 标记：玩家「结束」后，正在连发的图片协程立刻刹车
      · 线程池：图片编码、扫目录、生成 12 题等同步耗时操作不占用事件循环
      · 后台任务强引用 + 并发上限：任务不会被 GC，也不会被刷屏撑爆
    不同群的锁是不同对象，所以多群之间完全并行、互不阻塞。
"""
import asyncio
import inspect
import json
import logging
import random
import re
import secrets
import time
import uuid

import websockets

from config import load_config, resolve_assets_dir
from game import (PER_SCORE, TOTAL_QUESTIONS, QuizSession, load_projects,
                  load_seiyu)
from image_utils import to_image_segment

# ===================== 文案池 =====================
CORRECT_REPLIES = [
    "✅ 答对啦！就是「{name}」～",
    "🎯 完全正确！「{name}」没跑了！",
    "✨ 漂亮！「{name}」被你一眼认出来了！",
    "👏 厉害，答案就是「{name}」！",
    "💯 正确！这就是「{name}」～",
    "🌟 稳！正确答案「{name}」！",
    "🔥 秒答正确！「{name}」本人吧？",
]

WRONG_REPLIES = [
    "❌ 答错咯～正确答案是 {opt}、「{name}」",
    "😵 不是这个啦～正确答案是 {opt}、「{name}」",
    "🙈 差一点点！正确选项是 {opt}、「{name}」",
    "💔 认错了哦～正确答案是 {opt}、「{name}」",
    "🤔 眼力还要再练练！正确答案是 {opt}、「{name}」",
    "😅 可惜！正确答案是 {opt}、「{name}」",
]

COMMENTS = [
    (1.0, "🏆 满分！你就是声优本人吧？"),
    (0.8, "🎉 太强了，这是把声优图鉴背下来的吧！"),
    (0.6, "👍 不错不错，认人功力在线～"),
    (0.4, "🙂 还行，再多听几张图就熟了。"),
    (0.01, "😇 有进步空间，别灰心，再来一局？"),
    (0.0, "💀 全错也是一种天赋……要不要再挑战一次？"),
]

HELP_TEXT = (
    "🎤 趣味猜女声优器 · 使用说明\n"
    "——————————————\n"
    "· @我 + 猜声优   开一局新的游戏\n"
    "· @我 + 企划     查看全部企划\n"
    "· @我 + 帮助     显示这份说明\n"
    "· @我 + 结束     结束当前对局\n"
    "· @我 + 状态     看看当前进度\n"
    "——————————————\n"
    "开局流程：\n"
    "1️⃣ 先选企划，回复序号即可，多个用空格隔开（如 1 3）\n"
    "   想玩 XD 模式（正确率<50% 得分归零）就在后面加 xd（如 1 2 xd）\n"
    "2️⃣ 开始答题，直接回复 A/B/C/D（或 1/2/3/4）作答\n"
    "   注意：对局中作答不需要 @ 我哦～\n"
    "——————————————\n"
    f"共 {TOTAL_QUESTIONS} 题，每题 {PER_SCORE} 分，满分 {TOTAL_QUESTIONS * PER_SCORE} 分。"
)

# ===================== 指令表 =====================
CMD_START = {"猜声优", "猜谜", "猜图", "开始", "开始游戏", "开一局", "来一局", "玩", "start", "newgame"}
CMD_HELP = {"帮助", "help", "指令", "菜单", "玩法", "说明", "怎么玩", "咋玩"}
CMD_LIST = {"企划", "列表", "项目", "projects", "list", "有哪些企划"}
CMD_END = {"结束", "退出", "不玩了", "结束游戏", "取消", "end", "quit", "stop"}
CMD_AGAIN = {"再来", "再来一局", "重来", "重开", "继续", "next"}
CMD_STATUS = {"状态", "分数", "进度", "score", "status"}

_ANSWER_MAP = {"A": 0, "B": 1, "C": 2, "D": 3, "1": 0, "2": 1, "3": 2, "4": 3}
_ALL_SELECTION_WORDS = {"全部", "所有", "all"}

STATE_SELECTING = "selecting"
STATE_PLAYING = "playing"
STATE_DONE = "done"

log = logging.getLogger("qqbot")

# websockets 15+ 新增了 proxy 参数：不显式指定时，它会自动读取 HTTP_PROXY / HTTPS_PROXY
# 环境变量。国内开发机常常开着代理，这会让连本机 NapCat 的 ws://127.0.0.1:3001
# 也被塞进代理，报 "proxy rejected connection: HTTP 502" 之类看不懂的错。
_CONNECT_SUPPORTS_PROXY = "proxy" in inspect.signature(websockets.connect).parameters


def build_connect_kwargs(cfg):
    kwargs = {"max_size": 32 * 1024 * 1024, "ping_interval": 30, "ping_timeout": 60}
    if _CONNECT_SUPPORTS_PROXY:
        # 默认显式关闭代理；确实需要走代理时在 config.json 里把 use_system_proxy 设为 true
        kwargs["proxy"] = True if cfg.get("use_system_proxy") else None
    return kwargs


# ===================== 消息解析工具 =====================
def text_seg(text):
    return {"type": "text", "data": {"text": text}}


def parse_incoming(event, self_id):
    """
    解析收到的消息，返回 (纯文本, 是否@了机器人)
    兼容 OneBot v11 的数组格式与 CQ 字符串格式
    """
    message = event.get("message")
    at_bot = False

    if isinstance(message, list):
        parts = []
        for seg in message:
            if not isinstance(seg, dict):
                continue
            seg_type = seg.get("type")
            data = seg.get("data") or {}
            if seg_type == "text":
                parts.append(str(data.get("text", "")))
            elif seg_type == "at":
                if str(data.get("qq")) == str(self_id):
                    at_bot = True
        raw = "".join(parts)
    else:
        raw = str(message or "")
        cq_at = re.findall(r"\[CQ:at,qq=([^\]]+)\]", raw)
        if str(self_id) in [q.strip() for q in cq_at]:
            at_bot = True
        # 去掉所有 CQ 码，只留纯文本
        raw = re.sub(r"\[CQ:[^\]]*\]", "", raw)
        # 还原 CQ 转义字符
        raw = (raw.replace("&#91;", "[").replace("&#93;", "]")
                  .replace("&#44;", ",").replace("&amp;", "&"))

    return raw.strip(), at_bot


def strip_prefix(text, prefixes):
    """去掉指令前缀（# / ！ 等），返回是否带前缀 + 剩余文本"""
    t = text.strip()
    if not t:
        return False, t
    for p in prefixes:
        if p and t.startswith(p):
            return True, t[len(p):].strip()
    return False, t


def match_command(text):
    """返回 (指令名, 原文) —— 命中返回指令常量，否则 (None, text)"""
    t = text.strip().lower().replace(" ", "")
    if not t:
        return None, text
    for name, aliases in (
        ("start", CMD_START), ("help", CMD_HELP), ("list", CMD_LIST),
        ("end", CMD_END), ("again", CMD_AGAIN), ("status", CMD_STATUS),
    ):
        if t in aliases:
            return name, text
    return None, text


def parse_selection(text):
    """
    解析企划选择输入。
    返回 (序号列表, 是否开启XD)；无法解析时返回 (None, None)
    例："1 3" -> ([1,3], False)   "1 2 xd" -> ([1,2], True)   "全部 xd" -> ("all", True)
    """
    t = text.strip().lower()
    if not t:
        return None, None

    xd = False
    if re.search(r"\bxd\b|x d|挑战", t):
        xd = True
        t = re.sub(r"\bxd\b|挑战", " ", t)
    elif re.search(r"普通|正常", t):
        t = re.sub(r"普通|正常", " ", t)

    if any(w in t for w in _ALL_SELECTION_WORDS):
        return "all", xd

    nums = re.findall(r"\d+", t)
    if not nums:
        return None, None
    # 输入里除了数字和分隔符不应有别的字符，否则不算选择（避免把聊天内容误判）
    residue = re.sub(r"[\d\s,，、./+和与及]+", "", t)
    if residue:
        return None, None
    return [int(n) for n in nums], xd


def parse_answer(text):
    """
    解析作答。返回 0-3 的选项下标，无法解析返回 None。
    支持 "A" "a" "B。" "选C" "答案是D" "1" "2。" 等
    """
    t = text.strip()
    if not t or len(t) > 12:
        return None
    t = re.sub(r"[\s　。.！!？?，,、~～\-—_]+", "", t)
    if len(t) == 1 and t.upper() in _ANSWER_MAP:
        return _ANSWER_MAP[t.upper()]
    m = re.fullmatch(r"(?:我选|选|答案(?:是)?|是)?([ABCDabcd1-4])", t)
    if m:
        return _ANSWER_MAP[m.group(1).upper()]
    return None


# ===================== 并发原语 =====================
class _ReentrantLock:
    """
    可重入的异步锁（asyncio.Lock 本身不可重入）。

    为什么要可重入：发送一条题目的「图片连发」需要把 [标题 + A/B/C/D 四张图 + 提示]
    作为整体发给同一个群，中间不能被别人的消息插进来（否则 A/B/C/D 的先后顺序就乱了，
    玩家会看着错位的图作答）。所以外层先占住「群发送锁」，内层每发一条消息又要用同一把锁，
    同一任务重入必须放行，否则自己把自己锁死。

    实现：记录持锁任务 + 重入计数，只有计数归零才真正释放。
    """

    def __init__(self):
        self._lock = asyncio.Lock()
        self._owner = None
        self._depth = 0

    async def __aenter__(self):
        task = asyncio.current_task()
        if self._owner is task:
            self._depth += 1
            return self
        await self._lock.acquire()
        self._owner = task
        self._depth = 1
        return self

    async def __aexit__(self, *exc):
        if self._owner is not asyncio.current_task():
            return
        self._depth -= 1
        if self._depth <= 0:
            self._depth = 0
            self._owner = None
            self._lock.release()


# ===================== 会话 =====================
class UserSession:
    def __init__(self, key, chat_type, chat_id, user_id):
        self.key = key
        self.chat_type = chat_type
        self.chat_id = chat_id
        self.user_id = user_id
        self.state = STATE_SELECTING
        self.quiz = None
        self.selected = []
        self.xd_mode = False
        self.last_active = time.time()

        # alive=False 表示这一局已被「结束 / 重来 / 超时回收」作废。
        # 正在连发图片的协程在每个 await 之后检查它，可以立刻刹车，
        # 不会在「已结束」之后还继续往群里刷图。
        self.alive = True

        # awaiting=True 表示「当前题已完整发出、正等着收一次作答」。
        # 它是一次性令牌：收到作答时在任何 await 之前就同步置 False，
        # 这样连点/手抖发的第二条作答会被判定为过期而丢弃，
        # 不会把答案算到下一题上（网页版当年就是靠同样的标记修掉这个 bug）。
        self.awaiting = False

    def touch(self):
        self.last_active = time.time()


def _install_quiet_exception_handler(loop):
    """
    websockets 在连接被强制断开时（QQ 端进程被结束、Ctrl+C 等），
    Windows 的 ProactorEventLoop 会抛出无意义的 "EOFError: stream ended" 回调异常。
    这里把它降级为 debug，其他异常照常上报，避免日志里出现吓人的 traceback。
    """
    previous = loop.get_exception_handler()

    def handler(loop_, context):
        exc = context.get("exception")
        message = str(context.get("message", ""))
        if isinstance(exc, EOFError) and "_call_connection_lost" in message:
            log.debug("连接被对端强制关闭（可忽略）：%s", exc)
            return
        if previous:
            previous(loop_, context)
        else:
            loop_.default_exception_handler(context)

    loop.set_exception_handler(handler)


# ===================== 机器人主体 =====================
class QuizBot:
    def __init__(self, cfg):
        self.cfg = cfg
        self.assets_dir = resolve_assets_dir(cfg)
        self.rng = random.Random(secrets.randbits(256))
        self.sessions = {}
        self.ws = None
        self._pending = {}
        self.self_id = None
        self._projects_cache = None
        self._projects_cache_time = 0

        # ---- 并发控制 ----
        # 每条消息都是一个独立 asyncio 任务并发跑（_receiver 里 create_task），
        # 所以要显式保证：同一个玩家的一轮交互不被另一条消息插队，
        # 同一个群的消息不互相交错，而不同群之间完全互不阻塞。
        #
        # 玩家级串行锁：key = "group:<群号>:<QQ号>" / "private:<QQ号>"
        #   把「解析 -> 判题 -> 出下一题」整轮持锁，保证一局的状态迁移不撕裂。
        self._key_locks = {}
        # 群级发送锁：key = "group:<群号>" / "private:<QQ号>"
        #   保证发往同一个群的「图片连发」是一个整体，不和别人的消息交错。
        #   不同群用的是不同的锁对象，互相不等待。
        self._chat_locks = {}
        # 后台任务强引用集合。asyncio 只对任务持弱引用，不自己存一份的话，
        # 任务可能在跑完前就被 GC 掉（表现为「消息偶尔毫无反应」）。
        self._background_tasks = set()
        # 同时在处理的消息数上限，防止被刷屏时任务无限膨胀
        self._handler_sem = asyncio.Semaphore(int(cfg.get("max_concurrent_handlers", 64)))

    # ---------- 并发原语 ----------
    def _lock_for(self, table, key):
        """按 key 取（或惰性创建）一把锁；同一 key 永远拿到同一个对象"""
        lock = table.get(key)
        if lock is None:
            lock = _ReentrantLock()
            table[key] = lock
        return lock

    def _chat_key(self, chat_type, chat_id):
        return f"{chat_type}:{chat_id}"

    def _chat_lock(self, chat_type, chat_id):
        return self._lock_for(self._chat_locks, self._chat_key(chat_type, chat_id))

    def _alive(self, session):
        """会话是否仍然有效（被结束/重来/回收后就不要再往外发东西了）"""
        return session is not None and session.alive

    # ---------- 素材 ----------
    def projects(self):
        """企划列表带 10 秒缓存，避免频繁扫目录"""
        now = time.time()
        if self._projects_cache is None or now - self._projects_cache_time > 10:
            self._projects_cache = load_projects(self.assets_dir)
            self._projects_cache_time = now
        return self._projects_cache

    def projects_text(self):
        projects = self.projects()
        if not projects:
            return "⚠️ 没有找到任何企划，请检查 assets 目录配置。"
        lines = ["📚 当前可玩的企划：", ""]
        for i, p in enumerate(projects, 1):
            lines.append(f"{i}. {p}")
        lines.append("")
        lines.append("回复序号开玩（可多选，如 1 3；加 xd 开启挑战模式）")
        return "\n".join(lines)

    # ---------- 发送 ----------
    async def _call(self, action, params, timeout=20):
        if self.ws is None:
            return None
        echo = uuid.uuid4().hex
        fut = asyncio.get_running_loop().create_future()
        self._pending[echo] = fut
        try:
            await self.ws.send(json.dumps({"action": action, "params": params, "echo": echo}))
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            log.warning("调用 %s 超时", action)
            return None
        except Exception as e:
            log.warning("调用 %s 失败：%s", action, e)
            return None
        finally:
            self._pending.pop(echo, None)

    async def send(self, chat_type, chat_id, segments):
        """发送一条消息；segments 可以是字符串或消息段列表"""
        if isinstance(segments, str):
            segments = [text_seg(segments)]
        segments = [s for s in segments if s]
        if not segments:
            return
        # 同一个群的发送串行化，保证消息顺序确定、互不穿插。
        # 不同群是不同锁对象，因此多群之间完全并行。
        async with self._chat_lock(chat_type, chat_id):
            if chat_type == "group":
                await self._call("send_group_msg",
                                 {"group_id": int(chat_id), "message": segments})
            else:
                await self._call("send_private_msg",
                                 {"user_id": int(chat_id), "message": segments})

    async def send_image(self, chat_type, chat_id, img_path, size=None, prefix=None):
        """发送一张图片（按配置编码）；prefix 会作为图片前的一条文本一起发出"""
        size = size or self.cfg["image_size"]
        mode = self.cfg["image_send_mode"]
        # 缩放 + 编码是 CPU 活（Pillow），直接跑会占住整个事件循环，
        # 让别人（哪怕在别的群）的消息也跟着卡住。丢到线程池里做。
        seg = await asyncio.to_thread(to_image_segment, img_path, size, mode)
        if seg is None:
            await self.send(chat_type, chat_id,
                            (prefix or "") + "（图片加载失败，已跳过）")
            return False
        segments = [text_seg(prefix)] if prefix else []
        segments.append(seg)
        await self.send(chat_type, chat_id, segments)
        return True

    # ---------- 事件处理 ----------
    async def handle_event(self, event):
        post_type = event.get("post_type")
        if event.get("self_id"):
            self.self_id = event["self_id"]
        if post_type == "meta_event":
            if event.get("meta_event_type") == "lifecycle":
                log.info("已连接，登录号：%s", event.get("self_id"))
            return
        if post_type != "message":
            return
        try:
            await self.on_message(event)
        except Exception:
            log.exception("处理消息时出错")

    def _session_key(self, event):
        mtype = event.get("message_type")
        if mtype == "group":
            return "group:{}:{}".format(event.get("group_id"), event.get("user_id"))
        if mtype == "private":
            return "private:{}".format(event.get("user_id"))
        return None

    def _cleanup_sessions(self):
        """回收超时无活动的对局，并顺手清理不再使用的锁，防止无界增长"""
        ttl = self.cfg["session_ttl"]
        now = time.time()
        for key in [k for k, s in self.sessions.items()
                    if now - s.last_active > ttl]:
            dead = self.sessions.pop(key, None)
            if dead is not None:
                dead.alive = False

        active_chats = {self._chat_key(s.chat_type, s.chat_id)
                        for s in self.sessions.values()}
        active_keys = set(self.sessions)
        for table, alive in ((self._chat_locks, active_chats),
                             (self._key_locks, active_keys)):
            for k in [k for k in table if k not in alive]:
                table.pop(k, None)

    # ---------- 消息分发 ----------
    def spawn(self, event):
        """
        并发处理一条事件，并**持有任务的强引用**。
        asyncio.create_task 只保留弱引用，若调用方不保存返回值，
        任务可能在跑完前被垃圾回收，表现为「这条消息莫名其妙没反应」。
        """
        task = asyncio.ensure_future(self._handle_bounded(event))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    async def _handle_bounded(self, event):
        # 限制同时在处理的消息数，避免被刷屏时任务无限膨胀
        async with self._handler_sem:
            await self.handle_event(event)

    async def on_message(self, event):
        mtype = event.get("message_type")
        if mtype == "group" and not self.cfg["enable_group_chat"]:
            return
        if mtype == "private" and not self.cfg["enable_private_chat"]:
            return
        if mtype not in ("group", "private"):
            return

        # 机器人自己发的消息不回
        if str(event.get("user_id")) == str(self.self_id):
            return

        text, at_bot = parse_incoming(event, self.self_id)
        chat_type = mtype
        chat_id = event.get("group_id") if mtype == "group" else event.get("user_id")
        if chat_id is None:
            return

        key = self._session_key(event)
        if key is None:
            return

        # 是否视为「指令」
        has_prefix, body = strip_prefix(text, self.cfg["command_prefixes"])
        if mtype == "private":
            is_cmd = True
        elif self.cfg["require_at_in_group"]:
            is_cmd = at_bot or has_prefix
        else:
            is_cmd = True

        # ===== 关键：作答令牌必须在这里「同步」判定并消费 =====
        # 下面任何一个 await 之后，世界都可能已经变了（上一题发完了、
        # 会话被结束了……）。所以要趁现在、在还没让出控制权的时候就把
        # 「这条消息算不算一次有效作答」定下来，否则连点两下会把第二次
        # 作答算到下一题上，玩家会看到「没看过的题被判了」。
        pre = self.sessions.get(key)
        claimed = None       # (session, 选项下标)：本条消息合法占用了令牌
        stale_answer = False  # 令牌不在手上却发了作答 -> 过期，直接丢弃
        if pre is not None and pre.state == STATE_PLAYING:
            idx = parse_answer(body)
            if idx is not None:
                if pre.awaiting and pre.alive:
                    pre.awaiting = False
                    claimed = (pre, idx)
                else:
                    stale_answer = True

        if stale_answer:
            # 上一题还在连发图片，或者已经答过了。
            # 普通文字作答静默丢弃（多半是手抖/连点）；但如果他专门 @ 了机器人，
            # 说明是认真作答，提醒一下比默默吞掉友好。
            log.debug("丢弃过期作答：%s", body)
            if is_cmd:
                await self.send(chat_type, chat_id,
                                "这题的作答已经收到过啦（或者图片还没发完）～"
                                "等我发完当前题目再回复 A/B/C/D 哦")
            return

        if not body:
            if is_cmd:
                await self.send(chat_type, chat_id, "在的～回复「帮助」看看怎么玩吧！")
            return

        self._cleanup_sessions()

        # 同一个玩家/会话的整轮交互串行执行，不让两条消息的回复交叉
        async with self._lock_for(self._key_locks, key):
            session = self.sessions.get(key)

            if claimed is not None:
                owner, idx = claimed
                # 等锁期间会话可能已被结束/重开/替换：这一票作废
                if (owner is session and session.state == STATE_PLAYING
                        and session.alive):
                    await self.do_answer(session, idx)
                return

            cmd, _ = match_command(body)

            # ---------- 1. 已有对局：会话内交互优先 ----------
            if session is not None:
                session.touch()
                if await self.handle_in_session(session, body, cmd, is_cmd):
                    return

            # ---------- 2. 无对局（或会话未处理）：全局指令 ----------
            # 群里没 @ 机器人也没带前缀的普通聊天，一律不理会（避免误触发/刷屏）
            if not is_cmd:
                return

            if cmd == "help":
                await self.send(chat_type, chat_id, HELP_TEXT)
                return
            if cmd == "list":
                await self.send(chat_type, chat_id, self.projects_text())
                return
            if cmd in ("start", "again"):
                await self.start_selection(chat_type, chat_id, key, event.get("user_id"))
                return
            if cmd == "end":
                return  # 没有对局，无需结束（静默，避免刷屏）
            if cmd == "status":
                await self.send(chat_type, chat_id,
                                "你现在还没有进行中的对局，回复「猜声优」开一局吧～")
                return

            # ---------- 3. 兜底 ----------
            await self.send(chat_type, chat_id,
                            "没听懂这条指令呢 🤔\n回复「帮助」查看玩法，"
                            "或回复「猜声优」直接开一局～")

    # ---------- 会话内交互 ----------
    async def handle_in_session(self, session, body, cmd, is_cmd):
        chat_type, chat_id = session.chat_type, session.chat_id

        # 公共指令
        if cmd == "end":
            # 先置 alive=False，让「正在连发图片」的协程在下一个 await 后立刻刹车，
            # 否则会出现「已结束」之后还继续往群里刷图
            session.alive = False
            self.sessions.pop(session.key, None)
            await self.send(chat_type, chat_id, "👋 已结束当前对局，想玩了随时 @我 说「猜声优」～")
            return True
        if cmd == "help":
            await self.send(chat_type, chat_id, HELP_TEXT)
            return True
        if cmd == "again":
            await self.start_selection(chat_type, chat_id, session.key, session.user_id)
            return True
        if cmd == "list":
            await self.send(chat_type, chat_id, self.projects_text())
            return True
        if cmd == "status":
            await self.reply_status(session)
            return True

        # 选择企划阶段
        if session.state == STATE_SELECTING:
            if cmd == "start":
                await self.start_selection(chat_type, chat_id, session.key, session.user_id)
                return True
            nums, xd = parse_selection(body)
            if nums is None:
                if is_cmd:
                    await self.send(chat_type, chat_id,
                                    "请回复企划序号哦～例如：1 3（想挑战就写 1 3 xd）")
                return True
            await self.do_select_projects(session, nums, xd)
            return True

        # 答题阶段
        if session.state == STATE_PLAYING:
            idx = parse_answer(body)
            if idx is not None:
                # 走到这里说明令牌没被「收到消息那一刻」抢到：
                # 要么上一题还在连发图片，要么这题已经答过了 —— 一律丢弃。
                if not (session.awaiting and session.alive):
                    log.debug("丢弃未到作答时机的输入：%s", body)
                    return True
                session.awaiting = False
                await self.do_answer(session, idx)
                return True
            if cmd == "start":
                await self.send(chat_type, chat_id,
                                "这一局还在进行中哦～直接回复 A/B/C/D（或 1/2/3/4）作答，"
                                "想重开请回复「重来」。")
                return True
            if is_cmd:
                await self.send(chat_type, chat_id,
                                "现在请作答～回复 A / B / C / D（或 1 / 2 / 3 / 4）")
                return True
            return True  # 普通聊天内容，静默忽略

        # 结算后
        if session.state == STATE_DONE:
            if cmd == "start":
                await self.start_selection(chat_type, chat_id, session.key, session.user_id)
                return True
            if is_cmd:
                await self.send(chat_type, chat_id,
                                "这一局已经结束啦～回复「再来」或「猜声优」开新的一局！")
                return True
            return True

        return False

    # ---------- 各阶段动作 ----------
    async def start_selection(self, chat_type, chat_id, key, user_id):
        if key is None:
            return
        old = self.sessions.get(key)
        if old is None and len(self.sessions) >= self.cfg["max_sessions"]:
            await self.send(chat_type, chat_id, "😵 现在同时进行的对局太多啦，稍后再试试～")
            return
        if old is not None:
            # 重开时作废旧会话：它的连发图片协程会在下一个 await 后收手。
            # 注意锁是按「玩家 key」取的而不是按会话对象，所以换对象不影响串行化。
            old.alive = False
        session = UserSession(key, chat_type, chat_id, user_id)
        self.sessions[key] = session
        await self.send(chat_type, chat_id, self.projects_text())

    async def do_select_projects(self, session, nums, xd):
        chat_type, chat_id = session.chat_type, session.chat_id
        projects = self.projects()
        if not projects:
            await self.send(chat_type, chat_id, "⚠️ 没有找到任何企划，请联系管理员检查 assets 目录。")
            return

        if nums == "all":
            selected = list(projects)
        else:
            selected, invalid = [], []
            for n in nums:
                if 1 <= n <= len(projects):
                    name = projects[n - 1]
                    if name not in selected:
                        selected.append(name)
                else:
                    invalid.append(n)
            if invalid:
                await self.send(chat_type, chat_id,
                                "❓ 没有这些序号：{}（可选 1~{}），请重新选择～".format(
                                    "、".join(str(i) for i in invalid), len(projects)))
                return
            if not selected:
                await self.send(chat_type, chat_id, "请至少选择一个企划哦～")
                return

        rng = random.Random(secrets.randbits(256))
        # 扫目录 + 逐张 open/verify 图片是明显的同步 IO，题目的生成同样要校验大量图片。
        # 直接在这里跑会占住整个事件循环，把别的群的消息一起卡住 —— 丢到线程池。
        seiyu_list = await asyncio.to_thread(load_seiyu, self.assets_dir, selected, rng)
        if len(seiyu_list) < 4:
            await self.send(chat_type, chat_id,
                            "⚠️ 所选企划的声优不足 4 位，没法出题，请多选几个企划～")
            return

        quiz = QuizSession(seiyu_list, xd_mode=xd, rng=rng)
        await asyncio.to_thread(quiz.start)
        if not self._alive(session):
            return  # 出题期间玩家已经「结束」了，这一局作废
        session.quiz = quiz
        session.selected = selected
        session.xd_mode = xd
        session.state = STATE_PLAYING
        session.awaiting = False
        session.touch()

        head = "🎬 开局！企划：{}\n共 {} 题，每题 {} 分{}".format(
            "、".join(selected), TOTAL_QUESTIONS, PER_SCORE,
            "\n😈 XD 模式已开启（正确率低于 50% 得分归零）" if xd else "",
        )
        await self.send(chat_type, chat_id, head)
        await asyncio.sleep(self.cfg["send_interval"])
        await self.send_question(session)

    async def send_question(self, session):
        chat_type, chat_id = session.chat_type, session.chat_id
        quiz = session.quiz
        # 题目还在往外发，此刻不接受任何作答
        session.awaiting = False

        if not self._alive(session):
            return
        q = quiz.current_question
        if q is None:
            await self.send_result(session)
            return

        n, total = quiz.number, quiz.total_questions

        # 题型 2：看图猜人（1 张图 + 4 个名字）—— 本来就是一条消息，天然原子
        if q["type"] == 2:
            options_lines = "\n".join(
                "{}、{}".format(o["key"], o["name"]) for o in quiz.current_options()
            )
            size = self.cfg["image_size"]
            # 图片编码是 CPU 活，放线程里做，别堵住事件循环
            seg = await asyncio.to_thread(to_image_segment, q["image"], size,
                                          self.cfg["image_send_mode"])
            if not self._alive(session):
                return
            segments = [text_seg(
                "【第 {}/{} 题 · 看图猜人】\n这张图片对应的声优是？".format(n, total)
            )]
            if seg:
                segments.append(seg)
            else:
                segments.append(text_seg("（图片加载失败，本题跳过判图，凭记忆猜吧）"))
            segments.append(text_seg(
                "\n{}\n\n回复 A/B/C/D 或 1/2/3/4 作答".format(options_lines)
            ))
            await self.send(chat_type, chat_id, segments)
            session.awaiting = self._alive(session)
            return

        # 题型 1：看名选图（1 个名字 + 4 张图）
        # 「标题 + A/B/C/D 四张图 + 作答提示」必须作为一个整体发出去：
        # 中途若插进别人的消息，玩家看到的图片顺序就和判题顺序对不上了。
        # 这里占住本群的发送锁（可重入，内层 send 仍能通过），
        # 不同群用的是不同的锁，所以别的群不受影响。
        async with self._chat_lock(chat_type, chat_id):
            if not self._alive(session):
                return
            await self.send(chat_type, chat_id,
                            "【第 {}/{} 题 · 看名选图】\n请从下面 4 张图片中选出「{}」\n"
                            "（依次对应 A、B、C、D）".format(n, total, q["correct"]["name"]))
            for opt in quiz.current_options():
                await asyncio.sleep(self.cfg["send_interval"])
                if not self._alive(session):
                    return  # 玩家已「结束」/「重来」，别再刷图了
                await self.send_image(chat_type, chat_id, opt["folder"],
                                      prefix="{}：".format(opt["key"]))
            if not self._alive(session):
                return
            await self.send(chat_type, chat_id, "回复 A/B/C/D 或 1/2/3/4 作答")

        # 题目完整送达，这时才开放作答
        session.awaiting = self._alive(session)

    async def do_answer(self, session, idx):
        chat_type, chat_id = session.chat_type, session.chat_id
        session.awaiting = False
        if not self._alive(session):
            return
        quiz = session.quiz
        res = quiz.answer(idx)
        if res is None:
            await self.send(chat_type, chat_id, "这道题没法作答了，回复「重来」开新的一局吧～")
            return

        if res["correct"]:
            reply = self.rng.choice(CORRECT_REPLIES).format(name=res["correct_name"])
        else:
            reply = self.rng.choice(WRONG_REPLIES).format(
                opt=res["correct_option"], name=res["correct_name"])

        tail = "　（当前得分 {}）".format(res["score"])
        await self.send(chat_type, chat_id, reply + tail)
        await asyncio.sleep(self.cfg["send_interval"])
        if not self._alive(session):
            return
        if res["finished"]:
            await self.send_result(session)
        else:
            await self.send_question(session)

    async def send_result(self, session):
        chat_type, chat_id = session.chat_type, session.chat_id
        session.awaiting = False
        if not self._alive(session):
            return
        quiz = session.quiz
        r = quiz.result()
        session.state = STATE_DONE

        rate = r["correct_count"] / r["question_count"] if r["question_count"] else 0
        comment = "再接再厉～"
        for threshold, text in COMMENTS:
            if rate >= threshold:
                comment = text
                break

        lines = [
            "🎮 答题完成！",
            "",
            "得分：{}/{}".format(r["score"], r["total"]),
            "正确：{}/{} 题".format(r["correct_count"], r["question_count"]),
            "用时：{} 秒".format(r["total_time"]),
        ]
        if r["xd_applied"]:
            lines.append("")
            lines.append("⚠️😈 XD 模式：正确率低于 50%，你已被斩杀！")
        lines.append("")
        lines.append(comment)
        lines.append("——————————————")
        lines.append("回复「再来」开新的一局～")

        await self.send(chat_type, chat_id, "\n".join(lines))

    async def reply_status(self, session):
        chat_type, chat_id = session.chat_type, session.chat_id
        if session.state == STATE_SELECTING:
            await self.send(chat_type, chat_id, "你正在选择企划中，回复序号即可开始～")
            return
        if session.state == STATE_PLAYING:
            quiz = session.quiz
            await self.send(chat_type, chat_id,
                            "📊 当前进度：第 {}/{} 题，得分 {}，已用 {} 秒".format(
                                quiz.number, quiz.total_questions, quiz.score,
                                int(time.time() - quiz.start_time)))
            return
        await self.send(chat_type, chat_id, "这一局已经结束啦～回复「再来」开新的一局！")

    # ---------- 连接 ----------
    async def _receiver(self, ws):
        async for raw in ws:
            try:
                data = json.loads(raw)
            except Exception:
                continue

            echo = data.get("echo")
            if echo and echo in self._pending:
                fut = self._pending[echo]
                if not fut.done():
                    fut.set_result(data)
                continue

            if data.get("post_type"):
                # 用 spawn 而不是裸 create_task：必须自己持有任务的强引用
                self.spawn(data)

    def _fail_pending(self):
        """
        连接断开时，把还在等回执的请求立刻置为「无结果」。
        否则这些 future 要一直挂到 20 秒超时才醒过来，
        期间对应的玩家/群会被自己的发送锁白等一大截。
        """
        pending, self._pending = self._pending, {}
        for fut in pending.values():
            if not fut.done():
                fut.set_result(None)

    async def run_forever(self):
        _install_quiet_exception_handler(asyncio.get_running_loop())
        url = self.cfg["onebot_ws_url"]
        token = self.cfg.get("access_token") or ""
        if token and "access_token=" not in url:
            url = url + ("&" if "?" in url else "?") + "access_token=" + token

        backoff = 1
        log.info("正在连接 OneBot：%s", self.cfg["onebot_ws_url"])
        connect_kwargs = build_connect_kwargs(self.cfg)
        if _CONNECT_SUPPORTS_PROXY and connect_kwargs.get("proxy") is None:
            log.info("已关闭系统代理（若需走代理请把 config.json 的 use_system_proxy 设为 true）")
        while True:
            try:
                async with websockets.connect(url, **connect_kwargs) as ws:
                    self.ws = ws
                    backoff = 1
                    log.info("✅ 已连接，开始接收消息")
                    await self._receiver(ws)
            except asyncio.CancelledError:
                self.ws = None
                self._fail_pending()
                raise
            except Exception as e:
                log.warning("连接断开：%s，%s 秒后重连", e, backoff)
            self.ws = None
            self._fail_pending()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)


def main():
    cfg = load_config()
    logging.basicConfig(
        level=getattr(logging, str(cfg.get("log_level", "INFO")).upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    bot = QuizBot(cfg)
    if not bot.projects():
        log.warning("未在 %s 找到任何企划文件夹，请检查 config.json 的 assets_dir", bot.assets_dir)
    else:
        log.info("已加载 %d 个企划：%s", len(bot.projects()), "、".join(bot.projects()))
    log.info("图片发送模式：%s", cfg["image_send_mode"])
    try:
        asyncio.run(bot.run_forever())
    except KeyboardInterrupt:
        log.info("已退出")


if __name__ == "__main__":
    main()
