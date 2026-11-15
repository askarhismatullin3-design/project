"use strict";
const $ = id => document.getElementById(id);
let state, run = null, current = 0, page = "home", sheets = [], busy = false;
const titles = {home:"Обзор подготовки", practice:"Тренировка", stats:"Статистика", handbook:"Шпаргалки"};

function node(tag, text, cls) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (cls) element.className = cls;
  return element;
}
function notify(message, error = false) {
  $("notice").textContent = message;
  $("notice").className = error ? "error" : "success";
  $("notice").hidden = !message;
}
async function request(path, data = {}) {
  const response = await fetch(path, {method:"POST", headers:{
    "Content-Type":"application/json", "X-CSRF-Token":state?.csrf || ""
  }, body:JSON.stringify(data)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "Не удалось выполнить запрос.");
  return result;
}
async function action(work) {
  if (busy) return;
  busy = true;
  document.querySelector("main").setAttribute("aria-busy","true");
  const selects = [$("exam"),$("subject"),$("grade")];
  selects.forEach(s => s.disabled = true);
  notify("");
  try { await work(); }
  catch (error) { notify(error.message || "Нет соединения с сервером.",true); }
  finally {
    busy = false;
    document.querySelector("main").removeAttribute("aria-busy");
    selects.forEach(s => s.disabled = false);
  }
}
function selection() {
  return {exam:$("exam").value, subject:$("subject").value, grade:Number($("grade").value)};
}
function fillSubjects() {
  const previous = $("subject").value;
  $("subject").replaceChildren(...state.exams[$("exam").value].map(s => {
    const option = node("option",s); option.value = s; return option;
  }));
  if (state.exams[$("exam").value].includes(previous)) $("subject").value = previous;
  fillTopics();
}
function fillTopics() {
  const topics = [...state.topics[$("subject").value]];
  if ($("subject").value === "Математика" && $("exam").value === "ЕГЭ")
    topics.push("Производная","Логарифмы","Тригонометрия","Стереометрия");
  $("topics").replaceChildren(...topics.map(t => {const o=node("option");o.value=t;return o;}));
  $("topic").value = "";
  $("topic").placeholder = topics[0] || "Введи тему";
}
async function showPage(name) {
  page = name;
  document.querySelectorAll(".page").forEach(p => p.hidden = p.id !== name);
  document.querySelectorAll(".nav").forEach(b => {
    b.classList.toggle("active",b.dataset.page === name);
    if (b.dataset.page===name) b.setAttribute("aria-current","page");
    else b.removeAttribute("aria-current");
  });
  $("page-title").textContent = titles[name];
  if (name==="stats") await loadStats();
  if (name==="handbook") await loadSheets();
}
function resetTest() {
  run = null;
  current = 0;
  $("test").hidden = true;
  $("finish").hidden = true;
}
async function start(repeat = false, ids = undefined) {
  const data = {...selection(),repeat};
  if (ids) data.ids = ids;
  run = await request("/api/start",data);
  current = 0;
  await showPage("practice");
  $("test").hidden = false;
  $("test-name").textContent = repeat ? "Работа над ошибками" : selection().exam+" · "+selection().subject;
  renderTask();
}
function renderTask() {
  const task = run.tasks[current];
  $("numbers").replaceChildren(...run.tasks.map((t,index) => {
    const button = node("button",String(index+1));
    button.type = "button";
    button.disabled = !t.unlocked;
    button.className = "number"+(index===current?" selected":"")+(t.result===true?" correct":t.result===false?" wrong":"");
    button.setAttribute("aria-label","Задание "+(index+1)+(t.unlocked?"":" — пока недоступно"));
    if (index===current) button.setAttribute("aria-current","step");
    button.onclick = () => {
      if (busy || !t.unlocked) return;
      run.tasks[current].answer = $("answer").value;
      current = index; renderTask();
    };
    return button;
  }));
  $("progress").textContent = "Задание "+(current+1)+" из "+run.tasks.length;
  $("task-source").textContent = task.source;
  $("task-topic").textContent = task.topic;
  $("question").textContent = task.question;
  $("answer").value = task.answer || "";
  $("answer").disabled = task.result===true;
  $("check").disabled = task.result===true;
  $("next").disabled = task.result===null || current===run.tasks.length-1;
  const allDone = run.tasks.every(t => t.result===true);
  $("finish").hidden = !allDone;
  $("next").hidden = allDone;
  $("feedback").textContent = allDone ? "Все задания решены! Посмотри результаты в статистике."
    : task.result===true ? "Верно! Можно двигаться дальше."
    : task.result===false ? "Пока неверно. Прочитай подсказку и попробуй ещё." : "";
  $("feedback").className = task.result===false ? "wrong-text" : "correct-text";
  $("hint").hidden = !task.hint && !task.explanation;
  $("hint").textContent = task.result===true ? "Разбор: "+task.explanation : task.hint ? "Подсказка: "+task.hint : "";
  if (!task.result) $("answer").focus();
}
async function checkAnswer() {
  const answer = $("answer").value.trim();
  if (!answer) { notify("Сначала введи ответ.",true); return; }
  run = await request("/api/answer",{run:run.id, task:run.tasks[current].id,answer});
  renderTask();
}
async function loadStats() {
  const charts = [];
  for (const exam of Object.keys(state.exams)) {
    const card = node("article",undefined,"chart");
    card.append(node("h3",exam+" · "+$("grade").value+" класс"));
    if (!state.exams[exam].includes($("subject").value)) {
      card.append(node("p","Этот предмет не представлен в "+exam+".","muted"));
      charts.push(card); continue;
    }
    const result = await request("/api/stats",{...selection(),exam});
    let total=0, correct=0;
    result.rows.forEach(row => { total+=row.attempts; correct+=row.correct; });
    card.append(node("p",correct+" верных из "+total+" попыток","muted"));
    if (!result.rows.length) card.append(node("p","Пока нет заданий для выбранного класса. Создай набор с ИИ."));
    for (const row of result.rows) {
      const block = node("div",undefined,"bar-row");
      const percent = row.attempts ? Math.round(100*row.correct/row.attempts) : 0;
      block.append(node("span",row.topic),node("small",row.attempts ? percent+"% · "+row.correct+"/"+row.attempts : "Нет попыток"));
      const svg = document.createElementNS("http://www.w3.org/2000/svg","svg");
      svg.setAttribute("viewBox","0 0 100 4"); svg.setAttribute("role","img");
      svg.setAttribute("aria-label",row.topic+": "+percent+" процентов верных ответов");
      for (const [width,fill] of [[100,"#253650"],[percent,percent>=70?"#39b887":"#e17b6d"]]) {
        const rect = document.createElementNS(svg.namespaceURI,"rect");
        rect.setAttribute("width",String(width)); rect.setAttribute("height","4"); rect.setAttribute("rx","2"); rect.setAttribute("fill",fill); svg.append(rect);
      }
      block.append(svg);card.append(block);
    }
    charts.push(card);
  }
  $("charts").replaceChildren(...charts);
}
async function loadSheets() {
  sheets = (await request("/api/handbook",selection())).rows;
  renderSheets();
}
function renderSheets() {
  const query = $("search").value.trim().toLocaleLowerCase("ru");
  const visible = sheets.filter(s => (s.topic+" "+s.description).toLocaleLowerCase("ru").includes(query));
  const list = $("sheet-list");
  list.replaceChildren();
  const select = (sheet,button) => {
    list.querySelectorAll("button").forEach(b => b.classList.toggle("selected",b===button));
    $("sheet-text").replaceChildren(node("h3",sheet.topic),node("p",sheet.description));
  };
  visible.forEach(sheet => {
    const button = node("button",sheet.topic);
    button.onclick = () => select(sheet,button);
    list.append(button);
  });
  if (visible.length) select(visible[0],list.firstChild);
  else $("sheet-text").replaceChildren(node("p",sheets.length?"Подходящих шпаргалок нет.":"Для этого класса пока нет шпаргалок. Создай набор с ИИ — теория сохранится вместе с заданиями.","muted"));
}
function countdown() {
  const value = $("exam-date").value;
  if (!value) { $("countdown").textContent="Выбери дату";return; }
  const now = new Date(), target = new Date(value+"T00:00:00");
  const today = new Date(now.getFullYear(),now.getMonth(),now.getDate());
  const days = Math.round((target-today)/86400000);
  $("countdown").textContent = days===0 ? "Экзамен сегодня!" : days<0 ? "Дата уже прошла" : days+" дн.";
}
document.querySelectorAll(".nav").forEach(b => b.onclick=()=>action(()=>showPage(b.dataset.page)));
$("start").onclick = () => action(()=>start());
$("home-start").onclick = () => action(()=>start());
$("repeat").onclick = () => action(()=>start(true));
$("stats-repeat").onclick = () => action(()=>start(true));
$("go-generator").onclick = () => action(async()=>{
  await showPage("practice");$("generator").open=true;$("topic").focus();
});
$("answer-form").onsubmit = event => {event.preventDefault();action(checkAnswer);};
$("next").onclick = () => {
  if (busy || !run || $("next").disabled) return;
  run.tasks[current].answer = $("answer").value;
  current++;renderTask();
};
$("finish").onclick = () => action(async()=>{
  if (!run || !run.tasks.every(t => t.result===true)) return;
  await request("/api/finish",{run:run.id});
  resetTest();
  $("generator").open = false;
  await showPage("home");
  window.scrollTo({top:0,behavior:"instant"});
  notify("Тренировка завершена. Результаты сохранены в статистике.");
});
$("generate").onclick = () => action(async()=>{
  const button=$("generate");button.textContent=(state.ai_name || "ИИ")+" составляет задания…";
  try {
    const result = await request("/api/generate",{...selection(),topic:$("topic").value,level:$("level").value});
    await start(false,result.ids);
    notify("Создано "+result.count+" заданий и шпаргалка. Проверь спорные ответы ИИ.");
  } finally {button.textContent="Создать 5 заданий";}
});
$("save-date").onclick = () => action(async()=>{
  await request("/api/date",{date:$("exam-date").value});countdown();notify("Дата экзамена сохранена.");
});
$("exam-date").oninput=countdown;
$("search").oninput=renderSheets;
$("exam").onchange=()=>action(async()=>{
  $("grade").value=$("exam").value==="ОГЭ"?"9":"11";
  fillSubjects();resetTest();await showPage(page);
});
$("subject").onchange=()=>action(async()=>{fillTopics();resetTest();await showPage(page);});
$("grade").onchange=()=>action(async()=>{resetTest();await showPage(page);});
action(async()=>{
  state=await request("/api/state");fillSubjects();
  $("exam-date").value=state.exam_date;countdown();
  $("gemini-status").textContent=state.ai_connected?state.ai_name+" подключён":"Учебный режим · без ключа";
  $("generator-title").textContent="Создать задания с "+state.ai_name;
  if (state.resume) {
    $("exam").value=state.resume.selection.exam;fillSubjects();
    $("subject").value=state.resume.selection.subject;$("grade").value=state.resume.selection.grade;
    fillTopics();run=state.resume.run;
    current=Math.max(0,run.tasks.findIndex(t=>t.unlocked && t.result!==true));
    $("test").hidden=false;$("test-name").textContent="Продолжение тренировки";
    renderTask();
  }
});
setInterval(countdown,60000);
