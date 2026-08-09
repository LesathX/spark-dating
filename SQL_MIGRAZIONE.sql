-- MyCheating: crediti, moderatori, foto private, backup
ALTER TABLE users ADD COLUMN IF NOT EXISTS credits INTEGER DEFAULT 50;
ALTER TABLE users ADD COLUMN IF NOT EXISTS is_mod INTEGER DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS ruolo VARCHAR(20) DEFAULT 'user';

-- foto profilo (pubbliche e private)
CREATE TABLE IF NOT EXISTS user_photos (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    is_private INTEGER DEFAULT 0,
    ordine INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- movimenti crediti
CREATE TABLE IF NOT EXISTS credit_transactions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    amount INTEGER NOT NULL,
    motivo VARCHAR(100),
    related_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- messaggi intro su swipe (pagati con crediti)
CREATE TABLE IF NOT EXISTS swipe_messages (
    id SERIAL PRIMARY KEY,
    from_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    to_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    contenuto TEXT NOT NULL,
    credits_spent INTEGER DEFAULT 0,
    delivered INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- log backup
CREATE TABLE IF NOT EXISTS backups_log (
    id SERIAL PRIMARY KEY,
    tipo VARCHAR(20) DEFAULT 'manual',
    note TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_by INTEGER
);

-- assicurati crediti default per utenti esistenti
UPDATE users SET credits = 50 WHERE credits IS NULL;
UPDATE users SET ruolo = 'admin' WHERE is_admin = 1;
UPDATE users SET ruolo = 'user' WHERE ruolo IS NULL;


-- Accesso foto private (consenso in chat, pagato con crediti non rimborsabili)
CREATE TABLE IF NOT EXISTS photo_access_requests (
    id SERIAL PRIMARY KEY,
    from_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    to_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    credits_paid INTEGER NOT NULL DEFAULT 25,
    status VARCHAR(20) DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    decided_at TIMESTAMP,
    UNIQUE(from_user_id, to_user_id)
);

ALTER TABLE user_photos ADD COLUMN IF NOT EXISTS media_type VARCHAR(20) DEFAULT 'image';
ALTER TABLE user_photos ADD COLUMN IF NOT EXISTS media_type VARCHAR(20) DEFAULT 'image';

-- Restrizioni e sospensione utenti
ALTER TABLE users ADD COLUMN IF NOT EXISTS sospeso_fino TIMESTAMP;
ALTER TABLE users ADD COLUMN IF NOT EXISTS note_admin TEXT;

CREATE TABLE IF NOT EXISTS user_restrictions (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    no_gallery INTEGER DEFAULT 0,
    no_like INTEGER DEFAULT 0,
    no_messaggi INTEGER DEFAULT 0,
    no_primo_messaggio INTEGER DEFAULT 0,
    no_scopri INTEGER DEFAULT 0,
    no_chat INTEGER DEFAULT 0,
    no_vedi_foto INTEGER DEFAULT 0,
    no_storie INTEGER DEFAULT 0,
    no_commenti INTEGER DEFAULT 0,
    no_superlike INTEGER DEFAULT 0,
    no_swipe_messaggio INTEGER DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Sospensione e restrizioni utente
ALTER TABLE users ADD COLUMN IF NOT EXISTS sospeso_fino TIMESTAMP;
ALTER TABLE users ADD COLUMN IF NOT EXISTS note_admin TEXT;

CREATE TABLE IF NOT EXISTS user_restrictions (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    no_gallery INTEGER DEFAULT 0,
    no_like INTEGER DEFAULT 0,
    no_messaggi INTEGER DEFAULT 0,
    no_primo_messaggio INTEGER DEFAULT 0,
    no_scopri INTEGER DEFAULT 0,
    no_chat INTEGER DEFAULT 0,
    no_vedi_foto INTEGER DEFAULT 0,
    no_commenti INTEGER DEFAULT 0,
    no_storie INTEGER DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE users ADD COLUMN IF NOT EXISTS telefono VARCHAR(30);
ALTER TABLE user_restrictions ADD COLUMN IF NOT EXISTS no_post INTEGER DEFAULT 0;
ALTER TABLE user_restrictions ADD COLUMN IF NOT EXISTS no_doni INTEGER DEFAULT 0;
ALTER TABLE user_restrictions ADD COLUMN IF NOT EXISTS no_annunci INTEGER DEFAULT 0;
ALTER TABLE user_restrictions ADD COLUMN IF NOT EXISTS no_annunci_personali INTEGER DEFAULT 0;
ALTER TABLE user_restrictions ADD COLUMN IF NOT EXISTS no_annunci_hot INTEGER DEFAULT 0;
ALTER TABLE user_restrictions ADD COLUMN IF NOT EXISTS no_annunci_vendita INTEGER DEFAULT 0;
ALTER TABLE user_restrictions ADD COLUMN IF NOT EXISTS no_annunci_scambio INTEGER DEFAULT 0;
ALTER TABLE user_restrictions ADD COLUMN IF NOT EXISTS no_annunci_regalo INTEGER DEFAULT 0;

-- DONI
CREATE TABLE IF NOT EXISTS gift_types (
    id SERIAL PRIMARY KEY,
    nome VARCHAR(50) NOT NULL,
    emoji VARCHAR(10) NOT NULL,
    costo INTEGER NOT NULL DEFAULT 10,
    attivo INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS gifts_sent (
    id SERIAL PRIMARY KEY,
    from_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    to_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    gift_type_id INTEGER NOT NULL REFERENCES gift_types(id),
    conversation_id INTEGER,
    messaggio TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO gift_types (nome, emoji, costo) 
SELECT * FROM (VALUES
    ('Rosa', '🌹', 5),
    ('Cuore', '❤️', 10),
    ('Cioccolatini', '🍫', 15),
    ('Champagne', '🥂', 25),
    ('Diamante', '💎', 50),
    ('Fuochi', '🎆', 30),
    ('Orsacchiotto', '🧸', 20),
    ('Corona', '👑', 100)
) AS v(nome, emoji, costo)
WHERE NOT EXISTS (SELECT 1 FROM gift_types LIMIT 1);

ALTER TABLE user_restrictions ADD COLUMN IF NOT EXISTS no_ricevere_doni INTEGER DEFAULT 0;

CREATE TABLE IF NOT EXISTS gift_types (
    id SERIAL PRIMARY KEY,
    nome VARCHAR(50) NOT NULL,
    emoji VARCHAR(10) NOT NULL,
    costo INTEGER NOT NULL DEFAULT 10,
    attivo INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS gifts_sent (
    id SERIAL PRIMARY KEY,
    from_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    to_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    gift_type_id INTEGER NOT NULL REFERENCES gift_types(id),
    conversation_id INTEGER,
    messaggio TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
INSERT INTO gift_types (nome, emoji, costo)
SELECT * FROM (VALUES
    ('Rosa', '🌹', 5),
    ('Cuore', '❤️', 10),
    ('Cioccolatini', '🍫', 15),
    ('Champagne', '🥂', 25),
    ('Diamante', '💎', 50),
    ('Fuochi', '🎆', 30),
    ('Orsacchiotto', '🧸', 20),
    ('Corona', '👑', 100)
) AS v(nome, emoji, costo)
WHERE NOT EXISTS (SELECT 1 FROM gift_types LIMIT 1);

ALTER TABLE users ADD COLUMN IF NOT EXISTS is_bot INTEGER DEFAULT 0;

-- Chat media + autodistruzione + reports
ALTER TABLE messages ADD COLUMN IF NOT EXISTS media_url TEXT;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS media_type VARCHAR(20);
ALTER TABLE messages ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP;

CREATE TABLE IF NOT EXISTS reports (
    id SERIAL PRIMARY KEY,
    reporter_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    reported_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    motivo TEXT,
    conversation_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS blocks (
    id SERIAL PRIMARY KEY,
    blocker_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    blocked_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(blocker_id, blocked_id)
);

-- ========== BUCKET CHAT (Supabase Storage UI, non SQL) ==========
-- 1. Supabase → Storage → New bucket
-- 2. Nome: chat
-- 3. Public: ON (così i media in chat sono visualizzabili)
-- 4. Create
-- 5. Render env (opzionale): CHAT_STORAGE_BUCKET=chat
-- Gallery resta su bucket "gallery"; chat media su bucket "chat"

ALTER TABLE reports ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'open';
ALTER TABLE reports ADD COLUMN IF NOT EXISTS reviewed_by INTEGER;
ALTER TABLE reports ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMP;
ALTER TABLE reports ADD COLUMN IF NOT EXISTS false_report INTEGER DEFAULT 0;

ALTER TABLE users ADD COLUMN IF NOT EXISTS last_ip VARCHAR(64);
ALTER TABLE users ADD COLUMN IF NOT EXISTS is_bot INTEGER DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS false_reports_count INTEGER DEFAULT 0;
ALTER TABLE reports ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'open';
ALTER TABLE reports ADD COLUMN IF NOT EXISTS reviewed_by INTEGER;
ALTER TABLE reports ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMP;
ALTER TABLE reports ADD COLUMN IF NOT EXISTS false_report INTEGER DEFAULT 0;

ALTER TABLE users ADD COLUMN IF NOT EXISTS telefono VARCHAR(32);
ALTER TABLE users ADD COLUMN IF NOT EXISTS phone_verified INTEGER DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS phone_verified_at TIMESTAMP;
CREATE TABLE IF NOT EXISTS phone_otps (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    phone VARCHAR(32) NOT NULL,
    code VARCHAR(10) NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    used INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
ALTER TABLE reports ADD COLUMN IF NOT EXISTS content_type VARCHAR(40);
ALTER TABLE reports ADD COLUMN IF NOT EXISTS content_id VARCHAR(40);

-- BOT separati dagli utenti reali
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
);
CREATE INDEX IF NOT EXISTS idx_users_is_bot ON users(is_bot);

ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified INTEGER DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified_at TIMESTAMP;
CREATE TABLE IF NOT EXISTS email_otps (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    email VARCHAR(200) NOT NULL,
    code VARCHAR(10) NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    used INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
