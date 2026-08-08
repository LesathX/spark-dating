#!/usr/bin/env python3
from fastapi import FastAPI, Request, Form, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from passlib.context import CryptContext
from pathlib import Path
from datetime import datetime, date
from typing import Optional, Dict, List
import os
import math
import psycopg2
from psycopg2.extras import RealDictCursor

BASE_DIR = Path(__file__).parent
SECRET_KEY = os.environ.get("SECRET_KEY", "mycheating_secret_key_2026")

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, session_cookie="session", max_age=2592000, same_site="lax", https_only=False)

if (BASE_DIR / "static").exists():
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

class CM:
    def __init__(self):
        self.conns: Dict[int, List[WebSocket]] = {}
    async def connect(self, uid, ws):
        await ws.accept()
        self.conns.setdefault(uid, []).append(ws)
    def disconnect(self, uid, ws):
        if uid in self.conns and ws in self.conns[uid]:
            self.conns[uid].remove(ws)
            if not self.conns[uid]:
                del self.conns[uid]
    async def send(self, uid, msg):
        for ws in self.conns.get(uid, []):
            try:
                await ws.send_json(msg)
            except Exception:
                pass

manager = CM()

def db():
    return psycopg2.connect(
        host="aws-1-eu-west-1.pooler.supabase.com",
        port=5432,
        database="postgres",
        user="postgres.xbetgvmqqadkthydwxyr",
        password="YIM3kn5OXQtU2EQ8",
        cursor_factory=RealDictCursor,
        sslmode="require",
    )

def hash_pw(p):
    return pwd.hash(p)

def check_pw(p, h):
    try:
        return pwd.verify(p, h)
    except Exception:
        return False

def current_user(req):
    uid = req.session.get("user_id")
    if not uid:
        return None
    try:
        c = db()
        cur = c.cursor()
        cur.execute("SELECT * FROM users WHERE id=%s AND stato='attivo'", (uid,))
        u = cur.fetchone()
        cur.close()
        c.close()
        return dict(u) if u else None
    except Exception as e:
        print("current_user error:", e)
        return None

def eta(d):
    try:
        if isinstance(d, date):
            n = d
        else:
            n = datetime.strptime(str(d)[:10], "%Y-%m-%d").date()
        o = date.today()
        return o.year - n.year - ((o.month, o.day) < (n.month, n.day))
    except Exception:
        return 0

def unread_count(user_id):
    try:
        c = db()
        cur = c.cursor()
        cur.execute("SELECT COUNT(*) as c FROM notifications WHERE user_id=%s AND letto=0", (user_id,))
        n = cur.fetchone()["c"]
        cur.close()
        c.close()
        return n
    except Exception:
        return 0

def likes_received_count(user_id):
    try:
        c = db()
        cur = c.cursor()
        cur.execute("""
            SELECT COUNT(*) as c FROM swipes s
            WHERE s.to_user_id = %s
              AND s.tipo IN ('like', 'superlike')
              AND s.from_user_id NOT IN (
                  SELECT CASE WHEN m.user1_id = %s THEN m.user2_id ELSE m.user1_id END
                  FROM matches m WHERE (m.user1_id = %s OR m.user2_id = %s) AND m.attivo = 1
              )
              AND s.from_user_id NOT IN (SELECT to_user_id FROM swipes WHERE from_user_id = %s)
        """, (user_id, user_id, user_id, user_id, user_id))
        n = cur.fetchone()["c"]
        cur.close()
        c.close()
        return n
    except Exception as e:
        print("likes_received_count error:", e)
        return 0

def haversine_km(lat1, lon1, lat2, lon2):
    if None in (lat1, lon1, lat2, lon2):
        return None
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def require_admin(req):
    user = current_user(req)
    if not user or not user.get("is_admin"):
        return None
    return user

@app.websocket("/ws/{uid}")
async def ws_endpoint(websocket: WebSocket, uid: int):
    await manager.connect(uid, websocket)
    try:
        while True:
            await websocket.receive_text()
    except Exception:
        manager.disconnect(uid, websocket)

@app.get("/", response_class=HTMLResponse)
async def home(req: Request):
    if current_user(req):
        return RedirectResponse("/discover", 303)
    return templates.TemplateResponse("index.html", {"request": req})

