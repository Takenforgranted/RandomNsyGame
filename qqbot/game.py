# -*- coding: utf-8 -*-
"""
趣味猜女声优器 —— 纯游戏逻辑层（与 QQ 框架完全解耦）

设计目标：
  - 同一套出题 / 判题 / 结算逻辑，桌面版 (main.py) 与网页版 (web_app.py) 已验证过，
    这里抽成无 UI、无网络的纯逻辑，方便 QQ 机器人复用，也方便离线自测。
  - 不依赖 flask / tkinter，只依赖 pillow。

规则与现有版本保持一致：
  - 多企划混选、共 12 题、每题 10 分、满分 120
  - 两种题型交替：看名字选图（type=1） / 看图选名字（type=2）
  - 每轮正确答案声优绝不重复
  - XD 模式：正确率低于 50% 最终得分归零
"""
import os
import random
import time

from PIL import Image

# ===================== 配置区 =====================
SUPPORT_FORMATS = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.avif')
TOTAL_QUESTIONS = 12
PER_SCORE = 10
OPTION_KEYS = ["A", "B", "C", "D"]


# ===================== 素材层 =====================
def load_projects(assets_dir):
    """列出 assets 下所有企划文件夹（以 . 开头的不算）"""
    if not os.path.isdir(assets_dir):
        return []
    return sorted(
        item for item in os.listdir(assets_dir)
        if os.path.isdir(os.path.join(assets_dir, item)) and not item.startswith(".")
    )


def has_image(path):
    try:
        for f in os.listdir(path):
            if f.lower().endswith(SUPPORT_FORMATS):
                return True
    except OSError:
        return False
    return False


def list_images(folder_path):
    """列出文件夹内所有受支持的图片绝对路径（不校验有效性）"""
    try:
        return [
            os.path.join(folder_path, f)
            for f in os.listdir(folder_path)
            if f.lower().endswith(SUPPORT_FORMATS)
        ]
    except OSError:
        return []


def is_image_valid(img_path):
    """用 Pillow 校验图片能否正常打开（自动过滤损坏图片）"""
    try:
        with open(img_path, 'rb') as f:
            Image.open(f).verify()
        return True
    except Exception:
        return False


def pick_valid_image(folder_path, rng=None):
    """随机取一张校验通过的图片，返回绝对路径；失败则返回 None"""
    rng = rng or random
    images = list_images(folder_path)
    if not images:
        return None
    rng.shuffle(images)
    for p in images[:10]:
        if is_image_valid(p):
            return p
    return None


def load_seiyu(assets_dir, project_names, rng=None):
    """
    加载【多个企划】的所有声优。
    跨企划同名声优只保留第一个 —— 否则选项里会出现两条一模一样的名字，
    且两张图分属不同企划，判题时无法区分（网页版已验证的修正）。
    """
    rng = rng or random
    seiyu_list = []
    seen_names = set()
    for proj in project_names:
        proj_path = os.path.join(assets_dir, proj)
        if not os.path.isdir(proj_path):
            continue
        for folder_name in os.listdir(proj_path):
            folder_path = os.path.join(proj_path, folder_name)
            if not os.path.isdir(folder_path):
                continue
            # 目录规范： "001-爱美" -> 取短横线后的名字
            seiyu_name = folder_name.split('-', 1)[-1].strip()
            if not seiyu_name or seiyu_name in seen_names:
                continue
            seen_names.add(seiyu_name)
            if has_image(folder_path):
                seiyu_list.append({"name": seiyu_name, "path": folder_path})
    rng.shuffle(seiyu_list)
    return seiyu_list


