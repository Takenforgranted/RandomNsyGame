// ===================== 状态 =====================
const state = {
  page: 'select',
  xdMode: false,
  startTime: 0,
  total: 12,
  perScore: 10,
  score: 0,
  accepting: false,
  timerInterval: null,
};

const modal = document.getElementById('modal');
const modalTitle = document.getElementById('modalTitle');
const modalMessage = document.getElementById('modalMessage');

// ===================== 工具 =====================
function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.error || `请求失败（${res.status}）`);
  }
  return data;
}

function showPage(name) {
  state.page = name;
  document.getElementById('selectPage').classList.toggle('hidden', name !== 'select');
  document.getElementById('quizPage').classList.toggle('hidden', name !== 'quiz');
  window.scrollTo(0, 0);
}

function showError(msg) {
  const el = document.getElementById('selectError');
  el.textContent = msg;
  el.classList.remove('hidden');
}

// ===================== 企划选择页 =====================
async function loadProjects() {
  try {
    const data = await api('/api/projects');
    renderProjectList(data.projects || []);
  } catch (e) {
    document.getElementById('emptyHint').classList.remove('hidden');
  }
}

function renderProjectList(projects) {
  const container = document.getElementById('projectList');
  const emptyHint = document.getElementById('emptyHint');
  container.innerHTML = '';
  if (!projects.length) {
    emptyHint.classList.remove('hidden');
    return;
  }
  emptyHint.classList.add('hidden');
  projects.forEach((p) => {
    const label = document.createElement('label');
    label.className = 'project-item';
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.value = p;
    cb.className = 'project-check';
    const span = document.createElement('span');
    span.textContent = p;
    label.appendChild(cb);
    label.appendChild(span);
    container.appendChild(label);
  });
}

async function startGame() {
  const projects = Array.from(document.querySelectorAll('.project-check:checked')).map((cb) => cb.value);
  if (projects.length === 0) {
    showError('请至少选择一个企划！');
    return;
  }
  document.getElementById('selectError').classList.add('hidden');
  const xd = document.getElementById('xdMode').checked;
  try {
    const data = await api('/api/game/start', {
      method: 'POST',
      body: JSON.stringify({ projects, xd_mode: xd }),
    });
    state.xdMode = data.xd_mode;
    state.startTime = data.start_time;
    state.total = data.total_questions;
    state.perScore = data.per_score;
    state.score = 0;
    showPage('quiz');
    startTimer();
    renderQuestion(data.question);
  } catch (e) {
    showError(e.message);
  }
}

// ===================== 计时 =====================
function startTimer() {
  stopTimer();
  updateTimerLabel();
  state.timerInterval = setInterval(updateTimerLabel, 1000);
}

function updateTimerLabel() {
  const elapsed = Math.max(0, Math.floor(Date.now() / 1000 - state.startTime));
  document.getElementById('timerLabel').textContent = `用时：${elapsed} 秒`;
}

function stopTimer() {
  if (state.timerInterval) {
    clearInterval(state.timerInterval);
    state.timerInterval = null;
  }
}

