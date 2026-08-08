#!/usr/bin/env python3
"""
MyCheating - Dating App with Supabase
"""

from fastapi import FastAPI, Request, Form, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from passlib.context import CryptContext
from pathlib import Path
from datetime import datetime, date
from typing import Optional, Dict, List
import os
import psycopg2
from psycopg2.extras import RealDictCursor

# ============== CONFIG ==============
BASE_DIR = Path(__file__).parent

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise Exception("DATABASE_URL non impostata! Controlla le Environment Variables su Render.")

SECRET_KEY = os.environ.get("SECRET_KEY", "mycheating_secret_key_2026_super_sicura_123456")

app = FastAPI(title="MyCheating")

app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    session_cookie="mycheating_session",
    max_age=2592000,
    same_site="lax",
    https_only=False
)

static_dir = BASE_DIR / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ============== WEBSOCKET ==============
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[int, List[WebSocket]] = {}

    async def connect(self, user_id: int, websocket: WebSocket):
        await websocket.accept()
        if user_id not in self.active_connections:
            self.active_connections[user_id] = []
        self.active_connections[user_id].append(websocket)

    def disconnect(self, user_id: int, websocket: WebSocket):
        if user_id in self.active_connections:
            if websocket in self.active_connections[user_id]:
                self.active_connections[user_id].remove(websocket)
            if not self.active_connections[user_id]:
                del self.active_connections[user_id]

    async def send_to_user(self, user_id: int, message: dict):
        if user_id in self.active_connections:
            dead = []
            for connection in self.active_connections[user_id]:
                try:
                    await connection.send_json(message)
                except:
                    dead.append(connection)
            for d in dead:
                self.disconnect(user_id, d)


manager = ConnectionManager()


# ============== DATABASE ==============
def get_db():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    return conn


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return pwd_context.verify(plain, hashed)
    except:
        return False


def get_current_user(request: Request):
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE id = %s AND stato = 'attivo'", (user_id,))
        user = cur.fetchone()
        cur.close()
        conn.close()
        return dict(user) if user else None
    except Exception as e:
        print(f"Errore get_current_user: {e}")
        return None


def calcola_eta(data_nascita) -> int:
    try:
        if isinstance(data_nascita, str):
            nasc = datetime.strptime(data_nascita, "%Y-%m-%d").date()
        else:
            nasc = data_nascita
        oggi = date.today()
        return oggi.year - nasc.year - ((oggi.month, oggi.day) < (nasc.month, nasc.day))
    except:
        return 0


def get_interessi_utente(user_id: int) -> list:
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT i.nome FROM user_interests ui
            JOIN interests i ON i.id = ui.interest_id
            WHERE ui.user_id = %s
        """, (user_id,))
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [r["nome"] for r in rows]
    except:
        return []


@app.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: int):
    await manager.connect(user_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(user_id, websocket)


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
    cur = conn.cursor()
    try:
        hashed = hash_password(password)
        cur.execute("""
            INSERT INTO users (email, password_hash, username, nome, data_nascita,
                               genere, orientamento, bio, citta)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (email, hashed, username, nome, data_nascita, genere, orientamento, bio or None, citta or None))
        user_id = cur.fetchone()["id"]
        cur.execute("INSERT INTO user_preferences (user_id) VALUES (%s)", (user_id,))
        conn.commit()
        request.session["user_id"] = user_id
        return RedirectResponse("/discover", status_code=303)
    except psycopg2.IntegrityError:
        conn.rollback()
        return templates.TemplateResponse("register.html", {
            "request": request,
            "error": "Email o username già in uso"
        })
    finally:
        cur.close()
        conn.close()


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login")
async def login(request: Request, email: str = Form(...), password: str = Form(...)):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM users WHERE email = %s AND stato = 'attivo'", (email,))
        user = cur.fetchone()

        if not user:
            return templates.TemplateResponse("login.html", {
                "request": request,
                "error": "Email non trovata. Registrati prima."
            })

        if not verify_password(password, user["password_hash"]):
            return templates.TemplateResponse("login.html", {
                "request": request,
                "error": "Password non corretta"
            })

        request.session.clear()
        request.session["user_id"] = user["id"]

        try:
            cur.execute(
                "UPDATE users SET is_online = 1, ultimo_accesso = CURRENT_TIMESTAMP WHERE id = %s",
                (user["id"],)
            )
            conn.commit()
        except:
            pass

        return RedirectResponse("/discover", status_code=303)
    finally:
        cur.close()
        conn.close()


