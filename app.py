#!/usr/bin/env python3
"""
Spark - Dating Chat Web App
"""

from fastapi import FastAPI, Request, Form
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

# Monta static solo se esiste
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
    try:
        conn = get_db()
        user = conn.execute(
            "SELECT * FROM users WHERE id = ? AND stato = 'attivo'", (user_id,)
        ).fetchone()
        conn.close()
        return dict(user) if user else None
    except:
        return None


def calcola_eta(data_nascita: str) -> int:
    try:
        nasc = datetime.strptime(data_nascita, "%Y-%m-%d").date()
        oggi = date.today()
        return oggi.year - nasc.year - ((oggi.month, oggi.day) < (nasc.month, nasc.day))
    except:
        return 0


def get_interessi_utente(user_id: int) -> list:
    try:
        conn = get_db()
        rows = conn.execute("""
            SELECT i.nome FROM user_interests ui
            JOIN interests i ON i.id = ui.interest_id
            WHERE ui.user_id = ?
        """, (user_id,)).fetchall()
        conn.close()
        return [r["nome"] for r in rows]
    except:
        return []


# ============== ROUTES ==============

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse("/discover", status_code=303)
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
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
        return templates.TemplateResponse("register.html", {
            "request": request,
            "error": "Email o username già in uso"
        })
    finally:
        conn.close()


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {
        "request": request,
        "error": None
    })


@app.post("/login")
async def login(request: Request, email: str = Form(...), password: str = Form(...)):
    conn = get_db()
    user = conn.execute(
        "SELECT * FROM users WHERE email = ? AND stato = 'attivo'", (email,)
    ).fetchone()
    conn.close()

    if user and verify_password(password, user["password_hash"]):
        request.session["user_id"] = user["id"]
        try:
            conn = get_db()
            conn.execute(
                "UPDATE users SET is_online = 1, ultimo_accesso = CURRENT_TIMESTAMP WHERE id = ?",
                (user["id"],)
            )
            conn.commit()
            conn.close()
        except:
            pass
        return RedirectResponse("/discover", status_code=303)

    return templates.TemplateResponse("login.html", {
        "request": request,
        "error": "Email o password non corretti"
    })


@app.get("/logout")
async def logout(request: Request):
    user_id = request.session.get("user_id")
    if user_id:
        try:
            conn = get_db()
            conn.execute("UPDATE users SET is_online = 0 WHERE id = ?", (user_id,))
            conn.commit()
            conn.close()
        except:
            pass
    request.session.clear()
    return RedirectResponse("/", status_code=303)


