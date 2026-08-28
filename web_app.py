# -*- coding: utf-8 -*-
"""
趣味猜女声优器 —— 本地网页版（Flask 后端）

保留原 Tkinter 版 (main.py) 的全部玩法与逻辑：
  - 多企划多选、XD 模式、共 12 题、每题 10 分、满分 120
  - 两种题型交替：看名字选图 / 看图选名字
  - 正确答案声优绝不重复、自动校验图片、实时计时
  - XD 模式：正确率低于 50% 时最终得分归零

运行方式：python web_app.py   （浏览器会自动打开 http://127.0.0.1:5000）
"""
import base64
import os
import random
import secrets
import sys
import threading
import time
import uuid
import webbrowser
from io import BytesIO

from flask import Flask, Response, abort, jsonify, request, send_from_directory
from PIL import Image

# ===================== 配置区 =====================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(BASE_DIR, "assets")
WEB_DIR = os.path.join(BASE_DIR, "web")

SUPPORT_FORMATS = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.avif')
TOTAL_QUESTIONS = 12
PER_SCORE = 10
IMAGE_SIZE = 300

# 会话无活动超过该时长（秒）后自动回收，避免长期运行内存膨胀
SESSION_TTL = 60 * 60

# 防止在 GBK/CP936 等控制台下 print 含 emoji 的启动信息时
# 抛 UnicodeEncodeError 导致生产服务器启动崩溃
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except Exception:
        pass

# ===================== 【增强强随机】设置真随机种子 =====================
random.seed(time.time())

# 静态文件由 Flask 内置路由托管：/static/* 对应 web/ 目录
app = Flask(__name__, static_folder="web", static_url_path="/static")

# 已缩放图片缓存: {(abs_path, size): png_bytes}
_image_cache = {}

# 游戏状态（多会话隔离：每个玩家一个独立 game，互不干扰）
game_sessions = {}
_sessions_lock = threading.Lock()
_last_cleanup_time = [0.0]


def make_game_state():
    """创建一局独立、完整的游戏状态"""
    return {
        "selected_projects": [],
        "seiyu_list": [],
        "questions": [],
        "current_q_idx": 0,
        "score": 0,
        "start_time": 0,
        "xd_mode": False,
        "used_correct_seiyu": [],
        "accepting_answers": False,
        # 每个玩家独立的随机源：避免并发下全局 random 被其他玩家“抢占”，
        # 导致一个玩家的出题/取图顺序影响另一个玩家
        "rng": random.Random(secrets.randbits(256)),
        # 单局内的状态锁：串行化同一玩家在 waitress 多线程下的
        # answer / question / result 状态切换，避免竞态
        "lock": threading.Lock(),
        "last_active": time.time(),
    }


def _get_game(sid, touch=True):
    """线程安全地读取会话游戏，并刷新活跃时间"""
    with _sessions_lock:
        g = game_sessions.get(sid)
        if g is not None and touch:
            g["last_active"] = time.time()
        return g


def _put_game(sid, g):
    with _sessions_lock:
        game_sessions[sid] = g


def _cleanup_sessions():
    """回收超过 SESSION_TTL 无活动的会话，防止内存无限增长"""
    now = time.time()
    with _sessions_lock:
        expired = [
            k for k, g in game_sessions.items()
            if now - g.get("last_active", 0) > SESSION_TTL
        ]
        for k in expired:
            del game_sessions[k]


def get_session_game():
    """从请求头读取会话 ID，返回 (session_id, game)；无效时返回 (None, None)"""
    sid = request.headers.get("X-Session-Id") or request.args.get("session_id") or ""
    if not sid:
        return None, None
    g = _get_game(sid)
    if g is None:
        return None, None
    return sid, g


