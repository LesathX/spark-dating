#!/usr/bin/env python3
from fastapi import FastAPI, Request, Form, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, JSONResponse
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

(BASE_DIR / "static" / "uploads").mkdir(parents=True, exist_ok=True)
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


# ============== INCANTESIMI (costi in crediti) ==============
PHOTO_ACCESS_COST = 25
MAX_UPLOAD_BYTES = 500 * 1024 * 1024  # 500 MB

# Supabase Storage (produzione)
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://xbetgvmqqadkthydwxyr.supabase.co").rstrip("/")
SUPABASE_SERVICE_KEY = os.environ.get(
    "SUPABASE_SERVICE_KEY",
    os.environ.get("SUPABASE_KEY", ""),  # service_role key consigliata
)
STORAGE_BUCKET = os.environ.get("STORAGE_BUCKET", "gallery")


SPELLS = {
    "superlike": {"cost": 5, "label": "Super Like"},
    "messaggio_swipe": {"cost": 10, "label": "Messaggio al like"},
    "rivela_likes": {"cost": 15, "label": "Rivela chi ti piace"},
    "boost": {"cost": 20, "label": "Boost profilo"},
}

def spend_credits(user_id, amount, motivo, related_id=None):
    """Scala crediti e registra transazione. Ritorna True se ok."""
    if amount <= 0:
        return True
    c = db()
    cur = c.cursor()
    try:
        cur.execute("SELECT credits FROM users WHERE id=%s FOR UPDATE", (user_id,))
        row = cur.fetchone()
        if not row or (row["credits"] or 0) < amount:
            c.rollback()
            cur.close()
            c.close()
            return False
        cur.execute("UPDATE users SET credits = credits - %s WHERE id=%s", (amount, user_id))
        cur.execute(
            "INSERT INTO credit_transactions (user_id, amount, motivo, related_id) VALUES (%s, %s, %s, %s)",
            (user_id, -amount, motivo, related_id),
        )
        c.commit()
        return True
    except Exception as e:
        c.rollback()
        print("spend_credits error:", e)
        return False
    finally:
        cur.close()
        c.close()

def add_credits(user_id, amount, motivo="ricarica"):
    c = db()
    cur = c.cursor()
    try:
        cur.execute("UPDATE users SET credits = COALESCE(credits,0) + %s WHERE id=%s", (amount, user_id))
        cur.execute(
            "INSERT INTO credit_transactions (user_id, amount, motivo) VALUES (%s, %s, %s)",
            (user_id, amount, motivo),
        )
        c.commit()
    except Exception as e:
        c.rollback()
        print("add_credits error:", e)
    finally:
        cur.close()
        c.close()

def require_mod(req):
    """Admin o moderatore."""
    user = current_user(req)
    if not user:
        return None
    if user.get("is_admin") or user.get("is_mod") or user.get("ruolo") in ("admin", "mod"):
        return user
    return None


def get_restrictions(user_id):
    defaults = {
        "no_gallery": 0, "no_like": 0, "no_messaggi": 0, "no_primo_messaggio": 0,
        "no_scopri": 0, "no_chat": 0, "no_vedi_foto": 0, "no_commenti": 0, "no_storie": 0,
    }
    try:
        c = db()
        cur = c.cursor()
        cur.execute("SELECT * FROM user_restrictions WHERE user_id=%s", (user_id,))
        row = cur.fetchone()
        cur.close()
        c.close()
        if row:
            d = dict(row)
            for k in defaults:
                defaults[k] = int(d.get(k) or 0)
        return defaults
    except Exception as e:
        print("get_restrictions:", e)
        return defaults

def is_suspended(user):
    if not user:
        return True
    if user.get("stato") == "bannato":
        return True
    fino = user.get("sospeso_fino")
    if fino:
        from datetime import datetime
        try:
            if isinstance(fino, str):
                fino = datetime.fromisoformat(fino.replace("Z", ""))
            if fino > datetime.utcnow().replace(tzinfo=getattr(fino, "tzinfo", None)):
                return True
        except Exception:
            pass
    return False

def require_admin(req):
    user = current_user(req)
    if not user:
        return None
    if user.get("is_admin") or user.get("role") == "admin":
        return user
    return None

def require_staff(req):
    """Admin or moderator"""
    user = current_user(req)
    if not user:
        return None
    role = user.get("role") or ("admin" if user.get("is_admin") else "user")
    if role in ("admin", "mod") or user.get("is_admin"):
        return user
    return None

def get_role(user):
    if not user:
        return "user"
    if user.get("is_admin") or user.get("role") == "admin":
        return "admin"
    if user.get("role") == "mod":
        return "mod"
    return "user"

def add_credits(user_id, amount, motivo=""):
    c = db()
    cur = c.cursor()
    cur.execute("UPDATE users SET credits = COALESCE(credits,0) + %s WHERE id=%s RETURNING credits", (amount, user_id))
    row = cur.fetchone()
    cur.execute("INSERT INTO credit_transactions (user_id, amount, motivo) VALUES (%s,%s,%s)", (user_id, amount, motivo))
    c.commit()
    cur.close()
    c.close()
    return row["credits"] if row else None

def spend_credits(user_id, amount, motivo=""):
    c = db()
    cur = c.cursor()
    cur.execute("SELECT COALESCE(credits,0) as credits FROM users WHERE id=%s", (user_id,))
    row = cur.fetchone()
    if not row or row["credits"] < amount:
        cur.close()
        c.close()
        return False, row["credits"] if row else 0
    cur.execute("UPDATE users SET credits = credits - %s WHERE id=%s RETURNING credits", (amount, user_id))
    new_c = cur.fetchone()["credits"]
    cur.execute("INSERT INTO credit_transactions (user_id, amount, motivo) VALUES (%s,%s,%s)", (user_id, -amount, motivo))
    c.commit()
    cur.close()
    c.close()
    return True, new_c

