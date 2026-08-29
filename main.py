import os
import random
import time
import tkinter as tk
from tkinter import ttk, messagebox
from PIL import ImageTk, Image

# ===================== 配置区 =====================
SUPPORT_FORMATS = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.avif')
TOTAL_QUESTIONS = 12
PER_SCORE = 10
WINDOW_SIZE = "1200x1000"
IMAGE_SIZE = 300

# ===================== 【增强强随机】设置真随机种子 =====================
random.seed(time.time())


# ===================== 核心工具类 =====================
class SeiyuQuizApp:
    def __init__(self, root):
        self.root = root
        self.root.title("趣味猜女声优器")
        self.set_initial_window_size()
        self.root.resizable(True, True)

        self.all_projects = []
        self.selected_projects = []
        self.seiyu_list = []
        self.questions = []
        self.current_q_idx = 0
        self.score = 0

        # ✅ 【核心】记录本轮已经用过的正确答案声优（永不重复）
        self.used_correct_seiyu = []

        # XD 模式开关
        self.xd_mode = tk.BooleanVar(value=False)

        # 计时相关变量
        self.start_time = 0
        self.timer_running = False
        self.timer_label = None
        self.scroll_canvas = None
        self.page = None
        self.accepting_answers = False
        self.focus_job_ids = []

        self.load_all_projects()
        self.show_project_select_page()

    def load_all_projects(self):
        self.all_projects = []
        for item in os.listdir("./assets"):
            if os.path.isdir(os.path.join("./assets", item)) and not item.startswith("."):
                self.all_projects.append(item)
        if not self.all_projects:
            messagebox.showerror("错误", "未检测到任何企划文件夹！请创建bangdream等文件夹")
            self.root.quit()

    def set_initial_window_size(self):
        width_text, height_text = WINDOW_SIZE.lower().split("x", 1)
        window_width = int(width_text)
        window_height = int(height_text)

        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        max_width = max(480, screen_width - 80)
        max_height = max(480, screen_height - 120)

        self.root.geometry(f"{min(window_width, max_width)}x{min(window_height, max_height)}")

    # 加载【多个企划】的所有声优
    def load_seiyu_from_multi_projects(self, project_names):
        self.seiyu_list = []
        for proj in project_names:
            proj_path = os.path.join("./assets", proj)
            if not os.path.isdir(proj_path):
                continue

            for folder_name in os.listdir(proj_path):
                folder_path = os.path.join(proj_path, folder_name)
                if not os.path.isdir(folder_path):
                    continue
                seiyu_name = folder_name.split('-', 1)[-1].strip()
                if self.has_image(folder_path):
                    self.seiyu_list.append({
                        "name": seiyu_name,
                        "path": folder_path
                    })

        random.shuffle(self.seiyu_list)

        if len(self.seiyu_list) < 4:
            messagebox.showerror("错误", "所有选中企划的声优不足4人！")
            return False
        return True

    def has_image(self, path):
        for f in os.listdir(path):
            if f.lower().endswith(SUPPORT_FORMATS):
                return True
        return False

    # ———————————————— 安全取图 ————————————————
    def get_random_image_safe(self, folder_path):
        images = []
        for f in os.listdir(folder_path):
            if f.lower().endswith(SUPPORT_FORMATS):
                images.append(os.path.join(folder_path, f))
        if not images:
            return None
        return random.choice(images)

    def is_image_valid(self, img_path):
        try:
            with open(img_path, 'rb') as f:
                img = Image.open(f)
                img.verify()
            return True
        except:
            return False

    def resize_and_pad(self, img_path, target_size):
        try:
            img_path = os.path.abspath(img_path)
            with open(img_path, 'rb') as f:
                img = Image.open(f).convert("RGB")
            img.thumbnail((target_size, target_size), Image.Resampling.LANCZOS)
            background = Image.new("RGB", (target_size, target_size), "white")
            offset = ((target_size - img.width) // 2, (target_size - img.height) // 2)
            background.paste(img, offset)
            return background
        except Exception as e:
            return Image.new("RGB", (target_size, target_size), "#f0f0f0")

    # ===================== 计时 =====================
    def update_timer(self):
        if self.timer_running:
            elapsed = int(time.time() - self.start_time)
            self.timer_label.config(text=f"用时：{elapsed} 秒")
            self.root.after(1000, self.update_timer)

    def show_project_select_page(self):
        self.clear_window()
        self.create_scrollable_page()

        tk.Label(self.page, text="选择企划（Ctrl 多选）", font=("微软雅黑", 24)).pack(pady=20)

        self.project_listbox = tk.Listbox(
            self.page, font=("微软雅黑", 14), width=30, height=8,
            selectmode=tk.MULTIPLE
        )
        for proj in self.all_projects:
            self.project_listbox.insert(tk.END, proj)
        self.project_listbox.pack(pady=10)

        tk.Checkbutton(
            self.page, text="开启 XD 模式（正确率低于50%得分归零）",
            font=("微软雅黑", 14), variable=self.xd_mode,
            fg="red"
        ).pack(pady=10)

        def start_game():
            selected_indices = self.project_listbox.curselection()
            self.selected_projects = [self.all_projects[i] for i in selected_indices]
            if not self.selected_projects:
                messagebox.showwarning("提示", "请至少选择一个企划！")
                return

            if not self.load_seiyu_from_multi_projects(self.selected_projects):
                return

            # ✅ 每轮开始清空已用正确答案记录
            self.used_correct_seiyu = []

            self.start_time = time.time()
            self.timer_running = True
            self.generate_mixed_questions()
            self.show_question_page()
            self.update_timer()

        tk.Button(
            self.page, text="开始游戏", font=("微软雅黑", 18),
            command=start_game, width=18, height=2
        ).pack(pady=20)

    # ===================== ✅ 【核心修改】生成单题：正确答案绝不重复 =====================
    def generate_single_question(self):
        max_retry = 50
        for _ in range(max_retry):
            q_type = 1 if (self.current_q_idx % 2 == 0) else 2

            # 可选池：排除已经当过正确答案的声优
            available_for_correct = [s for s in self.seiyu_list if s not in self.used_correct_seiyu]
            if len(available_for_correct) < 1:
                available_for_correct = self.seiyu_list  # 极端情况兜底

            # 干扰项可以随便选
            choices = random.sample(self.seiyu_list, 4)
            # 正确答案必须从未用过
            correct = random.choice(available_for_correct)

            # 把正确答案强制加入选项
            if correct not in choices:
                choices[0] = correct
            random.shuffle(choices)

            img_path = self.get_random_image_safe(correct["path"])
            if not img_path or not self.is_image_valid(img_path):
                continue

            if q_type == 1:
                valid = True
                for c in choices:
                    ip = self.get_random_image_safe(c["path"])
                    if not ip or not self.is_image_valid(ip):
                        valid = False
                        break
                if not valid:
                    continue

            # ✅ 标记该声优已使用
            self.used_correct_seiyu.append(correct)
            return {
                "type": q_type,
                "correct": correct,
                "choices": choices,
                "image": img_path
            }

        # 终极兜底
        fallback_correct = random.choice(self.seiyu_list)
        return {
            "type": 1,
            "correct": fallback_correct,
            "choices": random.sample(self.seiyu_list, 4),
            "image": self.get_random_image_safe(fallback_correct["path"])
        }

    # ===================== 生成20题 =====================
    def generate_mixed_questions(self):
        self.questions = []
        self.used_correct_seiyu = []  # 重置
        for i in range(TOTAL_QUESTIONS):
            self.current_q_idx = i
            q = self.generate_single_question()
            if q:
                self.questions.append(q)
        self.current_q_idx = 0
        self.score = 0

    # ===================== 显示题目 =====================
    def show_question_page(self):
        self.clear_window()
        self.create_scrollable_page()
        self.accepting_answers = True

        self.timer_label = tk.Label(self.page, text="用时：0 秒", font=("微软雅黑", 14))
        self.timer_label.pack(anchor="ne", padx=20, pady=(10, 0))

        if self.current_q_idx >= TOTAL_QUESTIONS:
            self.show_result()
            return

        q = self.questions[self.current_q_idx]

        if q["type"] == 2:
            if not q["image"] or not self.is_image_valid(q["image"]):
                new_q = self.generate_single_question()
                if new_q:
                    self.questions[self.current_q_idx] = new_q
                self.show_question_page()
                return

        tk.Label(self.page, text=f"第 {self.current_q_idx + 1}/{TOTAL_QUESTIONS} 题", font=("微软雅黑", 20)).pack(pady=10)

        if q["type"] == 1:
            tk.Label(self.page, text=f"请选出：{q['correct']['name']}", font=("微软雅黑", 18, "bold"), fg="red").pack(pady=10)
            self.show_image_choices(q)
        else:
            tk.Label(self.page, text="这张图片对应的声优是？", font=("微软雅黑", 18, "bold"), fg="blue").pack(pady=10)
            frame_img = tk.Frame(self.page)
            frame_img.pack(pady=10)
            img = self.resize_and_pad(q["image"], IMAGE_SIZE)
            self.q_img = ImageTk.PhotoImage(img)
            tk.Label(frame_img, image=self.q_img).pack()
            self.show_name_choices(q)

        tk.Label(self.page, text=f"当前分数：{self.score}", font=("微软雅黑", 16)).pack(pady=10)
        self.bind_answer_keys()
        self.focus_game_window()

    def show_image_choices(self, q):
        frame = tk.Frame(self.page)
        frame.pack(pady=20)
        self.img_refs = []
        positions = [(0, 0), (0, 1), (1, 0), (1, 1)]
        labels = ["A", "B", "C", "D"]

        for i in range(4):
            img_path = self.get_random_image_safe(q["choices"][i]["path"])
            if not img_path or not self.is_image_valid(img_path):
                new_q = self.generate_single_question()
                if new_q:
                    self.questions[self.current_q_idx] = new_q
                self.show_question_page()
                return

            img = self.resize_and_pad(img_path, IMAGE_SIZE)
            photo = ImageTk.PhotoImage(img)
            self.img_refs.append(photo)
            btn = tk.Button(frame, image=photo, text=labels[i], compound="top", font=("微软雅黑", 14, "bold"),
                            command=lambda idx=i: self.answer(idx))
            btn.grid(row=positions[i][0], column=positions[i][1], padx=30, pady=20)

    def show_name_choices(self, q):
        frame = tk.Frame(self.page)
        frame.pack(pady=20)
        labels = ["A", "B", "C", "D"]
        for i in range(4):
            text = f"{labels[i]}、{q['choices'][i]['name']}"
            btn = tk.Button(frame, text=text, font=("微软雅黑", 16), width=20, height=2,
                            command=lambda idx=i: self.answer(idx))
            btn.grid(row=i, column=0, pady=10)

    # ===================== 判题 =====================
    def answer(self, idx):
        if not self.accepting_answers:
            return
        self.accepting_answers = False

        q = self.questions[self.current_q_idx]
        correct = q["correct"]
        select = q["choices"][idx]
        option_list = ["A", "B", "C", "D"]
        correct_opt = option_list[q["choices"].index(correct)]

        if select == correct:
            self.score += PER_SCORE
            messagebox.showinfo("正确", "✅ 答对啦！")
        else:
            messagebox.showerror("错误", f"❌ 正确答案：{correct_opt}")

        self.current_q_idx += 1
        self.show_question_page()

    def bind_answer_keys(self):
        for i in range(4):
            self.root.bind_all(f"<KeyPress-{i + 1}>", lambda e, idx=i: self.answer(idx))
            self.root.bind_all(f"<KeyPress-KP_{i + 1}>", lambda e, idx=i: self.answer(idx))

    def unbind_answer_keys(self):
        for i in range(4):
            self.root.unbind_all(f"<KeyPress-{i + 1}>")
            self.root.unbind_all(f"<KeyPress-KP_{i + 1}>")

    def focus_game_window(self):
        self.cancel_focus_jobs()

        def focus_target():
            if not self.root.winfo_exists():
                return
            self.root.lift()
            self.root.focus_force()
            if self.scroll_canvas and self.scroll_canvas.winfo_exists():
                self.scroll_canvas.focus_set()
            else:
                self.root.focus_set()

        self.focus_job_ids = [
            self.root.after_idle(focus_target),
            self.root.after(80, focus_target),
            self.root.after(200, focus_target)
        ]

    def cancel_focus_jobs(self):
        for job_id in self.focus_job_ids:
            try:
                self.root.after_cancel(job_id)
            except tk.TclError:
                pass
        self.focus_job_ids = []

    # ===================== 结算 =====================
    def show_result(self):
        self.timer_running = False
        total_time = int(time.time() - self.start_time)
        total = TOTAL_QUESTIONS * PER_SCORE

        final_score = self.score
        xd_tip = ""

        if self.xd_mode.get():
            correct_count = self.score // PER_SCORE
            rate = correct_count / TOTAL_QUESTIONS
            if rate < 0.5:
                final_score = 0
                xd_tip = "\n⚠️😈  XD 模式：正确率低于50%，你已被斩杀！"

        messagebox.showinfo(
            "游戏结束",
            f"🎮 答题完成！\n\n"
            f"得分：{final_score}/{total}\n"
            f"总用时：{total_time} 秒"
            f"{xd_tip}"
        )

        self.show_project_select_page()

    def clear_window(self):
        self.root.unbind_all("<MouseWheel>")
        self.root.unbind_all("<Button-4>")
        self.root.unbind_all("<Button-5>")
        self.unbind_answer_keys()
        self.accepting_answers = False
        self.cancel_focus_jobs()
        for widget in self.root.winfo_children():
            widget.destroy()
        self.scroll_canvas = None
        self.page = None

    def create_scrollable_page(self):
        outer_frame = tk.Frame(self.root)
        outer_frame.pack(fill="both", expand=True)

        canvas = tk.Canvas(outer_frame, highlightthickness=0, takefocus=True)
        scrollbar = ttk.Scrollbar(outer_frame, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        page = tk.Frame(canvas)
        page_window = canvas.create_window((0, 0), window=page, anchor="nw")

        def update_scroll_region(event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def fit_page_width(event):
            canvas.itemconfig(page_window, width=event.width)
            update_scroll_region()

        page.bind("<Configure>", update_scroll_region)
        canvas.bind("<Configure>", fit_page_width)

        self.scroll_canvas = canvas
        self.page = page
        self.root.bind_all("<MouseWheel>", self.on_mousewheel)
        self.root.bind_all("<Button-4>", self.on_mousewheel)
        self.root.bind_all("<Button-5>", self.on_mousewheel)
        self.root.after_idle(lambda: canvas.yview_moveto(0))
        return page

    def on_mousewheel(self, event):
        canvas = self.scroll_canvas
        if not canvas or not canvas.winfo_exists():
            return

        scroll_region = canvas.bbox("all")
        if not scroll_region:
            return

        content_height = scroll_region[3] - scroll_region[1]
        if content_height <= canvas.winfo_height():
            return

        if getattr(event, "num", None) == 4:
            canvas.yview_scroll(-1, "units")
        elif getattr(event, "num", None) == 5:
            canvas.yview_scroll(1, "units")
        else:
            delta = getattr(event, "delta", 0)
            if not delta:
                return
            if abs(delta) >= 120:
                units = int(-delta / 120)
            else:
                units = -1 if delta > 0 else 1
            canvas.yview_scroll(units, "units")


# ===================== 启动程序 =====================
if __name__ == "__main__":
    root = tk.Tk()
    app = SeiyuQuizApp(root)
    root.mainloop()
