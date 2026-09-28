import os
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple

def _load_dotenv(dotenv_path: str) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not os.path.isfile(dotenv_path):
        return values
    with open(dotenv_path, "r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("\"").strip("'")
    return values


DEFAULT_SCHEMA_SQL = """
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

CREATE TABLE IF NOT EXISTS opponent_stats (
    opponent_id TEXT PRIMARY KEY,
    total_wins INT NOT NULL DEFAULT 0,
    favorite_opening TEXT NOT NULL
);
"""


class DBEnvoy:
    def __init__(self, dotenv_path: str = ".env") -> None:
        env_values = _load_dotenv(dotenv_path)
        db_path = env_values.get("LOCAL_DB_PATH", "chess_db.sqlite3")
        self._db_path = (
            db_path
            if os.path.isabs(db_path)
            else os.path.abspath(os.path.join(os.getcwd(), db_path))
        )
        db_dir = os.path.dirname(self._db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        self._cache_ttl_seconds = self._parse_positive_int(
            env_values.get("CACHE_TTL_SECONDS"), default=3600
        )
        self._cache_miss_ttl_seconds = self._parse_positive_int(
            env_values.get("CACHE_MISS_TTL_SECONDS"), default=5
        )
        self._memory_cache: Dict[str, Tuple[str, float, float]] = {}
        self._memory_cache_miss: Dict[str, float] = {}
        self._ensure_tables()

    def _sqlite_connection(self) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(self._db_path)
            connection.row_factory = sqlite3.Row
            return connection
        except sqlite3.Error as exc:
            raise RuntimeError("SQLite 連線失敗，請檢查 LOCAL_DB_PATH。") from exc

    def check_cache(self, fen: str) -> Optional[Tuple[str, float]]:
        now = time.time()
        memory_cached = self._memory_cache.get(fen)
        if memory_cached:
            move, score, expires_at = memory_cached
            if expires_at > now:
                return move, score
            self._memory_cache.pop(fen, None)
        miss_expires_at = self._memory_cache_miss.get(fen)
        if miss_expires_at and miss_expires_at > now:
            return None

        try:
            with self._sqlite_connection() as connection:
                cursor = connection.cursor()
                cursor.execute(
                    """
                    SELECT move, score, expires_at
                    FROM analysis_cache
                    WHERE fen = ?
                    """,
                    (fen,),
                )
                row = cursor.fetchone()
                cursor.close()
            if not row:
                self._memory_cache_miss[fen] = now + self._cache_miss_ttl_seconds
                return None
            expires_at = float(row["expires_at"])
            if expires_at <= now:
                self._delete_cache(fen)
                return None
            move = str(row["move"])
            score = float(row["score"])
            self._memory_cache[fen] = (move, score, expires_at)
            self._memory_cache_miss.pop(fen, None)
            return move, score
        except (sqlite3.Error, ValueError, TypeError) as exc:
            print(f"快取讀取失敗：{exc}")
            return None

    def save_analysis(
        self,
        opp_id: str,
        fen: str,
        move: str,
        score: float,
        game_id: Optional[str] = None,
    ) -> bool:
        stored = self._save_sqlite(opp_id, fen, move, score, game_id)
        if not stored:
            return False
        return self._save_cache(fen, move, score)

    def get_opponent_history(self, opponent_id: str) -> List[Dict[str, Any]]:
        try:
            with self._sqlite_connection() as connection:
                cursor = connection.cursor()
                cursor.execute(
                    """
                    SELECT move_made AS move, COUNT(*) AS move_count, AVG(score) AS avg_score
                    FROM move_history
                    WHERE opponent_id = ?
                    GROUP BY move_made
                    ORDER BY move_count DESC
                    """,
                    (opponent_id,),
                )
                rows = cursor.fetchall()
                cursor.close()
                return [dict(row) for row in rows]
        except (sqlite3.Error, RuntimeError) as exc:
            print(f"SQLite 讀取失敗：{exc}")
            return []

    def _save_cache(self, fen: str, move: str, score: float) -> bool:
        expires_at = time.time() + self._cache_ttl_seconds
        try:
            with self._sqlite_connection() as connection:
                cursor = connection.cursor()
                cursor.execute(
                    """
                    INSERT INTO analysis_cache (fen, move, score, expires_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(fen) DO UPDATE SET
                        move = excluded.move,
                        score = excluded.score,
                        expires_at = excluded.expires_at
                    """,
                    (fen, move, score, expires_at),
                )
                connection.commit()
                cursor.close()
            self._memory_cache[fen] = (move, score, expires_at)
            self._memory_cache_miss.pop(fen, None)
            return True
        except sqlite3.Error as exc:
            self._memory_cache.pop(fen, None)
            self._memory_cache_miss.pop(fen, None)
            print(f"快取寫入失敗：{exc}")
            return False

    def _save_sqlite(
        self,
        opp_id: str,
        fen: str,
        move: str,
        score: float,
        game_id: Optional[str],
    ) -> bool:
        try:
            with self._sqlite_connection() as connection:
                cursor = connection.cursor()
                cursor.execute(
                    """
                    SELECT 1
                    FROM move_history
                    WHERE
                        ((game_id IS NULL AND ? IS NULL) OR game_id = ?)
                        AND opponent_id = ?
                        AND fen = ?
                        AND move_made = ?
                        AND score = ?
                    LIMIT 1
                    """,
                    (game_id, game_id, opp_id, fen, move, score),
                )
                if cursor.fetchone():
                    cursor.close()
                    return True
                cursor.execute(
                    """
                    INSERT INTO move_history (game_id, opponent_id, fen, move_made, score)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (game_id, opp_id, fen, move, score),
                )
                connection.commit()
                cursor.close()
            return True
        except (sqlite3.Error, RuntimeError) as exc:
            print(f"SQLite 寫入失敗：{exc}")
            return False

    def _ensure_tables(self) -> None:
        try:
            with self._sqlite_connection() as connection:
                cursor = connection.cursor()
                schema_sql = self._load_schema_sql()
                cursor.executescript(schema_sql)
                connection.commit()
                cursor.close()
        except (sqlite3.Error, RuntimeError) as exc:
            raise RuntimeError("SQLite 初始化失敗，請檢查本地資料庫設定。") from exc

    def get_opponent_opening(self, opp_id: str) -> Dict[str, Any]:
        try:
            with self._sqlite_connection() as connection:
                cursor = connection.cursor()
                cursor.execute(
                    """
                    SELECT fen, move_made, COUNT(*) AS move_count
                    FROM move_history
                    WHERE opponent_id = ?
                    GROUP BY fen, move_made
                    """,
                    (opp_id,),
                )
                rows = cursor.fetchall()
                cursor.close()
        except (sqlite3.Error, RuntimeError) as exc:
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

        filtered = [dict(row) for row in rows if _fullmove(row["fen"]) <= 5]
        if not filtered:
            return {}
        top = max(filtered, key=lambda item: item.get("move_count", 0))
        return {
            "fen": top.get("fen"),
            "move": top.get("move_made"),
            "count": top.get("move_count"),
        }

    @staticmethod
    def _cache_key(fen: str) -> str:
        return f"fen:{fen}"

    def _delete_cache(self, fen: str) -> None:
        self._memory_cache.pop(fen, None)
        self._memory_cache_miss[fen] = time.time() + self._cache_miss_ttl_seconds
        try:
            with self._sqlite_connection() as connection:
                cursor = connection.cursor()
                cursor.execute("DELETE FROM analysis_cache WHERE fen = ?", (fen,))
                connection.commit()
                cursor.close()
        except sqlite3.Error as exc:
            print(f"快取清理失敗：{exc}")

    @staticmethod
    def _parse_positive_int(value: Optional[str], default: int) -> int:
        if value is None:
            return default
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed > 0 else default

    @staticmethod
    def _load_schema_sql() -> str:
        schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
        if os.path.isfile(schema_path):
            with open(schema_path, "r", encoding="utf-8") as handle:
                return handle.read()
        return DEFAULT_SCHEMA_SQL
