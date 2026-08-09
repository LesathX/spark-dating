#!/usr/bin/env python3
from fastapi import FastAPI, Request, Form, WebSocket, WebSocketDisconnect, UploadFile, File
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
CHAT_STORAGE_BUCKET = os.environ.get("CHAT_STORAGE_BUCKET", "chat")


SPELLS = {
    "superlike": {"cost": 5, "label": "Super Like"},
    "messaggio_swipe": {"cost": 10, "label": "Messaggio al like"},
    "rivela_likes": {"cost": 15, "label": "Rivela chi ti piace"},
    "boost": {"cost": 20, "label": "Boost profilo"},
}




def spend_credits(user_id, amount, motivo="", related_id=None):
    """Scala crediti e registra transazione. Ritorna True se ok (sempre bool)."""
    if amount <= 0:
        return True
    c = db()
    cur = c.cursor()
    try:
        cur.execute("SELECT COALESCE(credits,0) as credits FROM users WHERE id=%s FOR UPDATE", (user_id,))
        row = cur.fetchone()
        if not row or row["credits"] < amount:
            c.rollback()
            return False
        cur.execute("UPDATE users SET credits = credits - %s WHERE id=%s", (amount, user_id))
        try:
            cur.execute(
                "INSERT INTO credit_transactions (user_id, amount, motivo, related_id) VALUES (%s,%s,%s,%s)",
                (user_id, -amount, motivo, related_id),
            )
        except Exception:
            try:
                cur.execute(
                    "INSERT INTO credit_transactions (user_id, amount, motivo) VALUES (%s,%s,%s)",
                    (user_id, -amount, motivo),
                )
            except Exception as e2:
                print("credit_transactions skip:", e2)
        c.commit()
        return True
    except Exception as e:
        c.rollback()
        print("spend_credits error:", e)
        return False
    finally:
        try:
            cur.close()
            c.close()
        except Exception:
            pass

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
        "no_doni": 0, "no_ricevere_doni": 0,
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


def active_spell(user_id, spell_code):
    try:
        c = db()
        cur = c.cursor()
        cur.execute("""
            SELECT * FROM spell_uses
            WHERE user_id=%s AND spell_code=%s
              AND (expires_at IS NULL OR expires_at > NOW() - interval '2 seconds')
            ORDER BY created_at DESC LIMIT 1
        """, (user_id, spell_code))
        row = cur.fetchone()
        cur.close()
        c.close()
        return dict(row) if row else None
    except Exception:
        return None

def set_user_online(uid: int, online: bool = True):
    """Aggiorna is_online + ultimo_accesso in modo affidabile."""
    try:
        c = db()
        cur = c.cursor()
        if online:
            cur.execute(
                "UPDATE users SET is_online=1, ultimo_accesso=CURRENT_TIMESTAMP WHERE id=%s",
                (uid,),
            )
        else:
            cur.execute(
                "UPDATE users SET is_online=0, ultimo_accesso=CURRENT_TIMESTAMP WHERE id=%s",
                (uid,),
            )
        c.commit()
        cur.close()
        c.close()
    except Exception as e:
        print("set_user_online:", e)


def format_last_seen(user_row) -> str:
    """Testo leggibile: Online / Ultimo accesso …"""
    if not user_row:
        return ""
    if user_row.get("is_online"):
        return "Online"
    ua = user_row.get("ultimo_accesso")
    if not ua:
        return "Offline"
    try:
        from datetime import datetime, timezone
        if hasattr(ua, "timestamp"):
            ts = ua.replace(tzinfo=timezone.utc).timestamp() if ua.tzinfo is None else ua.timestamp()
        else:
            return "Offline"
        import time
        diff = max(0, int(time.time() - ts))
        if diff < 60:
            return "Ultimo accesso: ora"
        if diff < 3600:
            return f"Ultimo accesso: {diff // 60} min fa"
        if diff < 86400:
            return f"Ultimo accesso: {diff // 3600} h fa"
        return f"Ultimo accesso: {diff // 86400} g fa"
    except Exception:
        return "Offline"


def has_active_boost(user_id: int) -> bool:
    try:
        c = db()
        cur = c.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_boosts (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                starts_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP NOT NULL
            )
        """)
        c.commit()
        cur.execute(
            "SELECT id FROM user_boosts WHERE user_id=%s AND expires_at > NOW() LIMIT 1",
            (user_id,),
        )
        ok = bool(cur.fetchone())
        cur.close(); c.close()
        return ok
    except Exception as e:
        print("has_active_boost:", e)
        return False


@app.websocket("/ws/{uid}")
async def ws_endpoint(websocket: WebSocket, uid: int):
    await manager.connect(uid, websocket)
    set_user_online(uid, True)
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                import json
                data = json.loads(raw) if raw and raw[0] in "{[" else {"type": "ping"}
            except Exception:
                data = {"type": "ping"}
            typ = (data.get("type") or "ping").lower()
            if typ in ("ping", "heartbeat"):
                set_user_online(uid, True)
                try:
                    await websocket.send_json({"type": "pong"})
                except Exception:
                    pass
            elif typ in ("typing", "typing_start"):
                to_uid = data.get("to_user_id")
                conv = data.get("conversation_id")
                if to_uid:
                    await manager.send(int(to_uid), {
                        "type": "typing",
                        "from_user_id": uid,
                        "conversation_id": conv,
                        "active": True,
                    })
            elif typ in ("typing_stop", "stop_typing"):
                to_uid = data.get("to_user_id")
                conv = data.get("conversation_id")
                if to_uid:
                    await manager.send(int(to_uid), {
                        "type": "typing",
                        "from_user_id": uid,
                        "conversation_id": conv,
                        "active": False,
                    })
            elif typ == "presence":
                set_user_online(uid, True)
    except WebSocketDisconnect:
        manager.disconnect(uid, websocket)
        set_user_online(uid, False)
    except Exception:
        manager.disconnect(uid, websocket)
        set_user_online(uid, False)

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
    email = (email or "").strip().lower()
    username = (username or "").strip()
    form_data = {
        "email": email, "username": username, "nome": nome,
        "data_nascita": data_nascita, "genere": genere, "orientamento": orientamento,
        "bio": bio, "citta": citta,
    }
    c = db()
    cur = c.cursor()
    try:
        # controlli espliciti prima dell'INSERT
        cur.execute(
            "SELECT id, username, nome FROM users WHERE lower(email)=%s LIMIT 1",
            (email,),
        )
        existing = cur.fetchone()
        if existing:
            nick = existing.get("username") or existing.get("nome") or "—"
            return templates.TemplateResponse("register.html", {
                "request": req,
                "error": f"Hai già un account con questa email. Nickname: @{nick}. Accedi oppure recupera la password (non possiamo mostrarti la password: è protetta).",
                "already_registered": True,
                "existing_username": nick,
                "form": form_data,
            })
        cur.execute(
            "SELECT id FROM users WHERE lower(username)=%s LIMIT 1",
            (username.lower(),),
        )
        if cur.fetchone():
            return templates.TemplateResponse("register.html", {
                "request": req,
                "error": "Questo username è già in uso. Scegline un altro.",
                "already_registered": False,
                "form": form_data,
            })
        if len(password or "") < 6:
            return templates.TemplateResponse("register.html", {
                "request": req,
                "error": "La password deve avere almeno 6 caratteri.",
                "form": form_data,
            })
        cur.execute("""INSERT INTO users (email,password_hash,username,nome,data_nascita,genere,orientamento,bio,citta)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (email, hash_pw(password), username, nome, data_nascita, genere, orientamento, bio or None, citta or None))
        uid = cur.fetchone()["id"]
        try:
            cur.execute("INSERT INTO user_preferences (user_id) VALUES (%s)", (uid,))
        except Exception:
            pass
        c.commit()
        req.session["user_id"] = uid
        return RedirectResponse("/discover", 303)
    except psycopg2.IntegrityError:
        c.rollback()
        # fallback se race condition
        return templates.TemplateResponse("register.html", {
            "request": req,
            "error": "Email o username già registrati. Se hai già un account, vai al login.",
            "already_registered": True,
            "form": form_data,
        })
    except Exception as e:
        print("register error:", e)
        try:
            c.rollback()
        except Exception:
            pass
        return templates.TemplateResponse("register.html", {
            "request": req,
            "error": "Errore durante la registrazione. Riprova.",
            "form": form_data,
        })
    finally:
        cur.close()
        c.close()


@app.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_page(req: Request):
    return templates.TemplateResponse("forgot_password.html", {
        "request": req, "step": "email", "error": None, "ok": None, "test_otp": None, "email": None, "username": None,
    })


