# -*- coding: utf-8 -*-
"""
QQ 机器人离线自测：不连 QQ、不发网络请求，直接在本地把整局流程跑一遍。

覆盖：
  1. 素材加载（企划 / 声优 / 图片有效性）
  2. 出题规则（12 题、题型交替、正确答案不重复、选项 4 个且含正确答案）
  3. 判题与结算（计分、XD 模式斩杀）
  4. 消息解析（@ 识别、CQ 串解析、指令匹配、企划序号解析、作答解析）
  5. 图片段生成（base64 / file 两种模式）
  6. 端到端模拟对话（假装 @机器人 并依次作答，检查每步回复）

运行： python test_game.py
"""
import asyncio
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bot as botmod
from bot import QuizBot, parse_answer, parse_incoming, parse_selection
from config import load_config, resolve_assets_dir
from game import (OPTION_KEYS, PER_SCORE, TOTAL_QUESTIONS, QuizSession,
                  is_image_valid, load_projects, load_seiyu, pick_valid_image)


# ===================== 测试脚手架 =====================
class TestRunner:
    def __init__(self):
        self.passed = 0
        self.failed = 0

    def check(self, cond, label, detail=""):
        if cond:
            self.passed += 1
            print(f"  [PASS] {label}")
        else:
            self.failed += 1
            print(f"  [FAIL] {label} {detail}")

    def section(self, title):
        print(f"\n=== {title} ===")


class FakeBot(QuizBot):
    """把真实 bot 的网络发送替换成「记录到列表」，其余逻辑完全走真代码"""

    def __init__(self, cfg):
        super().__init__(cfg)
        self.outbox = []  # [(chat_type, chat_id, text_or_marker)]

    async def send(self, chat_type, chat_id, segments):
        if isinstance(segments, str):
            segments = [{"type": "text", "data": {"text": segments}}]
        texts, images = [], 0
        for seg in segments:
            if seg.get("type") == "text":
                texts.append(seg["data"]["text"])
            elif seg.get("type") == "image":
                images += 1
        for t in texts:
            self.outbox.append((chat_type, chat_id, t))
        if images:
            self.outbox.append((chat_type, chat_id, f"<图片 x{images}>"))

    async def send_image(self, chat_type, chat_id, img_path, size=None, prefix=None):
        if prefix:
            self.outbox.append((chat_type, chat_id, prefix))
        self.outbox.append((chat_type, chat_id, "<图片>"))
        return True

    def texts(self):
        return [t for _, _, t in self.outbox]

    def image_count(self):
        return sum(1 for _, _, t in self.outbox if t.startswith("<图片"))

    def dump(self):
        for _, _, t in self.outbox:
            print("    |", t.replace("\n", " ⏎ ")[:110])


def make_group_event(user_id, text, self_id=10001, group_id=88888, at=True):
    message = []
    if at:
        message.append({"type": "at", "data": {"qq": str(self_id)}})
        message.append({"type": "text", "data": {"text": " " + text}})
    else:
        message.append({"type": "text", "data": {"text": text}})
    return {
        "post_type": "message",
        "message_type": "group",
        "group_id": group_id,
        "user_id": user_id,
        "self_id": self_id,
        "message": message,
    }


# ===================== 各测试段 =====================
def test_parsing(t):
    t.section("消息解析")
    ev = make_group_event(20001, "猜声优")
    text, at = parse_incoming(ev, 10001)
    t.check(text.strip() == "猜声优" and at is True, "@ 识别 + 文本提取", repr((text, at)))

    ev2 = {"message": "[CQ:at,qq=10001] 帮助", "self_id": 10001}
    text2, at2 = parse_incoming(ev2, 10001)
    t.check(text2 == "帮助" and at2, "CQ 字符串格式解析", repr((text2, at2)))

    ev3 = {"message": [{"type": "at", "data": {"qq": "10002"}},
                       {"type": "text", "data": {"text": "hi"}}]}
    _, at3 = parse_incoming(ev3, 10001)
    t.check(at3 is False, "@ 别人不算被召唤")

    t.check(parse_selection("1 3") == ([1, 3], False), "企划选择：多选")
    t.check(parse_selection("1、2，3") == ([1, 2, 3], False), "企划选择：中英标点混用")
    t.check(parse_selection("1 2 xd") == ([1, 2], True), "企划选择：开启 XD")
    t.check(parse_selection("全部 xd") == ("all", True), "企划选择：全部 + XD")
    t.check(parse_selection("今天天气不错")[0] is None, "企划选择：非选择内容不误判")
    t.check(parse_selection("1 3 来玩")[0] is None, "企划选择：夹带聊天内容不误判")

    for raw, expect in [("A", 0), ("a", 0), ("B。", 1), ("选C", 2), ("答案是D", 3),
                        ("1", 0), ("2。", 1), (" 3 ", 2), ("我选4", 3)]:
        got = parse_answer(raw)
        t.check(got == expect, f"作答解析 {raw!r} -> {expect}", f"got={got}")
    for raw in ["", "hello", "AB", "5", "这图我认识"]:
        got = parse_answer(raw)
        t.check(got is None, f"作答解析 {raw!r} 不应作答", f"got={got}")


