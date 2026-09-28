CREATE TABLE IF NOT EXISTS move_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id TEXT NULL,
    opponent_id TEXT NOT NULL,
    fen TEXT NOT NULL,
    move_made TEXT,
    score REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_move_history_fen ON move_history (fen);

CREATE TABLE IF NOT EXISTS analysis_cache (
    fen TEXT PRIMARY KEY,
    move TEXT NOT NULL,
    score REAL NOT NULL,
    expires_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS opponent_stats (
    opponent_id TEXT PRIMARY KEY,
    total_wins INT NOT NULL DEFAULT 0,
    favorite_opening TEXT NOT NULL
);
