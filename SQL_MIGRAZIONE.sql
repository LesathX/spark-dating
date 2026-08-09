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
