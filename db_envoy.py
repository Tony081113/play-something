import os
import sqlite3
import sys
from typing import Any, Dict, List, Optional, Tuple


def _app_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


class DBEnvoy:
    def __init__(self, db_path: Optional[str] = None) -> None:
        self._db_path = db_path or os.getenv(
            "PLAY_SOMETHING_DB",
            os.path.join(_app_dir(), "play-something.db"),
        )
        self._ensure_tables()

    def _connect(self) -> sqlite3.Connection:
        os.makedirs(os.path.dirname(os.path.abspath(self._db_path)), exist_ok=True)
        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def check_cache(self, fen: str) -> Optional[Tuple[str, float]]:
        cached = self.get_cached_analysis(fen)
        if cached is None:
            return None
        return cached["move"], float(cached["score"])

    def get_cached_analysis(self, fen: str) -> Optional[Dict[str, Any]]:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT move_made, score, mate_in
                    FROM move_history
                    WHERE fen = ?
                        AND source = 'analysis'
                    ORDER BY created_at DESC, id DESC
                    LIMIT 1
                    """,
                    (fen,),
                ).fetchone()
        except sqlite3.Error as exc:
            print(f"SQLite 讀取快取失敗：{exc}")
            return None

        if row is None or row["move_made"] is None or row["score"] is None:
            return None
        return {
            "move": row["move_made"],
            "score": float(row["score"]),
            "mate_in": row["mate_in"],
        }

    def save_analysis(
        self,
        opp_id: str,
        fen: str,
        move: str,
        score: float,
        game_id: Optional[str] = None,
        mate_in: Optional[int] = None,
        source: str = "analysis",
    ) -> bool:
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO move_history
                        (game_id, opponent_id, fen, move_made, score, mate_in, source)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (game_id, opp_id, fen, move, score, mate_in, source),
                )
                if source == "analysis":
                    self._save_training_example(
                        connection, fen, move, score, mate_in=mate_in
                    )
            return True
        except sqlite3.Error as exc:
            print(f"SQLite 寫入失敗：{exc}")
            return False

    def get_position_tendency(self, opponent_id: str, fen: str) -> Optional[Dict[str, Any]]:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT
                        move_made AS move,
                        COUNT(*) AS move_count,
                        AVG(score) AS avg_score,
                        MAX(id) AS last_id
                    FROM move_history
                    WHERE opponent_id = ?
                        AND fen = ?
                    GROUP BY move_made
                    ORDER BY move_count DESC, last_id DESC
                    LIMIT 1
                    """,
                    (opponent_id, fen),
                ).fetchone()
        except sqlite3.Error as exc:
            print(f"SQLite 讀取失敗：{exc}")
            return None
        return dict(row) if row is not None else None

    def get_opponent_history(self, opponent_id: str) -> List[Dict[str, Any]]:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT move_made AS move, COUNT(*) AS move_count, AVG(score) AS avg_score
                    FROM move_history
                    WHERE opponent_id = ?
                    GROUP BY move_made
                    ORDER BY move_count DESC
                    """,
                    (opponent_id,),
                ).fetchall()
        except sqlite3.Error as exc:
            print(f"SQLite 讀取失敗：{exc}")
            return []
        return [dict(row) for row in rows]

    def _ensure_tables(self) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    """
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
                    )
                    """
                )
                self._ensure_column(connection, "move_history", "mate_in", "INTEGER NULL")
                self._ensure_column(
                    connection,
                    "move_history",
                    "source",
                    "TEXT NOT NULL DEFAULT 'analysis'",
                )
                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_move_history_fen
                    ON move_history (fen)
                    """
                )
                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_move_history_opponent
                    ON move_history (opponent_id)
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS training_positions (
                        fen TEXT PRIMARY KEY,
                        best_move TEXT NOT NULL,
                        score REAL NOT NULL,
                        mate_in INTEGER NULL,
                        depth INTEGER NULL,
                        visits INTEGER NOT NULL DEFAULT 1,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
        except sqlite3.Error as exc:
            print(f"SQLite 初始化失敗：{exc}")

    def _ensure_column(
        self, connection: sqlite3.Connection, table: str, column: str, definition: str
    ) -> None:
        columns = {
            row["name"] for row in connection.execute(f"PRAGMA table_info({table})")
        }
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def _save_training_example(
        self,
        connection: sqlite3.Connection,
        fen: str,
        best_move: str,
        score: float,
        mate_in: Optional[int] = None,
        depth: Optional[int] = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO training_positions
                (fen, best_move, score, mate_in, depth, visits, updated_at)
            VALUES (?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
            ON CONFLICT(fen) DO UPDATE SET
                best_move = excluded.best_move,
                score = excluded.score,
                mate_in = excluded.mate_in,
                depth = excluded.depth,
                visits = training_positions.visits + 1,
                updated_at = CURRENT_TIMESTAMP
            """,
            (fen, best_move, score, mate_in, depth),
        )

    def get_training_stats(self) -> Dict[str, Any]:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT COUNT(*) AS positions, COALESCE(SUM(visits), 0) AS visits
                    FROM training_positions
                    """
                ).fetchone()
        except sqlite3.Error as exc:
            print(f"SQLite 讀取訓練資料失敗：{exc}")
            return {"positions": 0, "visits": 0}
        return dict(row)

    def get_opponent_opening(self, opp_id: str) -> Dict[str, Any]:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT fen, move_made, COUNT(*) AS move_count
                    FROM move_history
                    WHERE opponent_id = ?
                    GROUP BY fen, move_made
                    """,
                    (opp_id,),
                ).fetchall()
        except sqlite3.Error as exc:
            print(f"SQLite 讀取失敗：{exc}")
            return {}

        def _fullmove(fen: str) -> int:
            parts = fen.split()
            if len(parts) < 6:
                return 999
            try:
                return int(parts[5])
            except ValueError:
                return 999

        filtered = [dict(row) for row in rows if _fullmove(row["fen"] or "") <= 5]
        if not filtered:
            return {}
        top = max(filtered, key=lambda item: item.get("move_count", 0))
        return {
            "fen": top.get("fen"),
            "move": top.get("move_made"),
            "count": top.get("move_count"),
        }
