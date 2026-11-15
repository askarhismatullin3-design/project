"""Тесты, проверка кратких ответов, статистика и справочник."""
import json
import os
import time
import uuid
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from catalog import EXAMS, TOPICS, seed
from gemini import Problem, generate, provider_config

TASKS, SHEETS = seed()
snapshot = Path(__file__).with_name("seed.json")
if snapshot.exists():
    imported = json.loads(snapshot.read_text(encoding="utf-8"))
    TASKS, SHEETS = imported["tasks"], imported["sheets"]

def selection(body):
    exam, subject = body.get("exam"), body.get("subject")
    if exam not in EXAMS or subject not in EXAMS[exam]:
        raise Problem("Выберите экзамен и доступный предмет.")
    try:
        grade = int(body.get("grade",9 if exam=="ОГЭ" else 11))
    except (ValueError,TypeError):
        raise Problem("Некорректный класс.")
    if grade not in range(5,12):
        raise Problem("Выберите класс с 5 по 11.")
    return exam, subject, grade

def normalized(value):
    return " ".join(str(value).strip().casefold().replace("ё","е").split())

def equal_answer(value, expected):
    a, b = normalized(value), normalized(expected)
    try:
        x, y = Decimal(a.replace(",",".")), Decimal(b.replace(",","."))
        return x.is_finite() and y.is_finite() and x==y
    except InvalidOperation:
        return a==b

def all_tasks(profile):
    return {t["id"]:t for t in TASKS + profile["tasks"]}

def public_run(profile, run):
    tasks, rows = all_tasks(profile), []
    for index, tid in enumerate(run["ids"]):
        state = run["results"].get(tid)
        unlocked = all(prev in run["results"] for prev in run["ids"][:index])
        item = dict(id=tid, topic=tasks[tid]["topic"], unlocked=unlocked, result=state)
        if unlocked:
            item.update(question=tasks[tid]["question"], source=tasks[tid]["source"],
                        answer=run["answers"].get(tid,""))
            if state is False:
                item["hint"] = tasks[tid]["hint"]
            if state is True:
                item["explanation"] = tasks[tid]["explanation"]
        rows.append(item)
    return dict(id=run["id"], tasks=rows)

def api(path, body, profile):
    if path=="/api/state":
        config = provider_config()
        resume = None
        if "active_run" in profile:
            last = profile["runs"].get(profile["active_run"])
        else:
            last = next((r for r in reversed(list(profile["runs"].values())) if not r.get("finished")),None)
        if last:
            first = all_tasks(profile)[last["ids"][0]]
            resume = dict(run=public_run(profile,last), selection={k:first[k] for k in ("exam","subject","grade")})
        return dict(exams=EXAMS, topics=TOPICS, exam_date=profile["exam_date"], resume=resume,
                    csrf=profile["csrf"], gemini=bool(os.environ.get("GEMINI_API_KEY")),
                    ai_connected=bool(config["key"]), ai_name=config["name"])
    if path=="/api/date":
        value = str(body.get("date",""))
        try:
            if value:
                date.fromisoformat(value)
        except ValueError:
            raise Problem("Введите корректную дату.")
        profile["exam_date"] = value
        return {"ok":True}
    if path=="/api/generate":
        exam, subject, grade = selection(body)
        level = str(body.get("level","Обычный"))
        if level not in ("Простой","Обычный","Сложный","Базовая","Профильная"):
            raise Problem("Выберите уровень.")
        topic = str(body.get("topic","")).strip()[:120] or TOPICS[subject][0]
        return generate(exam,subject,grade,topic,level,profile)
    if path=="/api/start":
        exam, subject, grade = selection(body)
        tasks = all_tasks(profile)
        ids = [tid for tid,t in tasks.items()
               if (t["exam"],t["subject"],t["grade"])==(exam,subject,grade)]
        if "ids" in body:
            requested = body["ids"]
            if not isinstance(requested,list) or not requested or len(requested)>100 or any(tid not in ids for tid in requested):
                raise Problem("Некорректный набор заданий.")
            ids = list(dict.fromkeys(requested))
        if body.get("repeat"):
            latest = {a["task"]:a["correct"] for a in profile["attempts"]}
            ids = [tid for tid in ids if latest.get(tid) is False]
        if not ids:
            raise Problem("Ошибок для повторения нет." if body.get("repeat") else
                          "Для этого класса пока нет заданий. Создайте набор через ИИ.")
        rid = uuid.uuid4().hex
        run = dict(id=rid, ids=ids, results={}, answers={})
        profile["runs"][rid] = run
        profile["active_run"] = rid
        while len(profile["runs"])>10:
            del profile["runs"][next(iter(profile["runs"]))]
        return public_run(profile,run)
    if path=="/api/finish":
        run = profile["runs"].get(str(body.get("run","")))
        if not run:
            raise Problem("Тест не найден.",404)
        if not all(run["results"].get(tid) is True for tid in run["ids"]):
            raise Problem("Сначала решите все задания.",409)
        run["finished"] = True
        if profile.get("active_run",run["id"]) == run["id"]:
            profile["active_run"] = None
        return {"ok":True}
    if path=="/api/answer":
        run = profile["runs"].get(str(body.get("run","")))
        if not run:
            raise Problem("Тест не найден. Начните новый тест.",404)
        tid = str(body.get("task",""))
        if tid not in run["ids"]:
            raise Problem("Задание не найдено.",404)
        index = run["ids"].index(tid)
        if any(prev not in run["results"] for prev in run["ids"][:index]):
            raise Problem("Сначала отправьте ответ на предыдущие задания.",403)
        if run["results"].get(tid) is True:
            return public_run(profile,run)
        answer = str(body.get("answer","")).strip()
        if not answer or len(answer)>300:
            raise Problem("Введите краткий ответ длиной до 300 символов.")
        task = all_tasks(profile)[tid]
        correct = any(equal_answer(answer,expected) for expected in task["answers"])
        run["answers"][tid], run["results"][tid] = answer, correct
        profile["attempts"].append(dict(task=tid,correct=correct,answer=answer,time=time.time()))
        return public_run(profile,run)
    if path=="/api/stats":
        exam, subject, grade = selection(body)
        tasks, groups = all_tasks(profile), {}
        for task in tasks.values():
            if (task["exam"],task["subject"],task["grade"])==(exam,subject,grade):
                groups.setdefault(task["topic"],dict(topic=task["topic"],attempts=0,correct=0))
        for attempt in profile["attempts"]:
            task = tasks.get(attempt["task"])
            if task and (task["exam"],task["subject"],task["grade"])==(exam,subject,grade):
                row = groups[task["topic"]]
                row["attempts"] += 1
                row["correct"] += int(attempt["correct"])
        return {"rows":list(groups.values())}
    if path=="/api/handbook":
        exam, subject, grade = selection(body)
        return {"rows":[s for s in SHEETS+profile["sheets"]
                        if (s["exam"],s["subject"],s["grade"])==(exam,subject,grade)]}
    raise Problem("Страница не найдена.",404)
