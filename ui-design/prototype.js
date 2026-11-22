const byId = (id) => document.getElementById(id);
const pages = ["prepare", "facts", "answer", "review"];
const fields = ["company", "role", "round", "language"];

const documents = [
  { id: "resume", name: "简历摘录", type: "示例简历", text: "回答经历题时，先交代目标和你的职责，再说明你采取的行动，以及如何验证结果。" },
  { id: "notes", name: "面试准备笔记", type: "示例笔记", text: "遇到优先级冲突时，先确认截止时间、影响范围和依赖，再约定交付顺序。" },
];

const questions = [
  {
    label: "自我介绍",
    question: "请用一分钟介绍一下自己。",
    first: "我想先讲一段和这个岗位最相关的经历，说明我负责什么，再讲我怎么验证结果。",
    points: ["挑一段与岗位要求最接近的经历，先交代目标和自己的职责。", "最后用一句话说清这段经历为什么适合当前岗位。"],
    detail: "预留一个可以追问的细节，比如你做过的一个取舍，或你用来核对结果的方法。",
    source: "resume",
  },
  {
    label: "任务优先级",
    question: "两件紧急的事同时找上你，你怎么安排？",
    first: "我会先确认两个任务各自的截止时间和影响，看看哪件事会卡住更多人。",
    points: ["如果一个任务挡住了别人的进度，我先处理能解除阻塞的部分。", "我会把交付顺序和预期时间同步给相关人。"],
    detail: "需求有变时，我会更新交付承诺，并说明哪一部分需要延期。",
    source: "notes",
  },
  {
    label: "团队协作",
    question: "团队对方案有分歧时，你怎么处理？",
    first: "我会先问清楚大家最担心的是什么，再一起定一个能验证分歧的办法。",
    points: ["如果争议在成本或效果上，我会给两种方案用同一套标准。", "定下来后，我会确认谁来做、什么时候回看结果。"],
    detail: "分歧短时间内解决不了时，可以先做一个可回退的小验证。",
  },
];

const selectedDocs = new Set();
const turns = [];
let page = "prepare";
let currentDoc = documents[0].id;
let activeSession = null;
let sessionNumber = 0;
let lastTarget = "";
let savedOnly = false;
const waitingAnswer = "等待问题";
const selectedQuestion = () => Number(document.querySelector('input[name="question"]:checked').value);

function notify(message) {
  byId("notice").textContent = message;
}

function refreshPreview() {
  const target = [byId("company").value.trim(), byId("round").value].filter(Boolean).join(" · ");
  byId("preview-job").textContent = byId("role").value.trim() || "通用面试练习";
  byId("preview-company").textContent = target;
  byId("preview-company").hidden = !target;
  byId("preview-language").textContent = byId("language").value;
  byId("preview-materials").textContent = documents.filter((doc) => selectedDocs.has(doc.id)).map((doc) => doc.name).join("、") || "未选用";
}

function renderPrepare() {
  byId("materials").innerHTML = documents.map((doc) => `
    <label class="material">
      <input type="checkbox" value="${doc.id}" ${selectedDocs.has(doc.id) ? "checked" : ""} ${activeSession ? "disabled" : ""}>
      <span><strong>${doc.name}</strong><small>${doc.type}</small></span>
    </label>`).join("");
  for (const id of fields) byId(id).disabled = Boolean(activeSession);
  byId("start-text").textContent = activeSession ? "结束练习" : "开始练习";
  byId("start").classList.toggle("primary", !activeSession);
  byId("start").querySelector(".start-arrow").hidden = Boolean(activeSession);
  refreshPreview();
}

function renderFacts() {
  byId("fact-list").innerHTML = documents.map((doc) => `
    <div class="fact-row ${doc.id === currentDoc ? "is-selected" : ""}">
      <button type="button" class="fact-title" data-doc="${doc.id}" aria-pressed="${doc.id === currentDoc}">${doc.name}</button>
      <span class="meta">${doc.type}</span>
    </div>`).join("");
  const doc = documents.find((item) => item.id === currentDoc);
  byId("fact-source").innerHTML = `<h2>资料预览</h2><p>${doc.name}</p><blockquote>${doc.text}</blockquote>`;
}