@app.get("/register", response_class=HTMLResponse)
async def reg_page(req: Request):
    return templates.TemplateResponse("register.html", {"request": req, "error": None})

@app.post("/register")
async def reg(req: Request, email: str = Form(...), password: str = Form(...), username: str = Form(...),
              nome: str = Form(...), data_nascita: str = Form(...), genere: str = Form(...),
              orientamento: str = Form(...), bio: str = Form(""), citta: str = Form("")):
    c = db()
    cur = c.cursor()
    try:
        cur.execute("""INSERT INTO users (email,password_hash,username,nome,data_nascita,genere,orientamento,bio,citta)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (email, hash_pw(password), username, nome, data_nascita, genere, orientamento, bio or None, citta or None))
        uid = cur.fetchone()["id"]
        cur.execute("INSERT INTO user_preferences (user_id) VALUES (%s)", (uid,))
        c.commit()
        req.session["user_id"] = uid
        return RedirectResponse("/discover", 303)
    except psycopg2.IntegrityError:
        c.rollback()
        return templates.TemplateResponse("register.html", {"request": req, "error": "Email o username gia in uso"})
    finally:
        cur.close()
        c.close()

@app.get("/login", response_class=HTMLResponse)
async def login_page(req: Request):
    return templates.TemplateResponse("login.html", {"request": req, "error": None})

@app.post("/login")
async def login(req: Request, email: str = Form(...), password: str = Form(...)):
    c = db()
    cur = c.cursor()
    try:
        cur.execute("SELECT * FROM users WHERE email=%s AND stato='attivo'", (email,))
        u = cur.fetchone()
        if not u:
            return templates.TemplateResponse("login.html", {"request": req, "error": "Email non trovata"})
        if not check_pw(password, u["password_hash"]):
            return templates.TemplateResponse("login.html", {"request": req, "error": "Password errata"})
        req.session.clear()
        req.session["user_id"] = u["id"]
        try:
            cur.execute("UPDATE users SET is_online=1, ultimo_accesso=CURRENT_TIMESTAMP WHERE id=%s", (u["id"],))
            c.commit()
        except Exception:
            pass
        return RedirectResponse("/discover", 303)
    finally:
        cur.close()
        c.close()

@app.get("/logout")
async def logout(req: Request):
    uid = req.session.get("user_id")
    if uid:
        try:
            c = db()
            cur = c.cursor()
            cur.execute("UPDATE users SET is_online=0 WHERE id=%s", (uid,))
            c.commit()
            cur.close()
            c.close()
        except Exception:
            pass
    req.session.clear()
    return RedirectResponse("/", 303)

@app.post("/location")
async def save_location(req: Request):
    user = current_user(req)
    if not user:
        return JSONResponse({"ok": False, "error": "not logged in"}, status_code=401)
    try:
        body = await req.json()
        lat = float(body.get("latitude"))
        lng = float(body.get("longitude"))
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            return JSONResponse({"ok": False, "error": "invalid coords"}, status_code=400)
        c = db()
        cur = c.cursor()
        cur.execute(
            "UPDATE users SET latitude=%s, longitude=%s, location_updated_at=CURRENT_TIMESTAMP WHERE id=%s",
            (lat, lng, user["id"]),
        )
        c.commit()
        cur.close()
        c.close()
        return JSONResponse({"ok": True, "latitude": lat, "longitude": lng})
    except Exception as e:
        print("location error:", e)
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

@app.get("/discover", response_class=HTMLResponse)
async def discover(req: Request):
    user = current_user(req)
    if not user:
        return RedirectResponse("/login", 303)

    only_online = req.query_params.get("online") == "1"

    # max distance from preferences
    distanza_max = 50
    try:
        c0 = db()
        cur0 = c0.cursor()
        cur0.execute("SELECT distanza_max FROM user_preferences WHERE user_id=%s", (user["id"],))
        pref = cur0.fetchone()
        if pref and pref.get("distanza_max"):
            distanza_max = int(pref["distanza_max"])
        cur0.close()
        c0.close()
    except Exception:
        pass

    my_lat = user.get("latitude")
    my_lng = user.get("longitude")
    has_gps = my_lat is not None and my_lng is not None

    c = db()
    cur = c.cursor()

    if has_gps:
        # Haversine distance in SQL, order by nearest first
        cur.execute("""
            SELECT u.*,
                   (6371 * acos(
                       LEAST(1.0, GREATEST(-1.0,
                           cos(radians(%s)) * cos(radians(u.latitude))
                           * cos(radians(u.longitude) - radians(%s))
                           + sin(radians(%s)) * sin(radians(u.latitude))
                       ))
                   )) AS distance_km
            FROM users u
            WHERE u.id != %s
              AND u.stato = 'attivo'
              AND u.latitude IS NOT NULL
              AND u.longitude IS NOT NULL
              AND u.id NOT IN (SELECT to_user_id FROM swipes WHERE from_user_id = %s)
              AND u.id NOT IN (SELECT blocked_id FROM blocks WHERE blocker_id = %s)
              AND u.id NOT IN (SELECT blocker_id FROM blocks WHERE blocked_id = %s)
              AND (6371 * acos(
                       LEAST(1.0, GREATEST(-1.0,
                           cos(radians(%s)) * cos(radians(u.latitude))
                           * cos(radians(u.longitude) - radians(%s))
                           + sin(radians(%s)) * sin(radians(u.latitude))
                       ))
                   )) <= %s
            ORDER BY distance_km ASC
            LIMIT 1
        """, (my_lat, my_lng, my_lat, user["id"], user["id"], user["id"], user["id"],
              my_lat, my_lng, my_lat, distanza_max))
        cand = cur.fetchone()

        # fallback: users without GPS if no nearby found
        if not cand:
            online_sql = " AND u.is_online = 1" if only_online else ""
            cur.execute(f"""
                SELECT u.* FROM users u
                WHERE u.id != %s AND u.stato = 'attivo'
                  AND u.id NOT IN (SELECT to_user_id FROM swipes WHERE from_user_id = %s)
                  AND u.id NOT IN (SELECT blocked_id FROM blocks WHERE blocker_id = %s)
                  AND u.id NOT IN (SELECT blocker_id FROM blocks WHERE blocked_id = %s)
                  {online_sql}
                ORDER BY RANDOM() LIMIT 1
            """, (user["id"], user["id"], user["id"], user["id"]))
            cand = cur.fetchone()
    else:
        online_sql = " AND u.is_online = 1" if only_online else ""
        cur.execute(f"""
            SELECT u.* FROM users u
            WHERE u.id != %s AND u.stato = 'attivo'
              AND u.id NOT IN (SELECT to_user_id FROM swipes WHERE from_user_id = %s)
              AND u.id NOT IN (SELECT blocked_id FROM blocks WHERE blocker_id = %s)
              AND u.id NOT IN (SELECT blocker_id FROM blocks WHERE blocked_id = %s)
              {online_sql}
            ORDER BY RANDOM() LIMIT 1
        """, (user["id"], user["id"], user["id"], user["id"]))
        cand = cur.fetchone()

    candidate = None
    if cand:
        candidate = dict(cand)
        candidate["eta"] = eta(candidate["data_nascita"])
        candidate["interessi"] = []
        if candidate.get("distance_km") is not None:
            candidate["distance_km"] = round(float(candidate["distance_km"]), 1)
        elif has_gps and candidate.get("latitude") and candidate.get("longitude"):
            d = haversine_km(my_lat, my_lng, candidate["latitude"], candidate["longitude"])
            candidate["distance_km"] = round(d, 1) if d is not None else None
        else:
            candidate["distance_km"] = None
        try:
            cur.execute("SELECT i.nome FROM user_interests ui JOIN interests i ON i.id=ui.interest_id WHERE ui.user_id=%s", (candidate["id"],))
            candidate["interessi"] = [r["nome"] for r in cur.fetchall()]
        except Exception:
            pass

    cur.close()
    c.close()
    return templates.TemplateResponse("discover.html", {
        "request": req,
        "user": user,
        "candidate": candidate,
        "unread": unread_count(user["id"]),
        "likes_count": likes_received_count(user["id"]),
        "has_gps": has_gps,
        "distanza_max": distanza_max,
        "only_online": only_online,
    })

@app.post("/swipe")
async def swipe(req: Request, to_user_id: int = Form(...), tipo: str = Form(...)):
    user = current_user(req)
    if not user:
        return RedirectResponse("/login", 303)
    if tipo not in ("like", "dislike", "superlike"):
        return RedirectResponse("/discover", 303)
    c = db()
    cur = c.cursor()
    match_created = False
    try:
        cur.execute("INSERT INTO swipes (from_user_id,to_user_id,tipo) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING", (user["id"], to_user_id, tipo))
        if tipo in ("like", "superlike"):
            cur.execute("SELECT id FROM swipes WHERE from_user_id=%s AND to_user_id=%s AND tipo IN ('like','superlike')", (to_user_id, user["id"]))
            if cur.fetchone():
                u1, u2 = sorted([user["id"], to_user_id])
                cur.execute("INSERT INTO matches (user1_id,user2_id) VALUES (%s,%s) ON CONFLICT DO NOTHING RETURNING id", (u1, u2))
                row = cur.fetchone()
                if row:
                    mid = row["id"]
                    cur.execute("INSERT INTO conversations (match_id) VALUES (%s)", (mid,))
                    for uid in (user["id"], to_user_id):
                        cur.execute("INSERT INTO notifications (user_id,tipo,titolo,contenuto,related_id) VALUES (%s,'nuovo_match','Nuovo Match!','Hai un nuovo match!',%s)", (uid, mid))
                    match_created = True
        c.commit()
    except Exception as e:
        c.rollback()
        print("swipe error:", e)
    finally:
        cur.close()
        c.close()
    if match_created:
        await manager.send(user["id"], {"type": "nuovo_match", "title": "Nuovo Match!", "message": "Hai un nuovo match!"})
        await manager.send(to_user_id, {"type": "nuovo_match", "title": "Nuovo Match!", "message": "Hai un nuovo match!"})
        return RedirectResponse("/chats?new_match=1", 303)
    return RedirectResponse("/discover", 303)


@app.get("/chats", response_class=HTMLResponse)
async def chats_page(req: Request):
    user = current_user(req)
    if not user:
        return RedirectResponse("/login", 303)
    c = db()
    cur = c.cursor()
    cur.execute("""SELECT m.id as match_id, m.data_match,
                  CASE WHEN m.user1_id=%s THEN m.user2_id ELSE m.user1_id END as altro_id,
                  u.username, u.nome, u.foto_principale_url, u.is_online, u.latitude, u.longitude,
                  c.id as conversation_id
           FROM matches m
           JOIN users u ON u.id = CASE WHEN m.user1_id=%s THEN m.user2_id ELSE m.user1_id END
           LEFT JOIN conversations c ON c.match_id = m.id
           WHERE (m.user1_id=%s OR m.user2_id=%s) AND m.attivo=1
           ORDER BY COALESCE(c.ultimo_messaggio_at, m.data_match) DESC""", (user["id"], user["id"], user["id"], user["id"]))
    matches = cur.fetchall()
    match_list = []
    my_lat, my_lng = user.get("latitude"), user.get("longitude")
    for m in matches:
        d = dict(m)
        try:
            cur.execute("SELECT data_nascita FROM users WHERE id=%s", (d["altro_id"],))
            row = cur.fetchone()
            d["eta"] = eta(row["data_nascita"]) if row else 0
        except Exception:
            d["eta"] = 0
        if my_lat and my_lng and d.get("latitude") and d.get("longitude"):
            dist = haversine_km(my_lat, my_lng, d["latitude"], d["longitude"])
            d["distance_km"] = round(dist, 1) if dist is not None else None
        else:
            d["distance_km"] = None
        match_list.append(d)
    new_match = req.query_params.get("new_match")
    cur.close()
    c.close()
    return templates.TemplateResponse("chats.html", {
        "request": req, "user": user, "matches": match_list, "new_match": new_match, "unread": unread_count(user["id"])
    })


@app.get("/matches", response_class=HTMLResponse)
async def matches_page(req: Request):
    user = current_user(req)
    if not user:
        return RedirectResponse("/login", 303)
    c = db()
    cur = c.cursor()
    cur.execute("""SELECT m.id as match_id, m.data_match,
                  CASE WHEN m.user1_id=%s THEN m.user2_id ELSE m.user1_id END as altro_id,
                  u.username, u.nome, u.foto_principale_url, u.is_online, u.latitude, u.longitude,
                  c.id as conversation_id
           FROM matches m
           JOIN users u ON u.id = CASE WHEN m.user1_id=%s THEN m.user2_id ELSE m.user1_id END
           LEFT JOIN conversations c ON c.match_id = m.id
           WHERE (m.user1_id=%s OR m.user2_id=%s) AND m.attivo=1
           ORDER BY m.data_match DESC""", (user["id"], user["id"], user["id"], user["id"]))
    matches = cur.fetchall()
    match_list = []
    my_lat, my_lng = user.get("latitude"), user.get("longitude")
    for m in matches:
        d = dict(m)
        try:
            cur.execute("SELECT data_nascita FROM users WHERE id=%s", (d["altro_id"],))
            row = cur.fetchone()
            d["eta"] = eta(row["data_nascita"]) if row else 0
        except Exception:
            d["eta"] = 0
        if my_lat and my_lng and d.get("latitude") and d.get("longitude"):
            dist = haversine_km(my_lat, my_lng, d["latitude"], d["longitude"])
            d["distance_km"] = round(dist, 1) if dist is not None else None
        else:
            d["distance_km"] = None
        match_list.append(d)
    new_match = req.query_params.get("new_match")
    cur.close()
    c.close()
    return templates.TemplateResponse("matches.html", {
        "request": req, "user": user, "matches": match_list, "new_match": new_match, "unread": unread_count(user["id"])
    })

@app.get("/chat/{conversation_id}", response_class=HTMLResponse)
async def chat_page(req: Request, conversation_id: int):
    user = current_user(req)
    if not user:
        return RedirectResponse("/login", 303)
    c = db()
    cur = c.cursor()
    cur.execute("""SELECT c.id, m.user1_id, m.user2_id FROM conversations c
           JOIN matches m ON m.id = c.match_id WHERE c.id=%s""", (conversation_id,))
    conv = cur.fetchone()
    if not conv or user["id"] not in (conv["user1_id"], conv["user2_id"]):
        cur.close()
        c.close()
        return RedirectResponse("/chats", 303)
    altro_id = conv["user2_id"] if user["id"] == conv["user1_id"] else conv["user1_id"]
    cur.execute("SELECT * FROM users WHERE id=%s", (altro_id,))
    altro = dict(cur.fetchone())
    altro["eta"] = eta(altro["data_nascita"])
    cur.execute("""SELECT m.*, u.nome FROM messages m JOIN users u ON u.id=m.sender_id
           WHERE m.conversation_id=%s AND m.eliminato=0 ORDER BY m.data_invio""", (conversation_id,))
    messaggi = cur.fetchall()
    cur.execute("UPDATE messages SET letto=1 WHERE conversation_id=%s AND sender_id!=%s AND letto=0", (conversation_id, user["id"]))
    c.commit()
    cur.close()
    c.close()
    return templates.TemplateResponse("chat.html", {
        "request": req, "user": user, "altro": altro, "conversation_id": conversation_id,
        "messaggi": [dict(m) for m in messaggi], "unread": unread_count(user["id"])
    })

@app.post("/chat/{conversation_id}/send")
async def send_message(req: Request, conversation_id: int, contenuto: str = Form(...)):
    user = current_user(req)
    if not user:
        return RedirectResponse("/login", 303)
    c = db()
    cur = c.cursor()
    cur.execute("""SELECT c.id, m.user1_id, m.user2_id FROM conversations c
           JOIN matches m ON m.id = c.match_id WHERE c.id=%s""", (conversation_id,))
    conv = cur.fetchone()
    if not conv or user["id"] not in (conv["user1_id"], conv["user2_id"]):
        cur.close()
        c.close()
        return RedirectResponse("/chats", 303)
    cur.execute("INSERT INTO messages (conversation_id,sender_id,tipo,contenuto) VALUES (%s,%s,'testo',%s)", (conversation_id, user["id"], contenuto))
    cur.execute("UPDATE conversations SET ultimo_messaggio_at=CURRENT_TIMESTAMP WHERE id=%s", (conversation_id,))
    altro_id = conv["user2_id"] if user["id"] == conv["user1_id"] else conv["user1_id"]
    cur.execute("INSERT INTO notifications (user_id,tipo,titolo,contenuto,related_id) VALUES (%s,'nuovo_messaggio','Nuovo messaggio',%s,%s)", (altro_id, contenuto[:80], conversation_id))
    c.commit()
    cur.close()
    c.close()
    await manager.send(altro_id, {"type": "nuovo_messaggio", "title": "Nuovo messaggio", "message": contenuto[:80], "conversation_id": conversation_id})
    return RedirectResponse(f"/chat/{conversation_id}", 303)

@app.get("/profile", response_class=HTMLResponse)
async def profile_page(req: Request):
    user = current_user(req)
    if not user:
        return RedirectResponse("/login", 303)
    user["eta"] = eta(user["data_nascita"])
    interessi = []
    all_interests = []
    prefs = {}
    try:
        c = db()
        cur = c.cursor()
        cur.execute("SELECT i.nome FROM user_interests ui JOIN interests i ON i.id=ui.interest_id WHERE ui.user_id=%s", (user["id"],))
        interessi = [r["nome"] for r in cur.fetchall()]
        cur.execute("SELECT * FROM interests ORDER BY nome")
        all_interests = cur.fetchall()
        cur.execute("SELECT * FROM user_preferences WHERE user_id=%s", (user["id"],))
        prefs = cur.fetchone()
        cur.close()
        c.close()
    except Exception:
        pass
    return templates.TemplateResponse("profile.html", {
        "request": req, "user": user, "interessi": interessi,
        "all_interests": [dict(i) for i in all_interests],
        "prefs": dict(prefs) if prefs else {},
        "unread": unread_count(user["id"])
    })

@app.post("/profile/update")
async def update_profile(req: Request, bio: str = Form(""), citta: str = Form(""), altezza: Optional[int] = Form(None),
                         fuma: str = Form(""), beve: str = Form(""), cerca: str = Form(""),
                         distanza_max: Optional[int] = Form(None)):
    user = current_user(req)
    if not user:
        return RedirectResponse("/login", 303)
    c = db()
    cur = c.cursor()
    cur.execute("UPDATE users SET bio=%s, citta=%s, altezza=%s, fuma=%s, beve=%s, cerca=%s WHERE id=%s",
                (bio or None, citta or None, altezza, fuma or None, beve or None, cerca or None, user["id"]))
    if distanza_max is not None and 1 <= distanza_max <= 500:
        cur.execute("""INSERT INTO user_preferences (user_id, distanza_max) VALUES (%s, %s)
                       ON CONFLICT (user_id) DO UPDATE SET distanza_max = EXCLUDED.distanza_max""",
                    (user["id"], distanza_max))
    c.commit()
    cur.close()
    c.close()
    return RedirectResponse("/profile", 303)

@app.post("/profile/interessi")
async def update_interessi(req: Request):
    user = current_user(req)
    if not user:
        return RedirectResponse("/login", 303)
    form = await req.form()
    selected = form.getlist("interessi")
    c = db()
    cur = c.cursor()
    cur.execute("DELETE FROM user_interests WHERE user_id=%s", (user["id"],))
    for iid in selected:
        try:
            cur.execute("INSERT INTO user_interests (user_id,interest_id) VALUES (%s,%s) ON CONFLICT DO NOTHING", (user["id"], int(iid)))
        except Exception:
            pass
    c.commit()
    cur.close()
    c.close()
    return RedirectResponse("/profile", 303)

@app.get("/notifications", response_class=HTMLResponse)
async def notifications_page(req: Request):
    user = current_user(req)
    if not user:
        return RedirectResponse("/login", 303)
    c = db()
    cur = c.cursor()
    cur.execute("SELECT * FROM notifications WHERE user_id=%s ORDER BY data_creazione DESC LIMIT 50", (user["id"],))
    notifs = cur.fetchall()
    cur.execute("UPDATE notifications SET letto=1 WHERE user_id=%s", (user["id"],))
    c.commit()
    cur.close()
    c.close()
    return templates.TemplateResponse("notifications.html", {
        "request": req, "user": user, "notifications": [dict(n) for n in notifs], "unread": 0
    })



@app.get("/likes", response_class=HTMLResponse)
async def likes_page(req: Request):
    user = current_user(req)
    if not user:
        return RedirectResponse("/login", 303)
    c = db()
    cur = c.cursor()
    cur.execute("""
        SELECT u.* FROM swipes s
        JOIN users u ON u.id = s.from_user_id
        WHERE s.to_user_id = %s
          AND s.tipo IN ('like', 'superlike')
          AND u.stato = 'attivo'
          AND s.from_user_id NOT IN (
              SELECT CASE WHEN m.user1_id = %s THEN m.user2_id ELSE m.user1_id END
              FROM matches m WHERE (m.user1_id = %s OR m.user2_id = %s) AND m.attivo = 1
          )
          AND s.from_user_id NOT IN (SELECT to_user_id FROM swipes WHERE from_user_id = %s)
        ORDER BY s.created_at DESC
        LIMIT 50
    """, (user["id"], user["id"], user["id"], user["id"], user["id"]))
    rows = cur.fetchall()
    my_lat, my_lng = user.get("latitude"), user.get("longitude")
    likes = []
    for r in rows:
        d = dict(r)
        d["eta"] = eta(d.get("data_nascita"))
        if my_lat and my_lng and d.get("latitude") and d.get("longitude"):
            dist = haversine_km(my_lat, my_lng, d["latitude"], d["longitude"])
            d["distance_km"] = round(dist, 1) if dist is not None else None
        else:
            d["distance_km"] = None
        likes.append(d)
    cur.close()
    c.close()
    return templates.TemplateResponse("likes.html", {
        "request": req, "user": user, "likes": likes,
        "unread": unread_count(user["id"]),
        "likes_count": len(likes),
    })


@app.post("/block/{user_id}")
async def block_user(req: Request, user_id: int):
    user = current_user(req)
    if not user:
        return RedirectResponse("/login", 303)
    if user_id == user["id"]:
        return RedirectResponse("/discover", 303)
    c = db()
    cur = c.cursor()
    try:
        cur.execute(
            "INSERT INTO blocks (blocker_id, blocked_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (user["id"], user_id),
        )
        # also dislike so they don't show again
        cur.execute(
            "INSERT INTO swipes (from_user_id, to_user_id, tipo) VALUES (%s, %s, 'dislike') ON CONFLICT DO NOTHING",
            (user["id"], user_id),
        )
        c.commit()
    except Exception as e:
        c.rollback()
        print("block error:", e)
    finally:
        cur.close()
        c.close()
    return RedirectResponse("/discover", 303)

@app.get("/admin", response_class=HTMLResponse)
async def admin_panel(req: Request):
    user = require_admin(req)
    if not user:
        return RedirectResponse("/login", 303)

    tab = req.query_params.get("tab") or "users"
    q = (req.query_params.get("q") or "").strip()

    c = db()
    cur = c.cursor()

    cur.execute("SELECT COUNT(*) as c FROM users")
    users_count = cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) as c FROM matches WHERE attivo=1")
    matches_count = cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) as c FROM messages")
    messages_count = cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) as c FROM users WHERE is_online=1")
    online_count = cur.fetchone()["c"]
    try:
        cur.execute("SELECT COUNT(*) as c FROM swipes")
        swipes_count = cur.fetchone()["c"]
    except Exception:
        swipes_count = 0
    cur.execute("SELECT COUNT(*) as c FROM users WHERE stato != 'attivo'")
    banned_count = cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) as c FROM users WHERE latitude IS NOT NULL")
    gps_count = cur.fetchone()["c"]

    users = []
    recent_messages = []
    recent_matches = []
    online_users = []

    if tab == "users" or not tab:
        if q:
            like = f"%{q}%"
            cur.execute("""
                SELECT id, nome, username, email, stato, is_admin, is_online, latitude, longitude
                FROM users
                WHERE nome ILIKE %s OR email ILIKE %s OR username ILIKE %s
                ORDER BY id DESC LIMIT 100
            """, (like, like, like))
        else:
            cur.execute("""
                SELECT id, nome, username, email, stato, is_admin, is_online, latitude, longitude
                FROM users ORDER BY id DESC LIMIT 100
            """)
        users = [dict(u) for u in cur.fetchall()]

    if tab == "messages":
        try:
            cur.execute("""
                SELECT m.contenuto, m.data_invio, m.conversation_id, u.nome as sender_nome
                FROM messages m
                JOIN users u ON u.id = m.sender_id
                WHERE COALESCE(m.eliminato, 0) = 0
                ORDER BY m.data_invio DESC LIMIT 40
            """)
            recent_messages = [dict(r) for r in cur.fetchall()]
        except Exception as e:
            print("admin messages error:", e)

    if tab == "matches":
        cur.execute("""
            SELECT m.data_match, u1.nome as nome1, u2.nome as nome2
            FROM matches m
            JOIN users u1 ON u1.id = m.user1_id
            JOIN users u2 ON u2.id = m.user2_id
            ORDER BY m.data_match DESC LIMIT 30
        """)
        recent_matches = [dict(r) for r in cur.fetchall()]

    if tab == "online":
        cur.execute("""
            SELECT id, nome, username FROM users
            WHERE is_online = 1 AND stato = 'attivo'
            ORDER BY ultimo_accesso DESC NULLS LAST LIMIT 50
        """)
        online_users = [dict(r) for r in cur.fetchall()]

    cur.close()
    c.close()

    return templates.TemplateResponse("admin.html", {
        "request": req,
        "user": user,
        "tab": tab,
        "q": q,
        "stats": {
            "users": users_count,
            "matches": matches_count,
            "messages": messages_count,
            "online": online_count,
            "swipes": swipes_count,
            "banned": banned_count,
            "gps": gps_count,
        },
        "users": users,
        "recent_messages": recent_messages,
        "recent_matches": recent_matches,
        "online_users": online_users,
    })


@app.post("/admin/ban/{user_id}")
async def admin_ban(req: Request, user_id: int):
    if not require_admin(req):
        return RedirectResponse("/login", 303)
    c = db()
    cur = c.cursor()
    cur.execute("UPDATE users SET stato='bannato', is_online=0 WHERE id=%s AND COALESCE(is_admin,0)=0", (user_id,))
    c.commit()
    cur.close()
    c.close()
    return RedirectResponse("/admin", 303)

@app.post("/admin/unban/{user_id}")
async def admin_unban(req: Request, user_id: int):
    if not require_admin(req):
        return RedirectResponse("/login", 303)
    c = db()
    cur = c.cursor()
    cur.execute("UPDATE users SET stato='attivo' WHERE id=%s", (user_id,))
    c.commit()
    cur.close()
    c.close()
    return RedirectResponse("/admin", 303)

@app.post("/admin/delete/{user_id}")
async def admin_delete(req: Request, user_id: int):
    if not require_admin(req):
        return RedirectResponse("/login", 303)
    c = db()
    cur = c.cursor()
    cur.execute("DELETE FROM users WHERE id=%s AND COALESCE(is_admin,0)=0", (user_id,))
    c.commit()
    cur.close()
    c.close()
    return RedirectResponse("/admin", 303)

@app.post("/admin/make_admin/{user_id}")
async def admin_make_admin(req: Request, user_id: int):
    if not require_admin(req):
        return RedirectResponse("/login", 303)
    c = db()
    cur = c.cursor()
    cur.execute("UPDATE users SET is_admin=1 WHERE id=%s", (user_id,))
    c.commit()
    cur.close()
    c.close()
    return RedirectResponse("/admin", 303)


@app.post("/admin/remove_admin/{user_id}")
async def admin_remove_admin(req: Request, user_id: int):
    admin = require_admin(req)
    if not admin:
        return RedirectResponse("/login", 303)
    if user_id == admin["id"]:
        return RedirectResponse("/admin", 303)
    c = db()
    cur = c.cursor()
    cur.execute("UPDATE users SET is_admin=0 WHERE id=%s", (user_id,))
    c.commit()
    cur.close()
    c.close()
    return RedirectResponse("/admin", 303)

