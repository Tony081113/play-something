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
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT move_made, score
                    FROM move_history
                    WHERE fen = ?
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
        return row["move_made"], float(row["score"])

    def save_analysis(
        self,
        opp_id: str,
        fen: str,
        move: str,
        score: float,
        game_id: Optional[str] = None,
    ) -> bool:
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO move_history
                        (game_id, opponent_id, fen, move_made, score)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (game_id, opp_id, fen, move, score),
                )
            return True
        except sqlite3.Error as exc:
            print(f"SQLite 寫入失敗：{exc}")
            return False

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
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
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
        except sqlite3.Error as exc:
            print(f"SQLite 初始化失敗：{exc}")

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