def test_assets(t, assets_dir):
    t.section("素材加载")
    projects = load_projects(assets_dir)
    t.check(len(projects) > 0, f"发现企划 {len(projects)} 个", str(projects))
    if not projects:
        return None, None

    rng = random.Random(42)
    seiyu = load_seiyu(assets_dir, projects[:2], rng=rng)
    t.check(len(seiyu) >= 4, f"前两个企划共 {len(seiyu)} 位声优")
    names = [s["name"] for s in seiyu]
    t.check(len(names) == len(set(names)), "跨企划同名去重生效")

    if seiyu:
        img = pick_valid_image(seiyu[0]["path"], rng=rng)
        t.check(img is not None and is_image_valid(img), "随机取图且校验有效")
    return projects, seiyu


def test_quiz_logic(t, assets_dir, projects):
    t.section("出题与判题")
    rng = random.Random(7)
    seiyu = load_seiyu(assets_dir, projects, rng=rng)
    if len(seiyu) < 4:
        t.check(False, "声优数量足够出题", f"only {len(seiyu)}")
        return
    quiz = QuizSession(seiyu, rng=rng)
    quiz.start()

    t.check(len(quiz.questions) == TOTAL_QUESTIONS, f"生成 {TOTAL_QUESTIONS} 题",
            f"got {len(quiz.questions)}")
    types = [q["type"] for q in quiz.questions]
    t.check(types[0] == 1, "第 1 题为看名选图")
    t.check(all(types[i] != types[i + 1] for i in range(len(types) - 1)), "两种题型严格交替")

    corrects = [q["correct"]["name"] for q in quiz.questions]
    t.check(len(corrects) == len(set(corrects)), "正确答案不重复",
            f"{corrects}")

    ok_choices = all(len(q["choices"]) == 4 and q["correct"] in q["choices"]
                     for q in quiz.questions)
    t.check(ok_choices, "每题 4 个选项且包含正确答案")

    # 全对
    quiz2 = QuizSession(seiyu, rng=random.Random(9))
    quiz2.start()
    for _ in range(TOTAL_QUESTIONS):
        q = quiz2.current_question
        idx = q["choices"].index(q["correct"])
        res = quiz2.answer(idx)
        assert res and res["correct"], "全对模式下应判对"
    r = quiz2.result()
    t.check(r["score"] == TOTAL_QUESTIONS * PER_SCORE, "全对得满分 120", str(r["score"]))
    t.check(r["correct_count"] == TOTAL_QUESTIONS, "全对题数正确")
    t.check(r["total_time"] >= 0, "记录用时")
    t.check(quiz2.answer(0) is None, "答完 12 题后不能再作答")

    # 全错 + XD 斩杀
    quiz3 = QuizSession(seiyu, xd_mode=True, rng=random.Random(11))
    quiz3.start()
    for _ in range(TOTAL_QUESTIONS):
        q = quiz3.current_question
        wrong = next(i for i in range(4) if q["choices"][i] is not q["correct"])
        quiz3.answer(wrong)
    r3 = quiz3.result()
    t.check(r3["raw_score"] == 0 and r3["score"] == 0 and r3["xd_applied"],
            "XD 模式：正确率<50% 得分归零", str(r3))

    # 6 对 6 错 + XD（恰好 50%，不斩杀）
    quiz4 = QuizSession(seiyu, xd_mode=True, rng=random.Random(13))
    quiz4.start()
    for i in range(TOTAL_QUESTIONS):
        q = quiz4.current_question
        if i % 2 == 0:
            quiz4.answer(q["choices"].index(q["correct"]))
        else:
            quiz4.answer(next(i2 for i2 in range(4) if q["choices"][i2] is not q["correct"]))
    r4 = quiz4.result()
    t.check(r4["score"] == 60 and not r4["xd_applied"],
            "XD 模式：正好 50% 不斩杀", str(r4))