@app.post("/forgot-password/send")
async def forgot_password_send(req: Request, email: str = Form(...)):
    email = (email or "").strip().lower()
    c = db()
    cur = c.cursor()
    try:
        cur.execute("SELECT id, username, nome, email FROM users WHERE lower(email)=%s AND COALESCE(is_bot,0)=0 LIMIT 1", (email,))
        u = cur.fetchone()
    except Exception:
        u = None
    if not u:
        cur.close(); c.close()
        # non rivelare se email esiste? qui utente sta recuperando - messaggio generico + se non esiste chiaro
        return templates.TemplateResponse("forgot_password.html", {
            "request": req, "step": "email",
            "error": "Nessun account con questa email.",
            "ok": None, "test_otp": None, "email": email, "username": None,
        })
    import random
    code = f"{random.randint(0, 999999):06d}"
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS email_otps (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                email VARCHAR(200) NOT NULL,
                code VARCHAR(10) NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                used INTEGER DEFAULT 0,
                phone VARCHAR(32),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.commit()
    except Exception:
        try: c.rollback()
        except Exception: pass
    try:
        cur.execute(
            """INSERT INTO email_otps (user_id, email, code, expires_at)
               VALUES (%s,%s,%s, NOW() + interval '15 minutes')""",
            (u["id"], email, code),
        )
        c.commit()
    except Exception as e:
        print("forgot otp:", e)
        try: c.rollback()
        except Exception: pass
    cur.close(); c.close()
    sent = send_otp_email(email, code)
    try:
        req.session["reset_user_id"] = u["id"]
        req.session["reset_email"] = email
        if not sent:
            req.session["reset_test_otp"] = code
        else:
            req.session.pop("reset_test_otp", None)
    except Exception:
        pass
    return templates.TemplateResponse("forgot_password.html", {
        "request": req, "step": "otp",
        "error": None,
        "ok": "Codice inviato" if sent else None,
        "test_otp": None if sent else code,
        "email": email,
        "username": u.get("username"),
    })


@app.post("/forgot-password/reset")
async def forgot_password_reset(req: Request, otp: str = Form(...), password: str = Form(...), password2: str = Form(...)):
    email = (req.session.get("reset_email") or "").strip().lower()
    uid = req.session.get("reset_user_id")
    if not email or not uid:
        return RedirectResponse("/forgot-password", 303)
    if (password or "") != (password2 or ""):
        return templates.TemplateResponse("forgot_password.html", {
            "request": req, "step": "otp", "error": "Le password non coincidono",
            "ok": None, "test_otp": req.session.get("reset_test_otp"), "email": email,
            "username": None,
        })
    if len(password or "") < 6:
        return templates.TemplateResponse("forgot_password.html", {
            "request": req, "step": "otp", "error": "Password minimo 6 caratteri",
            "ok": None, "test_otp": req.session.get("reset_test_otp"), "email": email,
            "username": None,
        })
    code = "".join(ch for ch in otp if ch.isdigit())
    c = db()
    cur = c.cursor()
    try:
        cur.execute(
            """SELECT id FROM email_otps WHERE user_id=%s AND email=%s AND code=%s AND used=0 AND expires_at > NOW()
               ORDER BY id DESC LIMIT 1""",
            (uid, email, code),
        )
        row = cur.fetchone()
        if not row:
            cur.close(); c.close()
            return templates.TemplateResponse("forgot_password.html", {
                "request": req, "step": "otp", "error": "Codice errato o scaduto",
                "ok": None, "test_otp": req.session.get("reset_test_otp"), "email": email,
                "username": None,
            })
        cur.execute("UPDATE email_otps SET used=1 WHERE id=%s", (row["id"],))
        cur.execute("UPDATE users SET password_hash=%s WHERE id=%s", (hash_pw(password), uid))
        # recupera nick
        cur.execute("SELECT username FROM users WHERE id=%s", (uid,))
        u = cur.fetchone()
        c.commit()
    except Exception as e:
        print("reset pw:", e)
        try: c.rollback()
        except Exception: pass
        cur.close(); c.close()
        return templates.TemplateResponse("forgot_password.html", {
            "request": req, "step": "otp", "error": "Errore reset", "ok": None,
            "test_otp": None, "email": email, "username": None,
        })
    cur.close(); c.close()
    try:
        req.session.pop("reset_user_id", None)
        req.session.pop("reset_email", None)
        req.session.pop("reset_test_otp", None)
    except Exception:
        pass
    return templates.TemplateResponse("forgot_password.html", {
        "request": req, "step": "done",
        "error": None, "ok": "Password aggiornata",
        "test_otp": None, "email": email,
        "username": (u or {}).get("username"),
    })


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
        # salva IP (proxy-aware)
        ip = None
        try:
            xf = req.headers.get("x-forwarded-for") or req.headers.get("X-Forwarded-For")
            if xf:
                ip = xf.split(",")[0].strip()
            if not ip:
                ip = getattr(getattr(req, "client", None), "host", None)
        except Exception:
            ip = None
        try:
            if ip:
                cur.execute(
                    """UPDATE users SET is_online=1, ultimo_accesso=CURRENT_TIMESTAMP, last_ip=%s WHERE id=%s""",
                    (ip[:64], u["id"]),
                )
            else:
                cur.execute("UPDATE users SET is_online=1, ultimo_accesso=CURRENT_TIMESTAMP WHERE id=%s", (u["id"],))
            c.commit()
        except Exception as e:
            print("login ip col?:", e)
            try:
                c.rollback()
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
                ORDER BY (
                    CASE WHEN EXISTS (
                        SELECT 1 FROM user_boosts ub
                        WHERE ub.user_id = u.id AND ub.expires_at > NOW()
                    ) THEN 0 ELSE 1 END
                ), RANDOM() LIMIT 1
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
            ORDER BY (
                    CASE WHEN EXISTS (
                        SELECT 1 FROM user_boosts ub
                        WHERE ub.user_id = u.id AND ub.expires_at > NOW()
                    ) THEN 0 ELSE 1 END
                ), RANDOM() LIMIT 1
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
    verified = bool(
        user.get("phone_verified") in (1, True, "1")
        or user.get("is_verified") in (1, True, "1")
    )
    verify_step = (req.query_params.get("verify") or "").strip()  # otp | ok | open | ""
    verify_err = (req.query_params.get("verify_err") or "").strip()

    # successo: pulisci sessione e NON mostrare popup
    if verified or verify_step == "ok":
        try:
            req.session.pop("verify_pending_phone", None)
            req.session.pop("verify_test_otp", None)
            req.session.pop("verify_dismissed", None)
        except Exception:
            pass
        if verified:
            verify_step = ""
        show_verify = False
        pending_phone = None
        test_otp = None
    else:
        pending_phone = req.session.get("verify_pending_phone") or user.get("telefono")
        # OTP in corso resta dopo refresh
        if req.session.get("verify_pending_phone") and verify_step not in ("ok",):
            verify_step = "otp"
        if verify_step == "open":
            try:
                req.session.pop("verify_dismissed", None)
            except Exception:
                pass
            verify_step = ""
        test_otp = req.session.get("verify_test_otp") if verify_step == "otp" else None
        show_verify = False
        if verify_step == "otp" or req.session.get("verify_pending_phone"):
            show_verify = True
        elif not req.session.get("verify_dismissed"):
            show_verify = True

    return templates.TemplateResponse("discover.html", {
        "request": req,
        "user": user,
        "candidate": candidate,
        "unread": unread_count(user["id"]),
        "likes_count": likes_received_count(user["id"]),
        "has_gps": has_gps,
        "distanza_max": distanza_max,
        "only_online": only_online,
        "show_verify": show_verify,
        "verify_step": verify_step,
        "verify_err": verify_err,
        "test_otp": test_otp,
        "pending_phone": pending_phone,
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
        # notifica like (related_id = chi ha messo like)
        if tipo in ("like", "superlike"):
            try:
                cur.execute(
                    """INSERT INTO notifications (user_id, tipo, titolo, contenuto, related_id)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (
                        to_user_id,
                        "like" if tipo == "like" else "superlike",
                        "Nuovo like!" if tipo == "like" else "Super Like!",
                        f"{user.get('nome') or user.get('username') or 'Qualcuno'} ha messo like al tuo profilo",
                        user["id"],  # related = from_user_id
                    ),
                )
            except Exception as e:
                print("like notif:", e)
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
                        try:
                            cur.execute("""
                                SELECT id, from_user_id, contenuto FROM swipe_messages
                                WHERE ((from_user_id=%s AND to_user_id=%s) OR (from_user_id=%s AND to_user_id=%s))
                                  AND COALESCE(delivered,0)=0
                            """, (user["id"], to_user_id, to_user_id, user["id"]))
                            for sm in cur.fetchall():
                                cur.execute(
                                    "INSERT INTO messages (conversation_id,sender_id,tipo,contenuto) VALUES (%s,%s,'testo',%s)",
                                    (conv_id, sm["from_user_id"], sm["contenuto"]),
                                )
                                try:
                                    cur.execute("UPDATE swipe_messages SET delivered=1 WHERE id=%s", (sm["id"],))
                                except Exception:
                                    pass
                        except Exception as e_sm:
                            print("deliver swipe_messages:", e_sm)
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


@app.get("/chat/with/{other_id}")
async def chat_with_user(req: Request, other_id: int):
    """Apre (o crea) la chat con un match."""
    user = current_user(req)
    if not user:
        return RedirectResponse("/login", 303)
    if other_id == user["id"]:
        return RedirectResponse("/matches", 303)
    c = db()
    cur = c.cursor()
    try:
        u1, u2 = sorted([user["id"], other_id])
        cur.execute(
            "SELECT id FROM matches WHERE user1_id=%s AND user2_id=%s AND COALESCE(attivo,1)=1",
            (u1, u2),
        )
        m = cur.fetchone()
        if not m:
            cur.close()
            c.close()
            return RedirectResponse("/matches", 303)
        mid = m["id"]
        cur.execute("SELECT id FROM conversations WHERE match_id=%s", (mid,))
        conv = cur.fetchone()
        if not conv:
            cur.execute("INSERT INTO conversations (match_id) VALUES (%s) RETURNING id", (mid,))
            conv = cur.fetchone()
            c.commit()
        conv_id = conv["id"]
        cur.close()
        c.close()
        return RedirectResponse(f"/chat/{conv_id}", 303)
    except Exception as e:
        try:
            c.rollback()
            cur.close()
            c.close()
        except Exception:
            pass
        print("chat_with error:", e)
        return RedirectResponse("/matches", 303)

@app.get("/chat/{conversation_id}", response_class=HTMLResponse)
async def chat_page(req: Request, conversation_id: int):
    user = current_user(req)
    if not user:
        return RedirectResponse("/login", 303)

    altro = None
    messaggi = []
    photo_access = None
    pending_request = None

    try:
        c = db()
        cur = c.cursor()
    except Exception as e:
        print("chat db connect:", e)
        return RedirectResponse("/chats?err=chat", 303)

    try:
        # 1) conversazione + match
        try:
            cur.execute(
                """SELECT c.id as cid, m.user1_id, m.user2_id
                   FROM conversations c
                   JOIN matches m ON m.id = c.match_id
                   WHERE c.id=%s""",
                (conversation_id,),
            )
            conv = cur.fetchone()
        except Exception as e:
            print("chat conv query:", e)
            cur.close()
            c.close()
            return RedirectResponse("/chats", 303)

        if not conv:
            cur.close()
            c.close()
            return RedirectResponse("/chats", 303)

        u1, u2 = conv["user1_id"], conv["user2_id"]
        if user["id"] not in (u1, u2):
            cur.close()
            c.close()
            return RedirectResponse("/chats", 303)

        altro_id = u2 if user["id"] == u1 else u1

        # 2) altro utente
        try:
            cur.execute("SELECT * FROM users WHERE id=%s", (altro_id,))
            row = cur.fetchone()
            if not row:
                cur.close()
                c.close()
                return RedirectResponse("/chats", 303)
            altro = dict(row)
        except Exception as e:
            print("chat altro:", e)
            cur.close()
            c.close()
            return RedirectResponse("/chats", 303)

        try:
            altro["eta"] = eta(altro.get("data_nascita"))
        except Exception:
            altro["eta"] = None
        if not altro.get("nome"):
            altro["nome"] = altro.get("username") or "Utente"
        if not altro.get("username"):
            altro["username"] = "user"

        # 3) messaggi (query minimali)
        # elimina messaggi autodistrutti scaduti
        try:
            cur.execute("DELETE FROM messages WHERE expires_at IS NOT NULL AND expires_at < NOW() - interval '5 seconds'")
            c.commit()
        except Exception as e:
            print("delete expired:", e)
            try:
                c.rollback()
            except Exception:
                pass

        rows = []
        try:
            try:
                cur.execute(
                    """SELECT id, conversation_id, sender_id, contenuto, data_invio, media_url, media_type, expires_at,
                          COALESCE(reply_to_id, NULL) as reply_to_id, COALESCE(letto,0) as letto
                       FROM messages WHERE conversation_id=%s
                         AND (expires_at IS NULL OR expires_at > NOW() - interval '2 seconds')
                       ORDER BY id ASC LIMIT 500""",
                    (conversation_id,),
                )
            except Exception:
                c.rollback()
                cur.execute(
                    """SELECT id, conversation_id, sender_id, contenuto, data_invio
                       FROM messages WHERE conversation_id=%s ORDER BY id ASC LIMIT 500""",
                    (conversation_id,),
                )
            rows = cur.fetchall()
        except Exception as e:
            print("chat msgs simple:", e)
            try:
                c.rollback()
                cur.execute(
                    "SELECT * FROM messages WHERE conversation_id=%s ORDER BY id ASC LIMIT 500",
                    (conversation_id,),
                )
                rows = cur.fetchall()
            except Exception as e2:
                print("chat msgs *:", e2)
                c.rollback()
                rows = []

        for r in rows:
            d = dict(r)
            di = d.get("data_invio")
            ora = ""
            if di is not None:
                try:
                    if hasattr(di, "strftime"):
                        ora = di.strftime("%H:%M")
                    else:
                        s = str(di)
                        if "T" in s:
                            ora = s.split("T")[1][:5]
                        elif " " in s:
                            ora = s.split(" ")[1][:5]
                        else:
                            ora = s[:5]
                except Exception:
                    ora = ""
            d["ora"] = ora
            exp = d.get("expires_at")
            d["expires_in"] = None
            if exp is not None:
                try:
                    from datetime import datetime, timezone
                    if hasattr(exp, "timestamp"):
                        # aware or naive datetime from DB
                        ts = exp.timestamp() if exp.tzinfo else exp.replace(tzinfo=timezone.utc).timestamp()
                    else:
                        s = str(exp).replace(" ", "T")
                        if s.endswith("Z"):
                            s = s[:-1] + "+00:00"
                        dt = datetime.fromisoformat(s)
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        ts = dt.timestamp()
                    left = int(ts - datetime.now(timezone.utc).timestamp())
                    if left < 0:
                        left = 0
                    d["expires_in"] = left
                    d["expires_at"] = str(int(ts))  # unix seconds for client
                except Exception as e:
                    print("expires parse:", e)
                    d["expires_at"] = None
                    d["expires_in"] = None
            messaggi.append(d)

        # 4) mark read (optional)
        try:
            cur.execute(
                "UPDATE messages SET letto=1 WHERE conversation_id=%s AND sender_id!=%s",
                (conversation_id, user["id"]),
            )
            c.commit()
        except Exception:
            try:
                c.rollback()
            except Exception:
                pass

        # 4b) reazioni + reply preview
        try:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS message_reactions (
                    id SERIAL PRIMARY KEY,
                    message_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    emoji VARCHAR(16) NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(message_id, user_id)
                )
            """)
            c.commit()
        except Exception:
            try: c.rollback()
            except Exception: pass
        try:
            ids = [m["id"] for m in messaggi if m.get("id")]
            reactions_map = {}
            if ids:
                cur.execute(
                    "SELECT message_id, emoji, user_id FROM message_reactions WHERE message_id = ANY(%s)",
                    (ids,),
                )
                for r in cur.fetchall():
                    reactions_map.setdefault(r["message_id"], []).append(dict(r))
            by_id = {m["id"]: m for m in messaggi if m.get("id")}
            for m in messaggi:
                m["reactions"] = reactions_map.get(m["id"], [])
                rid = m.get("reply_to_id")
                if rid and rid in by_id:
                    m["reply_preview"] = (by_id[rid].get("contenuto") or "media")[:80]
                else:
                    m["reply_preview"] = None
        except Exception as e:
            print("reactions load:", e)
            try: c.rollback()
            except Exception: pass

        # 5) photo access (optional)
        try:
            cur.execute(
                "SELECT * FROM photo_access_requests WHERE from_user_id=%s AND to_user_id=%s",
                (user["id"], altro["id"]),
            )
            out_req = cur.fetchone()
            cur.execute(
                "SELECT * FROM photo_access_requests WHERE from_user_id=%s AND to_user_id=%s AND status='pending'",
                (altro["id"], user["id"]),
            )
            in_req = cur.fetchone()
            if out_req:
                photo_access = out_req.get("status") if isinstance(out_req, dict) else out_req["status"]
            if in_req:
                pending_request = dict(in_req)
        except Exception as e:
            print("chat photo optional:", e)
            try:
                c.rollback()
            except Exception:
                pass

        cur.close()
        c.close()
    except Exception as e:
        print("chat_page body:", type(e).__name__, e)
        try:
            cur.close()
            c.close()
        except Exception:
            pass
        return RedirectResponse("/chats?err=chat", 303)

    if not altro:
        return RedirectResponse("/chats", 303)

    try:
        ur = unread_count(user["id"])
    except Exception:
        ur = 0

    try:
        icebreakers = [
            "Ciao! Come va oggi? 😊",
            "Cosa ti ha portato su MyCheating?",
            "Film o serie preferita?",
            "Aperitivo o cena?",
            "Qual è il tuo posto preferito in città?",
        ]
        # stale online: se ultimo_accesso > 2 min → offline
        try:
            c2 = db()
            cur2 = c2.cursor()
            cur2.execute(
                """UPDATE users SET is_online=0
                   WHERE id=%s AND is_online=1
                     AND ultimo_accesso IS NOT NULL
                     AND ultimo_accesso < NOW() - interval '2 minutes'""",
                (altro.get("id"),),
            )
            c2.commit()
            cur2.execute("SELECT is_online, ultimo_accesso FROM users WHERE id=%s", (altro.get("id"),))
            row2 = cur2.fetchone()
            if row2:
                altro["is_online"] = row2.get("is_online")
                altro["ultimo_accesso"] = row2.get("ultimo_accesso")
            cur2.close(); c2.close()
        except Exception:
            pass
        last_seen = format_last_seen(altro)

        return templates.TemplateResponse("chat.html", {
            "request": req,
            "user": user,
            "altro": altro,
            "conversation_id": conversation_id,
            "messaggi": messaggi,
            "unread": ur,
            "photo_access": photo_access,
            "pending_request": pending_request,
            "photo_cost": 25,
            "icebreakers": icebreakers,
            "last_seen": last_seen,
            "stickers": STICKERS,
        })
    except Exception as e:
        print("chat template error:", type(e).__name__, e)
        return RedirectResponse("/chats?err=chat", 303)


@app.post("/chat/{conversation_id}/send")
async def send_message(req: Request, conversation_id: int, contenuto: str = Form(""), ttl_seconds: int = Form(0)):
    user = current_user(req)
    if not user:
        return RedirectResponse("/login", 303)
    if is_suspended(user):
        return RedirectResponse("/profile?err=sospeso", 303)
    r = get_restrictions(user["id"])
    if r.get("no_messaggi") or r.get("no_chat"):
        return RedirectResponse(f"/chat/{conversation_id}?err=no_msg", 303)
    contenuto = (contenuto or "").strip()
    if not contenuto:
        return RedirectResponse(f"/chat/{conversation_id}", 303)
    c = db()
    cur = c.cursor()
    cur.execute("""SELECT c.id, m.user1_id, m.user2_id FROM conversations c
           JOIN matches m ON m.id = c.match_id WHERE c.id=%s""", (conversation_id,))
    conv = cur.fetchone()
    if not conv or user["id"] not in (conv["user1_id"], conv["user2_id"]):
        cur.close()
        c.close()
        return RedirectResponse("/chats", 303)
    expires_sql = None
    try:
        ttl = int(ttl_seconds or 0)
    except Exception:
        ttl = 0
    if ttl > 0:
        try:
            # epoch ms lato client: salviamo expires_at in UTC esplicito
            cur.execute(
                """INSERT INTO messages (conversation_id,sender_id,tipo,contenuto,expires_at)
                   VALUES (%s,%s,'testo',%s, NOW() + (%s * interval '1 second'))""",
                (conversation_id, user["id"], contenuto, int(ttl)),
            )
        except Exception as e:
            print("insert ttl msg:", e)
            try:
                c.rollback()
                cur.execute(
                    """INSERT INTO messages (conversation_id,sender_id,tipo,contenuto,expires_at)
                       VALUES (%s,%s,'testo',%s, NOW() + (%s * interval '1 second'))""",
                    (conversation_id, user["id"], contenuto, int(ttl)),
                )
            except Exception as e2:
                print("insert ttl msg fallback:", e2)
                c.rollback()
                cur.execute(
                    "INSERT INTO messages (conversation_id,sender_id,tipo,contenuto) VALUES (%s,%s,'testo',%s)",
                    (conversation_id, user["id"], contenuto),
                )
    else:
        cur.execute(
            "INSERT INTO messages (conversation_id,sender_id,tipo,contenuto) VALUES (%s,%s,'testo',%s)",
            (conversation_id, user["id"], contenuto),
        )
    try:
        cur.execute("UPDATE conversations SET ultimo_messaggio_at=CURRENT_TIMESTAMP WHERE id=%s", (conversation_id,))
    except Exception:
        pass
    altro_id = conv["user2_id"] if user["id"] == conv["user1_id"] else conv["user1_id"]
    try:
        cur.execute(
            "INSERT INTO notifications (user_id,tipo,titolo,contenuto,related_id) VALUES (%s,'nuovo_messaggio',%s,%s,%s)",
            (altro_id, f"Messaggio da {user.get('nome') or 'utente'}", (contenuto or '')[:80], conversation_id),
        )
    except Exception as e:
        print("msg notif:", e)
    c.commit()
    cur.close()
    c.close()
    try:
        await manager.send(altro_id, {"type": "nuovo_messaggio", "title": "Nuovo messaggio", "message": contenuto[:80], "conversation_id": conversation_id})
    except Exception:
        pass
    return RedirectResponse(f"/chat/{conversation_id}", 303)



@app.post("/chat/{conversation_id}/delete/{message_id}")
async def delete_message(req: Request, conversation_id: int, message_id: int):
    """Cancella un messaggio proprio (hard delete)."""
    user = current_user(req)
    if not user:
        return RedirectResponse("/login", 303)
    c = db()
    cur = c.cursor()
    try:
        # verifica appartenenza conversazione
        cur.execute("""SELECT c.id, m.user1_id, m.user2_id FROM conversations c
               JOIN matches m ON m.id = c.match_id WHERE c.id=%s""", (conversation_id,))
        conv = cur.fetchone()
        if not conv or user["id"] not in (conv["user1_id"], conv["user2_id"]):
            cur.close()
            c.close()
            return RedirectResponse("/chats", 303)
        # solo i propri messaggi
        cur.execute(
            "SELECT id, sender_id FROM messages WHERE id=%s AND conversation_id=%s",
            (message_id, conversation_id),
        )
        msg = cur.fetchone()
        if not msg:
            cur.close()
            c.close()
            return RedirectResponse(f"/chat/{conversation_id}", 303)
        if msg["sender_id"] != user["id"] and not user.get("is_admin"):
            cur.close()
            c.close()
            return RedirectResponse(f"/chat/{conversation_id}?err=not_yours", 303)
        cur.execute("DELETE FROM messages WHERE id=%s", (message_id,))
        c.commit()
    except Exception as e:
        print("delete_message:", e)
        try:
            c.rollback()
        except Exception:
            pass
    finally:
        try:
            cur.close()
            c.close()
        except Exception:
            pass
    return RedirectResponse(f"/chat/{conversation_id}", 303)


@app.post("/chat/{conversation_id}/send-media")
async def send_media(req: Request, conversation_id: int):
    user = current_user(req)
    if not user:
        return RedirectResponse("/login", 303)
    if is_suspended(user):
        return RedirectResponse("/profile?err=sospeso", 303)
    r = get_restrictions(user["id"])
    if r.get("no_messaggi") or r.get("no_chat"):
        return RedirectResponse(f"/chat/{conversation_id}?err=no_msg", 303)

    form = await req.form()
    file = form.get("file")
    try:
        ttl = int(form.get("ttl_seconds") or 0)
    except Exception:
        ttl = 0
    kind = (str(form.get("media_kind") or "image")).strip().lower()
    if kind not in ("image", "video", "audio"):
        kind = "image"
    caption = (str(form.get("contenuto") or "")).strip()

    # Validazione file
    if file is None or not getattr(file, "filename", None):
        print("send_media: no file in form")
        return RedirectResponse(f"/chat/{conversation_id}?err=nofile", 303)

    c = db()
    cur = c.cursor()
    try:
        cur.execute("""SELECT c.id, m.user1_id, m.user2_id FROM conversations c
               JOIN matches m ON m.id = c.match_id WHERE c.id=%s""", (conversation_id,))
        conv = cur.fetchone()
    except Exception as e:
        print("send_media conv:", e)
        cur.close()
        c.close()
        return RedirectResponse("/chats", 303)

    if not conv or user["id"] not in (conv["user1_id"], conv["user2_id"]):
        cur.close()
        c.close()
        return RedirectResponse("/chats", 303)

    # Leggi bytes
    try:
        data = await file.read()
    except Exception as e:
        print("send_media read:", e)
        cur.close()
        c.close()
        return RedirectResponse(f"/chat/{conversation_id}?err=media", 303)

    if not data or len(data) < 10:
        print("send_media empty file")
        cur.close()
        c.close()
        return RedirectResponse(f"/chat/{conversation_id}?err=empty", 303)

    import uuid, os
    fname = file.filename or "media.bin"
    ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else "bin"
    if len(ext) > 8:
        ext = "bin"
    content_type = getattr(file, "content_type", None) or "application/octet-stream"
    if kind == "image":
        content_type = content_type if str(content_type).startswith("image/") else "image/jpeg"
        if ext not in ("jpg", "jpeg", "png", "gif", "webp", "heic"):
            ext = "jpg"
    elif kind == "video":
        content_type = content_type if str(content_type).startswith("video/") else "video/mp4"
        if ext not in ("mp4", "webm", "mov", "mkv"):
            ext = "mp4"
    elif kind == "audio":
        content_type = content_type if str(content_type).startswith("audio/") else "audio/webm"
        if ext not in ("webm", "mp3", "ogg", "m4a", "wav"):
            ext = "webm"

    storage_path = f"{user['id']}/{uuid.uuid4().hex}.{ext}"
    url = None

    # 1) Supabase Storage — bucket dedicato chat
    try:
        if storage_enabled():
            url = await storage_upload(storage_path, data, content_type, bucket=CHAT_STORAGE_BUCKET)
            print("send_media storage ok:", url[:80] if url else None)
    except Exception as e:
        print("send_media storage fail:", e)
        url = None

    # 2) Locale (fallback – su Render sparisce al redeploy)
    if not url:
        try:
            os.makedirs("static/uploads/chat", exist_ok=True)
            path = f"static/uploads/chat/{uuid.uuid4().hex}.{ext}"
            with open(path, "wb") as f:
                f.write(data)
            url = "/" + path
            print("send_media local:", url)
        except Exception as e:
            print("send_media local fail:", e)

    if not url:
        cur.close()
        c.close()
        return RedirectResponse(f"/chat/{conversation_id}?err=media", 303)

    # Testo visibile sempre (se media_url colonna manca, resta almeno questo)
    labels = {"image": "📷 Foto", "video": "🎬 Video", "audio": "🎤 Audio"}
    body = caption if caption else labels.get(kind, "📎 Media")
    # includi URL nel testo come backup leggibile
    body_with_url = f"{body}\n{url}"

    inserted = False
    # Prova con colonne media
    try:
        if ttl > 0:
            cur.execute(
                """INSERT INTO messages (conversation_id, sender_id, tipo, contenuto, media_url, media_type, expires_at)
                   VALUES (%s,%s,%s,%s,%s,%s, NOW() + (%s * interval '1 second'))
                   RETURNING id""",
                (conversation_id, user["id"], kind, body, url, kind, int(ttl)),
            )
        else:
            cur.execute(
                """INSERT INTO messages (conversation_id, sender_id, tipo, contenuto, media_url, media_type)
                   VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
                (conversation_id, user["id"], kind, body, url, kind),
            )
        row = cur.fetchone()
        inserted = bool(row)
        print("send_media insert media cols ok id=", row)
    except Exception as e:
        print("send_media insert media cols fail:", e)
        try:
            c.rollback()
        except Exception:
            pass

    if not inserted:
        try:
            cur.execute(
                """INSERT INTO messages (conversation_id, sender_id, tipo, contenuto)
                   VALUES (%s,%s,%s,%s) RETURNING id""",
                (conversation_id, user["id"], kind, body_with_url[:2000]),
            )
            row = cur.fetchone()
            inserted = bool(row)
            print("send_media insert text fallback id=", row)
        except Exception as e2:
            print("send_media insert total fail:", e2)
            try:
                c.rollback()
            except Exception:
                pass
            cur.close()
            c.close()
            return RedirectResponse(f"/chat/{conversation_id}?err=db", 303)

    try:
        cur.execute("UPDATE conversations SET ultimo_messaggio_at=CURRENT_TIMESTAMP WHERE id=%s", (conversation_id,))
    except Exception:
        try:
            c.rollback()
        except Exception:
            pass

    altro_id = conv["user2_id"] if user["id"] == conv["user1_id"] else conv["user1_id"]
    try:
        cur.execute(
            "INSERT INTO notifications (user_id,tipo,titolo,contenuto,related_id) VALUES (%s,'nuovo_messaggio',%s,%s,%s)",
            (altro_id, f"{labels.get(kind,'Media')} da {user.get('nome') or 'utente'}", body[:80], conversation_id),
        )
    except Exception:
        try:
            c.rollback()
        except Exception:
            pass

    try:
        c.commit()
    except Exception as e:
        print("send_media commit:", e)
        c.rollback()

    cur.close()
    c.close()
    try:
        await manager.send(altro_id, {
            "type": "nuovo_messaggio",
            "title": labels.get(kind, "Media"),
            "message": body[:80],
            "conversation_id": conversation_id,
        })
    except Exception:
        pass
    return RedirectResponse(f"/chat/{conversation_id}", 303)


@app.post("/chat/{conversation_id}/block")
async def chat_block(req: Request, conversation_id: int):
    user = current_user(req)
    if not user:
        return RedirectResponse("/login", 303)
    c = db()
    cur = c.cursor()
    try:
        cur.execute("""SELECT m.user1_id, m.user2_id FROM conversations c
               JOIN matches m ON m.id = c.match_id WHERE c.id=%s""", (conversation_id,))
        conv = cur.fetchone()
        if not conv or user["id"] not in (conv["user1_id"], conv["user2_id"]):
            cur.close()
            c.close()
            return RedirectResponse("/chats", 303)
        altro_id = conv["user2_id"] if user["id"] == conv["user1_id"] else conv["user1_id"]
        try:
            cur.execute(
                "INSERT INTO blocks (blocker_id, blocked_id) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                (user["id"], altro_id),
            )
        except Exception:
            c.rollback()
            try:
                cur.execute(
                    "INSERT INTO blocks (blocker_id, blocked_id) VALUES (%s,%s)",
                    (user["id"], altro_id),
                )
            except Exception as e:
                print("block:", e)
                c.rollback()
        # disattiva match
        try:
            u1, u2 = sorted([user["id"], altro_id])
            cur.execute("UPDATE matches SET attivo=0 WHERE user1_id=%s AND user2_id=%s", (u1, u2))
        except Exception:
            pass
        c.commit()
    finally:
        cur.close()
        c.close()
    return RedirectResponse("/chats?blocked=1", 303)



@app.post("/chat/{conversation_id}/reply")
async def chat_reply(req: Request, conversation_id: int, reply_to_id: int = Form(...), contenuto: str = Form(...)):
    """Rispondi a un messaggio specifico (stile IG/WA)."""
    user = current_user(req)
    if not user:
        return RedirectResponse("/login", 303)
    contenuto = (contenuto or "").strip()
    if not contenuto:
        return RedirectResponse(f"/chat/{conversation_id}", 303)
    c = db()
    cur = c.cursor()
    try:
        cur.execute("""SELECT c.id, m.user1_id, m.user2_id FROM conversations c
               JOIN matches m ON m.id = c.match_id WHERE c.id=%s""", (conversation_id,))
        conv = cur.fetchone()
        if not conv or user["id"] not in (conv["user1_id"], conv["user2_id"]):
            cur.close(); c.close()
            return RedirectResponse("/chats", 303)
        try:
            cur.execute("ALTER TABLE messages ADD COLUMN IF NOT EXISTS reply_to_id INTEGER")
            c.commit()
        except Exception:
            try: c.rollback()
            except Exception: pass
        cur.execute(
            """INSERT INTO messages (conversation_id, sender_id, tipo, contenuto, reply_to_id)
               VALUES (%s,%s,'testo',%s,%s)""",
            (conversation_id, user["id"], contenuto[:2000], reply_to_id),
        )
        try:
            cur.execute("UPDATE conversations SET ultimo_messaggio_at=CURRENT_TIMESTAMP WHERE id=%s", (conversation_id,))
        except Exception:
            pass
        altro = conv["user2_id"] if user["id"] == conv["user1_id"] else conv["user1_id"]
        try:
            cur.execute(
                "INSERT INTO notifications (user_id,tipo,titolo,contenuto,related_id) VALUES (%s,'nuovo_messaggio',%s,%s,%s)",
                (altro, f"Risposta da {user.get('nome') or 'utente'}", contenuto[:80], conversation_id),
            )
        except Exception:
            pass
        c.commit()
    except Exception as e:
        print("chat reply:", e)
        try: c.rollback()
        except Exception: pass
    cur.close(); c.close()
    return RedirectResponse(f"/chat/{conversation_id}", 303)


@app.post("/chat/{conversation_id}/react/{message_id}")
async def chat_react(req: Request, conversation_id: int, message_id: int, emoji: str = Form("❤️")):
    """Reazione emoji a un messaggio (stile IG/FB)."""
    user = current_user(req)
    if not user:
        return RedirectResponse("/login", 303)
    emoji = (emoji or "❤️")[:8]
    c = db()
    cur = c.cursor()
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS message_reactions (
                id SERIAL PRIMARY KEY,
                message_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                emoji VARCHAR(16) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(message_id, user_id)
            )
        """)
        c.commit()
    except Exception:
        try: c.rollback()
        except Exception: pass
    try:
        cur.execute(
            """INSERT INTO message_reactions (message_id, user_id, emoji) VALUES (%s,%s,%s)
               ON CONFLICT (message_id, user_id) DO UPDATE SET emoji=EXCLUDED.emoji""",
            (message_id, user["id"], emoji),
        )
        c.commit()
    except Exception as e:
        print("react:", e)
        try:
            c.rollback()
            cur.execute("DELETE FROM message_reactions WHERE message_id=%s AND user_id=%s", (message_id, user["id"]))
            cur.execute(
                "INSERT INTO message_reactions (message_id, user_id, emoji) VALUES (%s,%s,%s)",
                (message_id, user["id"], emoji),
            )
            c.commit()
        except Exception as e2:
            print("react2:", e2)
            try: c.rollback()
            except Exception: pass
    cur.close(); c.close()
    return RedirectResponse(f"/chat/{conversation_id}", 303)


@app.post("/chat/{conversation_id}/unmatch")
async def chat_unmatch(req: Request, conversation_id: int):
    """Togli match e chiudi chat (stile Tinder)."""
    user = current_user(req)
    if not user:
        return RedirectResponse("/login", 303)
    c = db()
    cur = c.cursor()
    try:
        cur.execute("""SELECT c.id, c.match_id, m.user1_id, m.user2_id FROM conversations c
               JOIN matches m ON m.id = c.match_id WHERE c.id=%s""", (conversation_id,))
        conv = cur.fetchone()
        if not conv or user["id"] not in (conv["user1_id"], conv["user2_id"]):
            cur.close(); c.close()
            return RedirectResponse("/chats", 303)
        mid = conv.get("match_id")
        if mid:
            cur.execute("UPDATE matches SET attivo=0 WHERE id=%s", (mid,))
        c.commit()
    except Exception as e:
        print("unmatch:", e)
        try: c.rollback()
        except Exception: pass
    cur.close(); c.close()
    return RedirectResponse("/chats?unmatched=1", 303)


@app.post("/chat/{conversation_id}/report")
async def chat_report(req: Request, conversation_id: int, motivo: str = Form("")):
    """Segnala + blocca + notifica admin/mod + auto-sospensione a 5 report."""
    user = current_user(req)
    if not user:
        return RedirectResponse("/login", 303)
    motivo = (motivo or "").strip()
    if len(motivo) < 3:
        return RedirectResponse(f"/chat/{conversation_id}?err=motivo", 303)

    c = db()
    cur = c.cursor()
    altro_id = None
    auto_sospeso = False
    report_count = 0
    try:
        cur.execute("""SELECT m.user1_id, m.user2_id FROM conversations c
               JOIN matches m ON m.id = c.match_id WHERE c.id=%s""", (conversation_id,))
        conv = cur.fetchone()
        if not conv or user["id"] not in (conv["user1_id"], conv["user2_id"]):
            cur.close()
            c.close()
            return RedirectResponse("/chats", 303)
        altro_id = conv["user2_id"] if user["id"] == conv["user1_id"] else conv["user1_id"]

        # evita admin/mod auto-ban accidentale da report massivi
        try:
            cur.execute("SELECT is_admin, is_mod, ruolo, nome FROM users WHERE id=%s", (altro_id,))
            target = cur.fetchone() or {}
        except Exception:
            target = {}
        target_nome = (target.get("nome") if isinstance(target, dict) else None) or str(altro_id)

        # tabella reports
        try:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS reports (
                    id SERIAL PRIMARY KEY,
                    reporter_id INTEGER,
                    reported_id INTEGER,
                    motivo TEXT,
                    conversation_id INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            c.commit()
        except Exception:
            try:
                c.rollback()
            except Exception:
                pass

        # un solo report per reporter→reported (evita spam segnalazioni)
        try:
            cur.execute(
                "SELECT id FROM reports WHERE reporter_id=%s AND reported_id=%s LIMIT 1",
                (user["id"], altro_id),
            )
            already = cur.fetchone()
        except Exception:
            already = None
            try:
                c.rollback()
            except Exception:
                pass

        if not already:
            try:
                cur.execute(
                    """INSERT INTO reports (reporter_id, reported_id, motivo, conversation_id)
                       VALUES (%s,%s,%s,%s)""",
                    (user["id"], altro_id, motivo[:500], conversation_id),
                )
            except Exception as e:
                print("report insert:", e)
                try:
                    c.rollback()
                except Exception:
                    pass

        # conta segnalazioni distinte (reporter diversi)
        try:
            cur.execute(
                """SELECT COUNT(DISTINCT reporter_id) AS n FROM reports
                   WHERE reported_id=%s
                     AND COALESCE(created_at, NOW()) > NOW() - interval '30 days'
                     AND COALESCE(status,'open') NOT IN ('false','dismissed')""",
                (altro_id,),
            )
            row = cur.fetchone()
            report_count = int((row["n"] if row else 0) or 0)
        except Exception as e:
            print("report count:", e)
            try:
                c.rollback()
                cur.execute(
                    "SELECT COUNT(DISTINCT reporter_id) AS n FROM reports WHERE reported_id=%s",
                    (altro_id,),
                )
                row = cur.fetchone()
                report_count = int((row["n"] if row else 0) or 0)
            except Exception:
                try:
                    c.rollback()
                except Exception:
                    pass
                report_count = 0

        # blocco bilaterale lato reporter
        try:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS blocks (
                    id SERIAL PRIMARY KEY,
                    blocker_id INTEGER,
                    blocked_id INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(blocker_id, blocked_id)
                )
            """)
            c.commit()
        except Exception:
            try:
                c.rollback()
            except Exception:
                pass
        try:
            cur.execute(
                "INSERT INTO blocks (blocker_id, blocked_id) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                (user["id"], altro_id),
            )
        except Exception:
            try:
                c.rollback()
                cur.execute(
                    "INSERT INTO blocks (blocker_id, blocked_id) VALUES (%s,%s)",
                    (user["id"], altro_id),
                )
            except Exception as e:
                print("report block:", e)
                try:
                    c.rollback()
                except Exception:
                    pass

        # disattiva match
        try:
            u1, u2 = sorted([user["id"], altro_id])
            cur.execute(
                "UPDATE matches SET attivo=0 WHERE (user1_id=%s AND user2_id=%s) OR (user1_id=%s AND user2_id=%s)",
                (u1, u2, u2, u1),
            )
        except Exception:
            try:
                c.rollback()
            except Exception:
                pass

        # auto-sospensione chat a 5 segnalazioni (non admin)
        is_staff = False
        if isinstance(target, dict):
            is_staff = bool(target.get("is_admin") or target.get("is_mod") or target.get("ruolo") in ("admin", "mod"))
        if report_count >= 5 and not is_staff:
            try:
                cur.execute(
                    """UPDATE users SET
                           stato = CASE WHEN COALESCE(stato,'') = 'bannato' THEN stato ELSE 'sospeso' END,
                           sospeso_fino = NOW() + interval '7 days',
                           updated_at = NOW()
                       WHERE id=%s""",
                    (altro_id,),
                )
                auto_sospeso = True
            except Exception as e:
                print("auto sospeso:", e)
                try:
                    c.rollback()
                    cur.execute(
                        "UPDATE users SET stato='sospeso' WHERE id=%s",
                        (altro_id,),
                    )
                    auto_sospeso = True
                except Exception as e2:
                    print("auto sospeso2:", e2)
                    try:
                        c.rollback()
                    except Exception:
                        pass
            # restrizione chat esplicita
            try:
                cur.execute("""
                    INSERT INTO user_restrictions (user_id, no_chat, no_messaggi)
                    VALUES (%s, 1, 1)
                    ON CONFLICT (user_id) DO UPDATE SET no_chat=1, no_messaggi=1
                """, (altro_id,))
            except Exception as e:
                print("restrizioni report:", e)
                try:
                    c.rollback()
                except Exception:
                    pass

        # notifiche a TUTTI admin e moderatori
        staff_ids = []
        try:
            cur.execute("""
                SELECT id FROM users
                WHERE COALESCE(is_admin,0)=1
                   OR COALESCE(is_mod,0)=1
                   OR COALESCE(ruolo,'') IN ('admin','mod')
            """)
            staff_ids = [r["id"] for r in cur.fetchall()]
        except Exception as e:
            print("staff list:", e)
            try:
                c.rollback()
            except Exception:
                pass

        titolo = "🚨 SEGNALAZIONE URGENTE" if auto_sospeso else "⚠️ Nuova segnalazione"
        corpo = (
            f"{user.get('nome') or user['id']} ha segnalato {target_nome} (id {altro_id}). "
            f"Motivo: {motivo[:150]}. Report totali: {report_count}."
        )
        if auto_sospeso:
            corpo += " Utente SOSPESO automaticamente (7 giorni) — intervento staff richiesto."

        for sid in staff_ids:
            try:
                cur.execute(
                    """INSERT INTO notifications (user_id, tipo, titolo, contenuto, related_id)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (sid, "report_urgente" if auto_sospeso else "report", titolo, corpo[:400], altro_id),
                )
            except Exception as e:
                print("report notif staff:", e)
                try:
                    c.rollback()
                except Exception:
                    pass

        # notifica all'utente sospeso
        if auto_sospeso:
            try:
                cur.execute(
                    """INSERT INTO notifications (user_id, tipo, titolo, contenuto, related_id)
                       VALUES (%s, 'sospensione', %s, %s, %s)""",
                    (altro_id, "Account sospeso",
                     "Hai ricevuto molte segnalazioni. Chat temporaneamente sospesa (7 giorni). Contatta il supporto.",
                     user["id"]),
                )
            except Exception:
                try:
                    c.rollback()
                except Exception:
                    pass

        c.commit()
    except Exception as e:
        print("chat_report fatal:", e)
        try:
            c.rollback()
        except Exception:
            pass
    finally:
        try:
            cur.close()
            c.close()
        except Exception:
            pass

    return RedirectResponse("/chats?reported=1&blocked=1", 303)



@app.post("/report")
async def universal_report(req: Request):
    """Segnalazione universale: profilo, gallery, messaggio, commento, annuncio…"""
    user = current_user(req)
    if not user:
        return RedirectResponse("/login", 303)
    form = await req.form()
    motivo = (form.get("motivo") or "").strip()
    try:
        reported_user_id = int(form.get("reported_user_id") or 0)
    except Exception:
        reported_user_id = 0
    content_type = (form.get("content_type") or "user").strip()[:40]
    content_id = (form.get("content_id") or "").strip()[:40]
    also_block = form.get("also_block") in ("1", "on", "true", "yes")
    redirect = (form.get("redirect") or "/notifications").strip() or "/notifications"
    if not redirect.startswith("/"):
        redirect = "/notifications"
    if len(motivo) < 3 or not reported_user_id:
        return RedirectResponse(redirect + ("&" if "?" in redirect else "?") + "err=report", 303)
    if reported_user_id == user["id"]:
        return RedirectResponse(redirect, 303)

    c = db()
    cur = c.cursor()
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS reports (
                id SERIAL PRIMARY KEY,
                reporter_id INTEGER,
                reported_id INTEGER,
                motivo TEXT,
                conversation_id INTEGER,
                content_type VARCHAR(40),
                content_id VARCHAR(40),
                status VARCHAR(20) DEFAULT 'open',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.commit()
    except Exception:
        try:
            c.rollback()
        except Exception:
            pass
    try:
        cur.execute("ALTER TABLE reports ADD COLUMN IF NOT EXISTS content_type VARCHAR(40)")
        cur.execute("ALTER TABLE reports ADD COLUMN IF NOT EXISTS content_id VARCHAR(40)")
        c.commit()
    except Exception:
        try:
            c.rollback()
        except Exception:
            pass

    # anti-spam: stesso reporter + stesso contenuto
    try:
        cur.execute(
            """SELECT id FROM reports WHERE reporter_id=%s AND reported_id=%s
               AND COALESCE(content_type,'')=%s AND COALESCE(content_id,'')=%s
               AND COALESCE(created_at,NOW()) > NOW() - interval '24 hours' LIMIT 1""",
            (user["id"], reported_user_id, content_type, content_id),
        )
        if cur.fetchone():
            cur.close()
            c.close()
            return RedirectResponse(redirect + ("&" if "?" in redirect else "?") + "reported=1", 303)
    except Exception:
        try:
            c.rollback()
        except Exception:
            pass

    try:
        cur.execute(
            """INSERT INTO reports (reporter_id, reported_id, motivo, content_type, content_id, status)
               VALUES (%s,%s,%s,%s,%s,'open')""",
            (user["id"], reported_user_id, motivo[:500], content_type, content_id or None),
        )
    except Exception as e:
        print("universal report insert:", e)
        try:
            c.rollback()
            cur.execute(
                """INSERT INTO reports (reporter_id, reported_id, motivo) VALUES (%s,%s,%s)""",
                (user["id"], reported_user_id, f"[{content_type}:{content_id}] {motivo[:400]}"),
            )
        except Exception as e2:
            print("universal report fallback:", e2)
            try:
                c.rollback()
            except Exception:
                pass

    if also_block:
        try:
            cur.execute(
                "INSERT INTO blocks (blocker_id, blocked_id) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                (user["id"], reported_user_id),
            )
        except Exception:
            try:
                c.rollback()
                cur.execute("INSERT INTO blocks (blocker_id, blocked_id) VALUES (%s,%s)", (user["id"], reported_user_id))
            except Exception:
                try:
                    c.rollback()
                except Exception:
                    pass

    # notifica staff
    try:
        cur.execute("""
            SELECT id FROM users
            WHERE COALESCE(is_admin,0)=1 OR COALESCE(is_mod,0)=1 OR COALESCE(ruolo,'') IN ('admin','mod')
        """)
        for a in cur.fetchall():
            cur.execute(
                """INSERT INTO notifications (user_id,tipo,titolo,contenuto,related_id)
                   VALUES (%s,'report',%s,%s,%s)""",
                (a["id"], f"⚠️ Segnalazione ({content_type})",
                 f"{user.get('nome') or user['id']} → user {reported_user_id}: {motivo[:120]}",
                 reported_user_id),
            )
    except Exception as e:
        print("report notif:", e)
        try:
            c.rollback()
        except Exception:
            pass

    try:
        c.commit()
    except Exception:
        pass
    cur.close()
    c.close()
    sep = "&" if "?" in redirect else "?"
    return RedirectResponse(f"{redirect}{sep}reported=1", 303)


def _normalize_phone(prefix: str, phone: str) -> str:
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    prefix = (prefix or "+39").strip()
    if not prefix.startswith("+"):
        prefix = "+" + prefix
    # togli 0 iniziale nazionale
    if digits.startswith("0"):
        digits = digits[1:]
    return prefix + digits


def _send_sms_otp(phone_e164: str, code: str) -> tuple:
    """Ritorna (ok, mode). mode=twilio|test"""
    import os, urllib.request, urllib.parse, base64
    sid = os.environ.get("TWILIO_ACCOUNT_SID", "").strip()
    token = os.environ.get("TWILIO_AUTH_TOKEN", "").strip()
    from_num = os.environ.get("TWILIO_FROM_NUMBER", "").strip()
    if sid and token and from_num:
        try:
            url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
            data = urllib.parse.urlencode({
                "To": phone_e164,
                "From": from_num,
                "Body": f"MyCheating codice verifica: {code}. Valido 10 minuti.",
            }).encode()
            req = urllib.request.Request(url, data=data, method="POST")
            cred = base64.b64encode(f"{sid}:{token}".encode()).decode()
            req.add_header("Authorization", f"Basic {cred}")
            with urllib.request.urlopen(req, timeout=20) as resp:
                if resp.status in (200, 201):
                    return True, "twilio"
            return False, "twilio"
        except Exception as e:
            print("twilio sms:", e)
            return False, "twilio"
    # test mode
    print(f"[OTP TEST] {phone_e164} -> {code}")
    return True, "test"



_last_smtp_error = None

def send_otp_email(to_email: str, code: str, phone: str = None) -> bool:
    """Invia OTP: prima EmailJS (API), poi SMTP se configurato."""
    global _last_smtp_error
    _last_smtp_error = None
    import os, json
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError, URLError

    to_email = (to_email or "").strip()
    if not to_email:
        _last_smtp_error = "missing recipient"
        return False

    # ---------- EmailJS ----------
    service_id = (os.environ.get("EMAILJS_SERVICE_ID") or "").strip()
    template_id = (os.environ.get("EMAILJS_TEMPLATE_ID") or "").strip()
    public_key = (os.environ.get("EMAILJS_PUBLIC_KEY") or "").strip()
    private_key = (os.environ.get("EMAILJS_PRIVATE_KEY") or "").strip()

    if service_id and template_id and public_key:
        if not private_key:
            _last_smtp_error = "EMAILJS_PRIVATE_KEY mancante (obbligatoria dal server). Su EmailJS: Account → Security → Allow API for non-browser applications"
            print("[EmailJS]", _last_smtp_error)
        payload = {
            "service_id": service_id,
            "template_id": template_id,
            "user_id": public_key,
            "accessToken": private_key or "",
            "template_params": {
                "code": str(code),
                "to_email": to_email,
                "email": to_email,
                "phone": phone or "",
                "message": f"Codice verifica MyCheating: {code}",
            },
        }
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        req = Request(
            "https://api.emailjs.com/api/v1.0/email/send",
            data=body,
            method="POST",
            headers=headers,
        )
        try:
            with urlopen(req, timeout=25) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                print(f"[EmailJS OK] status={resp.status} to={to_email} body={raw[:120]}")
                _last_smtp_error = None
                return True
        except HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace") if e.fp else ""
            _last_smtp_error = f"EmailJS HTTP {e.code}: {err_body[:300]}"
            print("[EmailJS fail]", _last_smtp_error)
        except URLError as e:
            _last_smtp_error = f"EmailJS URLError: {e}"
            print("[EmailJS fail]", _last_smtp_error)
        except Exception as e:
            _last_smtp_error = f"EmailJS {type(e).__name__}: {e}"
            print("[EmailJS fail]", _last_smtp_error)
    else:
        print("[EmailJS] non configurato (mancano SERVICE_ID/TEMPLATE_ID/PUBLIC_KEY)")

    # ---------- SMTP fallback ----------
    import smtplib, ssl
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    host = (os.environ.get("SMTP_HOST") or "").strip()
    user = (os.environ.get("SMTP_USER") or "").strip()
    password = (os.environ.get("SMTP_PASS") or "").strip()
    try:
        port = int((os.environ.get("SMTP_PORT") or "465").strip() or "465")
    except Exception:
        port = 465
    from_addr = (os.environ.get("SMTP_FROM") or user or "admin@mycheating.it").strip()

    if not (host and user and password):
        if not _last_smtp_error:
            _last_smtp_error = "no EmailJS and no SMTP config"
        print(f"[EMAIL OTP TEST] code={code} to={to_email}")
        return False

    msg = MIMEMultipart()
    msg["Subject"] = "Codice verifica MyCheating"
    msg["From"] = from_addr
    msg["To"] = to_email
    text = f"MyCheating – codice di verifica: {code}\nValido 10 minuti.\n"
    if phone:
        text += f"Numero: {phone}\n"
    msg.attach(MIMEText(text, "plain", "utf-8"))
    raw = msg.as_string()
    ctx = ssl._create_unverified_context()
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=20, context=ctx) as s:
                s.login(user, password)
                s.sendmail(from_addr, [to_email], raw)
        else:
            with smtplib.SMTP(host, port, timeout=20) as s:
                s.ehlo(); s.starttls(context=ctx); s.ehlo()
                s.login(user, password)
                s.sendmail(from_addr, [to_email], raw)
        print(f"[EMAIL OTP OK SMTP] to={to_email}")
        _last_smtp_error = None
        return True
    except Exception as e:
        _last_smtp_error = (str(_last_smtp_error) + " | " if _last_smtp_error else "") + f"SMTP: {e}"
        print("[SMTP fail]", e)
        print(f"[EMAIL OTP TEST fallback] {to_email} code={code}")
        return False


@app.get("/verify-phone", response_class=HTMLResponse)
async def verify_phone_page(req: Request):
    user = current_user(req)
    if not user:
        return RedirectResponse("/login", 303)
    if user.get("phone_verified"):
        return RedirectResponse("/profile?phone=ok", 303)
    return templates.TemplateResponse("verify_phone.html", {
        "request": req, "user": user, "step": "phone", "error": None, "ok": None, "test_otp": None,
    })


@app.post("/verify-phone/send")
async def verify_phone_send(req: Request, prefix: str = Form("+39"), phone: str = Form(...)):
    user = current_user(req)
    if not user:
        return RedirectResponse("/login", 303)
    e164 = _normalize_phone(prefix, phone)
    if len(e164) < 10 or len(e164) > 18:
        return templates.TemplateResponse("verify_phone.html", {
            "request": req, "user": user, "step": "phone", "error": "Numero non valido",
            "phone_display": phone, "ok": None, "test_otp": None,
        })
    import random
    from datetime import datetime, timedelta
    code = f"{random.randint(0, 999999):06d}"
    c = db()
    cur = c.cursor()
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS phone_otps (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                phone VARCHAR(32) NOT NULL,
                code VARCHAR(10) NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                used INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.commit()
    except Exception:
        try:
            c.rollback()
        except Exception:
            pass
    # rate limit: max 5 OTP / ora
    try:
        cur.execute(
            """SELECT COUNT(*) as n FROM phone_otps
               WHERE user_id=%s AND created_at > NOW() - interval '1 hour'""",
            (user["id"],),
        )
        if int(cur.fetchone()["n"] or 0) >= 5:
            cur.close()
            c.close()
            return templates.TemplateResponse("verify_phone.html", {
                "request": req, "user": user, "step": "phone",
                "error": "Troppi tentativi. Riprova tra un'ora.", "ok": None, "test_otp": None,
            })
    except Exception:
        try:
            c.rollback()
        except Exception:
            pass
    # telefono già usato da altro account verificato?
    try:
        cur.execute(
            "SELECT id FROM users WHERE telefono=%s AND COALESCE(phone_verified,0)=1 AND id<>%s LIMIT 1",
            (e164, user["id"]),
        )
        if cur.fetchone():
            cur.close()
            c.close()
            return templates.TemplateResponse("verify_phone.html", {
                "request": req, "user": user, "step": "phone",
                "error": "Questo numero è già verificato su un altro account.", "ok": None, "test_otp": None,
            })
    except Exception:
        try:
            c.rollback()
        except Exception:
            pass

    ok_sms, mode = _send_sms_otp(e164, code)
    if not ok_sms and mode == "twilio":
        cur.close()
        c.close()
        return templates.TemplateResponse("verify_phone.html", {
            "request": req, "user": user, "step": "phone",
            "error": "Invio SMS fallito. Riprova o contatta supporto.", "ok": None, "test_otp": None,
        })
    try:
        cur.execute(
            """INSERT INTO phone_otps (user_id, phone, code, expires_at)
               VALUES (%s,%s,%s, NOW() + interval '10 minutes')""",
            (user["id"], e164, code),
        )
        c.commit()
    except Exception as e:
        print("otp insert:", e)
        try:
            c.rollback()
        except Exception:
            pass
    cur.close()
    c.close()
    return templates.TemplateResponse("verify_phone.html", {
        "request": req, "user": user, "step": "otp",
        "phone": e164, "phone_display": e164, "phone_raw": phone, "prefix": prefix,
        "error": None, "ok": "Codice inviato" if mode == "twilio" else None,
        "test_otp": code if mode == "test" else None,
    })


@app.post("/verify-phone/confirm")
async def verify_phone_confirm(req: Request, phone: str = Form(...), otp: str = Form(...)):
    user = current_user(req)
    if not user:
        return RedirectResponse("/login", 303)
    code = "".join(ch for ch in otp if ch.isdigit())
    c = db()
    cur = c.cursor()
    try:
        cur.execute(
            """SELECT id FROM phone_otps
               WHERE user_id=%s AND phone=%s AND code=%s AND used=0
                 AND expires_at > NOW()
               ORDER BY id DESC LIMIT 1""",
            (user["id"], phone, code),
        )
        row = cur.fetchone()
        if not row:
            cur.close()
            c.close()
            return templates.TemplateResponse("verify_phone.html", {
                "request": req, "user": user, "step": "otp", "phone": phone,
                "phone_display": phone, "error": "Codice errato o scaduto", "ok": None, "test_otp": None,
            })
        cur.execute("UPDATE phone_otps SET used=1 WHERE id=%s", (row["id"],))
        try:
            cur.execute(
                """UPDATE users SET telefono=%s, phone_verified=1, phone_verified_at=NOW() WHERE id=%s""",
                (phone, user["id"]),
            )
        except Exception:
            c.rollback()
            cur.execute("UPDATE users SET telefono=%s WHERE id=%s", (phone, user["id"]))
        c.commit()
    except Exception as e:
        print("verify confirm:", e)
        try:
            c.rollback()
        except Exception:
            pass
        cur.close()
        c.close()
        return templates.TemplateResponse("verify_phone.html", {
            "request": req, "user": user, "step": "otp", "phone": phone,
            "phone_display": phone, "error": "Errore verifica", "ok": None, "test_otp": None,
        })
    cur.close()
    c.close()
    return RedirectResponse("/profile?phone=verified", 303)



@app.get("/verify-email", response_class=HTMLResponse)
async def verify_email_page(req: Request):
    user = current_user(req)
    if not user:
        return RedirectResponse("/login", 303)
    if user.get("email_verified"):
        return RedirectResponse("/profile?email=ok", 303)
    return templates.TemplateResponse("verify_email.html", {
        "request": req, "user": user, "step": "send", "error": None, "ok": None, "test_otp": None,
    })


@app.post("/verify-email/send")
async def verify_email_send(req: Request):
    user = current_user(req)
    if not user:
        return RedirectResponse("/login", 303)
    import random, smtplib, os
    from email.mime.text import MIMEText
    code = f"{random.randint(0, 999999):06d}"
    email = user.get("email")
    if not email:
        return templates.TemplateResponse("verify_email.html", {
            "request": req, "user": user, "step": "send", "error": "Email account mancante", "ok": None, "test_otp": None,
        })
    c = db()
    cur = c.cursor()
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS email_otps (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                email VARCHAR(200) NOT NULL,
                code VARCHAR(10) NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                used INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.commit()
    except Exception:
        try:
            c.rollback()
        except Exception:
            pass
    try:
        cur.execute(
            """SELECT COUNT(*) as n FROM email_otps WHERE user_id=%s AND created_at > NOW() - interval '1 hour'""",
            (user["id"],),
        )
        if int(cur.fetchone()["n"] or 0) >= 8:
            cur.close(); c.close()
            return templates.TemplateResponse("verify_email.html", {
                "request": req, "user": user, "step": "send", "error": "Troppi tentativi. Riprova più tardi.", "ok": None, "test_otp": None,
            })
    except Exception:
        try:
            c.rollback()
        except Exception:
            pass
    try:
        cur.execute(
            """INSERT INTO email_otps (user_id, email, code, expires_at) VALUES (%s,%s,%s, NOW() + interval '15 minutes')""",
            (user["id"], email, code),
        )
        c.commit()
    except Exception as e:
        print("email otp insert:", e)
        try:
            c.rollback()
        except Exception:
            pass
    cur.close(); c.close()

    # invio email se SMTP configurato
    smtp_host = os.environ.get("SMTP_HOST", "").strip()
    smtp_user = os.environ.get("SMTP_USER", "").strip()
    smtp_pass = os.environ.get("SMTP_PASS", "").strip()
    smtp_port = int(os.environ.get("SMTP_PORT", "587") or 587)
    smtp_from = os.environ.get("SMTP_FROM", smtp_user or "noreply@mycheating.local")
    sent = False
    if smtp_host and smtp_user and smtp_pass:
        try:
            msg = MIMEText(f"Il tuo codice MyCheating è: {code}\nValido 15 minuti.")
            msg["Subject"] = "Codice verifica MyCheating"
            msg["From"] = smtp_from
            msg["To"] = email
            sent = send_otp_email(email, code)
        except Exception as e:
            print("smtp send:", e)
            sent = False
    else:
        print(f"[EMAIL OTP TEST] {email} -> {code}")

    return templates.TemplateResponse("verify_email.html", {
        "request": req, "user": user, "step": "otp", "error": None,
        "ok": "Codice inviato alla tua email" if sent else None,
        "test_otp": None if sent else code,
        "email": email,
    })


@app.post("/verify-email/confirm")
async def verify_email_confirm(req: Request, otp: str = Form(...)):
    user = current_user(req)
    if not user:
        return RedirectResponse("/login", 303)
    code = "".join(ch for ch in otp if ch.isdigit())
    c = db()
    cur = c.cursor()
    try:
        cur.execute(
            """SELECT id FROM email_otps WHERE user_id=%s AND code=%s AND used=0 AND expires_at > NOW()
               ORDER BY id DESC LIMIT 1""",
            (user["id"], code),
        )
        row = cur.fetchone()
        if not row:
            cur.close(); c.close()
            return templates.TemplateResponse("verify_email.html", {
                "request": req, "user": user, "step": "otp", "error": "Codice errato o scaduto",
                "ok": None, "test_otp": None, "email": user.get("email"),
            })
        cur.execute("UPDATE email_otps SET used=1 WHERE id=%s", (row["id"],))
        try:
            cur.execute("UPDATE users SET email_verified=1, email_verified_at=NOW() WHERE id=%s", (user["id"],))
        except Exception:
            c.rollback()
            try:
                cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified INTEGER DEFAULT 0")
                cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified_at TIMESTAMP")
                c.commit()
                cur.execute("UPDATE users SET email_verified=1, email_verified_at=NOW() WHERE id=%s", (user["id"],))
            except Exception as e:
                print("email verified col:", e)
                c.rollback()
        c.commit()
    except Exception as e:
        print("email confirm:", e)
        try:
            c.rollback()
        except Exception:
            pass
        cur.close(); c.close()
        return templates.TemplateResponse("verify_email.html", {
            "request": req, "user": user, "step": "otp", "error": "Errore verifica", "ok": None, "test_otp": None,
        })
    cur.close(); c.close()
    return RedirectResponse("/profile?email=verified", 303)



@app.post("/verify/start")
async def verify_start(req: Request, prefix: str = Form("+39"), phone: str = Form(...)):
    """Telefono privato + OTP via email (10 min)."""
    user = current_user(req)
    if not user:
        return RedirectResponse("/login", 303)
    if user.get("phone_verified") or user.get("is_verified"):
        return RedirectResponse("/discover", 303)
    e164 = _normalize_phone(prefix, phone)
    if len(e164) < 10 or len(e164) > 18:
        return RedirectResponse("/discover?verify_err=numero", 303)
    import random
    c = db()
    cur = c.cursor()
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS email_otps (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                email VARCHAR(200) NOT NULL,
                code VARCHAR(10) NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                used INTEGER DEFAULT 0,
                phone VARCHAR(32),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.commit()
    except Exception:
        try: c.rollback()
        except Exception: pass
    try:
        cur.execute("ALTER TABLE email_otps ADD COLUMN IF NOT EXISTS phone VARCHAR(32)")
        c.commit()
    except Exception:
        try: c.rollback()
        except Exception: pass
    try:
        cur.execute(
            "SELECT id FROM users WHERE telefono=%s AND COALESCE(phone_verified,0)=1 AND id<>%s LIMIT 1",
            (e164, user["id"]),
        )
        if cur.fetchone():
            cur.close(); c.close()
            return RedirectResponse("/discover?verify_err=usato", 303)
    except Exception:
        try: c.rollback()
        except Exception: pass
    try:
        cur.execute(
            "SELECT COUNT(*) as n FROM email_otps WHERE user_id=%s AND created_at > NOW() - interval '1 hour'",
            (user["id"],),
        )
        if int(cur.fetchone()["n"] or 0) >= 8:
            cur.close(); c.close()
            return RedirectResponse("/discover?verify_err=limit", 303)
    except Exception:
        try: c.rollback()
        except Exception: pass

    code = f"{random.randint(0, 999999):06d}"
    email = user.get("email") or ""
    try:
        cur.execute(
            """INSERT INTO email_otps (user_id, email, code, expires_at, phone)
               VALUES (%s,%s,%s, NOW() + interval '10 minutes', %s)""",
            (user["id"], email, code, e164),
        )
        cur.execute("UPDATE users SET telefono=%s WHERE id=%s", (e164, user["id"]))
        c.commit()
    except Exception as e:
        print("verify start:", e)
        try: c.rollback()
        except Exception: pass
        cur.close(); c.close()
        return RedirectResponse("/discover?verify_err=db", 303)
    cur.close(); c.close()

    sent = send_otp_email(email, code, e164)
    try:
        req.session["verify_pending_phone"] = e164
        if not sent:
            req.session["verify_test_otp"] = code
        else:
            req.session.pop("verify_test_otp", None)
    except Exception:
        pass
    return RedirectResponse("/discover?verify=otp", 303)


@app.post("/verify/confirm")
async def verify_confirm(req: Request, otp: str = Form(...)):
    user = current_user(req)
    if not user:
        return RedirectResponse("/login", 303)
    code = "".join(ch for ch in otp if ch.isdigit())
    c = db()
    cur = c.cursor()
    try:
        cur.execute(
            """SELECT id, phone FROM email_otps
               WHERE user_id=%s AND code=%s AND used=0 AND expires_at > NOW()
               ORDER BY id DESC LIMIT 1""",
            (user["id"], code),
        )
        row = cur.fetchone()
        if not row:
            cur.close(); c.close()
            return RedirectResponse("/discover?verify=otp&verify_err=codice", 303)
        phone = row.get("phone") or user.get("telefono")
        cur.execute("UPDATE email_otps SET used=1 WHERE id=%s", (row["id"],))
        try:
            cur.execute(
                """UPDATE users SET telefono=%s, phone_verified=1, phone_verified_at=NOW(),
                       is_verified=1, email_verified=1, email_verified_at=NOW()
                   WHERE id=%s""",
                (phone, user["id"]),
            )
        except Exception:
            c.rollback()
            try:
                cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_verified INTEGER DEFAULT 0")
                c.commit()
                cur.execute(
                    """UPDATE users SET telefono=%s, phone_verified=1, is_verified=1 WHERE id=%s""",
                    (phone, user["id"]),
                )
            except Exception as e:
                print("verify confirm:", e)
                c.rollback()
        c.commit()
    except Exception as e:
        print("verify confirm fatal:", e)
        try: c.rollback()
        except Exception: pass
        cur.close(); c.close()
        return RedirectResponse("/discover?verify=otp&verify_err=db", 303)
    cur.close(); c.close()
    try:
        req.session.pop("verify_pending_phone", None)
        req.session.pop("verify_test_otp", None)
        req.session.pop("verify_dismissed", None)
    except Exception:
        pass
    return RedirectResponse("/discover?verify=ok", 303)


@app.get("/verify/open")
async def verify_open(req: Request):
    """Riapre il popup verifica (dal profilo)."""
    user = current_user(req)
    if not user:
        return RedirectResponse("/login", 303)
    try:
        req.session.pop("verify_dismissed", None)
    except Exception:
        pass
    if user.get("phone_verified") or user.get("is_verified"):
        return RedirectResponse("/profile?already=1", 303)
    return RedirectResponse("/discover?verify=open", 303)


@app.post("/verify/dismiss")
async def verify_dismiss(req: Request):
    if current_user(req):
        try:
            req.session["verify_dismissed"] = 1
        except Exception:
            pass
    return RedirectResponse("/discover", 303)


@app.get("/verify-phone/skip")
async def verify_phone_skip(req: Request):
    user = current_user(req)
    if not user:
        return RedirectResponse("/login", 303)
    # soft skip: consente uso app ma banner possibile
    return RedirectResponse("/discover", 303)



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


@app.get("/u/{user_id}", response_class=HTMLResponse)
async def public_profile(req: Request, user_id: int):
    """Profilo di un altro utente (match / notifica)."""
    user = current_user(req)
    if not user:
        return RedirectResponse("/login", 303)
    if user_id == user["id"]:
        return RedirectResponse("/profile", 303)
    c = db()
    cur = c.cursor()
    cur.execute("SELECT * FROM users WHERE id=%s AND stato='attivo'", (user_id,))
    row = cur.fetchone()
    if not row:
        cur.close()
        c.close()
        return RedirectResponse("/matches", 303)
    altro = dict(row)
    try:
        altro["eta"] = eta(altro.get("data_nascita"))
    except Exception:
        altro["eta"] = None
    # match?
    u1, u2 = sorted([user["id"], user_id])
    cur.execute(
        "SELECT id FROM matches WHERE user1_id=%s AND user2_id=%s AND COALESCE(attivo,1)=1",
        (u1, u2),
    )
    is_match = cur.fetchone() is not None
    photos = []
    try:
        if is_match:
            cur.execute(
                "SELECT * FROM user_photos WHERE user_id=%s AND COALESCE(is_private,0)=0 ORDER BY id LIMIT 12",
                (user_id,),
            )
        else:
            cur.execute(
                "SELECT * FROM user_photos WHERE user_id=%s AND COALESCE(is_private,0)=0 ORDER BY id LIMIT 6",
                (user_id,),
            )
        photos = [dict(p) for p in cur.fetchall()]
    except Exception:
        pass
    conv_id = None
    if is_match:
        try:
            cur.execute(
                """SELECT c.id FROM conversations c
                   JOIN matches m ON m.id = c.match_id
                   WHERE m.user1_id=%s AND m.user2_id=%s LIMIT 1""",
                (u1, u2),
            )
            r = cur.fetchone()
            conv_id = r["id"] if r else None
        except Exception:
            pass
    cur.close()
    c.close()
    return templates.TemplateResponse("public_profile.html", {
        "request": req, "user": user, "altro": altro, "photos": photos,
        "is_match": is_match, "conv_id": conv_id,
        "unread": unread_count(user["id"]),
    })


@app.get("/notifications/{notif_id}/open")
async def notification_open(req: Request, notif_id: int):
    """
    Click notifica:
    - messaggio  → chat
    - match      → profilo dell'altro
    - dono       → profilo mittente
    - like       → profilo di chi ha messo like
    - foto       → chat
    """
    user = current_user(req)
    if not user:
        return RedirectResponse("/login", 303)

    def go_profile(uid):
        if uid and int(uid) != int(user["id"]):
            return RedirectResponse(f"/u/{int(uid)}", 303)
        return RedirectResponse("/notifications", 303)

    def go_chat(cid):
        if cid:
            return RedirectResponse(f"/chat/{int(cid)}", 303)
        return RedirectResponse("/chats", 303)

    c = None
    cur = None
    try:
        c = db()
        cur = c.cursor()
        cur.execute(
            "SELECT * FROM notifications WHERE id=%s AND user_id=%s",
            (notif_id, user["id"]),
        )
        row = cur.fetchone()
        if not row:
            return RedirectResponse("/notifications", 303)
        n = dict(row)
        try:
            cur.execute("UPDATE notifications SET letto=1 WHERE id=%s", (notif_id,))
            c.commit()
        except Exception:
            try:
                c.rollback()
            except Exception:
                pass

        tipo = (n.get("tipo") or "").strip().lower()
        related = n.get("related_id")
        from_uid = n.get("from_user_id")  # se colonna esiste

        # --- MESSAGGIO → chat (related = conversation_id) ---
        if tipo in ("nuovo_messaggio", "messaggio", "chat", "message"):
            if related:
                # verifica che sia conversazione valida, altrimenti profilo altro
                try:
                    cur.execute(
                        """SELECT c.id, m.user1_id, m.user2_id
                           FROM conversations c
                           JOIN matches m ON m.id = c.match_id
                           WHERE c.id=%s""",
                        (related,),
                    )
                    conv = cur.fetchone()
                    if conv and user["id"] in (conv["user1_id"], conv["user2_id"]):
                        return go_chat(related)
                    # fallback profilo
                    if conv:
                        other = conv["user2_id"] if conv["user1_id"] == user["id"] else conv["user1_id"]
                        return go_profile(other)
                except Exception as e:
                    print("notif open chat:", e)
            return RedirectResponse("/chats", 303)

        # --- MATCH → profilo altro (related = match_id) ---
        if tipo in ("nuovo_match", "match"):
            other = None
            if related:
                try:
                    cur.execute("SELECT user1_id, user2_id FROM matches WHERE id=%s", (related,))
                    m = cur.fetchone()
                    if m:
                        other = m["user2_id"] if int(m["user1_id"]) == int(user["id"]) else m["user1_id"]
                except Exception as e:
                    print("notif open match:", e)
            if other:
                return go_profile(other)
            return RedirectResponse("/matches", 303)

        # --- DONO → profilo mittente (related = gifts_sent.id) ---
        if tipo in ("dono", "gift", "regalo"):
            other = from_uid
            if related and not other:
                try:
                    cur.execute("SELECT from_user_id FROM gifts_sent WHERE id=%s", (related,))
                    g = cur.fetchone()
                    if g:
                        other = g["from_user_id"]
                except Exception as e:
                    print("notif open gift:", e)
            if other:
                return go_profile(other)
            return RedirectResponse("/gifts", 303)

        # --- LIKE → related_id = user_id di chi ha messo like ---
        if tipo in ("like", "superlike", "nuovo_like"):
            other = from_uid or related
            if related:
                try:
                    cur.execute("SELECT from_user_id FROM swipes WHERE id=%s", (related,))
                    s = cur.fetchone()
                    if s:
                        other = s["from_user_id"]
                except Exception:
                    other = related
            if other:
                return go_profile(other)
            return RedirectResponse("/likes", 303)

        # --- FOTO ---
        if "foto" in tipo or "photo" in tipo:
            if related:
                return go_chat(related)
            return RedirectResponse("/chats", 303)

        # --- BOOST e altro ---
        if tipo in ("boost",):
            return RedirectResponse("/profile", 303)

        if from_uid:
            return go_profile(from_uid)
        if related:
            # prova come user id
            try:
                cur.execute("SELECT id FROM users WHERE id=%s", (related,))
                if cur.fetchone():
                    return go_profile(related)
            except Exception:
                pass
        return RedirectResponse("/notifications", 303)

    except Exception as e:
        print("notification_open fatal:", type(e).__name__, e)
        return RedirectResponse("/notifications", 303)
    finally:
        try:
            if cur:
                cur.close()
            if c:
                c.close()
        except Exception:
            pass


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
    # ensure optional columns
    try:
        c0 = db()
        cur0 = c0.cursor()
        cur0.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_ip VARCHAR(64)")
        cur0.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_bot INTEGER DEFAULT 0")
        cur0.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS false_reports_count INTEGER DEFAULT 0")
        cur0.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS telefono VARCHAR(32)")
        cur0.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS phone_verified INTEGER DEFAULT 0")
        cur0.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS phone_verified_at TIMESTAMP")
        cur0.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified INTEGER DEFAULT 0")
        cur0.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified_at TIMESTAMP")
        cur0.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_verified INTEGER DEFAULT 0")
        c0.commit()
        cur0.close()
        c0.close()
    except Exception:
        pass

    q = (req.query_params.get("q") or "").strip()
    mq = (req.query_params.get("mq") or "").strip()
    message_hits = []
    bots = []
    phones = []
    fq = (req.query_params.get("fq") or "").strip()
    filter_stato = req.query_params.get("filter") or "tutti"

    c = db()
    cur = c.cursor()

    try:
        cur.execute("SELECT COUNT(*) as c FROM users WHERE COALESCE(is_bot,0)=0")
        users_count = cur.fetchone()["c"]
    except Exception:
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
                SELECT id, nome, username, email, stato, is_admin, is_mod, credits, is_online, latitude, longitude, bio, citta, genere, orientamento, data_nascita, ruolo, sospeso_fino, note_admin, last_ip
                FROM users
                WHERE COALESCE(is_bot,0)=0
                  AND (nome ILIKE %s OR email ILIKE %s OR username ILIKE %s)
                ORDER BY id DESC LIMIT 100
            """, (like, like, like))
        else:
            cur.execute("""
                SELECT id, nome, username, email, stato, is_admin, is_mod, credits, is_online, latitude, longitude, bio, citta, genere, orientamento, data_nascita, ruolo, sospeso_fino, note_admin, last_ip
                FROM users WHERE COALESCE(is_bot,0)=0 ORDER BY id DESC LIMIT 100
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
    mq = (req.query_params.get("mq") or "").strip()
    message_hits = []

    if tab == "messages":
        try:
            mq_like = f"%{mq}%" if mq else None

            if mq:
                # 1) Messaggi che contengono la parola (query semplice)
                try:
                    cur.execute("""
                        SELECT msg.id, msg.contenuto, msg.conversation_id, msg.data_invio, msg.sender_id,
                               u.nome as sender_nome
                        FROM messages msg
                        LEFT JOIN users u ON u.id = msg.sender_id
                        WHERE msg.contenuto ILIKE %s
                        ORDER BY msg.id DESC
                        LIMIT 150
                    """, (mq_like,))
                    raw_hits = [dict(r) for r in cur.fetchall()]
                except Exception as e:
                    print("msg hits simple:", e)
                    c.rollback()
                    raw_hits = []

                # arricchisci con nomi chat
                message_hits = []
                conv_ids = set()
                for h in raw_hits:
                    cid = h.get("conversation_id")
                    if cid:
                        conv_ids.add(cid)
                    h["nome1"] = h.get("nome1") or "?"
                    h["nome2"] = h.get("nome2") or "?"
                    message_hits.append(h)

                # nomi partecipanti per ogni hit
                for h in message_hits:
                    try:
                        cur.execute("""
                            SELECT u1.nome as nome1, u2.nome as nome2
                            FROM conversations c
                            JOIN matches m ON m.id = c.match_id
                            JOIN users u1 ON u1.id = m.user1_id
                            JOIN users u2 ON u2.id = m.user2_id
                            WHERE c.id = %s
                        """, (h["conversation_id"],))
                        row = cur.fetchone()
                        if row:
                            h["nome1"] = row["nome1"]
                            h["nome2"] = row["nome2"]
                    except Exception:
                        try:
                            c.rollback()
                        except Exception:
                            pass

                # 2) Lista conversazioni filtrate
                conversations = []
                if conv_ids:
                    ids = list(conv_ids)[:200]
                    try:
                        cur.execute("""
                            SELECT c.id, c.ultimo_messaggio_at,
                                   m.user1_id, m.user2_id,
                                   u1.nome as nome1, u2.nome as nome2
                            FROM conversations c
                            JOIN matches m ON m.id = c.match_id
                            JOIN users u1 ON u1.id = m.user1_id
                            JOIN users u2 ON u2.id = m.user2_id
                            WHERE c.id = ANY(%s)
                            ORDER BY c.id DESC
                        """, (ids,))
                        conversations = [dict(r) for r in cur.fetchall()]
                        for cv in conversations:
                            # snippet del messaggio trovato
                            for h in message_hits:
                                if h.get("conversation_id") == cv["id"]:
                                    cv["last_message"] = h.get("contenuto")
                                    break
                    except Exception as e:
                        print("msg conv filter:", e)
                        try:
                            c.rollback()
                        except Exception:
                            pass
                        # fallback senza ANY
                        for cid in ids[:50]:
                            try:
                                cur.execute("""
                                    SELECT c.id, u1.nome as nome1, u2.nome as nome2
                                    FROM conversations c
                                    JOIN matches m ON m.id = c.match_id
                                    JOIN users u1 ON u1.id = m.user1_id
                                    JOIN users u2 ON u2.id = m.user2_id
                                    WHERE c.id = %s
                                """, (cid,))
                                row = cur.fetchone()
                                if row:
                                    d = dict(row)
                                    d["last_message"] = next((h["contenuto"] for h in message_hits if h.get("conversation_id")==cid), "")
                                    conversations.append(d)
                            except Exception:
                                c.rollback()
            else:
                try:
                    cur.execute("""
                        SELECT c.id, c.ultimo_messaggio_at, m.data_match,
                               m.user1_id, m.user2_id,
                               u1.nome as nome1, u2.nome as nome2
                        FROM conversations c
                        JOIN matches m ON m.id = c.match_id
                        JOIN users u1 ON u1.id = m.user1_id
                        JOIN users u2 ON u2.id = m.user2_id
                        ORDER BY c.id DESC
                        LIMIT 200
                    """)
                    conversations = [dict(r) for r in cur.fetchall()]
                    for cv in conversations:
                        try:
                            cur.execute(
                                "SELECT contenuto FROM messages WHERE conversation_id=%s ORDER BY id DESC LIMIT 1",
                                (cv["id"],),
                            )
                            lm = cur.fetchone()
                            cv["last_message"] = lm["contenuto"] if lm else ""
                        except Exception:
                            c.rollback()
                            cv["last_message"] = ""
                except Exception as e:
                    print("msg list all:", e)
                    c.rollback()
                    conversations = []

            # thread aperto
            conv_id = req.query_params.get("conv")
            if conv_id:
                try:
                    conv_id = int(conv_id)
                except Exception:
                    conv_id = None
            if conv_id:
                try:
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
                        if mq:
                            cur.execute("""
                                SELECT m.id, m.sender_id, m.contenuto, m.data_invio, u.nome as sender_nome
                                FROM messages m
                                LEFT JOIN users u ON u.id = m.sender_id
                                WHERE m.conversation_id = %s AND m.contenuto ILIKE %s
                                ORDER BY m.id ASC
                            """, (conv_id, mq_like))
                        else:
                            cur.execute("""
                                SELECT m.id, m.sender_id, m.contenuto, m.data_invio, u.nome as sender_nome
                                FROM messages m
                                LEFT JOIN users u ON u.id = m.sender_id
                                WHERE m.conversation_id = %s
                                ORDER BY m.id ASC
                            """, (conv_id,))
                        thread_messages = [dict(r) for r in cur.fetchall()]
                except Exception as e:
                    print("msg thread:", e)
                    try:
                        c.rollback()
                    except Exception:
                        pass
        except Exception as e:
            print("admin messages error:", e)
            try:
                c.rollback()
            except Exception:
                pass
            conversations = conversations or []
            message_hits = message_hits or []


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

    gallery_items = []
    bots = []
    gallery_folders = []
    folder_user_id = None
    folder_user = None
    gift_types = []
    gifts_recent = []
    blocks = []
    reports = []
    reports_open = 0

    gallery_folders = []  # [{user_id, nome, username, count, private_count}]
    folder_user_id = None
    folder_user = None

    if tab == "foto":
        try:
            if fq:
                like = f"%{fq}%"
                cur.execute("""
                    SELECT p.user_id,
                           u.nome as nome_user,
                           u.username,
                           u.email,
                           COUNT(*) as cnt,
                           SUM(CASE WHEN COALESCE(p.is_private,0)=1 THEN 1 ELSE 0 END) as private_cnt
                    FROM user_photos p
                    LEFT JOIN users u ON u.id = p.user_id
                    WHERE u.nome ILIKE %s OR u.username ILIKE %s OR u.email ILIKE %s
                       OR COALESCE(u.telefono,'') ILIKE %s
                       OR CAST(p.user_id AS TEXT) = %s
                    GROUP BY p.user_id, u.nome, u.username, u.email
                    ORDER BY u.nome NULLS LAST, p.user_id
                """, (like, like, like, like, fq.strip()))
            else:
                cur.execute("""
                    SELECT p.user_id,
                           u.nome as nome_user,
                           u.username,
                           u.email,
                           COUNT(*) as cnt,
                           SUM(CASE WHEN COALESCE(p.is_private,0)=1 THEN 1 ELSE 0 END) as private_cnt
                    FROM user_photos p
                    LEFT JOIN users u ON u.id = p.user_id
                    GROUP BY p.user_id, u.nome, u.username, u.email
                    ORDER BY u.nome NULLS LAST, p.user_id
                """)
            gallery_folders = [dict(r) for r in cur.fetchall()]
        except Exception as e:
            print("admin foto folders:", e)
            try:
                c.rollback()
                if fq:
                    like = f"%{fq}%"
                    cur.execute("""
                        SELECT DISTINCT p.user_id, u.nome as nome_user, u.username, u.email
                        FROM user_photos p
                        LEFT JOIN users u ON u.id = p.user_id
                        WHERE u.nome ILIKE %s OR u.username ILIKE %s OR u.email ILIKE %s
                           OR COALESCE(u.telefono,'') ILIKE %s
                        ORDER BY u.nome NULLS LAST
                    """, (like, like, like, like))
                else:
                    cur.execute("""
                        SELECT DISTINCT p.user_id, u.nome as nome_user, u.username, u.email
                        FROM user_photos p
                        LEFT JOIN users u ON u.id = p.user_id
                        ORDER BY u.nome NULLS LAST
                    """)
                gallery_folders = []
                for r in cur.fetchall():
                    d = dict(r)
                    d["cnt"] = 0
                    d["private_cnt"] = 0
                    gallery_folders.append(d)
            except Exception as e2:
                print("admin foto folders2:", e2)

        # cartella aperta?
        try:
            fu = req.query_params.get("user")
            if fu:
                folder_user_id = int(fu)
        except Exception:
            folder_user_id = None

        if folder_user_id:
            try:
                cur.execute(
                    "SELECT id, nome, username, email FROM users WHERE id=%s",
                    (folder_user_id,),
                )
                row = cur.fetchone()
                folder_user = dict(row) if row else {"id": folder_user_id, "nome": f"User {folder_user_id}"}
                cur.execute("""
                    SELECT p.*, u.nome as nome_user
                    FROM user_photos p
                    LEFT JOIN users u ON u.id = p.user_id
                    WHERE p.user_id=%s
                    ORDER BY p.id DESC LIMIT 200
                """, (folder_user_id,))
                gallery_items = [dict(r) for r in cur.fetchall()]
            except Exception as e:
                print("admin foto folder content:", e)

    if tab == "doni":
        try:
            cur.execute("SELECT * FROM gift_types ORDER BY costo ASC")
            gift_types = [dict(r) for r in cur.fetchall()]
        except Exception as e:
            print("admin gift_types:", e)
        try:
            cur.execute("""
                SELECT g.*, gt.nome, gt.emoji, uf.nome as from_nome, ut.nome as to_nome
                FROM gifts_sent g
                JOIN gift_types gt ON gt.id = g.gift_type_id
                JOIN users uf ON uf.id = g.from_user_id
                JOIN users ut ON ut.id = g.to_user_id
                ORDER BY g.created_at DESC LIMIT 50
            """)
            gifts_recent = [dict(r) for r in cur.fetchall()]
        except Exception as e:
            print("admin gifts_sent:", e)

    if tab == "blocchi":
        try:
            cur.execute("""
                SELECT b.id, b.blocker_id, b.blocked_id, b.created_at,
                       u1.nome as blocker_nome, u1.username as blocker_user,
                       u2.nome as blocked_nome, u2.username as blocked_user
                FROM blocks b
                JOIN users u1 ON u1.id = b.blocker_id
                JOIN users u2 ON u2.id = b.blocked_id
                ORDER BY b.id DESC LIMIT 200
            """)
            blocks = [dict(r) for r in cur.fetchall()]
        except Exception as e:
            print("admin blocks:", e)
            try:
                c.rollback()
                cur.execute("""
                    SELECT b.*, u1.nome as blocker_nome, u2.nome as blocked_nome
                    FROM blocks b
                    JOIN users u1 ON u1.id = b.blocker_id
                    JOIN users u2 ON u2.id = b.blocked_id
                    ORDER BY b.id DESC LIMIT 200
                """)
                blocks = [dict(r) for r in cur.fetchall()]
            except Exception as e2:
                print("admin blocks2:", e2)

    reports = []
    reports_open = 0
    if tab == "segnalazioni":
        try:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS reports (
                    id SERIAL PRIMARY KEY,
                    reporter_id INTEGER,
                    reported_id INTEGER,
                    motivo TEXT,
                    conversation_id INTEGER,
                    status VARCHAR(20) DEFAULT 'open',
                    reviewed_by INTEGER,
                    reviewed_at TIMESTAMP,
                    false_report INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            c.commit()
        except Exception:
            try:
                c.rollback()
            except Exception:
                pass
        try:
            cur.execute("ALTER TABLE reports ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'open'")
            cur.execute("ALTER TABLE reports ADD COLUMN IF NOT EXISTS reviewed_by INTEGER")
            cur.execute("ALTER TABLE reports ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMP")
            cur.execute("ALTER TABLE reports ADD COLUMN IF NOT EXISTS false_report INTEGER DEFAULT 0")
            c.commit()
        except Exception:
            try:
                c.rollback()
            except Exception:
                pass
        try:
            cur.execute("""
                SELECT r.*,
                       u1.nome as reporter_nome, u1.username as reporter_user,
                       u2.nome as reported_nome, u2.username as reported_user,
                       u2.stato as reported_stato, u2.sospeso_fino as reported_sospeso
                FROM reports r
                LEFT JOIN users u1 ON u1.id = r.reporter_id
                LEFT JOIN users u2 ON u2.id = r.reported_id
                ORDER BY
                  CASE WHEN COALESCE(r.status,'open')='open' THEN 0 ELSE 1 END,
                  r.created_at DESC NULLS LAST
                LIMIT 300
            """)
            reports = [dict(x) for x in cur.fetchall()]
            reports_open = sum(1 for x in reports if (x.get("status") or "open") == "open")
        except Exception as e:
            print("admin reports:", e)
            try:
                c.rollback()
            except Exception:
                pass

    search_results = []  # lista globale {tipo, titolo, sottotitolo, link, meta}


    phones = []
    if tab == "telefoni":
        try:
            cur.execute("""
                SELECT id, nome, username, email, telefono, phone_verified, phone_verified_at, stato, last_ip
                FROM users
                WHERE telefono IS NOT NULL AND telefono <> ''
                ORDER BY COALESCE(phone_verified,0) DESC, id DESC
                LIMIT 200
            """)
            phones = [dict(r) for r in cur.fetchall()]
        except Exception as e:
            print("admin phones:", e)
            try:
                c.rollback()
                cur.execute("SELECT id, nome, username, email, telefono, stato FROM users WHERE telefono IS NOT NULL LIMIT 200")
                phones = [dict(r) for r in cur.fetchall()]
            except Exception:
                phones = []

    if tab == "bot":
        try:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS bots (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER UNIQUE,
                    nome VARCHAR(100),
                    username VARCHAR(80),
                    email VARCHAR(200),
                    genere VARCHAR(20),
                    orientamento VARCHAR(30),
                    data_nascita DATE,
                    bio TEXT,
                    citta VARCHAR(100),
                    credits INTEGER DEFAULT 50,
                    latitude DOUBLE PRECISION,
                    longitude DOUBLE PRECISION,
                    foto_url TEXT,
                    attivo INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            c.commit()
        except Exception:
            try:
                c.rollback()
            except Exception:
                pass
        try:
            cur.execute("""
                SELECT b.id, b.user_id, b.nome, b.username, b.email, b.genere, b.orientamento,
                       b.data_nascita, b.bio, b.citta, b.credits, b.latitude, b.longitude,
                       b.attivo, b.foto_url,
                       COALESCE(u.stato, CASE WHEN b.attivo=1 THEN 'attivo' ELSE 'disattivo' END) as stato
                FROM bots b
                LEFT JOIN users u ON u.id = b.user_id
                ORDER BY b.id DESC LIMIT 100
            """)
            bots = [dict(r) for r in cur.fetchall()]
        except Exception as e:
            print("admin bots table:", e)
            try:
                c.rollback()
                cur.execute("""
                    SELECT id, id as user_id, nome, username, email, genere, orientamento,
                           data_nascita, bio, citta, credits, latitude, longitude,
                           1 as attivo, foto_principale_url as foto_url, stato
                    FROM users WHERE COALESCE(is_bot,0)=1 ORDER BY id DESC LIMIT 100
                """)
                bots = [dict(r) for r in cur.fetchall()]
            except Exception as e2:
                print("admin bots fallback:", e2)
                bots = []


    if tab == "ricerca" and q:
        like = f"%{q}%"
        # UTENTI
        try:
            cur.execute("""
                SELECT id, nome, username, email, stato, is_admin, is_mod, credits, citta, data_nascita, ruolo, sospeso_fino, bio, telefono
                FROM users
                WHERE nome ILIKE %s OR email ILIKE %s OR username ILIKE %s OR citta ILIKE %s
                   OR COALESCE(bio,'') ILIKE %s OR COALESCE(telefono,'') ILIKE %s
                   OR CAST(id AS TEXT) = %s
                ORDER BY id DESC LIMIT 40
            """, (like, like, like, like, like, like, q.strip()))
            users = [dict(u) for u in cur.fetchall()]
            for u in users:
                u["restrictions"] = get_restrictions(u["id"])
                try:
                    u["eta"] = eta(u.get("data_nascita"))
                except Exception:
                    u["eta"] = None
                search_results.append({
                    "tipo": "utente",
                    "icon": "👤",
                    "titolo": u.get("nome") or u.get("username") or "Utente",
                    "sottotitolo": f"{u.get('email','')} · @{u.get('username','')} · {u.get('citta') or '—'}",
                    "link": f"/admin?tab=users&q={u.get('email') or u.get('username') or ''}",
                    "meta": u.get("stato") or "",
                    "user_id": u["id"],
                })
        except Exception as e:
            print("admin ricerca users:", e)

        # MESSAGGI
        try:
            cur.execute("""
                SELECT m.id, m.contenuto, m.conversation_id, m.sender_id, m.data_invio, u.nome as sender_nome
                FROM messages m
                LEFT JOIN users u ON u.id = m.sender_id
                WHERE m.contenuto ILIKE %s
                ORDER BY m.id DESC LIMIT 40
            """, (like,))
            for r in cur.fetchall():
                d = dict(r)
                search_results.append({
                    "tipo": "messaggio",
                    "icon": "✉️",
                    "titolo": (d.get("contenuto") or "")[:120],
                    "sottotitolo": f"Da {d.get('sender_nome') or d.get('sender_id')} · conv #{d.get('conversation_id')}",
                    "link": f"/admin?tab=messages&conv={d.get('conversation_id')}",
                    "meta": str(d.get("data_invio") or ""),
                })
        except Exception as e:
            print("admin ricerca msg:", e)

        # NOTIFICHE
        try:
            cur.execute("""
                SELECT n.id, n.tipo, n.titolo, n.contenuto, n.user_id, n.data_creazione, u.nome as user_nome
                FROM notifications n
                LEFT JOIN users u ON u.id = n.user_id
                WHERE n.titolo ILIKE %s OR n.contenuto ILIKE %s OR n.tipo ILIKE %s
                ORDER BY n.id DESC LIMIT 30
            """, (like, like, like))
            for r in cur.fetchall():
                d = dict(r)
                search_results.append({
                    "tipo": "notifica",
                    "icon": "🔔",
                    "titolo": d.get("titolo") or d.get("tipo") or "Notifica",
                    "sottotitolo": f"{(d.get('contenuto') or '')[:80]} · a {d.get('user_nome') or d.get('user_id')}",
                    "link": "/admin?tab=users",
                    "meta": str(d.get("data_creazione") or ""),
                })
        except Exception as e:
            print("admin ricerca notif:", e)

        # DONI INVIATI
        try:
            cur.execute("""
                SELECT g.id, g.messaggio, g.created_at, gt.nome, gt.emoji,
                       uf.nome as from_nome, ut.nome as to_nome
                FROM gifts_sent g
                JOIN gift_types gt ON gt.id = g.gift_type_id
                LEFT JOIN users uf ON uf.id = g.from_user_id
                LEFT JOIN users ut ON ut.id = g.to_user_id
                WHERE gt.nome ILIKE %s OR COALESCE(g.messaggio,'') ILIKE %s
                   OR uf.nome ILIKE %s OR ut.nome ILIKE %s OR uf.email ILIKE %s OR ut.email ILIKE %s
                ORDER BY g.id DESC LIMIT 30
            """, (like, like, like, like, like, like))
            for r in cur.fetchall():
                d = dict(r)
                search_results.append({
                    "tipo": "dono",
                    "icon": d.get("emoji") or "🎁",
                    "titolo": f"{d.get('emoji','')} {d.get('nome')}",
                    "sottotitolo": f"{d.get('from_nome')} → {d.get('to_nome')} · {(d.get('messaggio') or '')[:60]}",
                    "link": "/admin?tab=doni",
                    "meta": str(d.get("created_at") or ""),
                })
        except Exception as e:
            print("admin ricerca gifts:", e)

        # TIPI DONO
        try:
            cur.execute(
                "SELECT * FROM gift_types WHERE nome ILIKE %s OR emoji ILIKE %s OR CAST(costo AS TEXT) ILIKE %s ORDER BY id LIMIT 20",
                (like, like, like),
            )
            for r in cur.fetchall():
                d = dict(r)
                search_results.append({
                    "tipo": "catalogo_dono",
                    "icon": d.get("emoji") or "🎁",
                    "titolo": f"{d.get('emoji')} {d.get('nome')} — {d.get('costo')} crediti",
                    "sottotitolo": "Catalogo doni",
                    "link": "/admin?tab=doni",
                    "meta": "attivo" if d.get("attivo", 1) else "disattivo",
                })
        except Exception as e:
            print("admin ricerca gift_types:", e)

        # FOTO / GALLERY
        try:
            cur.execute("""
                SELECT p.id, p.url, p.user_id, p.is_private, u.nome as nome_user, u.username
                FROM user_photos p
                LEFT JOIN users u ON u.id = p.user_id
                WHERE COALESCE(p.url,'') ILIKE %s OR u.nome ILIKE %s OR u.username ILIKE %s OR u.email ILIKE %s
                ORDER BY p.id DESC LIMIT 30
            """, (like, like, like, like))
            for r in cur.fetchall():
                d = dict(r)
                search_results.append({
                    "tipo": "foto",
                    "icon": "🖼️",
                    "titolo": f"Media di {d.get('nome_user') or d.get('user_id')}",
                    "sottotitolo": (d.get("url") or "")[:80] + (" · privata" if d.get("is_private") else ""),
                    "link": "/admin?tab=foto",
                    "meta": str(d.get("id")),
                })
        except Exception as e:
            print("admin ricerca foto:", e)

        # MATCH
        try:
            cur.execute("""
                SELECT m.id, m.data_match, u1.nome as nome1, u2.nome as nome2, u1.email as email1, u2.email as email2
                FROM matches m
                JOIN users u1 ON u1.id = m.user1_id
                JOIN users u2 ON u2.id = m.user2_id
                WHERE u1.nome ILIKE %s OR u2.nome ILIKE %s OR u1.username ILIKE %s OR u2.username ILIKE %s
                   OR u1.email ILIKE %s OR u2.email ILIKE %s
                ORDER BY m.id DESC LIMIT 30
            """, (like, like, like, like, like, like))
            for r in cur.fetchall():
                d = dict(r)
                search_results.append({
                    "tipo": "match",
                    "icon": "♥",
                    "titolo": f"{d.get('nome1')} ↔ {d.get('nome2')}",
                    "sottotitolo": f"{d.get('email1')} · {d.get('email2')}",
                    "link": "/admin?tab=messages",
                    "meta": str(d.get("data_match") or ""),
                })
        except Exception as e:
            print("admin ricerca match:", e)

        # BLOCCHI
        try:
            cur.execute("""
                SELECT b.*, u1.nome as blocker_nome, u2.nome as blocked_nome
                FROM blocks b
                JOIN users u1 ON u1.id = b.blocker_id
                JOIN users u2 ON u2.id = b.blocked_id
                WHERE u1.nome ILIKE %s OR u2.nome ILIKE %s OR u1.email ILIKE %s OR u2.email ILIKE %s
                ORDER BY b.id DESC LIMIT 20
            """, (like, like, like, like))
            for r in cur.fetchall():
                d = dict(r)
                search_results.append({
                    "tipo": "blocco",
                    "icon": "🚫",
                    "titolo": f"{d.get('blocker_nome')} ha bloccato {d.get('blocked_nome')}",
                    "sottotitolo": "Blocco utenti",
                    "link": "/admin?tab=blocchi",
                    "meta": str(d.get("created_at") or d.get("id") or ""),
                })
        except Exception as e:
            print("admin ricerca blocks:", e)

    cur.close()
    c.close()

    return templates.TemplateResponse("admin.html", {
        "request": req,
        "user": user,
        "tab": tab,
        "q": q,
        "filter": locals().get("filter_stato", "tutti"),
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
        "gallery_items": gallery_items,
        "gallery_folders": gallery_folders,
        "folder_user_id": folder_user_id,
        "folder_user": folder_user,
        "fq": fq,
        "gift_types": gift_types,
        "gifts_recent": gifts_recent,
        "blocks": blocks,
        "reports": reports if tab == "segnalazioni" else [],
        "reports_open": reports_open if tab == "segnalazioni" else 0,
        "bots": bots,
        "phones": phones if tab == "telefoni" else [],
        "mq": mq,
        "message_hits": message_hits,
        "search_results": search_results,
    })








@app.post("/admin/blocks/remove/{block_id}")
async def admin_remove_block(req: Request, block_id: int):
    """Admin/mod annulla un blocco tra due utenti."""
    if not require_mod(req):
        return RedirectResponse("/login", 303)
    c = db()
    cur = c.cursor()
    try:
        cur.execute("SELECT blocker_id, blocked_id FROM blocks WHERE id=%s", (block_id,))
        row = cur.fetchone()
        if row:
            cur.execute("DELETE FROM blocks WHERE id=%s", (block_id,))
            # opzionale: riattiva match se esisteva
            try:
                u1, u2 = sorted([row["blocker_id"], row["blocked_id"]])
                cur.execute(
                    "UPDATE matches SET attivo=1 WHERE user1_id=%s AND user2_id=%s",
                    (u1, u2),
                )
            except Exception:
                pass
            c.commit()
    except Exception as e:
        print("admin_remove_block:", e)
        try:
            c.rollback()
        except Exception:
            pass
    finally:
        cur.close()
        c.close()
    return RedirectResponse("/admin?tab=blocchi&ok=sbloccato", 303)


@app.post("/admin/blocks/remove-pair")
async def admin_remove_block_pair(req: Request, blocker_id: int = Form(...), blocked_id: int = Form(...)):
    if not require_mod(req):
        return RedirectResponse("/login", 303)
    c = db()
    cur = c.cursor()
    try:
        cur.execute(
            "DELETE FROM blocks WHERE (blocker_id=%s AND blocked_id=%s) OR (blocker_id=%s AND blocked_id=%s)",
            (blocker_id, blocked_id, blocked_id, blocker_id),
        )
        c.commit()
    except Exception as e:
        print("remove pair:", e)
        try:
            c.rollback()
        except Exception:
            pass
    finally:
        cur.close()
        c.close()
    return RedirectResponse("/admin?tab=blocchi&ok=sbloccato", 303)


@app.post("/admin/reports/{report_id}/accept")
async def admin_report_accept(req: Request, report_id: int):
    """Segnalazione valida: conferma, opzionale ban/sospensione resta a GESTISCI."""
    admin = require_admin(req)
    if not admin:
        return RedirectResponse("/login", 303)
    c = db()
    cur = c.cursor()
    try:
        cur.execute(
            """UPDATE reports SET status='accepted', reviewed_by=%s, reviewed_at=NOW(), false_report=0
               WHERE id=%s RETURNING reported_id, reporter_id""",
            (admin["id"], report_id),
        )
        row = cur.fetchone()
        if row:
            # notifica reporter: grazie
            try:
                cur.execute(
                    """INSERT INTO notifications (user_id, tipo, titolo, contenuto, related_id)
                       VALUES (%s,'report_ok',%s,%s,%s)""",
                    (row["reporter_id"], "✅ Segnalazione presa in carico",
                     "Grazie: la tua segnalazione è stata esaminata dallo staff.", row["reported_id"]),
                )
            except Exception:
                pass
        c.commit()
    except Exception as e:
        print("report accept:", e)
        try:
            c.rollback()
        except Exception:
            pass
    finally:
        cur.close()
        c.close()
    return RedirectResponse("/admin?tab=segnalazioni&ok=accepted", 303)


@app.post("/admin/reports/{report_id}/dismiss")
async def admin_report_dismiss(req: Request, report_id: int):
    """Segnalazione archiviata senza azione (non conta come falsa)."""
    admin = require_admin(req)
    if not admin:
        return RedirectResponse("/login", 303)
    c = db()
    cur = c.cursor()
    try:
        cur.execute(
            """UPDATE reports SET status='dismissed', reviewed_by=%s, reviewed_at=NOW()
               WHERE id=%s""",
            (admin["id"], report_id),
        )
        c.commit()
    except Exception as e:
        print("report dismiss:", e)
        try:
            c.rollback()
        except Exception:
            pass
    finally:
        cur.close()
        c.close()
    return RedirectResponse("/admin?tab=segnalazioni&ok=dismissed", 303)


@app.post("/admin/reports/{report_id}/false")
async def admin_report_false(req: Request, report_id: int):
    """Segnalazione falsa: penalizza il reporter dopo 3 false."""
    admin = require_admin(req)
    if not admin:
        return RedirectResponse("/login", 303)
    c = db()
    cur = c.cursor()
    try:
        cur.execute(
            """UPDATE reports SET status='false', false_report=1, reviewed_by=%s, reviewed_at=NOW()
               WHERE id=%s RETURNING reporter_id, reported_id""",
            (admin["id"], report_id),
        )
        row = cur.fetchone()
        if row and row.get("reporter_id"):
            rid = row["reporter_id"]
            # conta false reports ultimi 90 giorni
            cur.execute(
                """SELECT COUNT(*) AS n FROM reports
                   WHERE reporter_id=%s AND COALESCE(false_report,0)=1
                     AND created_at >= NOW() - interval '90 days'""",
                (rid,),
            )
            n = int((cur.fetchone() or {}).get("n") or 0)
            if n >= 3:
                # penalità: restrizione segnalare + warning sospensione soft
                try:
                    cur.execute("""
                        INSERT INTO user_restrictions (user_id, no_chat)
                        VALUES (%s, 0)
                        ON CONFLICT (user_id) DO NOTHING
                    """, (rid,))
                except Exception:
                    pass
                try:
                    cur.execute(
                        """UPDATE users SET stato='sospeso', sospeso_fino = NOW() + interval '3 days'
                           WHERE id=%s AND COALESCE(stato,'') <> 'bannato'""",
                        (rid,),
                    )
                except Exception:
                    try:
                        c.rollback()
                        cur.execute("UPDATE users SET stato='sospeso' WHERE id=%s", (rid,))
                    except Exception:
                        pass
                try:
                    cur.execute(
                        """INSERT INTO notifications (user_id, tipo, titolo, contenuto, related_id)
                           VALUES (%s,'penalty',%s,%s,%s)""",
                        (rid, "⚠️ Abuso segnalazioni",
                         f"Hai accumulato {n} segnalazioni false. Account sospeso 3 giorni.",
                         report_id),
                    )
                except Exception:
                    pass
            else:
                try:
                    cur.execute(
                        """INSERT INTO notifications (user_id, tipo, titolo, contenuto, related_id)
                           VALUES (%s,'report_false',%s,%s,%s)""",
                        (rid, "Segnalazione non valida",
                         f"Una tua segnalazione è stata giudicata non fondata ({n}/3). Al terzo abuso potresti essere sospeso.",
                         row.get("reported_id")),
                    )
                except Exception:
                    pass
        c.commit()
    except Exception as e:
        print("report false:", e)
        try:
            c.rollback()
        except Exception:
            pass
    finally:
        cur.close()
        c.close()
    return RedirectResponse("/admin?tab=segnalazioni&ok=false", 303)


@app.post("/admin/reports/{report_id}/unsuspend")
async def admin_report_unsuspend(req: Request, report_id: int):
    """Toglie sospensione all'utente segnalato (review umana)."""
    admin = require_admin(req)
    if not admin:
        return RedirectResponse("/login", 303)
    c = db()
    cur = c.cursor()
    try:
        cur.execute("SELECT reported_id FROM reports WHERE id=%s", (report_id,))
        row = cur.fetchone()
        if row:
            uid = row["reported_id"]
            cur.execute(
                """UPDATE users SET stato='attivo', sospeso_fino=NULL WHERE id=%s AND COALESCE(stato,'')='sospeso'""",
                (uid,),
            )
            try:
                cur.execute(
                    """UPDATE user_restrictions SET no_chat=0, no_messaggi=0 WHERE user_id=%s""",
                    (uid,),
                )
            except Exception:
                pass
            try:
                cur.execute(
                    """INSERT INTO notifications (user_id, tipo, titolo, contenuto, related_id)
                       VALUES (%s,'unsuspend',%s,%s,%s)""",
                    (uid, "✅ Sospensione revocata", "Lo staff ha riesaminato il tuo account. La sospensione è stata tolta.", admin["id"]),
                )
            except Exception:
                pass
            cur.execute(
                """UPDATE reports SET status='reviewed_ok', reviewed_by=%s, reviewed_at=NOW() WHERE id=%s""",
                (admin["id"], report_id),
            )
        c.commit()
    except Exception as e:
        print("unsuspend:", e)
        try:
            c.rollback()
        except Exception:
            pass
    finally:
        cur.close()
        c.close()
    return RedirectResponse("/admin?tab=segnalazioni&ok=unsuspend", 303)


@app.post("/admin/bots/create")
async def admin_bot_create(req: Request):
    """Crea bot nella tabella bots (+ account ombra is_bot=1 per match/swipe, separato dagli utenti reali)."""
    if not require_admin(req):
        return RedirectResponse("/login", 303)
    form = await req.form()
    genere = (form.get("genere") or "donna").strip().lower()
    orientamento = (form.get("orientamento") or "etero").strip().lower()
    try:
        quanti = int(form.get("quanti") or 1)
    except Exception:
        quanti = 1
    quanti = max(1, min(quanti, 20))

    import random, uuid
    from datetime import date

    NOMI_F = ["Giulia", "Sara", "Francesca", "Chiara", "Valentina", "Martina", "Elena", "Alice",
              "Sofia", "Aurora", "Greta", "Noemi", "Irene", "Camilla", "Beatrice", "Ludovica"]
    NOMI_M = ["Marco", "Luca", "Alessandro", "Andrea", "Matteo", "Davide", "Francesco", "Lorenzo",
              "Simone", "Riccardo", "Gabriele", "Tommaso", "Nicola", "Stefano", "Paolo", "Antonio"]
    NOMI_A = NOMI_F + NOMI_M + ["Alex", "Sam", "Taylor"]
    CITTA = ["Milano", "Roma", "Torino", "Napoli", "Bologna", "Firenze", "Palermo", "Genova",
             "Verona", "Padova", "Bari", "Catania", "Venezia", "Brescia", "Parma"]
    BIOS = [
        "Qui per conoscersi senza filtri 🔥",
        "Amo viaggiare e le serate in città",
        "Cercando qualcuno di genuino",
        "Sport, musica e chiacchiere fino a tardi",
        "Nuovo/a in zona, apriamo una chat?",
        "Niente storie complicate, solo feeling",
    ]
    gps = {
        "Milano": (45.46, 9.19), "Roma": (41.90, 12.50), "Torino": (45.07, 7.69),
        "Napoli": (40.85, 14.27), "Bologna": (44.49, 11.34), "Firenze": (43.77, 11.25),
        "Palermo": (38.12, 13.36), "Genova": (44.41, 8.93), "Verona": (45.44, 10.99),
        "Padova": (45.41, 11.88), "Bari": (41.12, 16.87), "Catania": (37.51, 15.08),
        "Venezia": (45.44, 12.32), "Brescia": (45.54, 10.21), "Parma": (44.80, 10.33),
    }

    if genere in ("donna", "f", "female"):
        genere, nomi = "donna", NOMI_F
    elif genere in ("uomo", "m", "male"):
        genere, nomi = "uomo", NOMI_M
    else:
        genere, nomi = "altro", NOMI_A
    ori_map = {"etero": "etero", "omo": "omo", "gay": "omo", "bi": "bi", "tutti": "tutti"}
    orientamento = ori_map.get(orientamento, orientamento) or "etero"

    c = db()
    cur = c.cursor()
    # tabella bots dedicata
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bots (
                id SERIAL PRIMARY KEY,
                user_id INTEGER UNIQUE,
                nome VARCHAR(100),
                username VARCHAR(80),
                email VARCHAR(200),
                genere VARCHAR(20),
                orientamento VARCHAR(30),
                data_nascita DATE,
                bio TEXT,
                citta VARCHAR(100),
                credits INTEGER DEFAULT 50,
                latitude DOUBLE PRECISION,
                longitude DOUBLE PRECISION,
                foto_url TEXT,
                attivo INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.commit()
    except Exception:
        try:
            c.rollback()
        except Exception:
            pass

    created = 0
    for _ in range(quanti):
        nome = random.choice(nomi)
        age = random.randint(21, 38)
        today = date.today()
        birth = date(today.year - age, random.randint(1, 12), random.randint(1, 28))
        nick = nome.lower().replace("à","a").replace("è","e").replace("é","e").replace("ì","i").replace("ò","o").replace("ù","u")
        username = f"bot_{nick}{random.randint(10,99)}{uuid.uuid4().hex[:4]}"
        email = f"{username}@bot.mycheating.local"
        citta = random.choice(CITTA)
        bio = random.choice(BIOS)
        credits = random.choice([30, 50, 80, 100])
        pwd_hash = hash_pw("bot_" + uuid.uuid4().hex[:10])
        lat, lng = gps.get(citta, (41.9, 12.5))
        lat += random.uniform(-0.08, 0.08)
        lng += random.uniform(-0.08, 0.08)
        try:
            # account ombra (non compare in lista utenti reali)
            cur.execute(
                """INSERT INTO users (
                       email, password_hash, username, nome, data_nascita, genere, orientamento,
                       bio, citta, credits, stato, is_bot, is_online, latitude, longitude
                   ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'attivo',1,1,%s,%s) RETURNING id""",
                (email, pwd_hash, username, nome, birth.isoformat(), genere, orientamento,
                 bio, citta, credits, lat, lng),
            )
            uid = cur.fetchone()["id"]
            cur.execute(
                """INSERT INTO bots (user_id, nome, username, email, genere, orientamento, data_nascita, bio, citta, credits, latitude, longitude, attivo)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1)""",
                (uid, nome, username, email, genere, orientamento, birth.isoformat(), bio, citta, credits, lat, lng),
            )
            try:
                cur.execute("INSERT INTO user_preferences (user_id) VALUES (%s) ON CONFLICT DO NOTHING", (uid,))
            except Exception:
                try:
                    c.rollback()
                    # re-do minimal after rollback is bad - skip
                except Exception:
                    pass
            created += 1
        except Exception as e:
            print("bot create one:", e)
            try:
                c.rollback()
            except Exception:
                pass
    try:
        c.commit()
    except Exception as e:
        print("bot commit:", e)
        try:
            c.rollback()
        except Exception:
            pass
    cur.close()
    c.close()
    if created == 0:
        return RedirectResponse("/admin?tab=bot&err=create", 303)
    return RedirectResponse(f"/admin?tab=bot&ok=created&n={created}", 303)


async def admin_bot_update(req: Request, bot_id: int):
    if not require_admin(req):
        return RedirectResponse("/login", 303)
    form = await req.form()
    nome = (form.get("nome") or "").strip()
    username = (form.get("username") or "").strip()
    citta = (form.get("citta") or "").strip() or None
    bio = (form.get("bio") or "").strip() or None
    genere = form.get("genere") or "altro"
    stato = form.get("stato") or "attivo"
    try:
        credits = int(form.get("credits") or 0)
    except Exception:
        credits = 0
    attivo = 1 if stato == "attivo" else 0
    c = db()
    cur = c.cursor()
    try:
        cur.execute("SELECT user_id FROM bots WHERE id=%s", (bot_id,))
        row = cur.fetchone()
        if row and row.get("user_id"):
            uid = row["user_id"]
            cur.execute(
                """UPDATE bots SET nome=%s, username=%s, bio=%s, citta=%s, genere=%s, credits=%s, attivo=%s WHERE id=%s""",
                (nome, username, bio, citta, genere, credits, attivo, bot_id),
            )
            cur.execute(
                """UPDATE users SET nome=%s, username=%s, bio=%s, citta=%s, genere=%s, stato=%s, credits=%s
                   WHERE id=%s AND COALESCE(is_bot,0)=1""",
                (nome, username, bio, citta, genere, stato, credits, uid),
            )
        else:
            # legacy: bot_id era user id
            cur.execute(
                """UPDATE users SET nome=%s, username=%s, bio=%s, citta=%s, genere=%s, stato=%s, credits=%s
                   WHERE id=%s""",
                (nome, username, bio, citta, genere, stato, credits, bot_id),
            )
        c.commit()
    except Exception as e:
        c.rollback()
        print("bot update:", e)
        return RedirectResponse("/admin?tab=bot&err=update", 303)
    finally:
        cur.close()
        c.close()
    return RedirectResponse("/admin?tab=bot&ok=updated", 303)


async def admin_bot_delete(req: Request, bot_id: int):
    if not require_admin(req):
        return RedirectResponse("/login", 303)
    c = db()
    cur = c.cursor()
    try:
        cur.execute("SELECT user_id FROM bots WHERE id=%s", (bot_id,))
        row = cur.fetchone()
        if row:
            uid = row.get("user_id")
            cur.execute("DELETE FROM bots WHERE id=%s", (bot_id,))
            if uid:
                cur.execute("DELETE FROM users WHERE id=%s AND COALESCE(is_bot,0)=1", (uid,))
        else:
            cur.execute("DELETE FROM users WHERE id=%s AND COALESCE(is_bot,0)=1", (bot_id,))
        c.commit()
    except Exception as e:
        c.rollback()
        print("bot delete:", e)
        return RedirectResponse("/admin?tab=bot&err=delete", 303)
    finally:
        cur.close()
        c.close()
    return RedirectResponse("/admin?tab=bot&ok=deleted", 303)


@app.post("/admin/gifts/create")
async def admin_gift_create(req: Request):
    if not require_admin(req):
        return RedirectResponse("/login", 303)
    form = await req.form()
    nome = (form.get("nome") or "").strip()
    emoji = (form.get("emoji") or "🎁").strip()[:10]
    try:
        costo = int(form.get("costo") or 10)
    except Exception:
        costo = 10
    if costo < 0:
        costo = 0
    if not nome:
        return RedirectResponse("/admin?tab=doni&err=nome", 303)
    c = db()
    cur = c.cursor()
    try:
        cur.execute(
            "INSERT INTO gift_types (nome, emoji, costo, attivo) VALUES (%s,%s,%s,1) RETURNING id",
            (nome, emoji, costo),
        )
        c.commit()
    except Exception as e:
        c.rollback()
        print("gift create:", e)
        try:
            cur.execute(
                "INSERT INTO gift_types (nome, emoji, costo) VALUES (%s,%s,%s) RETURNING id",
                (nome, emoji, costo),
            )
            c.commit()
        except Exception as e2:
            c.rollback()
            print("gift create2:", e2)
            cur.close()
            c.close()
            return RedirectResponse("/admin?tab=doni&err=db", 303)
    cur.close()
    c.close()
    return RedirectResponse("/admin?tab=doni&ok=created", 303)


@app.post("/admin/gifts/{gift_id}/update")
async def admin_gift_update(req: Request, gift_id: int):
    if not require_admin(req):
        return RedirectResponse("/login", 303)
    form = await req.form()
    nome = (form.get("nome") or "").strip()
    emoji = (form.get("emoji") or "🎁").strip()[:10]
    try:
        costo = int(form.get("costo") or 10)
    except Exception:
        costo = 10
    attivo = 1 if form.get("attivo") in ("1", "on", "true", "True") else 0
    if not nome:
        return RedirectResponse("/admin?tab=doni&err=nome", 303)
    c = db()
    cur = c.cursor()
    try:
        cur.execute(
            "UPDATE gift_types SET nome=%s, emoji=%s, costo=%s, attivo=%s WHERE id=%s",
            (nome, emoji, costo, attivo, gift_id),
        )
        c.commit()
    except Exception as e:
        c.rollback()
        print("gift update:", e)
        try:
            cur.execute(
                "UPDATE gift_types SET nome=%s, emoji=%s, costo=%s WHERE id=%s",
                (nome, emoji, costo, gift_id),
            )
            c.commit()
        except Exception as e2:
            c.rollback()
            print("gift update2:", e2)
    cur.close()
    c.close()
    return RedirectResponse("/admin?tab=doni&ok=updated", 303)


@app.post("/admin/gifts/{gift_id}/delete")
async def admin_gift_delete(req: Request, gift_id: int):
    if not require_admin(req):
        return RedirectResponse("/login", 303)
    c = db()
    cur = c.cursor()
    try:
        # soft delete se ha invii
        cur.execute("SELECT COUNT(*) as c FROM gifts_sent WHERE gift_type_id=%s", (gift_id,))
        cnt = cur.fetchone()
        n = cnt["c"] if cnt else 0
        if n and n > 0:
            try:
                cur.execute("UPDATE gift_types SET attivo=0 WHERE id=%s", (gift_id,))
            except Exception:
                cur.execute("DELETE FROM gift_types WHERE id=%s", (gift_id,))
        else:
            cur.execute("DELETE FROM gift_types WHERE id=%s", (gift_id,))
        c.commit()
    except Exception as e:
        c.rollback()
        print("gift delete:", e)
        return RedirectResponse("/admin?tab=doni&err=delete", 303)
    finally:
        cur.close()
        c.close()
    return RedirectResponse("/admin?tab=doni&ok=deleted", 303)


@app.post("/admin/gifts/{gift_id}/toggle")
async def admin_gift_toggle(req: Request, gift_id: int):
    if not require_admin(req):
        return RedirectResponse("/login", 303)
    c = db()
    cur = c.cursor()
    try:
        cur.execute("SELECT COALESCE(attivo,1) as attivo FROM gift_types WHERE id=%s", (gift_id,))
        row = cur.fetchone()
        if row:
            new_a = 0 if row["attivo"] else 1
            cur.execute("UPDATE gift_types SET attivo=%s WHERE id=%s", (new_a, gift_id))
            c.commit()
    except Exception as e:
        c.rollback()
        print("gift toggle:", e)
    cur.close()
    c.close()
    return RedirectResponse("/admin?tab=doni", 303)

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


def _storage_request(method: str, url: str, data: bytes = None, content_type: str = None, json_body: dict = None):
    """HTTP request senza httpx (urllib standard)."""
    import json as _json
    import urllib.request
    import urllib.error
    headers = {
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "apikey": SUPABASE_SERVICE_KEY,
    }
    body = None
    if json_body is not None:
        body = _json.dumps(json_body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif data is not None:
        body = data
        headers["Content-Type"] = content_type or "application/octet-stream"
        headers["x-upsert"] = "true"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        return e.code, err_body
    except Exception as e:
        return 0, str(e)


async def storage_upload(path: str, data: bytes, content_type: str, bucket: str = None) -> str:
    """Carica su Supabase Storage. Ritorna URL pubblico.
    bucket: se None usa STORAGE_BUCKET (gallery); per chat passa CHAT_STORAGE_BUCKET.
    """
    b = (bucket or STORAGE_BUCKET).strip() or STORAGE_BUCKET
    url = f"{SUPABASE_URL}/storage/v1/object/{b}/{path}"
    status, body = _storage_request("POST", url, data=data, content_type=content_type)
    if status not in (200, 201):
        status, body = _storage_request("PUT", url, data=data, content_type=content_type)
    if status not in (200, 201):
        raise RuntimeError(f"Storage {b} {status}: {body[:500]}")
    return f"{SUPABASE_URL}/storage/v1/object/public/{b}/{path}"


async def storage_upload_stream(path: str, file_obj, content_type: str, max_bytes: int) -> tuple:
    """Buffer su temp, upload. Ritorna (public_url, size)."""
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
    import json as _json
    url = f"{SUPABASE_URL}/storage/v1/object/sign/{STORAGE_BUCKET}/{path}"
    status, body = _storage_request("POST", url, json_body={"expiresIn": expires_sec})
    if status not in (200, 201):
        raise RuntimeError(f"sign failed: {status} {body[:200]}")
    data = _json.loads(body)
    signed = data.get("signedURL") or data.get("signedUrl") or ""
    if signed.startswith("http"):
        return signed
    return f"{SUPABASE_URL}/storage/v1{signed}"





@app.get("/smtp-test")
async def smtp_test_alias(req: Request):
    """Alias pubblico del path (stesso test admin)."""
    return await admin_smtp_test(req)

@app.get("/admin/smtp-test")
async def admin_smtp_test(req: Request):
    """Test invio email OTP (solo admin). Apri: /admin/smtp-test?to=tua@email.com"""
    admin = require_admin(req)
    if not admin:
        return RedirectResponse("/login", 303)
    import os, json
    to = (req.query_params.get("to") or admin.get("email") or "").strip()
    host = (os.environ.get("SMTP_HOST") or "").strip()
    user = (os.environ.get("SMTP_USER") or "").strip()
    password = (os.environ.get("SMTP_PASS") or "").strip()
    port = (os.environ.get("SMTP_PORT") or "").strip()
    frm = (os.environ.get("SMTP_FROM") or "").strip()
    info = {
        "SMTP_HOST": host or None,
        "SMTP_PORT": port or None,
        "SMTP_USER": user or None,
        "SMTP_FROM": frm or None,
        "SMTP_PASS_set": bool(password),
        "SMTP_PASS_len": len(password) if password else 0,
        "to": to,
    }
    if not to:
        return JSONResponse({**info, "ok": False, "error": "passa ?to=email@dominio.com"})
    code = "123456"
    ok = send_otp_email(to, code, phone="+390000000000")
    return JSONResponse({
        **info,
        "ok": ok,
        "error": _last_smtp_error,
        "code_version": "smtp-ssl-novverify-2",
        "note": "Se ok=false guarda error. Se ok=true controlla inbox+spam di " + to,
    })


@app.get("/admin/storage-test")
async def admin_storage_test(req: Request):
    """Test Storage senza httpx. Solo admin."""
    if not require_admin(req):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    info = {
        "supabase_url": SUPABASE_URL,
        "bucket": STORAGE_BUCKET,
        "chat_bucket": CHAT_STORAGE_BUCKET,
        "key_set": bool(SUPABASE_SERVICE_KEY),
        "key_prefix": (SUPABASE_SERVICE_KEY[:12] + "...") if SUPABASE_SERVICE_KEY else None,
        "storage_enabled": storage_enabled(),
        "transport": "urllib",
    }
    if not storage_enabled():
        return JSONResponse({"ok": False, "info": info, "error": "missing env"})
    try:
        status_b, body_b = _storage_request("GET", f"{SUPABASE_URL}/storage/v1/bucket")
        info["list_buckets_status"] = status_b
        info["list_buckets_body"] = body_b[:500]
        test_path = "_test/ping.txt"
        status_u, body_u = _storage_request(
            "POST",
            f"{SUPABASE_URL}/storage/v1/object/{STORAGE_BUCKET}/{test_path}",
            data=b"ok",
            content_type="text/plain",
        )
        info["upload_status"] = status_u
        info["upload_body"] = body_u[:500]
        # test bucket chat dedicato
        status_c, body_c = _storage_request(
            "POST",
            f"{SUPABASE_URL}/storage/v1/object/{CHAT_STORAGE_BUCKET}/{test_path}",
            data=b"ok-chat",
            content_type="text/plain",
        )
        info["chat_upload_status"] = status_c
        info["chat_upload_body"] = body_c[:500]
        info["ok"] = status_u in (200, 201)
        return JSONResponse(info)
    except Exception as e:
        info["exception"] = str(e)
        return JSONResponse({"ok": False, "info": info})


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
            # err detail solo nei log; UI generica
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

# ===================== STORIE 24h =====================
@app.get("/stories", response_class=HTMLResponse)
async def stories_feed(req: Request):
    user = current_user(req)
    if not user:
        return RedirectResponse("/login", 303)
    c = db()
    cur = c.cursor()
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS stories (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                media_url TEXT NOT NULL,
                media_type VARCHAR(20) DEFAULT 'image',
                caption TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP NOT NULL
            )
        """)
        c.commit()
    except Exception:
        try: c.rollback()
        except Exception: pass
    # storie attive raggruppate per utente (escludi scadute)
    try:
        cur.execute("""
            SELECT s.*, u.nome, u.username, u.foto_principale_url
            FROM stories s
            JOIN users u ON u.id = s.user_id
            WHERE s.expires_at > NOW()
              AND COALESCE(u.is_bot,0)=0
              AND u.stato='attivo'
              AND s.user_id NOT IN (SELECT blocked_id FROM blocks WHERE blocker_id=%s)
              AND s.user_id NOT IN (SELECT blocker_id FROM blocks WHERE blocked_id=%s)
            ORDER BY s.created_at DESC
            LIMIT 100
        """, (user["id"], user["id"]))
        rows = [dict(r) for r in cur.fetchall()]
    except Exception as e:
        print("stories feed:", e)
        rows = []
    # raggruppa
    by_user = {}
    for r in rows:
        by_user.setdefault(r["user_id"], {"user": r, "items": []})["items"].append(r)
    groups = list(by_user.values())
    # mie storie
    try:
        cur.execute(
            "SELECT * FROM stories WHERE user_id=%s AND expires_at > NOW() ORDER BY created_at DESC",
            (user["id"],),
        )
        my_stories = [dict(r) for r in cur.fetchall()]
    except Exception:
        my_stories = []
    cur.close(); c.close()
    return templates.TemplateResponse("stories.html", {
        "request": req, "user": user, "groups": groups, "my_stories": my_stories,
        "unread": unread_count(user["id"]),
    })


@app.post("/stories/create")
async def stories_create(req: Request, caption: str = Form(""), file: UploadFile = File(None)):
    user = current_user(req)
    if not user:
        return RedirectResponse("/login", 303)
    if not file or not file.filename:
        return RedirectResponse("/stories?err=file", 303)
    data = await file.read()
    if not data or len(data) > MAX_UPLOAD_BYTES:
        return RedirectResponse("/stories?err=size", 303)
    import uuid
    ext = (file.filename.rsplit(".", 1)[-1] if "." in file.filename else "jpg").lower()
    is_video = ext in ("mp4", "webm", "mov")
    media_type = "video" if is_video else "image"
    path = f"stories/{user['id']}/{uuid.uuid4().hex}.{ext}"
    # upload gallery bucket
    url = None
    try:
        # reuse storage upload if exists
        from urllib.request import Request as UrlReq, urlopen
        import os
        key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_KEY") or ""
        base = os.environ.get("SUPABASE_URL", SUPABASE_URL).rstrip("/")
        bucket = os.environ.get("STORAGE_BUCKET", STORAGE_BUCKET)
        if key and base:
            upload_url = f"{base}/storage/v1/object/{bucket}/{path}"
            req_u = UrlReq(upload_url, data=data, method="POST")
            req_u.add_header("Authorization", f"Bearer {key}")
            req_u.add_header("Content-Type", file.content_type or "application/octet-stream")
            req_u.add_header("x-upsert", "true")
            with urlopen(req_u, timeout=60) as resp:
                if resp.status in (200, 201):
                    url = f"{base}/storage/v1/object/public/{bucket}/{path}"
    except Exception as e:
        print("story upload:", e)
    if not url:
        # local fallback
        dest = BASE_DIR / "static" / "uploads" / "stories" / str(user["id"])
        dest.mkdir(parents=True, exist_ok=True)
        fname = f"{uuid.uuid4().hex}.{ext}"
        (dest / fname).write_bytes(data)
        url = f"/static/uploads/stories/{user['id']}/{fname}"
    c = db()
    cur = c.cursor()
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS stories (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                media_url TEXT NOT NULL,
                media_type VARCHAR(20) DEFAULT 'image',
                caption TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP NOT NULL
            )
        """)
        cur.execute(
            """INSERT INTO stories (user_id, media_url, media_type, caption, expires_at)
               VALUES (%s,%s,%s,%s, NOW() + interval '24 hours')""",
            (user["id"], url, media_type, (caption or "")[:200]),
        )
        c.commit()
    except Exception as e:
        print("story insert:", e)
        try: c.rollback()
        except Exception: pass
    cur.close(); c.close()
    return RedirectResponse("/stories?ok=1", 303)


@app.post("/stories/delete/{story_id}")
async def stories_delete(req: Request, story_id: int):
    user = current_user(req)
    if not user:
        return RedirectResponse("/login", 303)
    c = db()
    cur = c.cursor()
    try:
        cur.execute("DELETE FROM stories WHERE id=%s AND user_id=%s", (story_id, user["id"]))
        c.commit()
    except Exception:
        try: c.rollback()
        except Exception: pass
    cur.close(); c.close()
    return RedirectResponse("/stories", 303)


# ===================== GIF / STICKERS in chat =====================
STICKERS = [
    {"id": "🔥", "label": "Fuoco"},
    {"id": "❤️", "label": "Cuore"},
    {"id": "😍", "label": "Innamorato"},
    {"id": "😂", "label": "Lol"},
    {"id": "💋", "label": "Bacio"},
    {"id": "😈", "label": "Diavolo"},
    {"id": "🍑", "label": "Pesca"},
    {"id": "👀", "label": "Occhi"},
    {"id": "🙌", "label": "Hands"},
    {"id": "✨", "label": "Sparkle"},
    {"id": "🥵", "label": "Hot"},
    {"id": "🌹", "label": "Rosa"},
]


@app.post("/chat/{conversation_id}/sticker")
async def chat_sticker(req: Request, conversation_id: int, sticker: str = Form(...)):
    user = current_user(req)
    if not user:
        return RedirectResponse("/login", 303)
    sticker = (sticker or "")[:16]
    if not sticker:
        return RedirectResponse(f"/chat/{conversation_id}", 303)
    c = db()
    cur = c.cursor()
    try:
        cur.execute("""SELECT c.id, m.user1_id, m.user2_id FROM conversations c
               JOIN matches m ON m.id = c.match_id WHERE c.id=%s""", (conversation_id,))
        conv = cur.fetchone()
        if not conv or user["id"] not in (conv["user1_id"], conv["user2_id"]):
            cur.close(); c.close()
            return RedirectResponse("/chats", 303)
        cur.execute(
            "INSERT INTO messages (conversation_id,sender_id,tipo,contenuto) VALUES (%s,%s,'sticker',%s)",
            (conversation_id, user["id"], sticker),
        )
        try:
            cur.execute("UPDATE conversations SET ultimo_messaggio_at=CURRENT_TIMESTAMP WHERE id=%s", (conversation_id,))
        except Exception:
            pass
        altro = conv["user2_id"] if user["id"] == conv["user1_id"] else conv["user1_id"]
        try:
            cur.execute(
                "INSERT INTO notifications (user_id,tipo,titolo,contenuto,related_id) VALUES (%s,'nuovo_messaggio',%s,%s,%s)",
                (altro, f"Sticker da {user.get('nome') or 'utente'}", sticker, conversation_id),
            )
        except Exception:
            pass
        c.commit()
        try:
            await manager.send(altro, {"type": "nuovo_messaggio", "conversation_id": conversation_id, "preview": sticker})
        except Exception:
            pass
    except Exception as e:
        print("sticker:", e)
        try: c.rollback()
        except Exception: pass
    cur.close(); c.close()
    return RedirectResponse(f"/chat/{conversation_id}", 303)


@app.post("/chat/{conversation_id}/gif")
async def chat_gif(req: Request, conversation_id: int, gif_url: str = Form(...)):
    """Invia GIF da URL (Giphy o altro)."""
    user = current_user(req)
    if not user:
        return RedirectResponse("/login", 303)
    gif_url = (gif_url or "").strip()
    if not gif_url.startswith("http"):
        return RedirectResponse(f"/chat/{conversation_id}", 303)
    c = db()
    cur = c.cursor()
    try:
        cur.execute("""SELECT c.id, m.user1_id, m.user2_id FROM conversations c
               JOIN matches m ON m.id = c.match_id WHERE c.id=%s""", (conversation_id,))
        conv = cur.fetchone()
        if not conv or user["id"] not in (conv["user1_id"], conv["user2_id"]):
            cur.close(); c.close()
            return RedirectResponse("/chats", 303)
        try:
            cur.execute(
                """INSERT INTO messages (conversation_id,sender_id,tipo,contenuto,media_url,media_type)
                   VALUES (%s,%s,'gif','GIF',%s,'gif')""",
                (conversation_id, user["id"], gif_url[:500]),
            )
        except Exception:
            c.rollback()
            cur.execute(
                "INSERT INTO messages (conversation_id,sender_id,tipo,contenuto) VALUES (%s,%s,'gif',%s)",
                (conversation_id, user["id"], gif_url[:500]),
            )
        c.commit()
        altro = conv["user2_id"] if user["id"] == conv["user1_id"] else conv["user1_id"]
        try:
            await manager.send(altro, {"type": "nuovo_messaggio", "conversation_id": conversation_id, "preview": "GIF"})
        except Exception:
            pass
    except Exception as e:
        print("gif:", e)
        try: c.rollback()
        except Exception: pass
    cur.close(); c.close()
    return RedirectResponse(f"/chat/{conversation_id}", 303)


@app.get("/api/giphy")
async def api_giphy(req: Request, q: str = "funny"):
    """Proxy Giphy se GIPHY_API_KEY è settata, altrimenti sticker pack."""
    import os, json
    from urllib.request import urlopen, quote
    key = (os.environ.get("GIPHY_API_KEY") or "").strip()
    if not key:
        return JSONResponse({"ok": True, "source": "stickers", "items": [
            {"id": s["id"], "url": None, "label": s["label"]} for s in STICKERS
        ]})
    try:
        url = f"https://api.giphy.com/v1/gifs/search?api_key={key}&q={quote(q)}&limit=20&rating=pg-13"
        with urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        items = []
        for g in data.get("data") or []:
            imgs = g.get("images") or {}
            u = (imgs.get("fixed_height") or imgs.get("downsized") or {}).get("url")
            if u:
                items.append({"id": g.get("id"), "url": u, "label": g.get("title") or "GIF"})
        return JSONResponse({"ok": True, "source": "giphy", "items": items})
    except Exception as e:
        print("giphy:", e)
        return JSONResponse({"ok": False, "error": str(e), "items": []})


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
        try:
            c = db()
            cur = c.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS user_boosts (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    starts_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP NOT NULL
                )
            """)
            # 30 minuti di boost
            cur.execute(
                """INSERT INTO user_boosts (user_id, expires_at)
                   VALUES (%s, NOW() + interval '30 minutes')""",
                (user["id"],),
            )
            cur.execute(
                "INSERT INTO notifications (user_id,tipo,titolo,contenuto) VALUES (%s,'boost','Boost attivo','Il tuo profilo e in evidenza per 30 minuti!')",
                (user["id"],),
            )
            c.commit()
            cur.close()
            c.close()
        except Exception as e:
            print("boost activate:", e)
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







@app.post("/admin/create-user")
async def admin_create_user(req: Request):
    admin = require_admin(req)
    if not admin:
        return RedirectResponse("/login", 303)
    try:
        form = await req.form()
        nome = (form.get("nome") or "").strip()
        username = (form.get("username") or "").strip()
        email = (form.get("email") or "").strip().lower()
        password = (form.get("password") or "").strip()
        ruolo = (form.get("ruolo") or "user").strip()
        credits_raw = form.get("credits") or "0"
        genere = (form.get("genere") or "altro").strip() or "altro"
        citta = (form.get("citta") or "").strip() or None

        if not nome or not email or not password:
            return RedirectResponse("/admin?err=crea_campi", 303)
        if len(password) < 4:
            return RedirectResponse("/admin?err=crea_pass", 303)
        if not username:
            username = email.split("@")[0][:20]
        username = "".join(c for c in username if c.isalnum() or c in "_-")[:30] or "user"

        is_admin = 1 if ruolo == "admin" else 0
        is_mod = 1 if ruolo == "mod" else 0
        try:
            cr = max(0, int(credits_raw))
        except Exception:
            cr = 0

        # stessa funzione della registrazione
        pwd_hash = hash_pw(password)

        # data_nascita fittizia (18 anni) se obbligatoria nel DB
        from datetime import date
        data_nascita = date(date.today().year - 25, 1, 1).isoformat()

        c = db()
        cur = c.cursor()
        try:
            cur.execute("SELECT id FROM users WHERE email=%s OR username=%s", (email, username))
            if cur.fetchone():
                cur.close()
                c.close()
                return RedirectResponse("/admin?err=crea_esiste", 303)

            # INSERT identico alla registrazione (colonne certe)
            cur.execute(
                """INSERT INTO users (email, password_hash, username, nome, data_nascita, genere, orientamento, bio, citta)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (email, pwd_hash, username, nome, data_nascita, genere, "tutti", None, citta),
            )
            row = cur.fetchone()
            new_id = row["id"]
            try:
                cur.execute("INSERT INTO user_preferences (user_id) VALUES (%s)", (new_id,))
            except Exception:
                pass
            c.commit()

            # campi extra opzionali
            try:
                cur.execute(
                    """UPDATE users SET is_admin=%s, is_mod=%s, credits=%s, ruolo=%s, stato='attivo'
                       WHERE id=%s""",
                    (is_admin, is_mod, cr, ruolo, new_id),
                )
                c.commit()
            except Exception as e2:
                c.rollback()
                print("create user extra fields:", e2)
                try:
                    cur.execute(
                        "UPDATE users SET is_admin=%s, is_mod=%s WHERE id=%s",
                        (is_admin, is_mod, new_id),
                    )
                    c.commit()
                except Exception as e3:
                    c.rollback()
                    print("create user flags:", e3)

            cur.close()
            c.close()
            return RedirectResponse(f"/admin?created={new_id}", 303)
        except Exception as e:
            try:
                c.rollback()
                cur.close()
                c.close()
            except Exception:
                pass
            print("admin_create_user DB:", type(e).__name__, e)
            return RedirectResponse("/admin?err=crea_db", 303)
    except Exception as e:
        print("admin_create_user outer:", type(e).__name__, e)
        return RedirectResponse("/admin?err=crea_db", 303)



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
                "no_post", "no_doni", "no_ricevere_doni", "no_annunci", "no_annunci_personali",
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




# ============== DONI ==============
@app.get("/gifts", response_class=HTMLResponse)
async def gifts_page(req: Request):
    user = current_user(req)
    if not user:
        return RedirectResponse("/login", 303)
    r = get_restrictions(user["id"])
    c = db()
    cur = c.cursor()
    cur.execute("SELECT credits FROM users WHERE id=%s", (user["id"],))
    row = cur.fetchone()
    user["credits"] = row["credits"] if row else 0
    cur.execute("SELECT * FROM gift_types WHERE attivo=1 ORDER BY costo ASC")
    types = [dict(x) for x in cur.fetchall()]
    cur.execute("""
        SELECT g.*, gt.nome, gt.emoji, u.nome as from_nome
        FROM gifts_sent g
        JOIN gift_types gt ON gt.id = g.gift_type_id
        JOIN users u ON u.id = g.from_user_id
        WHERE g.to_user_id = %s
        ORDER BY g.created_at DESC LIMIT 30
    """, (user["id"],))
    ricevuti = [dict(x) for x in cur.fetchall()]
    cur.execute("""
        SELECT g.*, gt.nome, gt.emoji, u.nome as to_nome
        FROM gifts_sent g
        JOIN gift_types gt ON gt.id = g.gift_type_id
        JOIN users u ON u.id = g.to_user_id
        WHERE g.from_user_id = %s
        ORDER BY g.created_at DESC LIMIT 20
    """, (user["id"],))
    inviati = [dict(x) for x in cur.fetchall()]
    cur.close()
    c.close()
    return templates.TemplateResponse("gifts.html", {
        "request": req, "user": user, "types": types,
        "ricevuti": ricevuti, "inviati": inviati,
        "no_doni": r.get("no_doni"),
        "unread": unread_count(user["id"]),
    })


@app.get("/gifts/send/{to_user_id}", response_class=HTMLResponse)
async def gifts_send_page(req: Request, to_user_id: int):
    user = current_user(req)
    if not user:
        return RedirectResponse("/login", 303)
    if is_suspended(user):
        return RedirectResponse("/profile?err=sospeso", 303)
    r = get_restrictions(user["id"])
    if r.get("no_doni"):
        return RedirectResponse("/gifts?err=restrizione", 303)
    c = db()
    cur = c.cursor()
    cur.execute("SELECT id, nome, username FROM users WHERE id=%s AND stato='attivo'", (to_user_id,))
    altro = cur.fetchone()
    if not altro:
        cur.close()
        c.close()
        return RedirectResponse("/matches", 303)
    altro = dict(altro)
    cur.execute("SELECT credits FROM users WHERE id=%s", (user["id"],))
    user["credits"] = cur.fetchone()["credits"]
    cur.execute("SELECT * FROM gift_types WHERE attivo=1 ORDER BY costo ASC")
    types = [dict(x) for x in cur.fetchall()]
    # conversation if match
    cur.execute("""
        SELECT c.id FROM conversations c
        JOIN matches m ON m.id = c.match_id
        WHERE (m.user1_id=%s AND m.user2_id=%s) OR (m.user1_id=%s AND m.user2_id=%s)
        LIMIT 1
    """, (user["id"], to_user_id, to_user_id, user["id"]))
    conv = cur.fetchone()
    conv_id = conv["id"] if conv else None
    cur.close()
    c.close()
    return templates.TemplateResponse("gifts_send.html", {
        "request": req, "user": user, "altro": altro, "types": types,
        "conversation_id": conv_id, "unread": unread_count(user["id"]),
    })



@app.post("/gifts/send/{to_user_id}")
async def gifts_send(req: Request, to_user_id: int):
    user = current_user(req)
    if not user:
        return RedirectResponse("/login", 303)
    try:
        if is_suspended(user) or to_user_id == user["id"]:
            return RedirectResponse("/gifts", 303)
        r = get_restrictions(user["id"])
        if r.get("no_doni"):
            return RedirectResponse("/gifts?err=restrizione", 303)

        form = await req.form()
        try:
            gift_type_id = int(form.get("gift_type_id") or 0)
        except Exception:
            return RedirectResponse(f"/gifts/send/{to_user_id}", 303)
        messaggio = (form.get("messaggio") or "").strip()
        conv_raw = form.get("conversation_id")
        conversation_id = None
        if conv_raw not in (None, "", "None"):
            try:
                conversation_id = int(conv_raw)
            except Exception:
                conversation_id = None

        c = db()
        cur = c.cursor()
        try:
            cur.execute("SELECT * FROM gift_types WHERE id=%s AND COALESCE(attivo,1)=1", (gift_type_id,))
            gt = cur.fetchone()
        except Exception as e:
            cur.close()
            c.close()
            print("gift_types missing:", e)
            return RedirectResponse("/gifts?err=no_table", 303)
        if not gt:
            cur.close()
            c.close()
            return RedirectResponse(f"/gifts/send/{to_user_id}", 303)
        gt = dict(gt)
        cost = int(gt.get("costo") or 10)
        cur.close()
        c.close()

        r_to = get_restrictions(to_user_id)
        if r_to.get("no_ricevere_doni"):
            return RedirectResponse(f"/gifts/send/{to_user_id}?err=no_riceve", 303)

        if not spend_credits(user["id"], cost, f"dono_{gt.get('nome','')}", to_user_id):
            return RedirectResponse(f"/gifts/send/{to_user_id}?err=crediti", 303)

        reward = cost // 2
        if reward > 0:
            try:
                c0 = db()
                cur0 = c0.cursor()
                cur0.execute(
                    "UPDATE users SET credits = COALESCE(credits,0) + %s WHERE id=%s",
                    (reward, to_user_id),
                )
                try:
                    cur0.execute(
                        "INSERT INTO credit_transactions (user_id, amount, motivo, related_id) VALUES (%s,%s,%s,%s)",
                        (to_user_id, reward, "dono_ricevuto", user["id"]),
                    )
                except Exception:
                    pass
                c0.commit()
                cur0.close()
                c0.close()
            except Exception as e:
                print("gift reward error:", e)

        c = db()
        cur = c.cursor()
        try:
            conv_id = conversation_id
            if not conv_id:
                try:
                    cur.execute("""
                        SELECT c.id FROM conversations c
                        JOIN matches m ON m.id = c.match_id
                        WHERE (m.user1_id=%s AND m.user2_id=%s) OR (m.user1_id=%s AND m.user2_id=%s)
                        LIMIT 1
                    """, (user["id"], to_user_id, to_user_id, user["id"]))
                    row = cur.fetchone()
                    conv_id = row["id"] if row else None
                except Exception:
                    conv_id = None

            gift_id = None
            try:
                cur.execute(
                    """INSERT INTO gifts_sent (from_user_id, to_user_id, gift_type_id, conversation_id, messaggio)
                       VALUES (%s,%s,%s,%s,%s) RETURNING id""",
                    (user["id"], to_user_id, gift_type_id, conv_id, messaggio[:200] if messaggio else None),
                )
                gift_id = cur.fetchone()["id"]
            except Exception as e:
                print("gifts_sent insert:", e)
                c.rollback()
                # tabella mancante: non bloccare del tutto se crediti già scalati
                return RedirectResponse("/gifts?err=no_table", 303)

            if conv_id:
                testo = f"🎁 Ti ha inviato: {gt.get('emoji','🎁')} {gt.get('nome','Dono')}"
                if messaggio:
                    testo += f" — «{messaggio[:80]}»"
                try:
                    cur.execute(
                        "INSERT INTO messages (conversation_id, sender_id, tipo, contenuto) VALUES (%s,%s,%s,%s)",
                        (conv_id, user["id"], "testo", testo),
                    )
                    cur.execute(
                        "UPDATE conversations SET ultimo_messaggio_at=CURRENT_TIMESTAMP WHERE id=%s",
                        (conv_id,),
                    )
                except Exception as e:
                    print("gift message insert:", e)

            try:
                cur.execute(
                    """INSERT INTO notifications (user_id, tipo, titolo, contenuto, related_id)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (
                        to_user_id,
                        "dono",
                        f"Hai ricevuto {gt.get('emoji','🎁')} {gt.get('nome','un dono')}",
                        f"{user.get('nome', 'Qualcuno')} ti ha inviato un dono!",
                        gift_id,
                    ),
                )
            except Exception as e:
                print("gift notif:", e)

            c.commit()
            try:
                await manager.send(to_user_id, {
                    "type": "dono",
                    "title": f"Dono {gt.get('emoji','🎁')}",
                    "message": f"{user.get('nome', 'Qualcuno')} ti ha inviato {gt.get('nome','un dono')}",
                })
            except Exception:
                pass
        except Exception as e:
            c.rollback()
            print("gift send error:", e)
            return RedirectResponse(f"/gifts/send/{to_user_id}?err=db", 303)
        finally:
            try:
                cur.close()
                c.close()
            except Exception:
                pass

        if conversation_id:
            return RedirectResponse(f"/chat/{conversation_id}", 303)
        return RedirectResponse("/gifts?ok=1", 303)
    except Exception as e:
        print("gifts_send outer:", type(e).__name__, e)
        return RedirectResponse(f"/gifts/send/{to_user_id}?err=db", 303)


