"""Проверка реального WSGI-приложения без доступа к Gemini."""
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from wsgiref.util import setup_testing_defaults

temporary = tempfile.TemporaryDirectory()
os.environ["EXAMHUB_DB"] = str(Path(temporary.name)/"test.sqlite")
os.environ["SECRET_KEY"] = "test-only-secret"
os.environ["AI_PROVIDER"] = "gemini"
os.environ["GEMINI_API_KEY"] = ""
os.environ["DEEPSEEK_API_KEY"] = ""
os.environ.pop("DATABASE_URL",None)
os.environ.pop("RENDER",None)
sys.path.insert(0,str(Path(__file__).resolve().parent))
import app
import logic
import gemini

class Client:
    def __init__(self):
        self.cookie = ""
        self.csrf = ""
    def call(self,path,body=None,method="POST",csrf=True):
        env={}
        setup_testing_defaults(env)
        raw=json.dumps(body or {}).encode()
        env.update(PATH_INFO=path,REQUEST_METHOD=method,CONTENT_TYPE="application/json",
                   CONTENT_LENGTH=str(len(raw)),HTTP_COOKIE=self.cookie,
                   HTTP_X_CSRF_TOKEN=self.csrf if csrf else "",**{"wsgi.input":io.BytesIO(raw)})
        captured={}
        def start(status,headers):
            captured.update(status=int(status.split()[0]),headers=dict(headers))
        data=b"".join(app.application(env,start))
        if "Set-Cookie" in captured["headers"]:
            self.cookie=captured["headers"]["Set-Cookie"].split(";")[0]
        if captured["headers"]["Content-Type"].startswith("application/json"):
            data=json.loads(data)
        if path=="/api/state" and captured["status"]==200:
            self.csrf=data["csrf"]
        return captured["status"],data