def active_spell(user_id, spell_code):
    try:
        c = db()
        cur = c.cursor()
        cur.execute("""
            SELECT * FROM spell_uses
            WHERE user_id=%s AND spell_code=%s
              AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
            ORDER BY created_at DESC LIMIT 1
        """, (user_id, spell_code))
        row = cur.fetchone()
        cur.close()
        c.close()
        return dict(row) if row else None
    except Exception:
        return None

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
    if is_suspended(user):
        return RedirectResponse("/profile?err=sospeso", 303)
    r = get_restrictions(user["id"])
    if r.get("no_scopri"):
        return RedirectResponse("/profile?err=restrizione", 303)

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
async def swipe(req: Request, to_user_id: int = Form(...), tipo: str = Form(...), messaggio: str = Form("")):
    user = current_user(req)
    if not user:
        return RedirectResponse("/login", 303)
    if is_suspended(user):
        return RedirectResponse("/profile?err=sospeso", 303)
    r = get_restrictions(user["id"])
    if tipo in ("like", "superlike") and r.get("no_like"):
        return RedirectResponse("/discover?err=no_like", 303)
    if messaggio and r.get("no_primo_messaggio"):
        messaggio = ""
    if tipo not in ("like", "dislike", "superlike"):
        return RedirectResponse("/discover", 303)

    # Superlike costa crediti
    if tipo == "superlike":
        cost = SPELLS["superlike"]["cost"]
        if not spend_credits(user["id"], cost, "superlike", to_user_id):
            return RedirectResponse("/discover?err=crediti", 303)

    # Messaggio al like costa crediti
    messaggio = (messaggio or "").strip()
    if messaggio and tipo in ("like", "superlike"):
        cost = SPELLS["messaggio_swipe"]["cost"]
        if not spend_credits(user["id"], cost, "messaggio_swipe", to_user_id):
            messaggio = ""  # senza crediti non invia messaggio ma fa comunque like
        else:
            try:
                c0 = db()
                cur0 = c0.cursor()
                cur0.execute(
                    "INSERT INTO swipe_messages (from_user_id,to_user_id,contenuto,credits_spent) VALUES (%s,%s,%s,%s)",
                    (user["id"], to_user_id, messaggio[:500], SPELLS["messaggio_swipe"]["cost"]),
                )
                c0.commit()
                cur0.close()
                c0.close()
            except Exception as e:
                print("swipe_message error:", e)

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
                    cur.execute("INSERT INTO conversations (match_id) VALUES (%s) RETURNING id", (mid,))
                    conv_row = cur.fetchone()
                    conv_id = conv_row["id"] if conv_row else None
                    # consegna eventuali messaggi swipe pagati
                    if conv_id:
                        cur.execute("""
                            SELECT id, from_user_id, contenuto FROM swipe_messages
                            WHERE ((from_user_id=%s AND to_user_id=%s) OR (from_user_id=%s AND to_user_id=%s))
                              AND delivered=0
                        """, (user["id"], to_user_id, to_user_id, user["id"]))
                        for sm in cur.fetchall():
                            cur.execute(
                                "INSERT INTO messages (conversation_id,sender_id,tipo,contenuto) VALUES (%s,%s,'testo',%s)",
                                (conv_id, sm["from_user_id"], sm["contenuto"]),
                            )
                            cur.execute("UPDATE swipe_messages SET delivered=1 WHERE id=%s", (sm["id"],))
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
    # stato accesso foto private
    photo_access = None  # none | pending_out | pending_in | approved
    pending_request = None
    try:
        c2 = db()
        cur2 = c2.cursor()
        cur2.execute(
            "SELECT * FROM photo_access_requests WHERE from_user_id=%s AND to_user_id=%s",
            (user["id"], altro["id"]),
        )
        out_req = cur2.fetchone()
        cur2.execute(
            "SELECT * FROM photo_access_requests WHERE from_user_id=%s AND to_user_id=%s AND status='pending'",
            (altro["id"], user["id"]),
        )
        in_req = cur2.fetchone()
        if out_req:
            photo_access = out_req["status"]  # pending / approved / denied
        if in_req:
            pending_request = dict(in_req)
        cur2.close()
        c2.close()
    except Exception as e:
        print("chat photo state:", e)

    return templates.TemplateResponse("chat.html", {
        "request": req, "user": user, "altro": altro, "conversation_id": conversation_id,
        "messaggi": [dict(m) for m in messaggi], "unread": unread_count(user["id"]),
        "photo_access": photo_access,
        "pending_request": pending_request,
        "photo_cost": PHOTO_ACCESS_COST,
    })