@app.get("/discover", response_class=HTMLResponse)
async def discover(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    conn = get_db()
    candidates = conn.execute("""
        SELECT u.* FROM users u
        WHERE u.id != ?
          AND u.stato = 'attivo'
          AND u.id NOT IN (SELECT to_user_id FROM swipes WHERE from_user_id = ?)
          AND u.id NOT IN (SELECT blocked_id FROM blocks WHERE blocker_id = ?)
          AND u.id NOT IN (SELECT blocker_id FROM blocks WHERE blocked_id = ?)
        ORDER BY RANDOM()
        LIMIT 1
    """, (user["id"], user["id"], user["id"], user["id"])).fetchone()

    if candidates:
        candidate = dict(candidates)
        candidate["eta"] = calcola_eta(candidate["data_nascita"])
        candidate["interessi"] = get_interessi_utente(candidate["id"])
    else:
        candidate = None

    conn.close()
    return templates.TemplateResponse("discover.html", {
        "request": request,
        "user": user,
        "candidate": candidate
    })


@app.post("/swipe")
async def do_swipe(request: Request, to_user_id: int = Form(...), tipo: str = Form(...)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    if tipo not in ("like", "dislike", "superlike"):
        return RedirectResponse("/discover", status_code=303)

    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO swipes (from_user_id, to_user_id, tipo) VALUES (?, ?, ?)",
            (user["id"], to_user_id, tipo)
        )

        match_created = False
        if tipo in ("like", "superlike"):
            reciproco = conn.execute("""
                SELECT id FROM swipes
                WHERE from_user_id = ? AND to_user_id = ?
                  AND tipo IN ('like', 'superlike')
            """, (to_user_id, user["id"])).fetchone()

            if reciproco:
                u1, u2 = sorted([user["id"], to_user_id])
                cursor = conn.execute(
                    "INSERT OR IGNORE INTO matches (user1_id, user2_id) VALUES (?, ?)",
                    (u1, u2)
                )
                if cursor.rowcount > 0:
                    match_id = cursor.lastrowid
                    conn.execute("INSERT INTO conversations (match_id) VALUES (?)", (match_id,))
                    for uid in (user["id"], to_user_id):
                        conn.execute("""
                            INSERT INTO notifications (user_id, tipo, titolo, contenuto, related_id)
                            VALUES (?, 'nuovo_match', 'Nuovo Match! 🎉', 'Hai un nuovo match!', ?)
                        """, (uid, match_id))
                    match_created = True

        conn.commit()
    except sqlite3.IntegrityError:
        pass
    finally:
        conn.close()

    if match_created:
        return RedirectResponse("/matches?new_match=1", status_code=303)
    return RedirectResponse("/discover", status_code=303)


@app.get("/matches", response_class=HTMLResponse)
async def matches_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    conn = get_db()
    matches = conn.execute("""
        SELECT m.id as match_id, m.data_match,
               CASE WHEN m.user1_id = ? THEN m.user2_id ELSE m.user1_id END as altro_id,
               u.username, u.nome, u.foto_principale_url, u.is_online,
               c.id as conversation_id
        FROM matches m
        JOIN users u ON u.id = CASE WHEN m.user1_id = ? THEN m.user2_id ELSE m.user1_id END
        LEFT JOIN conversations c ON c.match_id = m.id
        WHERE (m.user1_id = ? OR m.user2_id = ?) AND m.attivo = 1
        ORDER BY m.data_match DESC
    """, (user["id"], user["id"], user["id"], user["id"])).fetchall()

    match_list = []
    for m in matches:
        d = dict(m)
        try:
            d["eta"] = calcola_eta(
                conn.execute("SELECT data_nascita FROM users WHERE id = ?", (d["altro_id"],)).fetchone()["data_nascita"]
            )
        except:
            d["eta"] = 0
        match_list.append(d)

    new_match = request.query_params.get("new_match")
    conn.close()

    return templates.TemplateResponse("matches.html", {
        "request": request,
        "user": user,
        "matches": match_list,
        "new_match": new_match
    })


@app.get("/chat/{conversation_id}", response_class=HTMLResponse)
async def chat_page(request: Request, conversation_id: int):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    conn = get_db()
    conv = conn.execute("""
        SELECT c.id, m.user1_id, m.user2_id
        FROM conversations c
        JOIN matches m ON m.id = c.match_id
        WHERE c.id = ?
    """, (conversation_id,)).fetchone()

    if not conv or user["id"] not in (conv["user1_id"], conv["user2_id"]):
        conn.close()
        return RedirectResponse("/matches", status_code=303)

    altro_id = conv["user2_id"] if user["id"] == conv["user1_id"] else conv["user1_id"]
    altro = dict(conn.execute("SELECT * FROM users WHERE id = ?", (altro_id,)).fetchone())
    altro["eta"] = calcola_eta(altro["data_nascita"])

    messaggi = conn.execute("""
        SELECT m.*, u.nome FROM messages m
        JOIN users u ON u.id = m.sender_id
        WHERE m.conversation_id = ? AND m.eliminato = 0
        ORDER BY m.data_invio
    """, (conversation_id,)).fetchall()

    conn.execute(
        "UPDATE messages SET letto = 1 WHERE conversation_id = ? AND sender_id != ? AND letto = 0",
        (conversation_id, user["id"])
    )
    conn.commit()
    conn.close()

    return templates.TemplateResponse("chat.html", {
        "request": request,
        "user": user,
        "altro": altro,
        "conversation_id": conversation_id,
        "messaggi": [dict(m) for m in messaggi]
    })


@app.post("/chat/{conversation_id}/send")
async def send_message(request: Request, conversation_id: int, contenuto: str = Form(...)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    conn = get_db()
    conv = conn.execute("""
        SELECT c.id, m.user1_id, m.user2_id FROM conversations c
        JOIN matches m ON m.id = c.match_id WHERE c.id = ?
    """, (conversation_id,)).fetchone()

    if not conv or user["id"] not in (conv["user1_id"], conv["user2_id"]):
        conn.close()
        return RedirectResponse("/matches", status_code=303)

    conn.execute(
        "INSERT INTO messages (conversation_id, sender_id, tipo, contenuto) VALUES (?, ?, 'testo', ?)",
        (conversation_id, user["id"], contenuto)
    )
    conn.execute(
        "UPDATE conversations SET ultimo_messaggio_at = CURRENT_TIMESTAMP WHERE id = ?",
        (conversation_id,)
    )

    altro_id = conv["user2_id"] if user["id"] == conv["user1_id"] else conv["user1_id"]
    conn.execute("""
        INSERT INTO notifications (user_id, tipo, titolo, contenuto, related_id)
        VALUES (?, 'nuovo_messaggio', 'Nuovo messaggio', ?, ?)
    """, (altro_id, contenuto[:80], conversation_id))
    conn.commit()
    conn.close()

    return RedirectResponse(f"/chat/{conversation_id}", status_code=303)


@app.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    user["eta"] = calcola_eta(user["data_nascita"])
    interessi = get_interessi_utente(user["id"])

    conn = get_db()
    all_interests = conn.execute("SELECT * FROM interests ORDER BY nome").fetchall()
    prefs = conn.execute("SELECT * FROM user_preferences WHERE user_id = ?", (user["id"],)).fetchone()
    conn.close()

    return templates.TemplateResponse("profile.html", {
        "request": request,
        "user": user,
        "interessi": interessi,
        "all_interests": [dict(i) for i in all_interests],
        "prefs": dict(prefs) if prefs else {}
    })


@app.post("/profile/update")
async def update_profile(
    request: Request,
    bio: str = Form(""),
    citta: str = Form(""),
    altezza: Optional[int] = Form(None),
    fuma: str = Form(""),
    beve: str = Form(""),
    cerca: str = Form(""),
):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    conn = get_db()
    conn.execute("""
        UPDATE users SET bio = ?, citta = ?, altezza = ?, fuma = ?, beve = ?, cerca = ?
        WHERE id = ?
    """, (bio or None, citta or None, altezza, fuma or None, beve or None, cerca or None, user["id"]))
    conn.commit()
    conn.close()
    return RedirectResponse("/profile", status_code=303)


@app.post("/profile/interessi")
async def update_interessi(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    form = await request.form()
    selected = form.getlist("interessi")

    conn = get_db()
    conn.execute("DELETE FROM user_interests WHERE user_id = ?", (user["id"],))
    for iid in selected:
        try:
            conn.execute(
                "INSERT INTO user_interests (user_id, interest_id) VALUES (?, ?)",
                (user["id"], int(iid))
            )
        except:
            pass
    conn.commit()
    conn.close()
    return RedirectResponse("/profile", status_code=303)


@app.get("/notifications", response_class=HTMLResponse)
async def notifications_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    conn = get_db()
    notifs = conn.execute("""
        SELECT * FROM notifications WHERE user_id = ?
        ORDER BY data_creazione DESC LIMIT 50
    """, (user["id"],)).fetchall()
    conn.execute("UPDATE notifications SET letto = 1 WHERE user_id = ?", (user["id"],))
    conn.commit()
    conn.close()

    return templates.TemplateResponse("notifications.html", {
        "request": request,
        "user": user,
        "notifications": [dict(n) for n in notifs]
    })