# ===================== 单局游戏 =====================
class QuizSession:
    """
    一局游戏。一个玩家 / 一个会话持有一个实例。
    rng 独立注入：多人同时玩时互不干扰（不抢占全局 random 状态）。
    """

    def __init__(self, seiyu_list, xd_mode=False, rng=None,
                 total_questions=TOTAL_QUESTIONS, per_score=PER_SCORE):
        self.rng = rng or random.Random()
        self.total_questions = total_questions
        self.per_score = per_score
        self.seiyu_list = list(seiyu_list)
        self.xd_mode = bool(xd_mode)

        self.questions = []
        self.used_correct_seiyu = []
        self.current_q_idx = 0
        self.score = 0
        self.start_time = 0.0
        self.answer_history = []  # [{"number", "correct", "chosen_name"}]

    # ---------- 出题 ----------
    def _generate_single_question(self):
        """生成单题：正确答案绝不重复"""
        rng = self.rng
        for _ in range(50):
            q_type = 1 if (self.current_q_idx % 2 == 0) else 2

            available = [s for s in self.seiyu_list if s not in self.used_correct_seiyu]
            if not available:
                available = self.seiyu_list  # 声优少于 12 人时的兜底

            choices = rng.sample(self.seiyu_list, 4)
            correct = rng.choice(available)
            if correct not in choices:
                choices[0] = correct
            rng.shuffle(choices)

            img_path = pick_valid_image(correct["path"], rng=rng)
            if not img_path:
                continue

            if q_type == 1:  # 四张图都要能打开
                if not all(pick_valid_image(c["path"], rng=rng) for c in choices):
                    continue

            self.used_correct_seiyu.append(correct)
            return {"type": q_type, "correct": correct,
                    "choices": choices, "image": img_path}

        # 终极兜底：素材异常时也保证有题可出
        fallback = rng.choice(self.seiyu_list)
        return {"type": 1, "correct": fallback,
                "choices": rng.sample(self.seiyu_list, 4),
                "image": pick_valid_image(fallback["path"], rng=rng)}

    def start(self):
        """开始计时并一次性生成全部题目"""
        self.questions = []
        self.used_correct_seiyu = []
        self.score = 0
        self.current_q_idx = 0
        self.answer_history = []
        for i in range(self.total_questions):
            self.current_q_idx = i
            self.questions.append(self._generate_single_question())
        self.current_q_idx = 0
        self.start_time = time.time()
        return self.questions

    # ---------- 查询 ----------
    @property
    def finished(self):
        return self.current_q_idx >= self.total_questions

    @property
    def number(self):
        return self.current_q_idx + 1

    @property
    def current_question(self):
        if self.finished:
            return None
        return self.questions[self.current_q_idx]

    def current_options(self):
        """返回 [{"key": "A", "name": ..., "folder": ...}, ...]"""
        q = self.current_question
        if q is None:
            return []
        return [
            {"key": OPTION_KEYS[i], "name": c["name"], "folder": c["path"]}
            for i, c in enumerate(q["choices"])
        ]

    # ---------- 判题 ----------
    def answer(self, idx):
        """作答；返回判题结果 dict。idx 越界或已结束时返回 None"""
        if self.finished or not isinstance(idx, int) or not (0 <= idx < 4):
            return None

        q = self.current_question
        correct = q["correct"]
        chosen = q["choices"][idx]
        correct_opt = OPTION_KEYS[q["choices"].index(correct)]
        is_correct = chosen is correct

        if is_correct:
            self.score += self.per_score

        self.answer_history.append({
            "number": self.number,
            "correct": is_correct,
            "chosen_name": chosen["name"],
            "answer_name": correct["name"],
        })
        self.current_q_idx += 1

        return {
            "correct": is_correct,
            "correct_index": q["choices"].index(correct),
            "correct_option": correct_opt,
            "correct_name": correct["name"],
            "chosen_option": OPTION_KEYS[idx],
            "chosen_name": chosen["name"],
            "score": self.score,
            "finished": self.finished,
        }

    # ---------- 结算 ----------
    def result(self):
        elapsed = int(time.time() - self.start_time) if self.start_time else 0
        total = self.total_questions * self.per_score
        final_score = self.score
        xd_applied = False

        if self.xd_mode:
            correct_count = self.score // self.per_score
            if correct_count / self.total_questions < 0.5:
                final_score = 0
                xd_applied = True

        return {
            "score": final_score,
            "raw_score": self.score,
            "total": total,
            "correct_count": self.score // self.per_score,
            "question_count": self.total_questions,
            "total_time": elapsed,
            "xd_mode": self.xd_mode,
            "xd_applied": xd_applied,
        }