// ===================== 答题页 =====================
function renderQuestion(q) {
  state.accepting = true;
  state.score = q.score;
  document.getElementById('questionNumber').textContent = `第 ${q.number}/${q.total} 题`;
  document.getElementById('scoreLabel').textContent = `当前分数：${q.score}`;

  const promptArea = document.getElementById('promptArea');
  const choicesArea = document.getElementById('choicesArea');
  promptArea.innerHTML = '';
  choicesArea.innerHTML = '';

  if (q.type === 1) {
    // 看名字选图：展示 4 张图片
    const prompt = document.createElement('div');
    prompt.className = 'prompt-type1';
    prompt.innerHTML = `${escapeHtml(q.prompt)}<span class="target-name">${escapeHtml(q.target_name)}</span>`;
    promptArea.appendChild(prompt);

    choicesArea.className = 'choices-area grid-2x2';
    q.choices.forEach((c, i) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'choice-img';
      btn.dataset.idx = i;
      const tag = document.createElement('span');
      tag.className = 'choice-tag';
      tag.textContent = c.key;
      const img = document.createElement('img');
      img.src = c.image;
      img.alt = `选项${c.key}`;
      img.loading = 'lazy';
      btn.appendChild(tag);
      btn.appendChild(img);
      btn.addEventListener('click', () => answer(i, btn));
      choicesArea.appendChild(btn);
    });
  } else {
    // 看图选名字：展示 1 张图片 + 4 个名字
    const prompt = document.createElement('div');
    prompt.className = 'prompt-type2';
    prompt.textContent = q.prompt;
    promptArea.appendChild(prompt);

    const imgWrap = document.createElement('div');
    imgWrap.className = 'quiz-image';
    const img = document.createElement('img');
    img.src = q.image;
    img.alt = '题目图片';
    imgWrap.appendChild(img);
    promptArea.appendChild(imgWrap);

    choicesArea.className = 'choices-area list';
    q.choices.forEach((c, i) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'choice-name';
      btn.dataset.idx = i;
      btn.textContent = `${c.key}、${c.name}`;
      btn.addEventListener('click', () => answer(i, btn));
      choicesArea.appendChild(btn);
    });
  }
}

async function answer(idx, btn) {
  if (!state.accepting) return;
  state.accepting = false;
  document.querySelectorAll('#choicesArea button').forEach((b) => { b.disabled = true; });
  try {
    const data = await api('/api/game/answer', {
      method: 'POST',
      body: JSON.stringify({ idx }),
    });
    state.score = data.score;
    if (btn) btn.classList.add('chosen');
    showFeedback(data.correct, data.correct_option, data.selected_option);
  } catch (e) {
    state.accepting = true;
    document.querySelectorAll('#choicesArea button').forEach((b) => { b.disabled = false; });
    alert(e.message);
  }
}

function showFeedback(correct, correctOpt, selectedOpt) {
  modalTitle.className = 'modal-title ' + (correct ? 'correct-title' : 'wrong-title');
  modalTitle.textContent = correct ? '正确' : '错误';
  modalMessage.textContent = correct ? '✅ 答对啦！' : `❌ 正确答案：${correctOpt}`;
  modal.dataset.ready = 'answer';
  modal.classList.remove('hidden');
}

async function nextQuestion() {
  try {
    const data = await api('/api/game/question');
    if (!data.question) {
      finishGame();
    } else {
      renderQuestion(data.question);
    }
  } catch (e) {
    alert(e.message);
  }
}

async function finishGame() {
  stopTimer();
  try {
    const data = await api('/api/game/result', { method: 'POST' });
    showResult(data);
  } catch (e) {
    alert(e.message);
  }
}

function showResult(data) {
  modalTitle.className = 'modal-title result-title';
  modalTitle.textContent = '游戏结束';
  modalMessage.textContent = data.message;
  modal.dataset.ready = 'result';
  modal.classList.remove('hidden');
}

function backToSelect() {
  modal.classList.add('hidden');
  stopTimer();
  document.getElementById('timerLabel').textContent = '用时：0 秒';
  showPage('select');
}

// ===================== 弹窗按钮 =====================
document.getElementById('modalOk').addEventListener('click', () => {
  if (modal.dataset.ready === 'result') {
    backToSelect();
  } else {
    modal.classList.add('hidden');
    nextQuestion();
  }
});

// ===================== 键盘操作 =====================
document.addEventListener('keydown', (e) => {
  if (!modal.classList.contains('hidden')) {
    if (e.key === 'Enter') {
      document.getElementById('modalOk').click();
    }
    return;
  }
  if (state.page !== 'quiz' || !state.accepting) return;
  const idx = ['1', '2', '3', '4'].indexOf(e.key);
  if (idx >= 0) {
    const btn = document.querySelector(`#choicesArea button[data-idx="${idx}"]`);
    if (btn && !btn.disabled) answer(idx, btn);
  }
});

// ===================== 初始化 =====================
document.getElementById('startBtn').addEventListener('click', startGame);
loadProjects();
