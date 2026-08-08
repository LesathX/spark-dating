//https://dashboard.render.com/web/srv-d9ritkv40ujc73bjnceg/logs
# 💘 Spark – Dating Chat

Applicazione web moderna di incontri (stile Tinder) con swipe, match e chat.

## Funzionalità

- Registrazione e login sicuri (password hashate)
- Profili con bio, città, interessi, altezza...
- Swipe (like / dislike / superlike)
- Match automatici + notifiche
- Chat tra match
- Design mobile-first moderno con navigazione in basso

## Utenti di prova già presenti

| Email | Password | Nome |
|-------|----------|------|
| marco@email.com | password123 | Marco (Milano) |
| giulia@email.com | password123 | Giulia (Milano) |
| luca@email.com | password123 | Luca (Roma) |
| sofia@email.com | password123 | Sofia (Firenze) |
| alessandro@email.com | password123 | Alessandro (Torino) |
| chiara@email.com | password123 | Chiara (Bologna) |
| davide@email.com | password123 | Davide (Bergamo) |
| elena@email.com | password123 | Elena (Napoli) |

## Avvio in locale

```bash
pip install -r requirements.txt
python app.py
```

Apri → http://localhost:8000

---

## Come metterlo ONLINE su Render (gratis) – Guida dettagliata

### Passo 1 – Crea account
1. Vai su **https://render.com**
2. Clicca **Get Started** e registrati (puoi usare Google/GitHub)

### Passo 2 – Prepara i file
Hai due possibilità:

**A. Con GitHub (consigliato)**
1. Crea un nuovo repository su GitHub
2. Carica tutti i file del progetto (app.py, requirements.txt, dating_chat.db, templates/, README.md)
3. Poi su Render scegli “Deploy from GitHub”

**B. Senza GitHub**
- Usa l’opzione “Deploy from ZIP” o “Manual deploy” se disponibile, oppure crea comunque un repo GitHub (è gratis e più semplice).

### Passo 3 – Crea il Web Service
1. Nella dashboard Render clicca **New +** → **Web Service**
2. Collega il repository (o carica i file)
3. Compila così:

| Campo | Valore |
|-------|--------|
| **Name** | spark-dating (o quello che vuoi) |
| **Region** | Frankfurt (più vicino all’Italia) |
| **Runtime** | Python 3 |
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `uvicorn app:app --host 0.0.0.0 --port $PORT` |

### Passo 4 – Variabile d’ambiente
Nella sezione **Environment**:
- Aggiungi `SECRET_KEY`
- Valore: genera una chiave sicura (puoi usare questo comando sul tuo computer):
  ```bash
  python -c "import secrets; print(secrets.token_hex(32))"
  ```
  Incolla il risultato.

### Passo 5 – Deploy
Clicca **Create Web Service**.

Render installerà tutto e dopo 1-3 minuti ti darà un link tipo:
`https://spark-dating.onrender.com`

### Passo 6 – Provalo
Apri il link dal telefono e fai login con uno degli utenti di prova.

---

## Nota importante sul database

Su Render il disco è **effimero**: ogni volta che fai un nuovo deploy il database SQLite si resetta.
Per un uso serio a lungo termine conviene passare a **PostgreSQL** (Render lo offre gratis).

## Struttura file

```
spark_dating_app/
├── app.py
├── dating_chat.db
├── requirements.txt
├── README.md
└── templates/
    ├── base.html
    ├── index.html
    ├── login.html
    ├── register.html
    ├── discover.html
    ├── matches.html
    ├── chat.html
    ├── profile.html
    └── notifications.html
```
