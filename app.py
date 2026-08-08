#!/usr/bin/env python3
"""
Spark - Dating Chat Web App
"""

from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from passlib.context import CryptContext
import sqlite3
from pathlib import Path
from datetime import datetime, date
from typing import Optional
import secrets
import os

# ============== CONFIG ==============
BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "dating_chat.db"
SECRET_KEY = os.environ.get("SECRET_KEY", secrets.token_hex(32))

app = FastAPI(title="Spark Dating")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

# Monta static solo se la cartella esiste
static_dir = BASE_DIR / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def get_current_user(request: Request):
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id = ? AND stato = 'attivo'", (user_id,)).fetchone()
    conn.close()
    return dict(user) if user else None


def calcola_eta(data_nascita: str) -> int:
    try:
        nasc = datetime.strptime(data_nascita, "%Y-%m-%d").date()
        oggi = date.today()
        return oggi.year - nasc.year - ((oggi.month, oggi.day) < (nasc.month, nasc.day))
    except:
        return 0


def get_interessi_utente(user_id: int) -> list:
    conn = get_db()
    rows = conn.execute("""
        SELECT i.nome FROM user_interests ui
        JOIN interests i ON i.id = ui.interest_id
        WHERE ui.user_id = ?
    """, (user_id,)).fetchall()
    conn.close()
    return [r["nome"] for r in rows]


# ============== ROUTES ==============

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse("/discover", status_code=303)
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    if get_current_user(request):
        return RedirectResponse("/discover", status_code=303)
    return templates.TemplateResponse("register.html", {"request": request, "error": None})


@app.post("/register")
async def register(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    username: str = Form(...),
    nome: str = Form(...),
    data_nascita: str = Form(...),
    genere: str = Form(...),
    orientamento: str = Form(...),
    bio: str = Form(""),
    citta: str = Form(""),
):
    conn = get_db()
    try:
        hashed = hash_password(password)
        cursor = conn.execute("""
            INSERT INTO users (email, password_hash, username, nome, data_nascita,
                               genere, orientamento, bio, citta)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (email, hashed, username, nome, data_nascita, genere, orientamento, bio or None, citta or None))
        user_id = cursor.lastrowid
        conn.execute("INSERT INTO user_preferences (user_id) VALUES (?)", (user_id,))
        conn.commit()
        request.session["user_id"] = user_id
        return RedirectResponse("/discover", status_code=303)
    except sqlite3.IntegrityError:
        return templates.TemplateResponse("register.html", {"request": request, "error": "Email o username già in uso"})
    finally:
        conn.close()


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if get_current_user(request):
        return RedirectResponse