@app.post("/chat/{conversation_id}/send")
async def send_message(req: Request, conversation_id: int, contenuto: str = Form(...)):
    user = current_user(req)
    if not user:
        return RedirectResponse("/login", 303)
    if is_suspended(user):
        return RedirectResponse("/profile?err=sospeso", 303)
    r = get_restrictions(user["id"])
    if r.get("no_messaggi") or r.get("no_chat"):
        return RedirectResponse(f"/chat/{conversation_id}?err=no_msg", 303)
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
    filter_stato = req.query_params.get("filter") or "tutti"

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
    conversations = []
    open_conversation = None
    thread_messages = []

    if tab == "users" or not tab:
        if q:
            like = f"%{q}%"
            cur.execute("""
                SELECT id, nome, username, email, stato, is_admin, is_mod, credits, is_online, latitude, longitude, bio, citta, genere, orientamento, data_nascita, ruolo, sospeso_fino, note_admin
                FROM users
                WHERE nome ILIKE %s OR email ILIKE %s OR username ILIKE %s
                ORDER BY id DESC LIMIT 100
            """, (like, like, like))
        else:
            cur.execute("""
                SELECT id, nome, username, email, stato, is_admin, is_mod, credits, is_online, latitude, longitude, bio, citta, genere, orientamento, data_nascita, ruolo, sospeso_fino, note_admin
                FROM users ORDER BY id DESC LIMIT 100
            """)
        users = [dict(u) for u in cur.fetchall()]
        for u in users:
            u["restrictions"] = get_restrictions(u["id"])
            u["sospeso"] = bool(u.get("sospeso_fino"))
            try:
                u["eta"] = eta(u.get("data_nascita"))
            except Exception:
                u["eta"] = None
        if filter_stato == "attivi":
            users = [u for u in users if u.get("stato") == "attivo" and not u.get("sospeso_fino")]
        elif filter_stato == "bannati":
            users = [u for u in users if u.get("stato") == "bannato"]
        elif filter_stato == "sospesi":
            users = [u for u in users if u.get("sospeso_fino") and u.get("stato") != "bannato"]

    conversations = []
    open_conversation = None
    thread_messages = []
    if tab == "messages":
        try:
            cur.execute("""
                SELECT c.id, c.ultimo_messaggio_at, m.data_match,
                       m.user1_id, m.user2_id,
                       u1.nome as nome1, u2.nome as nome2,
                       (SELECT COUNT(*) FROM messages msg WHERE msg.conversation_id = c.id AND COALESCE(msg.eliminato,0)=0) as msg_count,
                       (SELECT msg.contenuto FROM messages msg WHERE msg.conversation_id = c.id AND COALESCE(msg.eliminato,0)=0 ORDER BY msg.data_invio DESC LIMIT 1) as last_message
                FROM conversations c
                JOIN matches m ON m.id = c.match_id
                JOIN users u1 ON u1.id = m.user1_id
                JOIN users u2 ON u2.id = m.user2_id
                ORDER BY COALESCE(c.ultimo_messaggio_at, m.data_match) DESC
                LIMIT 200
            """)
            conversations = [dict(r) for r in cur.fetchall()]

            conv_id = req.query_params.get("conv")
            if conv_id:
                try:
                    conv_id = int(conv_id)
                except Exception:
                    conv_id = None
                if conv_id:
                    cur.execute("""
                        SELECT c.id, m.user1_id, m.user2_id, u1.nome as nome1, u2.nome as nome2
                        FROM conversations c
                        JOIN matches m ON m.id = c.match_id
                        JOIN users u1 ON u1.id = m.user1_id
                        JOIN users u2 ON u2.id = m.user2_id
                        WHERE c.id = %s
                    """, (conv_id,))
                    row = cur.fetchone()
                    if row:
                        open_conversation = dict(row)
                        cur.execute("""
                            SELECT m.id, m.sender_id, m.contenuto, m.data_invio, u.nome as sender_nome
                            FROM messages m
                            JOIN users u ON u.id = m.sender_id
                            WHERE m.conversation_id = %s AND COALESCE(m.eliminato,0)=0
                            ORDER BY m.data_invio ASC
                        """, (conv_id,))
                        thread_messages = [dict(r) for r in cur.fetchall()]
        except Exception as e:
            print("admin messages error:", e)
            conversations = []

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
        "filter": filter_stato if "filter_stato" in dir() else "tutti",
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
        "recent_messages": [],
        "conversations": conversations if tab == "messages" else [],
        "open_conversation": open_conversation if tab == "messages" else None,
        "thread_messages": thread_messages if tab == "messages" else [],
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



# ============== FOTO PROFILO ==============


def storage_enabled():
    return bool(SUPABASE_URL and SUPABASE_SERVICE_KEY)


async def storage_upload(path: str, data: bytes, content_type: str) -> str:
    """Carica su Supabase Storage e ritorna URL pubblico (o path per privati)."""
    import httpx
    url = f"{SUPABASE_URL}/storage/v1/object/{STORAGE_BUCKET}/{path}"
    headers = {
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "apikey": SUPABASE_SERVICE_KEY,
        "Content-Type": content_type or "application/octet-stream",
        "x-upsert": "true",
    }
    async with httpx.AsyncClient(timeout=300.0) as client:
        r = await client.post(url, content=data, headers=headers)
        if r.status_code not in (200, 201):
            # retry with put
            r = await client.put(url, content=data, headers=headers)
        if r.status_code not in (200, 201):
            raise RuntimeError(f"Storage upload failed: {r.status_code} {r.text[:300]}")
    # URL pubblico
    return f"{SUPABASE_URL}/storage/v1/object/public/{STORAGE_BUCKET}/{path}"


async def storage_upload_stream(path: str, file_obj, content_type: str, max_bytes: int) -> tuple:
    """Legge a chunk, carica su storage. Ritorna (public_url, size)."""
    import httpx
    # Per file grandi: buffer su temp poi upload (Supabase REST vuole body completo in una request)
    import tempfile
    size = 0
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp_path = tmp.name
        while True:
            chunk = await file_obj.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                tmp.close()
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
                raise ValueError("too_large")
            tmp.write(chunk)
    try:
        with open(tmp_path, "rb") as f:
            data = f.read()
        public_url = await storage_upload(path, data, content_type)
        return public_url, size
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


