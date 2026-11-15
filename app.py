"""Локально: python app.py. На хостинге: gunicorn app:application."""
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from http.cookies import SimpleCookie
from pathlib import Path
from wsgiref.simple_server import make_server
from logic import api
from gemini import Problem

ROOT = Path(__file__).resolve().parent
DATABASE_URL = os.environ.get("DATABASE_URL","")
LOCAL_DB = os.environ.get("EXAMHUB_DB", str(ROOT/"instance"/"web.sqlite"))
PRODUCTION = os.environ.get("RENDER","").lower()=="true"
if PRODUCTION and not DATABASE_URL:
    raise RuntimeError("Добавьте DATABASE_URL внешней PostgreSQL в Render, чтобы прогресс сохранялся.")
SECRET = os.environ.get("SECRET_KEY","")
if not SECRET:
    if PRODUCTION:
        raise RuntimeError("Добавьте SECRET_KEY в Render.")
    secret_file = ROOT/"instance"/"secret.key"
    secret_file.parent.mkdir(exist_ok=True)
    if not secret_file.exists():
        secret_file.write_text(secrets.token_hex(32),encoding="utf-8")
    SECRET = secret_file.read_text(encoding="utf-8").strip()

@contextmanager
def database():
    if DATABASE_URL:
        import psycopg
        con = psycopg.connect(DATABASE_URL, connect_timeout=15)
    else:
        Path(LOCAL_DB).parent.mkdir(parents=True,exist_ok=True)
        con = sqlite3.connect(LOCAL_DB,timeout=20)
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

def sql(con,query,args=()):
    return con.execute(query.replace("?","%s") if DATABASE_URL else query,args)

def initialize():
    with database() as con:
        sql(con,"CREATE TABLE IF NOT EXISTS web_profiles (id TEXT PRIMARY KEY, data TEXT NOT NULL)")

def new_profile():
    return dict(csrf=secrets.token_urlsafe(24),exam_date="",tasks=[],sheets=[],
                runs={},attempts=[],generation_times=[])

def profile_id(environ):
    cookie = SimpleCookie()
    try:
        cookie.load(environ.get("HTTP_COOKIE",""))
        token = cookie["exam_profile"].value if "exam_profile" in cookie else ""
    except Exception:
        token = ""
    parts = token.split(".")
    valid = len(parts)==2 and re.fullmatch("[a-f0-9]{32}",parts[0]) and hmac.compare_digest(
        parts[1],hmac.new(SECRET.encode(),parts[0].encode(),hashlib.sha256).hexdigest())
    pid = parts[0] if valid else uuid.uuid4().hex
    signature = hmac.new(SECRET.encode(),pid.encode(),hashlib.sha256).hexdigest()
    return pid, pid+"."+signature

def application(environ,start_response):
    path,method = environ.get("PATH_INFO","/"),environ.get("REQUEST_METHOD","GET")
    headers = [("X-Content-Type-Options","nosniff"),("X-Frame-Options","DENY"),
        ("Referrer-Policy","same-origin"),("Content-Security-Policy",
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")]
    status = 200
    try:
        if method=="GET" and path in ("/","/static/app.js","/static/style.css","/health"):
            if path=="/health":
                data,mime = b"ok","text/plain"
            else:
                target = ROOT/("templates/index.html" if path=="/" else path.lstrip("/"))
                data = target.read_bytes()
                mime = {"/":"text/html","/static/app.js":"text/javascript","/static/style.css":"text/css"}[path]
        elif path.startswith("/api/"):
            if method!="POST":
                raise Problem("Используйте POST.",405)
            if not environ.get("CONTENT_TYPE","").startswith("application/json"):
                raise Problem("Ожидается JSON.",415)
            try:
                length = int(environ.get("CONTENT_LENGTH") or 0)
            except ValueError:
                raise Problem("Некорректная длина запроса.")
            if not 0<=length<=16384:
                raise Problem("Слишком большой запрос.",413)
            try:
                body = json.loads(environ["wsgi.input"].read(length))
                if not isinstance(body,dict):
                    raise ValueError()
            except (ValueError,UnicodeError):
                raise Problem("Некорректный JSON.")
            pid,token = profile_id(environ)
            headers.append(("Set-Cookie","exam_profile="+token+
                "; HttpOnly; SameSite=Lax; Path=/; Max-Age=31536000"+("; Secure" if PRODUCTION else "")))
            with database() as con:
                if not DATABASE_URL:
                    sql(con,"BEGIN IMMEDIATE")
                sql(con,"INSERT INTO web_profiles (id,data) VALUES (?,?) ON CONFLICT (id) DO NOTHING",
                    (pid,json.dumps(new_profile())))
                query = "SELECT data FROM web_profiles WHERE id=?"+(" FOR UPDATE" if DATABASE_URL else "")
                profile = json.loads(sql(con,query,(pid,)).fetchone()[0])
                if path!="/api/state" and not hmac.compare_digest(
                    str(environ.get("HTTP_X_CSRF_TOKEN","")),profile["csrf"]):
                    raise Problem("Обновите страницу и повторите действие.",403)
                try:
                    result = api(path,body,profile)
                except Problem as error:
                    result,status = {"error":error.message},error.status
                sql(con,"UPDATE web_profiles SET data=? WHERE id=?",
                    (json.dumps(profile,ensure_ascii=False),pid))
            data,mime = json.dumps(result,ensure_ascii=False).encode(),"application/json"
            headers.append(("Cache-Control","no-store"))
        else:
            raise Problem("Страница не найдена.",404)
    except Problem as error:
        data,mime,status = json.dumps({"error":error.message},ensure_ascii=False).encode(),"application/json",error.status
    except Exception:
        logging.exception("Request failed")
        data,mime,status = b'{"error":"Server error. Please try again."}',"application/json",500
    headers.extend([("Content-Type",mime+"; charset=utf-8"),("Content-Length",str(len(data)))])
    names = {200:"OK",400:"Bad Request",403:"Forbidden",404:"Not Found",405:"Method Not Allowed",
        409:"Conflict",413:"Payload Too Large",415:"Unsupported Media Type",429:"Too Many Requests",
        500:"Internal Server Error",502:"Bad Gateway",503:"Service Unavailable"}
    start_response(str(status)+" "+names.get(status,"Error"),headers)
    return [data]

initialize()
if __name__=="__main__":
    print("ExamHub: http://127.0.0.1:8000")
    with make_server("127.0.0.1",int(os.environ.get("PORT","8000")),application) as server:
        server.serve_forever()