function renderReview() {
  const list = byId("review-list");
  const sessionTurns = turns.filter((turn) => turn.session === sessionNumber);
  const savedTurns = sessionTurns.filter((turn) => turn.saved);
  byId("turn-count").textContent = sessionTurns.length;
  byId("saved-count").textContent = savedTurns.length;
  byId("review-context").textContent = lastTarget || "回看本场问题和回答";
  list.replaceChildren();
  const visibleTurns = savedOnly ? savedTurns : sessionTurns;
  if (!visibleTurns.length) {
    list.innerHTML = `<div class="empty"><h2>${savedOnly ? "还没有收藏" : "还没有练习记录"}</h2>
      <p>${savedOnly ? "收藏答过的题目，就可以在这里集中回看。" : "答过的题目和回答会出现在这里。"}</p>
      <button type="button" class="link-button" data-jump="${savedOnly ? "review" : "answer"}" ${savedOnly ? "data-show-all" : ""}>${savedOnly ? "查看全部" : "去练习问答 →"}</button></div>`;
    return;
  }
  for (const turn of visibleTurns.slice().reverse()) {
    const row = document.createElement("article");
    row.className = "history-row";
    row.innerHTML = `<span class="meta"></span><h2></h2><p></p>
      <div class="action-row"><button type="button" class="link-button" data-save="${turn.id}"
      aria-pressed="${turn.saved}">${turn.saved ? "取消收藏" : "收藏此题"}</button>
      <button type="button" class="link-button" data-repeat="${turn.id}">再练一次 →</button></div>`;
    row.querySelector("span").textContent = questions[turn.key].label;
    row.querySelector("h2").textContent = turn.question;
    row.querySelector("p").textContent = turn.first;
    if (turn.source) {
      const reference = document.createElement("p");
      reference.className = "meta";
      reference.textContent = turn.source;
      row.querySelector(".action-row").before(reference);
    }
    list.append(row);
  }
}