@app.get("/logout")
async def logout(request: Request):
    user_id = request.session.get("user_id")
    if user_id:
        try:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("UPDATE users SET is_online = 0 WHERE id = %s", (user_id,))
            conn.commit()
            cur.close()
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
    cur = conn.cursor()
    cur.execute("""
        SELECT u.* FROM users u
        WHERE u.id != %s
          AND u.stato = 'attivo'
          AND u.id NOT IN (SELECT to_user_id FROM swipes WHERE from_user_id = %s)
          AND u.id NOT IN (SELECT blocked_id FROM blocks WHERE blocker_id = %s)
          AND u.id NOT IN (SELECT blocker_id FROM blocks WHERE blocked_id = %s)
        ORDER BY RANDOM()
        LIMIT 1
    """, (user["id"], user["id"], user["id"], user["id"]))
    candidates = cur.fetchone()

    if candidates:
        candidate = dict(candidates)
        candidate["eta"] = calcola_eta(candidate["data_nascita"])
        candidate["interessi"] = get_interessi_utente(candidate["id"])
    else:
        candidate = None

    cur.close()
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
    cur = conn.cursor()
    match_created = False

    try:
        cur.execute(
            "INSERT INTO swipes (from_user_id, to_user_id, tipo) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            (user["id"], to_user_id, tipo)
        )

        if tipo in ("like", "superlike"):
            cur.execute("""
                SELECT id FROM swipes
                WHERE from_user_id = %s AND to_user_id = %s
                  AND tipo IN ('like', 'superlike')
            """, (to_user_id, user["id"]))
            reciproco = cur.fetchone()

            if reciproco:
                u1, u2 = sorted([user["id"], to_user_id])
                cur.execute(
                    "INSERT INTO matches (user1_id, user2_id) VALUES (%s, %s) ON CONFLICT DO NOTHING RETURNING id",
                    (u1, u2)
                )
                row = cur.fetchone()
                if row:
                    match_id = row["id"]
                    cur.execute("INSERT INTO conversations (match_id) VALUES (%s)", (match_id,))
                    for uid in (user["id"], to_user_id):
                        cur.execute("""
                            INSERT INTO notifications (user_id, tipo, titolo, contenuto, related_id)
                            VALUES (%s, 'nuovo_match', 'Nuovo Match!', 'Hai un nuovo match!', %s)
                        """, (uid, match_id))
                    match_created = True

        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Errore swipe: {e}")
    finally:
        cur.close()
        conn.close()

    if match_created:
        await manager.send_to_user(user["id"], {
            "type": "nuovo_match",
            "title": "Nuovo Match! 🎉",
            "message": "Hai un nuovo match!"
        })
        await manager.send_to_user(to_user_id, {
            "type": "nuovo_match",
            "title": "Nuovo Match! 🎉",
            "message": "Hai un nuovo match!"
        })
        return RedirectResponse("/matches?new_match=1", status_code=303)

    return RedirectResponse("/discover", status_code=303)