async def storage_signed_url(path: str, expires_sec: int = 3600) -> str:
    """URL firmato per file privati."""
    import httpx
    # path relativo nel bucket
    url = f"{SUPABASE_URL}/storage/v1/object/sign/{STORAGE_BUCKET}/{path}"
    headers = {
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "apikey": SUPABASE_SERVICE_KEY,
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(url, headers=headers, json={"expiresIn": expires_sec})
        if r.status_code not in (200, 201):
            raise RuntimeError(f"sign failed: {r.status_code} {r.text[:200]}")
        data = r.json()
        signed = data.get("signedURL") or data.get("signedUrl") or ""
        if signed.startswith("http"):
            return signed
        return f"{SUPABASE_URL}/storage/v1{signed}"


@app.get("/gallery", response_class=HTMLResponse)
async def gallery_page_alias(req: Request):
    return await photos_page(req)

@app.get("/photos")
async def photos_redirect():
    return RedirectResponse("/gallery", 303)

@app.get("/gallery", response_class=HTMLResponse)
async def photos_page(req: Request):
    user = current_user(req)
    if not user:
        return RedirectResponse("/login", 303)
    c = db()
    cur = c.cursor()
    try:
        cur.execute("SELECT * FROM user_photos WHERE user_id=%s ORDER BY is_private, ordine, id", (user["id"],))
        photos = [dict(p) for p in cur.fetchall()]
    except Exception:
        photos = []
    cur.close()
    c.close()
    return templates.TemplateResponse("gallery.html", {
        "request": req, "user": user, "photos": photos, "unread": unread_count(user["id"]),
        "err": req.query_params.get("err"),
    })


@app.post("/gallery/upload")
async def photos_upload(
    req: Request,
    file: UploadFile = File(...),
    is_private: int = Form(0),
):
    user = current_user(req)
    if not user:
        return RedirectResponse("/login", 303)
    if is_suspended(user):
        return RedirectResponse("/profile?err=sospeso", 303)
    r = get_restrictions(user["id"])
    if r.get("no_gallery"):
        return RedirectResponse("/gallery?err=restrizione", 303)

    content_type = (file.content_type or "").lower()
    is_image = content_type.startswith("image/")
    is_video = content_type.startswith("video/")
    if not is_image and not is_video:
        return RedirectResponse("/gallery?err=tipo", 303)

    import uuid
    media_type = "video" if is_video else "image"
    ext = "mp4"
    if is_image:
        ext = "jpg"
        if "png" in content_type:
            ext = "png"
        elif "webp" in content_type:
            ext = "webp"
        elif "gif" in content_type:
            ext = "gif"
    else:
        if "webm" in content_type:
            ext = "webm"
        elif "quicktime" in content_type or "mov" in content_type:
            ext = "mov"
        else:
            name = (file.filename or "").lower()
            for e in ("mp4", "webm", "mov", "m4v"):
                if name.endswith("." + e):
                    ext = e
                    break
            else:
                ext = "mp4"

    private = 1 if int(is_private) else 0
    folder = "private" if private else "public"
    object_path = f"{folder}/{user['id']}/{uuid.uuid4().hex}.{ext}"

    url = None
    storage_path = None

    if storage_enabled():
        try:
            public_url, size = await storage_upload_stream(
                object_path, file, content_type, MAX_UPLOAD_BYTES
            )
            storage_path = object_path
            if private:
                # salva path interno; URL pubblico non esporre
                url = f"/gallery/file/{object_path}"
            else:
                url = public_url
        except ValueError:
            return RedirectResponse("/gallery?err=grande", 303)
        except Exception as e:
            print("supabase upload error:", e)
            return RedirectResponse("/gallery?err=storage", 303)
    else:
        # fallback locale (dev)
        user_dir = BASE_DIR / "static" / "uploads" / str(user["id"])
        user_dir.mkdir(parents=True, exist_ok=True)
        fname = f"{uuid.uuid4().hex}.{ext}"
        fpath = user_dir / fname
        size = 0
        try:
            with open(fpath, "wb") as out:
                while True:
                    chunk = await file.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_UPLOAD_BYTES:
                        out.close()
                        fpath.unlink(missing_ok=True)
                        return RedirectResponse("/gallery?err=grande", 303)
                    out.write(chunk)
        except Exception as e:
            print("local upload error:", e)
            return RedirectResponse("/gallery?err=db", 303)
        url = f"/static/uploads/{user['id']}/{fname}"

    c = db()
    cur = c.cursor()
    try:
        try:
            cur.execute(
                """INSERT INTO user_photos (user_id, url, is_private, media_type)
                   VALUES (%s, %s, %s, %s) RETURNING id""",
                (user["id"], url, private, media_type),
            )
        except Exception:
            c.rollback()
            cur.execute(
                "INSERT INTO user_photos (user_id, url, is_private) VALUES (%s, %s, %s) RETURNING id",
                (user["id"], url, private),
            )
        row = cur.fetchone()
        # se abbiamo storage_path privato, salva anche nel url come marker
        if storage_path and private:
            # url già /gallery/file/...
            pass
        if not private and media_type == "image":
            cur.execute(
                "UPDATE users SET foto_principale_url=%s WHERE id=%s AND (foto_principale_url IS NULL OR foto_principale_url='')",
                (url, user["id"]),
            )
        c.commit()
    except Exception as e:
        c.rollback()
        print("photos_upload db:", e)
        return RedirectResponse("/gallery?err=db", 303)
    finally:
        cur.close()
        c.close()
    return RedirectResponse("/gallery", 303)


@app.get("/gallery/file/{file_path:path}")
async def gallery_private_file(req: Request, file_path: str):
    """Serve file privato: solo owner o chi ha consenso. Redirect a signed URL Supabase."""
    user = current_user(req)
    if not user:
        return RedirectResponse("/login", 303)
    # path tipo private/123/uuid.mp4
    parts = file_path.split("/")
    if len(parts) < 2 or parts[0] not in ("private", "public"):
        return JSONResponse({"error": "not found"}, status_code=404)
    try:
        owner_id = int(parts[1])
    except Exception:
        return JSONResponse({"error": "not found"}, status_code=404)

    if user["id"] != owner_id and not has_photo_access(user["id"], owner_id):
        return JSONResponse({"error": "forbidden"}, status_code=403)

    if not storage_enabled():
        # locale
        local = BASE_DIR / "static" / "uploads" / str(owner_id) / parts[-1]
        if local.exists():
            from fastapi.responses import FileResponse
            return FileResponse(str(local))
        return JSONResponse({"error": "not found"}, status_code=404)

    try:
        signed = await storage_signed_url(file_path, expires_sec=3600)
        return RedirectResponse(signed, status_code=302)
    except Exception as e:
        print("signed url error:", e)
        return JSONResponse({"error": "storage"}, status_code=500)



@app.post("/gallery/delete/{photo_id}")
async def photos_delete(req: Request, photo_id: int):
    user = current_user(req)
    if not user:
        return RedirectResponse("/login", 303)
    c = db()
    cur = c.cursor()
    cur.execute("SELECT url FROM user_photos WHERE id=%s AND user_id=%s", (photo_id, user["id"]))
    row = cur.fetchone()
    if row:
        try:
            rel = row["url"]
            if rel.startswith("/static/"):
                fp = BASE_DIR / rel.lstrip("/")
                if fp.exists():
                    fp.unlink()
        except Exception:
            pass
        cur.execute("DELETE FROM user_photos WHERE id=%s AND user_id=%s", (photo_id, user["id"]))
        c.commit()
    cur.close()
    c.close()
    return RedirectResponse("/gallery", 303)


def has_photo_access(viewer_id, owner_id):
    """True se viewer può vedere le foto private di owner."""
    if viewer_id == owner_id:
        return True
    try:
        c = db()
        cur = c.cursor()
        cur.execute(
            """SELECT id FROM photo_access_requests
               WHERE from_user_id=%s AND to_user_id=%s AND status='approved'""",
            (viewer_id, owner_id),
        )
        ok = cur.fetchone() is not None
        cur.close()
        c.close()
        return ok
    except Exception:
        return False


@app.post("/gallery/request-access/{to_user_id}")
async def photos_request_access(req: Request, to_user_id: int, conversation_id: int = Form(...)):
    """Richiede accesso alle foto private. Crediti scalati subito e NON rimborsabili."""
    user = current_user(req)
    if not user:
        return RedirectResponse("/login", 303)
    if to_user_id == user["id"]:
        return RedirectResponse(f"/chat/{conversation_id}", 303)

    # già approvato?
    if has_photo_access(user["id"], to_user_id):
        return RedirectResponse(f"/chat/{conversation_id}?photo=ok", 303)

    c = db()
    cur = c.cursor()
    # pending già esistente?
    try:
        cur.execute(
            "SELECT id, status FROM photo_access_requests WHERE from_user_id=%s AND to_user_id=%s",
            (user["id"], to_user_id),
        )
        existing = cur.fetchone()
        if existing and existing["status"] == "pending":
            cur.close()
            c.close()
            return RedirectResponse(f"/chat/{conversation_id}?photo=pending", 303)
        if existing and existing["status"] == "approved":
            cur.close()
            c.close()
            return RedirectResponse(f"/chat/{conversation_id}?photo=ok", 303)
    except Exception as e:
        print("photo access check:", e)

    cur.close()
    c.close()

    # paga crediti (non rimborsabili anche se rifiutato)
    if not spend_credits(user["id"], PHOTO_ACCESS_COST, "richiesta_foto_private", to_user_id):
        return RedirectResponse(f"/chat/{conversation_id}?err=crediti", 303)

    c = db()
    cur = c.cursor()
    try:
        cur.execute(
            """INSERT INTO photo_access_requests (from_user_id, to_user_id, credits_paid, status)
               VALUES (%s, %s, %s, 'pending')
               ON CONFLICT (from_user_id, to_user_id)
               DO UPDATE SET status='pending', credits_paid=%s, created_at=CURRENT_TIMESTAMP, decided_at=NULL""",
            (user["id"], to_user_id, PHOTO_ACCESS_COST, PHOTO_ACCESS_COST),
        )
        cur.execute(
            """INSERT INTO notifications (user_id, tipo, titolo, contenuto, related_id)
               VALUES (%s, 'richiesta_foto', 'Richiesta foto private',
                       %s, %s)""",
            (
                to_user_id,
                f"{user.get('nome', 'Qualcuno')} vuole vedere le tue foto private (ha già pagato i crediti).",
                conversation_id,
            ),
        )
        # messaggio di sistema in chat
        cur.execute(
            """INSERT INTO messages (conversation_id, sender_id, tipo, contenuto)
               VALUES (%s, %s, 'sistema', %s)""",
            (
                conversation_id,
                user["id"],
                f"📷 Ha richiesto l'accesso alle foto private ({PHOTO_ACCESS_COST} crediti, non rimborsabili). In attesa del tuo consenso.",
            ),
        )
        c.commit()
    except Exception as e:
        c.rollback()
        print("request access error:", e)
    finally:
        cur.close()
        c.close()

    await manager.send(to_user_id, {
        "type": "richiesta_foto",
        "title": "Richiesta foto private",
        "message": f"{user.get('nome', 'Qualcuno')} chiede di vedere le tue foto private",
    })
    return RedirectResponse(f"/chat/{conversation_id}?photo=sent", 303)


@app.post("/gallery/decide/{request_id}")
async def photos_decide(req: Request, request_id: int, decision: str = Form(...), conversation_id: int = Form(...)):
    """Approva o rifiuta. I crediti NON vengono rimborsati."""
    user = current_user(req)
    if not user:
        return RedirectResponse("/login", 303)
    if decision not in ("approved", "denied"):
        return RedirectResponse(f"/chat/{conversation_id}", 303)

    c = db()
    cur = c.cursor()
    try:
        cur.execute(
            "SELECT * FROM photo_access_requests WHERE id=%s AND to_user_id=%s AND status='pending'",
            (request_id, user["id"]),
        )
        row = cur.fetchone()
        if not row:
            cur.close()
            c.close()
            return RedirectResponse(f"/chat/{conversation_id}", 303)
        cur.execute(
            "UPDATE photo_access_requests SET status=%s, decided_at=CURRENT_TIMESTAMP WHERE id=%s",
            (decision, request_id),
        )
        from_id = row["from_user_id"]
        if decision == "approved":
            msg = f"✅ {user.get('nome', 'Utente')} ha accettato: puoi vedere le foto private."
            titolo = "Foto private sbloccate"
            contenuto = f"{user.get('nome')} ha accettato la tua richiesta."
        else:
            msg = f"❌ {user.get('nome', 'Utente')} ha rifiutato l'accesso alle foto private. I crediti non vengono rimborsati."
            titolo = "Richiesta foto rifiutata"
            contenuto = f"{user.get('nome')} ha rifiutato. Crediti non rimborsati."
        cur.execute(
            "INSERT INTO messages (conversation_id, sender_id, tipo, contenuto) VALUES (%s, %s, 'sistema', %s)",
            (conversation_id, user["id"], msg),
        )
        cur.execute(
            "INSERT INTO notifications (user_id, tipo, titolo, contenuto, related_id) VALUES (%s, 'foto_decisione', %s, %s, %s)",
            (from_id, titolo, contenuto, conversation_id),
        )
        c.commit()
        await manager.send(from_id, {"type": "foto_decisione", "title": titolo, "message": contenuto})
    except Exception as e:
        c.rollback()
        print("photos_decide:", e)
    finally:
        cur.close()
        c.close()
    return RedirectResponse(f"/chat/{conversation_id}", 303)


@app.get("/gallery/view/{owner_id}", response_class=HTMLResponse)
async def photos_view(req: Request, owner_id: int):
    """Vede le foto di un utente: pubbliche sempre; private solo con consenso."""
    user = current_user(req)
    if not user:
        return RedirectResponse("/login", 303)
    c = db()
    cur = c.cursor()
    cur.execute("SELECT id, nome, username FROM users WHERE id=%s", (owner_id,))
    owner = cur.fetchone()
    if not owner:
        cur.close()
        c.close()
        return RedirectResponse("/matches", 303)
    owner = dict(owner)
    can_private = has_photo_access(user["id"], owner_id)
    cur.execute(
        "SELECT * FROM user_photos WHERE user_id=%s AND is_private=0 ORDER BY ordine, id",
        (owner_id,),
    )
    public = [dict(p) for p in cur.fetchall()]
    private = []
    if can_private:
        cur.execute(
            "SELECT * FROM user_photos WHERE user_id=%s AND is_private=1 ORDER BY ordine, id",
            (owner_id,),
        )
        private = [dict(p) for p in cur.fetchall()]
    cur.close()
    c.close()
    return templates.TemplateResponse("gallery_view.html", {
        "request": req, "user": user, "owner": owner,
        "public": public, "private": private, "can_private": can_private,
        "unread": unread_count(user["id"]),
    })


# ============== INCANTESIMI ==============
@app.get("/spells", response_class=HTMLResponse)
async def spells_page(req: Request):
    user = current_user(req)
    if not user:
        return RedirectResponse("/login", 303)
    # refresh credits
    c = db()
    cur = c.cursor()
    cur.execute("SELECT credits FROM users WHERE id=%s", (user["id"],))
    row = cur.fetchone()
    user["credits"] = row["credits"] if row else 0
    cur.execute(
        "SELECT * FROM credit_transactions WHERE user_id=%s ORDER BY created_at DESC LIMIT 20",
        (user["id"],),
    )
    txs = [dict(t) for t in cur.fetchall()]
    cur.close()
    c.close()
    return templates.TemplateResponse("spells.html", {
        "request": req, "user": user, "spells": SPELLS, "txs": txs, "unread": unread_count(user["id"])
    })


@app.post("/spells/cast")
async def spells_cast(req: Request, spell: str = Form(...)):
    user = current_user(req)
    if not user:
        return RedirectResponse("/login", 303)
    if spell not in SPELLS:
        return RedirectResponse("/spells", 303)
    cost = SPELLS[spell]["cost"]
    if spell == "rivela_likes":
        if not spend_credits(user["id"], cost, "rivela_likes"):
            return RedirectResponse("/spells?err=crediti", 303)
        return RedirectResponse("/likes?unlocked=1", 303)
    if spell == "boost":
        if not spend_credits(user["id"], cost, "boost"):
            return RedirectResponse("/spells?err=crediti", 303)
        # semplice: segna notification
        try:
            c = db()
            cur = c.cursor()
            cur.execute(
                "INSERT INTO notifications (user_id,tipo,titolo,contenuto) VALUES (%s,'boost','Boost attivo','Il tuo profilo e in evidenza!')",
                (user["id"],),
            )
            c.commit()
            cur.close()
            c.close()
        except Exception:
            pass
        return RedirectResponse("/spells?ok=boost", 303)
    return RedirectResponse("/spells", 303)


@app.post("/admin/credits/{user_id}")
async def admin_credits(req: Request, user_id: int, amount: int = Form(...)):
    if not require_admin(req):
        return RedirectResponse("/login", 303)
    add_credits(user_id, amount, "admin_ricarica")
    return RedirectResponse("/admin", 303)


@app.post("/admin/make_mod/{user_id}")
async def admin_make_mod(req: Request, user_id: int):
    if not require_admin(req):
        return RedirectResponse("/login", 303)
    c = db()
    cur = c.cursor()
    cur.execute("UPDATE users SET is_mod=1, ruolo='mod' WHERE id=%s AND COALESCE(is_admin,0)=0", (user_id,))
    c.commit()
    cur.close()
    c.close()
    return RedirectResponse("/admin", 303)


@app.post("/admin/remove_mod/{user_id}")
async def admin_remove_mod(req: Request, user_id: int):
    if not require_admin(req):
        return RedirectResponse("/login", 303)
    c = db()
    cur = c.cursor()
    cur.execute("UPDATE users SET is_mod=0, ruolo='user' WHERE id=%s", (user_id,))
    c.commit()
    cur.close()
    c.close()
    return RedirectResponse("/admin", 303)


# ============== BACKUP ==============
@app.get("/admin/backup")
async def admin_backup(req: Request):
    import json
    from fastapi.responses import Response
    admin = require_admin(req)
    if not admin:
        return RedirectResponse("/login", 303)
    c = db()
    cur = c.cursor()
    data = {}
    for table in ("users", "matches", "messages", "conversations", "swipes", "notifications", "user_photos", "credit_transactions"):
        try:
            cur.execute(f"SELECT * FROM {table}")
            rows = cur.fetchall()
            data[table] = []
            for r in rows:
                d = dict(r)
                for k, v in list(d.items()):
                    if hasattr(v, "isoformat"):
                        d[k] = v.isoformat()
                # non esportare password hash in chiaro oltre il necessario - le includiamo per restore
                data[table].append(d)
        except Exception as e:
            data[table] = {"error": str(e)}
    try:
        cur.execute(
            "INSERT INTO backups_log (tipo, note, created_by) VALUES ('manual', 'backup manuale admin', %s)",
            (admin["id"],),
        )
        c.commit()
    except Exception:
        c.rollback()
    cur.close()
    c.close()
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    return Response(
        content=payload,
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=mycheating_backup.json"},
    )


@app.get("/admin/backup/auto")
async def admin_backup_auto(req: Request):
    """Endpoint per cron giornaliero. Usa header X-Backup-Secret o query secret."""
    import json
    from datetime import datetime
    secret = req.query_params.get("secret") or req.headers.get("X-Backup-Secret")
    expected = os.environ.get("BACKUP_SECRET", "mycheating_backup_secret_2026")
    if secret != expected:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    c = db()
    cur = c.cursor()
    data = {"created_at": datetime.utcnow().isoformat(), "tipo": "auto"}
    for table in ("users", "matches", "messages", "conversations", "swipes"):
        try:
            cur.execute(f"SELECT * FROM {table}")
            rows = []
            for r in cur.fetchall():
                d = dict(r)
                for k, v in list(d.items()):
                    if hasattr(v, "isoformat"):
                        d[k] = v.isoformat()
                rows.append(d)
            data[table] = rows
        except Exception as e:
            data[table] = []
    try:
        cur.execute("INSERT INTO backups_log (tipo, note) VALUES ('auto', 'backup automatico giornaliero')")
        c.commit()
    except Exception:
        c.rollback()
    cur.close()
    c.close()
    # salva su filesystem se possibile
    try:
        backup_dir = BASE_DIR / "backups"
        backup_dir.mkdir(exist_ok=True)
        fname = backup_dir / f"auto_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
        fname.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        print("auto backup write:", e)
    return JSONResponse({"ok": True, "tables": list(data.keys())})



@app.get("/admin/user/{user_id}")
async def admin_user_get(req: Request, user_id: int):
    if not require_admin(req):
        return JSONResponse({"ok": False}, status_code=401)
    c = db()
    cur = c.cursor()
    cur.execute("SELECT * FROM users WHERE id=%s", (user_id,))
    u = cur.fetchone()
    cur.close()
    c.close()
    if not u:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    d = dict(u)
    for k, v in list(d.items()):
        if hasattr(v, "isoformat"):
            d[k] = v.isoformat()
    d.pop("password_hash", None)
    d["restrictions"] = get_restrictions(user_id)
    return JSONResponse({"ok": True, "user": d})



@app.post("/admin/user/{user_id}/update")
async def admin_user_update(req: Request, user_id: int):
    admin = require_admin(req)
    if not admin:
        return RedirectResponse("/login", 303)
    form = await req.form()
    only = form.get("_only") or "all"

    c = db()
    cur = c.cursor()
    try:
        if only in ("profilo", "all"):
            nome = form.get("nome")
            email = form.get("email")
            citta = form.get("citta")
            genere = form.get("genere")
            credits = form.get("credits")
            telefono = form.get("telefono")
            sets = []
            vals = []
            if nome is not None:
                sets.append("nome=%s"); vals.append(nome)
            if email is not None:
                sets.append("email=%s"); vals.append(email)
            if citta is not None:
                sets.append("citta=%s"); vals.append(citta or None)
            if genere is not None:
                sets.append("genere=%s"); vals.append(genere or None)
            if credits not in (None, ""):
                sets.append("credits=%s"); vals.append(int(credits))
            try:
                if telefono is not None:
                    sets.append("telefono=%s"); vals.append(telefono or None)
            except Exception:
                pass
            if sets:
                vals.append(user_id)
                cur.execute("UPDATE users SET " + ", ".join(sets) + " WHERE id=%s", tuple(vals))

        if only in ("accesso", "all"):
            action = form.get("accesso_action")
            if action == "ban":
                cur.execute("UPDATE users SET stato='bannato', is_online=0, sospeso_fino=NULL WHERE id=%s AND COALESCE(is_admin,0)=0", (user_id,))
            elif action == "unban":
                cur.execute("UPDATE users SET stato='attivo', sospeso_fino=NULL WHERE id=%s", (user_id,))
            elif action == "sospendi":
                fino = form.get("sospeso_fino") or None
                if fino:
                    cur.execute("UPDATE users SET sospeso_fino=%s WHERE id=%s AND COALESCE(is_admin,0)=0", (fino, user_id))
            elif action == "riattiva":
                cur.execute("UPDATE users SET sospeso_fino=NULL, stato='attivo' WHERE id=%s", (user_id,))

        if only in ("ruolo", "all"):
            ruolo = form.get("ruolo") or "user"
            is_admin = 1 if ruolo == "admin" else 0
            is_mod = 1 if ruolo == "mod" else 0
            cur.execute("UPDATE users SET ruolo=%s, is_admin=%s, is_mod=%s WHERE id=%s", (ruolo, is_admin, is_mod, user_id))

        if only in ("restrizioni", "all"):
            flags = [
                "no_gallery", "no_like", "no_messaggi", "no_primo_messaggio",
                "no_scopri", "no_chat", "no_vedi_foto", "no_commenti", "no_storie",
                "no_post", "no_doni", "no_annunci", "no_annunci_personali",
                "no_annunci_hot", "no_annunci_vendita", "no_annunci_scambio", "no_annunci_regalo",
            ]
            restr = {f: 1 if form.get(f) else 0 for f in flags}
            # try full insert; fallback minimal columns
            try:
                cur.execute(
                    """INSERT INTO user_restrictions (
                        user_id, no_gallery, no_like, no_messaggi, no_primo_messaggio,
                        no_scopri, no_chat, no_vedi_foto, no_commenti, no_storie, updated_at
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,CURRENT_TIMESTAMP)
                    ON CONFLICT (user_id) DO UPDATE SET
                        no_gallery=EXCLUDED.no_gallery, no_like=EXCLUDED.no_like,
                        no_messaggi=EXCLUDED.no_messaggi, no_primo_messaggio=EXCLUDED.no_primo_messaggio,
                        no_scopri=EXCLUDED.no_scopri, no_chat=EXCLUDED.no_chat,
                        no_vedi_foto=EXCLUDED.no_vedi_foto, no_commenti=EXCLUDED.no_commenti,
                        no_storie=EXCLUDED.no_storie, updated_at=CURRENT_TIMESTAMP
                    """,
                    (
                        user_id, restr["no_gallery"], restr["no_like"], restr["no_messaggi"],
                        restr["no_primo_messaggio"], restr["no_scopri"], restr["no_chat"],
                        restr["no_vedi_foto"], restr["no_commenti"], restr["no_storie"],
                    ),
                )
            except Exception as e:
                print("restr save:", e)

        c.commit()
    except Exception as e:
        c.rollback()
        print("admin_user_update:", e)
    finally:
        cur.close()
        c.close()
    return RedirectResponse("/admin?managed=" + str(user_id), 303)


@app.post("/admin/credits/{user_id}")
async def admin_credits(req: Request, user_id: int, amount: int = Form(...)):
    if not require_admin(req):
        return RedirectResponse("/login", 303)
    add_credits(user_id, amount, "admin_ricarica")
    return RedirectResponse("/admin", 303)


@app.post("/admin/make_mod/{user_id}")
async def admin_make_mod(req: Request, user_id: int):
    if not require_admin(req):
        return RedirectResponse("/login", 303)
    c = db()
    cur = c.cursor()
    cur.execute("UPDATE users SET is_mod=1, ruolo='mod' WHERE id=%s AND COALESCE(is_admin,0)=0", (user_id,))
    c.commit()
    cur.close()
    c.close()
    return RedirectResponse("/admin", 303)


@app.post("/admin/remove_mod/{user_id}")
async def admin_remove_mod(req: Request, user_id: int):
    if not require_admin(req):
        return RedirectResponse("/login", 303)
    c = db()
    cur = c.cursor()
    cur.execute("UPDATE users SET is_mod=0, ruolo='user' WHERE id=%s", (user_id,))
    c.commit()
    cur.close()
    c.close()
    return RedirectResponse("/admin", 303)


# ============== BACKUP ==============
@app.get("/admin/backup")
async def admin_backup(req: Request):
    import json
    from fastapi.responses import Response
    admin = require_admin(req)
    if not admin:
        return RedirectResponse("/login", 303)
    c = db()
    cur = c.cursor()
    data = {}
    for table in ("users", "matches", "messages", "conversations", "swipes", "notifications", "user_photos", "credit_transactions"):
        try:
            cur.execute(f"SELECT * FROM {table}")
            rows = cur.fetchall()
            data[table] = []
            for r in rows:
                d = dict(r)
                for k, v in list(d.items()):
                    if hasattr(v, "isoformat"):
                        d[k] = v.isoformat()
                # non esportare password hash in chiaro oltre il necessario - le includiamo per restore
                data[table].append(d)
        except Exception as e:
            data[table] = {"error": str(e)}
    try:
        cur.execute(
            "INSERT INTO backups_log (tipo, note, created_by) VALUES ('manual', 'backup manuale admin', %s)",
            (admin["id"],),
        )
        c.commit()
    except Exception:
        c.rollback()
    cur.close()
    c.close()
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    return Response(
        content=payload,
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=mycheating_backup.json"},
    )


@app.get("/admin/backup/auto")
async def admin_backup_auto(req: Request):
    """Endpoint per cron giornaliero. Usa header X-Backup-Secret o query secret."""
    import json
    from datetime import datetime
    secret = req.query_params.get("secret") or req.headers.get("X-Backup-Secret")
    expected = os.environ.get("BACKUP_SECRET", "mycheating_backup_secret_2026")
    if secret != expected:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    c = db()
    cur = c.cursor()
    data = {"created_at": datetime.utcnow().isoformat(), "tipo": "auto"}
    for table in ("users", "matches", "messages", "conversations", "swipes"):
        try:
            cur.execute(f"SELECT * FROM {table}")
            rows = []
            for r in cur.fetchall():
                d = dict(r)
                for k, v in list(d.items()):
                    if hasattr(v, "isoformat"):
                        d[k] = v.isoformat()
                rows.append(d)
            data[table] = rows
        except Exception as e:
            data[table] = []
    try:
        cur.execute("INSERT INTO backups_log (tipo, note) VALUES ('auto', 'backup automatico giornaliero')")
        c.commit()
    except Exception:
        c.rollback()
    cur.close()
    c.close()
    # salva su filesystem se possibile
    try:
        backup_dir = BASE_DIR / "backups"
        backup_dir.mkdir(exist_ok=True)
        fname = backup_dir / f"auto_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
        fname.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        print("auto backup write:", e)
    return JSONResponse({"ok": True, "tables": list(data.keys())})



@app.get("/admin/user/{user_id}")
async def admin_user_get(req: Request, user_id: int):
    if not require_admin(req):
        return JSONResponse({"ok": False}, status_code=401)
    c = db()
    cur = c.cursor()
    cur.execute("SELECT * FROM users WHERE id=%s", (user_id,))
    u = cur.fetchone()
    cur.close()
    c.close()
    if not u:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    d = dict(u)
    for k, v in list(d.items()):
        if hasattr(v, "isoformat"):
            d[k] = v.isoformat()
    d.pop("password_hash", None)
    d["restrictions"] = get_restrictions(user_id)
    return JSONResponse({"ok": True, "user": d})


@app.post("/admin/user/{user_id}/update")
async def admin_user_update(req: Request, user_id: int):
    admin = require_admin(req)
    if not admin:
        return RedirectResponse("/login", 303)
    form = await req.form()
    nome = form.get("nome") or ""
    username = form.get("username") or ""
    email = form.get("email") or ""
    bio = form.get("bio") or ""
    citta = form.get("citta") or ""
    genere = form.get("genere") or ""
    orientamento = form.get("orientamento") or ""
    data_nascita = form.get("data_nascita") or None
    credits = form.get("credits")
    note_admin = form.get("note_admin") or ""
    ruolo = form.get("ruolo") or "user"
    stato = form.get("stato") or "attivo"
    sospeso_fino = form.get("sospeso_fino") or None
    if sospeso_fino == "":
        sospeso_fino = None

    is_admin = 1 if ruolo == "admin" else 0
    is_mod = 1 if ruolo == "mod" else 0
    if ruolo == "admin":
        is_mod = 0

    # restrictions checkboxes
    flags = [
        "no_gallery", "no_like", "no_messaggi", "no_primo_messaggio",
        "no_scopri", "no_chat", "no_vedi_foto", "no_commenti", "no_storie",
    ]
    restr = {f: 1 if form.get(f) else 0 for f in flags}

    c = db()
    cur = c.cursor()
    try:
        cur.execute(
            """UPDATE users SET
                nome=%s, username=%s, email=%s, bio=%s, citta=%s, genere=%s,
                orientamento=%s, data_nascita=COALESCE(%s, data_nascita),
                ruolo=%s, is_admin=%s, is_mod=%s, stato=%s,
                sospeso_fino=%s, note_admin=%s,
                credits=COALESCE(%s, credits)
            WHERE id=%s""",
            (
                nome, username, email, bio or None, citta or None, genere or None,
                orientamento or None, data_nascita or None,
                ruolo, is_admin, is_mod, stato,
                sospeso_fino, note_admin or None,
                int(credits) if credits not in (None, "") else None,
                user_id,
            ),
        )
        cur.execute(
            """INSERT INTO user_restrictions (
                user_id, no_gallery, no_like, no_messaggi, no_primo_messaggio,
                no_scopri, no_chat, no_vedi_foto, no_commenti, no_storie, updated_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,CURRENT_TIMESTAMP)
            ON CONFLICT (user_id) DO UPDATE SET
                no_gallery=EXCLUDED.no_gallery, no_like=EXCLUDED.no_like,
                no_messaggi=EXCLUDED.no_messaggi, no_primo_messaggio=EXCLUDED.no_primo_messaggio,
                no_scopri=EXCLUDED.no_scopri, no_chat=EXCLUDED.no_chat,
                no_vedi_foto=EXCLUDED.no_vedi_foto, no_commenti=EXCLUDED.no_commenti,
                no_storie=EXCLUDED.no_storie, updated_at=CURRENT_TIMESTAMP
            """,
            (
                user_id, restr["no_gallery"], restr["no_like"], restr["no_messaggi"],
                restr["no_primo_messaggio"], restr["no_scopri"], restr["no_chat"],
                restr["no_vedi_foto"], restr["no_commenti"], restr["no_storie"],
            ),
        )
        c.commit()
    except Exception as e:
        c.rollback()
        print("admin_user_update:", e)
    finally:
        cur.close()
        c.close()
    return RedirectResponse("/admin?managed=" + str(user_id), 303)