function navigate(destination) {
  if (page !== destination) notify("");
  page = destination;
  for (const id of pages) byId(id).hidden = id !== destination;
  document.querySelectorAll("[data-go]").forEach((button) => {
    if (button.dataset.go === destination) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  if (destination === "facts") renderFacts();
  if (destination === "review") renderReview();
  byId("main").focus();
}

function updateSessionState() {
  byId("session-state").dataset.active = Boolean(activeSession);
  byId("session-label").textContent = activeSession ? "练习中" : "未开始练习";
  byId("answer-context").textContent = activeSession ? activeSession.target : "开始练习后可使用预设文字题。";
  byId("trigger").hidden = !activeSession;
  byId("end-practice").hidden = !activeSession;
  byId("go-prepare").hidden = Boolean(activeSession);
  byId("reanswer").disabled = !activeSession;
  byId("transcript").disabled = !activeSession;
}

function toggleSession() {
  if (activeSession) {
    activeSession = null;
    renderPrepare();
    updateSessionState();
    navigate("review");
    notify("本场已结束，可在复盘页回看。");
    return;
  }
  const target = ["company", "role", "round"].map((id) => byId(id).value.trim()).filter(Boolean).join(" · ") || "通用面试练习";
  activeSession = { target, language: byId("language").value, docs: new Set(selectedDocs) };
  lastTarget = target;
  sessionNumber += 1;
  savedOnly = false;
  byId("saved-filter").setAttribute("aria-pressed", false);
  byId("transcript").value = "";
  showAnswer(waitingAnswer, [], "", "等待提问");
  renderPrepare();
  updateSessionState();
  navigate("answer");
}

function showAnswer(first, points, source, status) {
  byId("response").dataset.waiting = first === waitingAnswer;
  byId("first-answer").textContent = first;
  byId("answer-status").textContent = status;
  byId("answer-detail").hidden = !points.length;
  byId("answer-points").replaceChildren(...points.map((point) => {
    const item = document.createElement("li");
    item.textContent = point;
    return item;
  }));
  byId("source-rule").hidden = first === waitingAnswer;
  const doc = documents.find((item) => source === item.id);
  byId("answer-source").textContent = doc ? doc.name : "通用示例";
  byId("open-answer-source").hidden = !doc;
  byId("open-answer-source").dataset.openDoc = source;
}

function answerDemo(key, fromText = false) {
  if (!activeSession) return;
  if (!fromText) byId("transcript").value = questions[key].question;
  const question = byId("transcript").value.trim();
  if (!question) {
    byId("answer-status").textContent = "先选择一道题。";
    byId("transcript").focus();
    return;
  }
  const comparable = (value) => value.replace(/[。！!？?\s]/g, "");
  if (fromText) key = questions.findIndex((item) => comparable(question) === comparable(item.question));
  if (key < 0) {
    showAnswer("请从练习题列表选择一道预设题。", [], "", "已保留编辑后的问题");
    return;
  }
  const scenario = questions[key];
  document.querySelector(`input[name="question"][value="${key}"]`).checked = true;
  const depth = document.querySelector('input[name="mode"]:checked').value === "深度";
  const points = depth ? [...scenario.points, scenario.detail] : scenario.points;
  const source = activeSession.docs.has(scenario.source) ? scenario.source : "";
  const status = activeSession.language === "中文" ? "示例回答" : "示例回答使用中文";
  showAnswer(scenario.first, points, source, status);
  turns.push({ id: turns.length, session: sessionNumber, key, question, first: scenario.first,
    source: documents.find((doc) => doc.id === source)?.name, saved: false });
}

const questionList = byId("question-list");
questions.forEach(({ label }, index) => {
  const item = document.createElement("label");
  item.className = "question-choice";
  item.innerHTML = `<input type="radio" name="question" value="${index}" ${index === 0 ? "checked" : ""}><span>${label}</span>`;
  questionList.append(item);
});
for (const id of fields) byId(id).addEventListener("input", refreshPreview);
byId("materials").addEventListener("change", ({ target }) => {
  if (target.checked) selectedDocs.add(target.value);
  else selectedDocs.delete(target.value);
  refreshPreview();
});
byId("start").addEventListener("click", toggleSession);
byId("end-practice").addEventListener("click", toggleSession);
byId("trigger").addEventListener("click", () => answerDemo(selectedQuestion()));
byId("reanswer").addEventListener("click", () => answerDemo(selectedQuestion(), true));
byId("compact").addEventListener("click", (event) => {
  const compact = byId("answer").classList.toggle("compact");
  event.currentTarget.setAttribute("aria-pressed", compact);
  event.currentTarget.textContent = compact ? "恢复布局" : "紧凑视图";
});
byId("saved-filter").addEventListener("click", (event) => {
  savedOnly = !savedOnly;
  event.currentTarget.setAttribute("aria-pressed", savedOnly);
  renderReview();
});

document.addEventListener("click", ({ target }) => {
  const button = target.closest("button");
  if (!button) return;
  const { go, jump, openDoc, doc, save, repeat } = button.dataset;
  if (go || jump) {
    if (openDoc) currentDoc = openDoc;
    if (button.hasAttribute("data-show-all")) {
      savedOnly = false;
      byId("saved-filter").setAttribute("aria-pressed", false);
    }
    navigate(go || jump);
  }
  if (doc) {
    currentDoc = doc;
    renderFacts();
    document.querySelector(`[data-doc="${doc}"]`).focus();
  }
  if (save !== undefined) {
    turns[Number(save)].saved = !turns[Number(save)].saved;
    renderReview();
    (document.querySelector(`[data-save="${save}"]`) || byId("saved-filter")).focus();
  }
  if (repeat !== undefined) {
    const { key } = turns[Number(repeat)];
    document.querySelector(`input[name="question"][value="${key}"]`).checked = true;
    if (!activeSession) {
      navigate("prepare");
      notify("开始练习后，可继续练这道题。");
      return;
    }
    navigate("answer");
    answerDemo(key);
  }
});
document.addEventListener("keydown", (event) => {
  if (page !== "answer" || !activeSession || event.repeat || !event.shiftKey || event.code !== "KeyZ" ||
    event.ctrlKey || event.altKey || event.metaKey || event.target.matches("input, textarea, select")) return;
  event.preventDefault();
  answerDemo(selectedQuestion());
});

renderPrepare();
updateSessionState();
