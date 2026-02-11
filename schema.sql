CREATE DATABASE IF NOT EXISTS chess_db;

USE chess_db;

CREATE TABLE IF NOT EXISTS move_history (
    id INT AUTO_INCREMENT PRIMARY KEY,
    game_id VARCHAR(50) NULL,
    opponent_id VARCHAR(50) NOT NULL,
    fen TEXT NOT NULL,
    move_made VARCHAR(10),
    score FLOAT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_move_history_fen (fen(30))
);

CREATE TABLE IF NOT EXISTS opponent_stats (
    opponent_id VARCHAR(64) PRIMARY KEY,
    total_wins INT NOT NULL DEFAULT 0,
    favorite_opening VARCHAR(128) NOT NULL
);