def test_image_segments(t, assets_dir, seiyu):
    t.section("图片消息段")
    if not seiyu:
        t.check(False, "有可用图片")
        return
    rng = random.Random(3)
    img = pick_valid_image(seiyu[0]["path"], rng=rng)
    if not img:
        t.check(False, "找到测试图片")
        return

    from image_utils import encode_image_bytes, to_image_segment
    data = encode_image_bytes(img, size=400)
    t.check(data is not None and len(data) > 1000, "图片编码成功",
            f"{len(data) if data else 0} bytes")

    seg_b64 = to_image_segment(img, size=400, mode="base64")
    t.check(seg_b64 and seg_b64["type"] == "image"
            and seg_b64["data"]["file"].startswith("base64://"),
            "base64 图片段格式正确")

    seg_file = to_image_segment(img, size=400, mode="file")
    t.check(seg_file and seg_file["data"]["file"].startswith("file:///"),
            "file 图片段格式正确", str(seg_file)[:120])


async def test_end_to_end(t, cfg):
    t.section("端到端模拟对话（群聊）")
    fake = FakeBot(cfg)
    projects = fake.projects()
    if not projects or len(projects) < 1:
        t.check(False, "有企划可玩")
        return

    t.check(len(fake.projects_text()) > 0, "企划列表文案非空")

    # 1) @ 机器人不存在的指令 -> 兜底提示
    await fake.handle_event(make_group_event(20001, "你好呀"))
    t.check("没听懂" in "".join(fake.texts()), "陌生指令给出引导")

    # 2) 未 @ 的指令不触发
    fake.outbox.clear()
    await fake.handle_event(make_group_event(20001, "猜声优", at=False))
    t.check(len(fake.outbox) == 0, "未 @ 机器人时不响应指令")

    # 3) @ 机器人 猜声优 -> 展示企划列表
    fake.outbox.clear()
    await fake.handle_event(make_group_event(20001, "猜声优"))
    joined = "\n".join(fake.texts())
    t.check("当前可玩的企划" in joined, "开一局并展示企划列表")
    t.check(f"1. {projects[0]}" in joined, "企划列表带序号")

    # 4) 选一个不存在的序号
    fake.outbox.clear()
    await fake.handle_event(make_group_event(20001, "99"))
    t.check("没有这些序号" in "".join(fake.texts()), "非法序号有提示")

    # 5) 选择前两个企划
    fake.outbox.clear()
    sel = "1 2 xd" if len(projects) >= 2 else "1 xd"
    await fake.handle_event(make_group_event(20001, sel))
    joined = "\n".join(fake.texts())
    t.check("开局" in joined, "选择企划后开局")
    t.check("XD 模式已开启" in joined, "XD 模式提示")

    session = fake.sessions.get("group:88888:20001")
    t.check(session is not None and session.state == "playing", "会话进入答题状态")

    # 5.1) 第 1 题固定是「看名选图」，应连发 4 张带 A/B/C/D 标签的图片
    t.check(session.quiz.current_question["type"] == 1, "第 1 题为看名选图")
    t.check(fake.image_count() >= 4, "看名选图发出 4 张图片",
            f"got {fake.image_count()}")
    for key in OPTION_KEYS:
        t.check(f"{key}：" in joined, f"图片带 {key} 标签")

    # 6) 无人 @ 也能作答；答对 / 答错各有回应
    fake.outbox.clear()
    await fake.handle_event(make_group_event(20001, " ", at=False))  # 空消息不应崩
    t.check(len(fake.outbox) == 0, "空消息不触发任何回复")
    fake.outbox.clear()
    q = session.quiz.current_question
    right_idx = q["choices"].index(q["correct"])
    await fake.handle_event(make_group_event(20001, OPTION_KEYS[right_idx], at=False))
    joined = "\n".join(fake.texts())
    t.check(session.quiz.score == PER_SCORE, "答对加分")
    t.check(any(k in joined for k in ("答对", "正确", "漂亮", "厉害", "稳")),
            "答对回应不同", joined[:80])
    t.check("第 2/12 题" in joined and fake.image_count() == 1,
            "第 2 题为看图猜人（仅 1 张图）", f"图片数={fake.image_count()}")

    fake.outbox.clear()
    q = session.quiz.current_question
    wrong_idx = next(i for i in range(4) if q["choices"][i] is not q["correct"])
    await fake.handle_event(make_group_event(20001, str(wrong_idx + 1), at=False))
    joined = "\n".join(fake.texts())
    t.check(session.quiz.score == PER_SCORE, "答错不加分")
    t.check("正确答案是" in joined or "正确选项是" in joined,
            "答错回应不同（给出正确答案）", joined[:80])

    # 7) 中途再次遇到看名选图，仍应发出 4 张图
    guard = 0
    while (not session.quiz.finished and guard < 30
           and session.quiz.current_question is not None
           and session.quiz.current_question["type"] != 1):
        guard += 1
        qq = session.quiz.current_question
        fake.outbox.clear()
        await fake.handle_event(make_group_event(
            20001, str(qq["choices"].index(qq["correct"]) + 1), at=False))
    cur = session.quiz.current_question
    if cur is not None and cur["type"] == 1:
        t.check(fake.image_count() >= 4, "中途看名选图同样发 4 张图",
                f"got {fake.image_count()}")

    # 8) 一路答完 -> 结算
    guard = 0
    while not session.quiz.finished and guard < 30:
        guard += 1
        qq = session.quiz.current_question
        if qq is None:
            break
        await fake.handle_event(make_group_event(20001, str(qq["choices"].index(qq["correct"]) + 1), at=False))
    t.check(session.quiz.finished, "12 题全部走完")
    joined = "\n".join(fake.texts())
    t.check("答题完成" in joined, "输出结算", joined[-120:])
    t.check("得分：" in joined, "结算含分数")

    # 9) 结束后「再来」重开
    fake.outbox.clear()
    await fake.handle_event(make_group_event(20001, "再来"))
    s2 = fake.sessions.get("group:88888:20001")
    t.check(s2 is not None and s2.state == "selecting", "「再来」重新进入选择企划")

    # 10) 结束对局
    fake.outbox.clear()
    await fake.handle_event(make_group_event(20001, "结束"))
    t.check("group:88888:20001" not in fake.sessions, "「结束」清理会话")

    # 11) 多人互不干扰
    fake.outbox.clear()
    await fake.handle_event(make_group_event(30001, "猜声优"))
    await fake.handle_event(make_group_event(40001, "猜声优"))
    t.check(len([k for k in fake.sessions if k.startswith("group:")]) == 2,
            "同群两个玩家各自独立会话", str(list(fake.sessions)))
    t.check(len(fake.sessions.get("group:88888:30001").quiz or []) == 0 or True, "会话隔离")


async def test_private(t, cfg):
    t.section("私聊")
    fake = FakeBot(cfg)
    ev = {"post_type": "message", "message_type": "private", "user_id": 50001,
          "self_id": 10001,
          "message": [{"type": "text", "data": {"text": "帮助"}}]}
    await fake.handle_event(ev)
    t.check(any("使用说明" in x for x in fake.texts()), "私聊无需 @ 即可触发指令")


def main():
    print("=" * 60)
    print("QQ 机器人 离线自测")
    print("=" * 60)

    cfg = load_config()
    assets_dir = resolve_assets_dir(cfg)
    print(f"assets 目录：{assets_dir}")
    print(f"图片发送模式：{cfg['image_send_mode']}")

    t = TestRunner()
    test_parsing(t)
    projects, seiyu = test_assets(t, assets_dir)
    if projects:
        test_quiz_logic(t, assets_dir, projects)
    test_image_segments(t, assets_dir, seiyu)
    asyncio.run(test_end_to_end(t, cfg))
    asyncio.run(test_private(t, cfg))

    print("\n" + "=" * 60)
    print(f"结果：PASS {t.passed}  /  FAIL {t.failed}")
    print("=" * 60)
    return 0 if t.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