# ===================== 页面与静态资源 =====================
@app.route("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


# ===================== 核心工具 =====================
def load_all_projects():
    projects = []
    if os.path.isdir(ASSETS_DIR):
        for item in os.listdir(ASSETS_DIR):
            if os.path.isdir(os.path.join(ASSETS_DIR, item)) and not item.startswith("."):
                projects.append(item)
    return projects


def load_seiyu_from_multi_projects(project_names, rng=None):
    """加载【多个企划】的所有声优；跨企划同名声优只保留第一个（避免选项重复名字/图片错乱）"""
    rng = rng or random
    seiyu_list = []
    seen_names = set()
    for proj in project_names:
        proj_path = os.path.join(ASSETS_DIR, proj)
        if not os.path.isdir(proj_path):
            continue

        for folder_name in os.listdir(proj_path):
            folder_path = os.path.join(proj_path, folder_name)
            if not os.path.isdir(folder_path):
                continue
            seiyu_name = folder_name.split('-', 1)[-1].strip()
            if seiyu_name in seen_names:
                continue  # 同名只保留第一份
            seen_names.add(seiyu_name)
            if has_image(folder_path):
                seiyu_list.append({
                    "name": seiyu_name,
                    "path": folder_path
                })

    rng.shuffle(seiyu_list)
    return seiyu_list


def has_image(path):
    for f in os.listdir(path):
        if f.lower().endswith(SUPPORT_FORMATS):
            return True
    return False


def get_random_image_safe(folder_path, rel=False, rng=None):
    """从文件夹随机取一张图；rel=True 时返回相对 assets 的路径（统一用正斜杠，兼容 Linux/Windows）"""
    rng = rng or random
    images = []
    for f in os.listdir(folder_path):
        if f.lower().endswith(SUPPORT_FORMATS):
            images.append(os.path.join(folder_path, f))
    if not images:
        return None
    chosen = rng.choice(images)
    if rel:
        # Windows 生成的是反斜杠，Linux 上无法解析；统一转正斜杠
        return os.path.relpath(chosen, ASSETS_DIR).replace(os.sep, "/")
    return chosen


def is_image_valid(img_path):
    try:
        with open(img_path, 'rb') as f:
            img = Image.open(f)
            img.verify()
        return True
    except Exception:
        return False


def resize_and_pad(img_path, target_size):
    """缩放并白底填充为正方形，与原版行为一致"""
    try:
        img_path = os.path.abspath(img_path)
        with open(img_path, 'rb') as f:
            img = Image.open(f).convert("RGB")
        img.thumbnail((target_size, target_size), Image.Resampling.LANCZOS)
        background = Image.new("RGB", (target_size, target_size), "white")
        offset = ((target_size - img.width) // 2, (target_size - img.height) // 2)
        background.paste(img, offset)
        return background
    except Exception:
        return Image.new("RGB", (target_size, target_size), "#f0f0f0")


def pick_valid_image_rel(folder_path, rng=None):
    """优先取一张校验通过的图片，返回相对 assets 的路径"""
    rng = rng or random
    for _ in range(10):
        rel = get_random_image_safe(folder_path, rel=True, rng=rng)
        if rel and is_image_valid(os.path.join(ASSETS_DIR, rel)):
            return rel
    return get_random_image_safe(folder_path, rel=True, rng=rng)


def img_url(rel_path, size=IMAGE_SIZE):
    token = base64.urlsafe_b64encode(rel_path.encode("utf-8")).decode("ascii")
    return f"/api/img/{token}?size={size}"


# ===================== 出题逻辑（与原版一致） =====================
def generate_single_question(game):
    """生成单题：正确答案绝不重复"""
    rng = game["rng"]
    max_retry = 50
    for _ in range(max_retry):
        q_type = 1 if (game["current_q_idx"] % 2 == 0) else 2

        # 可选池：排除已经当过正确答案的声优
        available_for_correct = [s for s in game["seiyu_list"] if s not in game["used_correct_seiyu"]]
        if len(available_for_correct) < 1:
            available_for_correct = game["seiyu_list"]  # 极端情况兜底

        # 干扰项可以随便选
        choices = rng.sample(game["seiyu_list"], 4)
        correct = rng.choice(available_for_correct)

        # 把正确答案强制加入选项
        if correct not in choices:
            choices[0] = correct
        rng.shuffle(choices)

        img_rel = get_random_image_safe(correct["path"], rel=True, rng=rng)
        if not img_rel or not is_image_valid(os.path.join(ASSETS_DIR, img_rel)):
            continue

        if q_type == 1:
            valid = True
            for c in choices:
                ip = get_random_image_safe(c["path"], rel=True, rng=rng)
                if not ip or not is_image_valid(os.path.join(ASSETS_DIR, ip)):
                    valid = False
                    break
            if not valid:
                continue

        # 标记该声优已使用
        game["used_correct_seiyu"].append(correct)
        return {
            "type": q_type,
            "correct": correct,
            "choices": choices,
            "image": img_rel,
        }

    # 终极兜底
    fallback_correct = rng.choice(game["seiyu_list"])
    return {
        "type": 1,
        "correct": fallback_correct,
        "choices": rng.sample(game["seiyu_list"], 4),
        "image": get_random_image_safe(fallback_correct["path"], rel=True, rng=rng),
    }


def generate_mixed_questions(game):
    """生成 12 题"""
    game["questions"] = []
    game["used_correct_seiyu"] = []  # 重置
    for i in range(TOTAL_QUESTIONS):
        game["current_q_idx"] = i
        q = generate_single_question(game)
        if q:
            game["questions"].append(q)
    game["current_q_idx"] = 0
    game["score"] = 0


def build_question_payload(game):
    """构造当前题目的前端数据"""
    if game["current_q_idx"] >= TOTAL_QUESTIONS:
        return None
    q = game["questions"][game["current_q_idx"]]
    number = game["current_q_idx"] + 1
    option_keys = ["A", "B", "C", "D"]

    if q["type"] == 1:
        # 看名字选图：给出名字，展示 4 张图片
        choices = []
        for i, c in enumerate(q["choices"]):
            rel = pick_valid_image_rel(c["path"], rng=game["rng"])
            choices.append({"key": option_keys[i], "image": img_url(rel) if rel else ""})
        return {
            "type": 1,
            "number": number,
            "total": TOTAL_QUESTIONS,
            "prompt": "请选出：",
            "target_name": q["correct"]["name"],
            "choices": choices,
            "score": game["score"],
        }

    # 看图选名字：展示 1 张图片，给出 4 个名字
    rel = q["image"]
    choices = [{"key": option_keys[i], "name": c["name"]} for i, c in enumerate(q["choices"])]
    return {
        "type": 2,
        "number": number,
        "total": TOTAL_QUESTIONS,
        "prompt": "这张图片对应的声优是？",
        "image": img_url(rel) if rel else "",
        "choices": choices,
        "score": game["score"],
    }


# ===================== API =====================
@app.before_request
def _periodic_session_cleanup():
    """每隔一段时间回收超时无活动的会话，避免长期运行内存膨胀"""
    now = time.time()
    if now - _last_cleanup_time[0] > 60:
        _last_cleanup_time[0] = now
        _cleanup_sessions()


@app.route("/api/projects")
def api_projects():
    return jsonify({"projects": load_all_projects()})


@app.route("/api/game/start", methods=["POST"])
def api_start():
    data = request.get_json(silent=True) or {}
    project_names = data.get("projects") or []
    xd_mode = bool(data.get("xd_mode", False))

    all_projects = load_all_projects()
    selected = [p for p in project_names if p in all_projects]
    if not selected:
        return jsonify({"error": "请至少选择一个企划！"}), 400

    # 每个玩家一局独立状态，互不干扰（修复多用户并发互相覆盖导致的 400 / 卡死）
    sid = uuid.uuid4().hex
    game = make_game_state()
    seiyu_list = load_seiyu_from_multi_projects(selected, rng=game["rng"])
    if len(seiyu_list) < 4:
        return jsonify({"error": "所有选中企划的声优不足4人！"}), 400

    _put_game(sid, game)

    game["selected_projects"] = selected
    game["seiyu_list"] = seiyu_list
    game["xd_mode"] = xd_mode
    game["start_time"] = time.time()

    # ✅ 每轮开始清空已用正确答案记录
    game["used_correct_seiyu"] = []
    generate_mixed_questions(game)
    game["current_q_idx"] = 0
    game["score"] = 0
    game["accepting_answers"] = True

    return jsonify({
        "ok": True,
        "session_id": sid,
        "total_questions": TOTAL_QUESTIONS,
        "per_score": PER_SCORE,
        "xd_mode": xd_mode,
        "start_time": game["start_time"],
        "question": build_question_payload(game),
    })


@app.route("/api/game/question")
def api_question():
    _, game = get_session_game()
    if game is None:
        return jsonify({"error": "会话已失效，请重新开始游戏"}), 400
    with game["lock"]:
        game["accepting_answers"] = True
        payload = build_question_payload(game)
    return jsonify({"question": payload})


@app.route("/api/game/answer", methods=["POST"])
def api_answer():
    _, game = get_session_game()
    if game is None:
        return jsonify({"error": "会话已失效，请重新开始游戏"}), 400

    data = request.get_json(silent=True) or {}
    idx = data.get("idx")
    if not isinstance(idx, int) or not (0 <= idx < 4):
        return jsonify({"error": "无效选项"}), 400

    # 单局状态锁：防止同一玩家的并发请求（如快速连点）竞态导致
    # 同一题被判两次 / 题目序号错乱
    with game["lock"]:
        if not game["accepting_answers"] or game["current_q_idx"] >= TOTAL_QUESTIONS:
            return jsonify({"error": "当前无法作答"}), 400

        q = game["questions"][game["current_q_idx"]]
        correct = q["correct"]
        select = q["choices"][idx]
        option_keys = ["A", "B", "C", "D"]
        correct_opt = option_keys[q["choices"].index(correct)]

        is_correct = select == correct
        if is_correct:
            game["score"] += PER_SCORE

        game["current_q_idx"] += 1
        game["accepting_answers"] = False

    return jsonify({
        "correct": is_correct,
        "correct_option": correct_opt,
        "selected_option": option_keys[idx],
        "score": game["score"],
    })


@app.route("/api/game/result", methods=["POST"])
def api_result():
    _, game = get_session_game()
    if game is None:
        return jsonify({"error": "会话已失效，请重新开始游戏"}), 400

    with game["lock"]:
        total_time = int(time.time() - game["start_time"])
        total = TOTAL_QUESTIONS * PER_SCORE

        final_score = game["score"]
        xd_tip = ""
        xd_applied = False

        if game["xd_mode"]:
            correct_count = game["score"] // PER_SCORE
            rate = correct_count / TOTAL_QUESTIONS
            if rate < 0.5:
                final_score = 0
                xd_applied = True
                xd_tip = "\n⚠️😈  XD 模式：正确率低于50%，你已被斩杀！"

        message = (
            "🎮 答题完成！\n\n"
            f"得分：{final_score}/{total}\n"
            f"总用时：{total_time} 秒"
            f"{xd_tip}"
        )

    return jsonify({
        "score": final_score,
        "raw_score": game["score"],
        "total": total,
        "total_time": total_time,
        "xd_mode": game["xd_mode"],
        "xd_applied": xd_applied,
        "xd_tip": xd_tip,
        "message": message,
    })


@app.route("/api/img/<token>")
def api_image(token):
    try:
        rel_path = base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8")
        # 统一用正斜杠拼接，兼容 Linux 服务器（修复反斜杠导致图片 404）
        rel_path = rel_path.replace("\\", "/")
    except Exception:
        abort(400)

    size = request.args.get("size", IMAGE_SIZE, type=int)
    if size <= 0 or size > 1000:
        size = IMAGE_SIZE

    abs_path = os.path.normpath(os.path.join(ASSETS_DIR, rel_path))
    # 防目录穿越
    if not os.path.realpath(abs_path).startswith(os.path.realpath(ASSETS_DIR)):
        abort(400)
    if not os.path.isfile(abs_path):
        abort(404)

    key = (abs_path, size)
    data = _image_cache.get(key)
    if data is None:
        img = resize_and_pad(abs_path, size)
        buf = BytesIO()
        img.save(buf, format="PNG")
        data = buf.getvalue()
        _image_cache[key] = data

    return Response(data, mimetype="image/png", headers={"Cache-Control": "max-age=3600"})


# ===================== 启动程序 =====================
def main():
    if not load_all_projects():
        print("⚠️  未检测到 assets 企划文件夹！请确保 assets 目录存在并包含企划文件夹。")

    # 生产部署：PRODUCTION=1 时用 waitress（真正的生产 WSGI 服务器，多线程并发，可对外访问）
    if os.environ.get("PRODUCTION") == "1":
        try:
            from waitress import serve
        except ImportError:
            print("⚠️  生产模式需要 waitress，请先安装：pip install waitress")
            print("   回退到 Flask 开发服务器（仅用于测试，不推荐生产使用）。")
            app.run(host="0.0.0.0", port=5000, debug=False)
            return
        print("🚀  生产模式（waitress）：监听 0.0.0.0:5000")
        serve(app, host="0.0.0.0", port=5000, threads=8)
        return

    # 本地开发：自动打开浏览器
    threading.Timer(1.0, lambda: webbrowser.open("http://127.0.0.1:5000")).start()
    print("🎤  趣味猜女声优器（网页版）已启动： http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=False)


if __name__ == "__main__":
    main()
