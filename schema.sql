CREATE TABLE IF NOT EXISTS move_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id TEXT NULL,
    opponent_id TEXT NOT NULL,
    fen TEXT NOT NULL,
    move_made TEXT,
    score REAL,
    mate_in INTEGER NULL,
    source TEXT NOT NULL DEFAULT 'analysis',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_move_history_fen
ON move_history (fen);

CREATE INDEX IF NOT EXISTS idx_move_history_opponent
ON move_history (opponent_id);

CREATE TABLE IF NOT EXISTS opponent_stats (
    opponent_id TEXT PRIMARY KEY,
    total_wins INTEGER NOT NULL DEFAULT 0,
    favorite_opening TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS training_positions (
    fen TEXT PRIMARY KEY,
    best_move TEXT NOT NULL,
    score REAL NOT NULL,
    mate_in INTEGER NULL,
    depth INTEGER NULL,
    visits INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