@app.get("/matches", response_class=HTMLResponse)
async def matches_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT m.id as match_id, m.data_match,
               CASE WHEN m.user1_id = %s THEN m.user2_id ELSE m.user1_id END as altro_id,
               u.username, u.nome, u.foto_principale_url, u.is_online,
               c.id as conversation_id
        FROM matches m
        JOIN users u ON u.id = CASE WHEN m.user1_id = %s THEN m.user2_id ELSE m.user1_id END
        LEFT JOIN conversations c ON c.match_id = m.id
        WHERE (m.user1_id = %s OR m.user2_id = %s) AND m.attivo = 1
        ORDER BY m.data_match DESC
    """, (user["id"], user["id"], user["id"], user["id"]))
    matches = cur.fetchall()

    match_list = []
    for m in matches:
        d = dict(m)
        try:
            cur.execute("SELECT data_nascita FROM users WHERE id = %s", (d["altro_id"],))
            row = cur.fetchone()
            d["eta"] = calcola_eta(row["data_nascita"]) if row else 0
        except:
            d["eta"] = 0
        match_list.append(d)

    new_match = request.query_params.get("new_match")
    cur.close()
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
    cur = conn.cursor()
    cur.execute("""
        SELECT c.id, m.user1_id, m.user2_id
        FROM conversations c
        JOIN matches m ON m.id = c.match_id
        WHERE c.id = %s
    """, (conversation_id,))
    conv = cur.fetchone()

    if not conv or user["id"] not in (conv["user1_id"], conv["user2_id"]):
        cur.close()
        conn.close()
        return RedirectResponse("/matches", status_code=303)

    altro_id = conv["user2_id"] if user["id"] == conv["user1_id"] else conv["user1_id"]
    cur.execute("SELECT * FROM users WHERE id = %s", (altro_id,))
    altro = dict(cur.fetchone())
    altro["eta"] = calcola_eta(altro["data_nascita"])

    cur.execute("""
        SELECT m.*, u.nome FROM messages m
        JOIN users u ON u.id = m.sender_id
        WHERE m.conversation_id = %s AND m.eliminato = 0
        ORDER BY m.data_invio
    """, (conversation_id,))
    messaggi = cur.fetchall()

    cur.execute(
        "UPDATE messages SET letto = 1 WHERE conversation_id = %s AND sender_id != %s AND letto = 0",
        (conversation_id, user["id"])
    )
    conn.commit()
    cur.close()
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
    cur = conn.cursor()
    cur.execute("""
        SELECT c.id, m.user1_id, m.user2_id FROM conversations c
        JOIN matches m ON m.id = c.match_id WHERE c.id = %s
    """, (conversation_id,))
    conv = cur.fetchone()

    if not conv or user["id"] not in (conv["user1_id"], conv["user2_id"]):
        cur.close()
        conn.close()
        return RedirectResponse("/matches", status_code=303)

    cur.execute(
        "INSERT INTO messages (conversation_id, sender_id, tipo, contenuto) VALUES (%s, %s, 'testo', %s)",
        (conversation_id, user["id"], contenuto)
    )
    cur.execute(
        "UPDATE conversations SET ultimo_messaggio_at = CURRENT_TIMESTAMP WHERE id = %s",
        (conversation_id,)
    )

    altro_id = conv["user2_id"] if user["id"] == conv["user1_id"] else conv["user1_id"]
    cur.execute("""
        INSERT INTO notifications (user_id, tipo, titolo, contenuto, related_id)
        VALUES (%s, 'nuovo_messaggio', 'Nuovo messaggio', %s, %s)
    """, (altro_id, contenuto[:80], conversation_id))
    conn.commit()
    cur.close()
    conn.close()

    await manager.send_to_user(altro_id, {
        "type": "nuovo_messaggio",
        "title": "Nuovo messaggio",
        "message": contenuto[:80],
        "conversation_id": conversation_id
    })

    return RedirectResponse(f"/chat/{conversation_id}", status_code=303)


@app.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    user["eta"] = calcola_eta(user["data_nascita"])
    interessi = get_interessi_utente(user["id"])

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM interests ORDER BY nome")
    all_interests = cur.fetchall()
    cur.execute("SELECT * FROM user_preferences WHERE user_id = %s", (user["id"],))
    prefs = cur.fetchone()
    cur.close()
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
    cur = conn.cursor()
    cur.execute("""
        UPDATE users SET bio = %s, citta = %s, altezza = %s, fuma = %s, beve = %s, cerca = %s
        WHERE id = %s
    """, (bio or None, citta or None, altezza, fuma or None, beve or None, cerca or None, user["id"]))
    conn.commit()
    cur.close()
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
    cur = conn.cursor()
    cur.execute("DELETE FROM user_interests WHERE user_id = %s", (user["id"],))
    for iid in selected:
        try:
            cur.execute(
                "INSERT INTO user_interests (user_id, interest_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (user["id"], int(iid))
            )
        except:
            pass
    conn.commit()
    cur.close()
    conn.close()
    return RedirectResponse("/profile", status_code=303)


@app.get("/notifications", response_class=HTMLResponse)
async def notifications_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT * FROM notifications WHERE user_id = %s
        ORDER BY data_creazione DESC LIMIT 50
    """, (user["id"],))
    notifs = cur.fetchall()
    cur.execute("UPDATE notifications SET letto = 1 WHERE user_id = %s", (user["id"],))
    conn.commit()
    cur.close()
    conn.close()

    return templates.TemplateResponse("notifications.html", {
        "request": request,
        "user": user,
        "notifications": [dict(n) for n in notifs]
    })