class WebTests(unittest.TestCase):
    def setUp(self):
        self.client=Client()
        self.client.call("/api/state")
        self.selection=dict(exam="ОГЭ",subject="Математика",grade=9)
    def start(self):
        status,result=self.client.call("/api/start",self.selection)
        self.assertEqual(status,200)
        return result
    def test_static_and_health(self):
        for path in ("/","/static/style.css","/static/app.js","/health"):
            self.assertEqual(self.client.call(path,method="GET")[0],200)
        self.assertEqual(self.client.call("/static/../../app.py",method="GET")[0],404)
    def test_future_tasks_locked_on_server(self):
        run=self.start()
        self.assertTrue(run["tasks"][0]["unlocked"])
        self.assertFalse(run["tasks"][1]["unlocked"])
        self.assertNotIn("question",run["tasks"][1])
        status,_=self.client.call("/api/answer",dict(run=run["id"],task=run["tasks"][1]["id"],answer="5"))
        self.assertEqual(status,403)
    def test_empty_answer_does_not_unlock(self):
        run=self.start()
        status,_=self.client.call("/api/answer",dict(run=run["id"],task=run["tasks"][0]["id"],answer=" "))
        self.assertEqual(status,400)
        saved=self.client.call("/api/state")[1]["resume"]["run"]
        self.assertFalse(saved["tasks"][1]["unlocked"])
    def test_wrong_then_correct_and_statistics(self):
        run=self.start()
        body=dict(run=run["id"],task=run["tasks"][0]["id"],answer="wrong")
        wrong=self.client.call("/api/answer",body)[1]
        self.assertFalse(wrong["tasks"][0]["result"])
        self.assertIn("hint",wrong["tasks"][0])
        self.assertTrue(wrong["tasks"][1]["unlocked"])
        self.assertFalse(wrong["tasks"][2]["unlocked"])
        repeat=self.client.call("/api/start",{**self.selection,"repeat":True})[1]
        self.assertEqual(len(repeat["tasks"]),1)
        body["answer"]="30,0"
        correct=self.client.call("/api/answer",body)[1]
        self.assertTrue(correct["tasks"][0]["result"])
        self.assertIn("explanation",correct["tasks"][0])
        self.client.call("/api/answer",body)
        stats=self.client.call("/api/stats",self.selection)[1]["rows"]
        self.assertEqual(sum(r["attempts"] for r in stats),2)
        self.assertEqual(sum(r["correct"] for r in stats),1)
        self.assertEqual(self.client.call("/api/start",{**self.selection,"repeat":True})[0],400)
    def test_cookie_isolation(self):
        run=self.start()
        other=Client();other.call("/api/state")
        self.assertIsNone(other.call("/api/state")[1]["resume"])
        status,_=other.call("/api/answer",dict(run=run["id"],task=run["tasks"][0]["id"],answer="30"))
        self.assertEqual(status,404)
    def test_date_and_resume_persist(self):
        run=self.start()
        self.assertEqual(self.client.call("/api/date",{"date":"2027-06-01"})[0],200)
        result=self.client.call("/api/state")[1]
        self.assertEqual(result["exam_date"],"2027-06-01")
        self.assertEqual(result["resume"]["run"]["id"],run["id"])
        self.assertEqual(self.client.call("/api/date",{"date":"not-a-date"})[0],400)
    def test_csrf(self):
        self.assertEqual(self.client.call("/api/start",self.selection,csrf=False)[0],403)
    def test_all_subjects_have_examples_and_sheets(self):
        for exam,subjects in logic.EXAMS.items():
            for subject in subjects:
                query=dict(exam=exam,subject=subject,grade=9 if exam=="ОГЭ" else 11)
                self.assertEqual(self.client.call("/api/start",query)[0],200,(exam,subject))
                self.assertTrue(self.client.call("/api/handbook",query)[1]["rows"])
        self.assertNotIn("Китайский язык",logic.EXAMS["ОГЭ"])
    def test_grade_filter(self):
        self.assertEqual(self.client.call("/api/start",{**self.selection,"grade":5})[0],400)
    def test_missing_gemini_key(self):
        with patch.dict(os.environ,{"GEMINI_API_KEY":""}):
            self.assertEqual(self.client.call("/api/generate",self.selection)[0],503)
    def test_gemini_success_and_limit(self):
        item=dict(topic="Уравнения",question="x+1=3. Найдите x.",answers=["2"],hint="Вычтите 1.",explanation="x=2.")
        payload={"tasks":[item.copy() for _ in range(5)],"sheet":dict(topic="Уравнения",description="Выполняйте одинаковые действия с обеими частями.")}
        raw={"candidates":[{"content":{"parts":[{"text":json.dumps(payload)}]}}]}
        with patch.dict(os.environ,{"GEMINI_API_KEY":"fake"}),patch.object(gemini,"urlopen",return_value=io.BytesIO(json.dumps(raw).encode())):
            status,result=self.client.call("/api/generate",{**self.selection,"grade":7,"topic":"Уравнения"})
        self.assertEqual(status,200)
        self.assertEqual(result["count"],5)
        status,run=self.client.call("/api/start",{**self.selection,"grade":7,"ids":result["ids"]})
        self.assertEqual(status,200)
        self.assertEqual(len(run["tasks"]),5)
        self.assertNotIn("answers",run["tasks"][0])
        with patch.dict(os.environ,{"GEMINI_API_KEY":"fake"}):
            self.assertEqual(self.client.call("/api/generate",self.selection)[0],429)
    def test_malformed_generation_atomic(self):
        raw={"candidates":[{"content":{"parts":[{"text":"{}"}]}}]}
        with patch.dict(os.environ,{"GEMINI_API_KEY":"fake"}),patch.object(gemini,"urlopen",return_value=io.BytesIO(json.dumps(raw).encode())):
            self.assertEqual(self.client.call("/api/generate",self.selection)[0],502)
        self.assertEqual(len(self.start()["tasks"]),6)
    def test_numeric_answers(self):
        for value,expected in [("0,5","0.5"),(" 30.0 ","30"),(" Ёлка ","елка")]:
            self.assertTrue(logic.equal_answer(value,expected))
        self.assertFalse(logic.equal_answer("NaN","NaN"))
        self.assertFalse(logic.equal_answer("Infinity","Infinity"))

if __name__=="__main__":
    unittest.main(verbosity=2)
