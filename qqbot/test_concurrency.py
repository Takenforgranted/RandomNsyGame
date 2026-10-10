# -*- coding: utf-8 -*-
"""
QQ 机器人并发测试 —— 专测「多个群 / 多个群友同时 @ 机器人」的场景

真实运行时每条消息都由 asyncio.create_task 并发处理，所有 await
（发图、send_interval 的 sleep、图片编码）都是让出点。
本文件用「每次发送都 await 一下」的假机器人来放大交错，从而把竞态暴露出来。

覆盖：
  1. 同一玩家快速连发两次作答 —— 只能消耗一题，不能错位到下一题
  2. 同一群里两个玩家同时出「看名选图」—— 两组 A/B/C/D 图片不能交错
  3. 图片连发途中「结束」—— 结束后不能再继续刷图
  4. 不同群之间互不阻塞
  5. 后台任务被强引用持有（asyncio 只持弱引用，否则可能被 GC 掉）
  6. 同一玩家并发重复开局 —— 不会产生两个对局

运行： python test_concurrency.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bot import QuizBot, STATE_PLAYING, STATE_SELECTING
from config import load_config
from game import OPTION_KEYS

SELF_ID = 10001


class SlowFakeBot(QuizBot):
    """
    模拟真实的网络往返：每次发送前 await 一小会儿。
    真正的 bot 里发送要过 WebSocket，图片还要 Pillow 编码，都是让出点，
    这里刻意复现同样的让出行为。
    """

    def __init__(self, cfg, send_delay=0.005):
        super().__init__(cfg)
        self.send_delay = send_delay
        self.outbox = []  # [{"chat": "group:88888", "text": "..."}]

    def _rec(self, chat_type, chat_id, text):
        self.outbox.append({"chat": f"{chat_type}:{chat_id}", "text": text})

    async def send(self, chat_type, chat_id, segments):
        if isinstance(segments, str):
            segments = [{"type": "text", "data": {"text": segments}}]
        await asyncio.sleep(self.send_delay)  # ← 让出点
        # 以下没有任何 await：一次调用的多条文本必须原子落盘
        for seg in segments:
            if seg.get("type") == "text":
                self._rec(chat_type, chat_id, seg["data"]["text"])
            elif seg.get("type") == "image":
                self._rec(chat_type, chat_id, "<图片>")

    async def send_image(self, chat_type, chat_id, img_path, size=None, prefix=None):
        await asyncio.sleep(self.send_delay)  # ← 让出点
        if prefix:
            self._rec(chat_type, chat_id, prefix)
        self._rec(chat_type, chat_id, "<图片>")
        return True

    # ---------- 断言辅助 ----------
    def texts(self, chat=None):
        return [e["text"] for e in self.outbox
                if chat is None or e["chat"] == chat]

    def chat_entries(self, chat):
        return [e["text"] for e in self.outbox if e["chat"] == chat]

    def count_contains(self, needle, chat=None):
        return sum(1 for t in self.texts(chat) if needle in t)


class R:
    def __init__(self):
        self.p, self.f = 0, 0

    def check(self, cond, label, detail=""):
        if cond:
            self.p += 1
            print(f"  [PASS] {label}")
        else:
            self.f += 1
            print(f"  [FAIL] {label}  {detail}")

    def section(self, t):
        print(f"\n=== {t} ===")


def ev(user_id, text, group_id=88888, at=True):
    msg = []
    if at:
        msg.append({"type": "at", "data": {"qq": str(SELF_ID)}})
        msg.append({"type": "text", "data": {"text": " " + text}})
    else:
        msg.append({"type": "text", "data": {"text": text}})
    return {"post_type": "message", "message_type": "group",
            "group_id": group_id, "user_id": user_id, "self_id": SELF_ID,
            "message": msg}


def make_cfg(interval=0.05):
    cfg = load_config()
    cfg["send_interval"] = interval   # 缩短，保证测试跑得快
    cfg["image_send_mode"] = "base64"
    return cfg


async def setup_playing(bot, user_id, group_id=88888):
    """把某玩家推进到「答题中」，并返回其会话"""
    await bot.handle_event(ev(user_id, "猜声优", group_id=group_id))
    await bot.handle_event(ev(user_id, "1 2 xd", group_id=group_id))
    return bot.sessions.get(f"group:{group_id}:{user_id}")


# ===================== 1. 连发两次作答 =====================
async def t_double_answer(r, cfg):
    r.section("1. 同一玩家快速连发两次作答（只能消耗一题）")
    bot = SlowFakeBot(cfg)
    s = await setup_playing(bot, 20001)
    r.check(s is not None and s.state == STATE_PLAYING, "进入答题状态")
    r.check(s.quiz.current_q_idx == 0, "当前在第 1 题")

    bot.outbox.clear()
    # 模拟两次「几乎同时」到达的作答（真实场景：连点 / 手抖发两条）
    await asyncio.gather(
        bot.handle_event(ev(20001, "A", at=False)),
        bot.handle_event(ev(20001, "A", at=False)),
    )

    consumed = s.quiz.current_q_idx
    r.check(consumed == 1, "两次作答只消耗 1 题（题目不错位）",
            f"实际消耗 {consumed} 题")
    fb = bot.count_contains("当前得分")
    r.check(fb == 1, "只回了一次判题结果", f"实际回了 {fb} 次")


# ===================== 2. 同群两玩家图片交错 =====================
def label_sequence(texts):
    """把消息流里 A/B/C/D 标签抽出来，用于判断图片组是否被交错"""
    labels = []
    for t in texts:
        for k in OPTION_KEYS:
            if t.startswith(k + "：") or t.startswith(k + ":"):
                labels.append(k)
    return labels


async def t_interleave_same_group(r, cfg):
    r.section("2. 同一群两个玩家同时出「看名选图」（图片不能交错）")
    bot = SlowFakeBot(cfg)
    # 两个玩家同时选企划 -> 同时开始发第 1 题（必定是看名选图，连发 4 张）
    await bot.handle_event(ev(20001, "猜声优"))
    await bot.handle_event(ev(30001, "猜声优"))
    bot.outbox.clear()

    await asyncio.gather(
        bot.handle_event(ev(20001, "1 2 xd")),
        bot.handle_event(ev(30001, "1 2 xd")),
    )

    labels = label_sequence(bot.chat_entries("group:88888"))
    expected = list(OPTION_KEYS) * 2
    r.check(len(labels) == 8, "两组共 8 个标签", f"实际 {labels}")
    r.check(labels == expected,
            "两组 A/B/C/D 各自连续，没有交错",
            f"实际顺序 {labels}，期望 {expected}")


# ===================== 3. 连发途中结束 =====================
async def t_end_during_burst(r, cfg):
    r.section("3. 图片连发途中「结束」，不能再继续刷图")
    bot = SlowFakeBot(cfg)
    await bot.handle_event(ev(20001, "猜声优"))
    bot.outbox.clear()

    task = asyncio.create_task(bot.handle_event(ev(20001, "1 2 xd")))
    await asyncio.sleep(0.03)          # 让图片连发跑起来
    await bot.handle_event(ev(20001, "结束"))
    await task

    texts = bot.chat_entries("group:88888")
    idx_end = next((i for i, t in enumerate(texts) if "已结束当前对局" in t), None)
    r.check(idx_end is not None, "「结束」有回应")
    if idx_end is not None:
        after = texts[idx_end + 1:]
        r.check(not any(t == "<图片>" for t in after),
                "结束后不再继续发图", f"结束后仍发了 {after.count('<图片>')} 张")
    r.check("group:88888:20001" not in bot.sessions, "会话已清理")


# ===================== 4. 不同群互不阻塞 =====================
async def t_cross_group(r, cfg):
    r.section("4. 不同群之间互不阻塞")
    bot = SlowFakeBot(cfg)
    s1 = await setup_playing(bot, 20001, group_id=88888)   # 群 A
    s2 = await setup_playing(bot, 40001, group_id=99999)   # 群 B
    r.check(s1 is not None and s2 is not None, "两个群各自开局成功")
    bot.outbox.clear()

    async def big_burst():
        # 群 A：作答后要连发下一题（第 3 题是看名选图，4 张图）
        q = s1.quiz.current_question
        await bot.handle_event(ev(20001, OPTION_KEYS[q["choices"].index(q["correct"])], at=False))
        q = s1.quiz.current_question
        await bot.handle_event(ev(20001, OPTION_KEYS[q["choices"].index(q["correct"])], at=False))

    async def quick_reply():
        q = s2.quiz.current_question
        await bot.handle_event(
            ev(40001, OPTION_KEYS[q["choices"].index(q["correct"])],
               group_id=99999, at=False))

    await asyncio.gather(big_burst(), quick_reply())

    a_texts = bot.chat_entries("group:88888")
    b_texts = bot.chat_entries("group:99999")
    r.check(any("当前得分" in t for t in b_texts), "群 B 拿到了判题结果")
    last_img_a = max((i for i, t in enumerate(a_texts) if t == "<图片>"), default=-1)
    # 全局顺序里，群 B 的判题结果应出现在群 A 最后一张图之前
    order = [e["chat"] for e in bot.outbox]
    idx_b_feedback = next((i for i, e in enumerate(bot.outbox)
                           if e["chat"].endswith("99999") and "当前得分" in e["text"]), None)
    idx_a_last = next((i for i in range(len(bot.outbox) - 1, -1, -1)
                       if bot.outbox[i]["chat"].endswith("88888")
                       and bot.outbox[i]["text"] == "<图片>"), None)
    r.check(idx_b_feedback is not None and idx_a_last is not None
            and idx_b_feedback < idx_a_last,
            "群 B 的回应没有被群 A 的连发图片挡住",
            f"群B反馈@{idx_b_feedback} 群A末图@{idx_a_last}")


# ===================== 5. 后台任务强引用 =====================
async def t_task_refs(r, cfg):
    r.section("5. 后台任务被强引用持有（否则可能被 GC 掉）")
    bot = SlowFakeBot(cfg, send_delay=0.05)
    holder = getattr(bot, "_background_tasks", None)
    r.check(isinstance(holder, set), "_background_tasks 是 set（可持强引用）",
            f"实际 {type(holder)}")
    if not isinstance(holder, set):
        return

    task = bot.spawn(ev(20001, "帮助"))
    await asyncio.sleep(0)
    r.check(task in holder, "spawn 出来的任务登记在集合里（跑的过程中不会被回收）")
    await task
    await asyncio.sleep(0)
    r.check(task not in holder, "任务结束后自动从集合移除（不会内存泄漏）")


# ===================== 6. 并发重复开局 =====================
async def t_duplicate_start(r, cfg):
    r.section("6. 同一玩家并发重复开局")
    bot = SlowFakeBot(cfg)
    await asyncio.gather(
        bot.handle_event(ev(20001, "猜声优")),
        bot.handle_event(ev(20001, "猜声优")),
        bot.handle_event(ev(20001, "猜声优")),
    )
    keys = [k for k in bot.sessions if k.startswith("group:88888:20001")]
    r.check(len(keys) == 1, "只存在一个对局会话", f"实际 {len(keys)} 个")
    s = bot.sessions.get("group:88888:20001")
    r.check(s is not None and s.state == STATE_SELECTING, "会话处于选择企划阶段")
    n = bot.count_contains("当前可玩的企划")
    # 每条指令恰好被回应一次：既不能丢（<3），也不能被并发放大（>3）
    r.check(n == 3, "3 条并发指令各回应一次，不多不少", f"实际回了 {n} 次")


# ===================== 7. 同一玩家跨群 / 群聊与私聊 =====================
async def t_multi_chat(r, cfg):
    r.section("7. 同一玩家跨群、群聊与私聊并发")
    bot = SlowFakeBot(cfg)
    # 同一个人（20001）同时在两个群玩 + 一个私聊
    await bot.handle_event(ev(20001, "猜声优", group_id=88888))
    await bot.handle_event(ev(20001, "猜声优", group_id=99999))
    priv = {"post_type": "message", "message_type": "private", "user_id": 20001,
            "self_id": SELF_ID,
            "message": [{"type": "text", "data": {"text": "猜声优"}}]}
    await bot.handle_event(priv)

    keys = sorted(bot.sessions)
    r.check(keys == ["group:88888:20001", "group:99999:20001", "private:20001"],
            "同一个人的群聊/私聊各自独立成局", f"实际 {keys}")

    bot.outbox.clear()
    await asyncio.gather(
        bot.handle_event(ev(20001, "1 2 xd", group_id=88888)),
        bot.handle_event(ev(20001, "1 2 xd", group_id=99999)),
        bot.handle_event({"post_type": "message", "message_type": "private",
                          "user_id": 20001, "self_id": SELF_ID,
                          "message": [{"type": "text", "data": {"text": "1 2 xd"}}]}),
    )
    for chat in ("group:88888", "group:99999", "private:20001"):
        texts = bot.chat_entries(chat)
        r.check(any("开局！企划" in t for t in texts), f"{chat} 正常开局",
                f"实际 {texts[:3]}")
    # 三局各自独立：各自都发了一整套 A/B/C/D
    for chat in ("group:88888", "group:99999"):
        r.check(label_sequence(bot.chat_entries(chat)) == list(OPTION_KEYS),
                f"{chat} 的 A/B/C/D 完整且不与其他会话混杂")


# ===================== 8. 单群多玩家互不串台 =====================
async def t_many_players_one_group(r, cfg):
    r.section("8. 同一群 5 个玩家同时作答，互不串台")
    bot = SlowFakeBot(cfg)
    players = [20001, 20002, 20003, 20004, 20005]
    for uid in players:
        await bot.handle_event(ev(uid, "猜声优"))
    await asyncio.gather(*[bot.handle_event(ev(uid, "1 2 xd")) for uid in players])

    sessions = [bot.sessions[f"group:88888:{uid}"] for uid in players]
    r.check(len({id(s) for s in sessions}) == 5, "5 个玩家各自一个会话对象")
    r.check(all(s.state == STATE_PLAYING for s in sessions), "5 个玩家都进入答题中")

    bot.outbox.clear()
    # 全部答对第一题
    await asyncio.gather(*[
        bot.handle_event(ev(uid, OPTION_KEYS[s.quiz.current_question["choices"].index(
            s.quiz.current_question["correct"])], at=False))
        for uid, s in zip(players, sessions)
    ])
    r.check(all(s.quiz.score == 10 for s in sessions),
            "5 个玩家都各自加满 10 分，没有互相吃掉答案",
            str([s.quiz.score for s in sessions]))
    r.check(all(s.quiz.current_q_idx == 1 for s in sessions),
            "5 个玩家都只前进 1 题",
            str([s.quiz.current_q_idx for s in sessions]))
    fb = bot.count_contains("当前得分")
    r.check(fb == 5, "恰好 5 条判题回应", f"实际 {fb} 条")


async def main():
    cfg = make_cfg()
    r = R()
    print("=" * 64)
    print("QQ 机器人 并发测试")
    print("=" * 64)
    await t_double_answer(r, cfg)
    await t_interleave_same_group(r, cfg)
    await t_end_during_burst(r, cfg)
    await t_cross_group(r, cfg)
    await t_task_refs(r, cfg)
    await t_duplicate_start(r, cfg)
    await t_multi_chat(r, cfg)
    await t_many_players_one_group(r, cfg)
    print("\n" + "=" * 64)
    print(f"结果：PASS {r.p}  /  FAIL {r.f}")
    print("=" * 64)
    return 0 if r.f == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
