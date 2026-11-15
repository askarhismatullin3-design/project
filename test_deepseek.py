"""Проверки DeepSeek с имитацией API: сеть и реальные ключи не используются."""
import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
sys.path.insert(0,str(Path(__file__).resolve().parent))
from test_web import Client
import gemini

class DeepSeekTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ,{"AI_PROVIDER":"deepseek","DEEPSEEK_API_KEY":"fake-test-key","DEEPSEEK_MODEL":"deepseek-chat"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.client = Client()
        self.client.call("/api/state")
        self.query = dict(exam="ОГЭ",subject="Физика",grade=8,topic="Механика")
    def response(self,reason="stop"):
        item = dict(topic="Механика",question="Тело прошло 12 м за 3 с. Скорость в м/с?",answers=["4"],
                    hint="Разделите путь на время.",explanation="12/3 = 4 м/с.")
        payload = dict(tasks=[item.copy() for _ in range(5)],sheet=dict(topic="Механика",description="При равномерном движении v = s/t."))
        return {"choices":[{"finish_reason":reason,"message":{"content":json.dumps(payload)}}]}
    def test_generation_and_request_format(self):
        response = io.BytesIO(json.dumps(self.response()).encode())
        with patch.object(gemini,"urlopen",return_value=response) as mocked:
            status,result = self.client.call("/api/generate",self.query)
        self.assertEqual(status,200)
        self.assertEqual(result["count"],5)
        request = mocked.call_args.args[0]
        self.assertEqual(request.full_url,"https://api.deepseek.com/chat/completions")
        self.assertEqual(request.get_header("Authorization"),"Bearer fake-test-key")
        payload = json.loads(request.data)
        self.assertEqual(payload["response_format"],{"type":"json_object"})
        status,run = self.client.call("/api/start",{**self.query,"ids":result["ids"]})
        self.assertEqual(status,200)
        self.assertEqual(run["tasks"][0]["source"],"DeepSeek · тренировочное задание")
        self.assertNotIn("answers",run["tasks"][0])
        self.assertTrue(self.client.call("/api/handbook",self.query)[1]["rows"])
    def test_status_does_not_disclose_key(self):
        status,state = self.client.call("/api/state")
        self.assertEqual(state["ai_name"],"DeepSeek")
        self.assertTrue(state["ai_connected"])
        self.assertNotIn("fake-test-key",json.dumps(state))
    def test_missing_key(self):
        with patch.dict(os.environ,{"DEEPSEEK_API_KEY":""}):
            status,result = self.client.call("/api/generate",self.query)
        self.assertEqual(status,503)
        self.assertIn("DEEPSEEK_API_KEY",result["error"])
    def test_balance_error(self):
        error = HTTPError("https://api.deepseek.com/chat/completions",402,"balance",{},None)
        with patch.object(gemini,"urlopen",side_effect=error):
            status,result = self.client.call("/api/generate",self.query)
        self.assertEqual(status,502)
        self.assertIn("баланс",result["error"])
    def test_bad_key(self):
        error = HTTPError("https://api.deepseek.com/chat/completions",401,"key",{},None)
        with patch.object(gemini,"urlopen",side_effect=error):
            status,result = self.client.call("/api/generate",self.query)
        self.assertEqual(status,502)
        self.assertIn("ключ",result["error"])
    def test_truncated_output_not_saved(self):
        response = io.BytesIO(json.dumps(self.response("length")).encode())
        with patch.object(gemini,"urlopen",return_value=response):
            status,result = self.client.call("/api/generate",self.query)
        self.assertEqual(status,502)
        self.assertEqual(self.client.call("/api/start",self.query)[0],400)
    def test_auto_selection_and_explicit_override(self):
        with patch.dict(os.environ,{"AI_PROVIDER":""}):
            self.assertEqual(gemini.provider_config()["provider"],"deepseek")
        with patch.dict(os.environ,{"AI_PROVIDER":"gemini"}):
            self.assertEqual(gemini.provider_config()["provider"],"gemini")

if __name__=="__main__":
    unittest.main(verbosity=2)
