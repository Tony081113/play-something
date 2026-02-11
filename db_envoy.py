import json
import os
from typing import Any, Dict, List, Optional, Tuple

import mysql.connector
import redis


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


class DBEnvoy:
    def __init__(self, dotenv_path: str = ".env") -> None:
        env_values = _load_dotenv(dotenv_path)
        self._mysql_config = {
            "host": env_values.get("MYSQL_HOST", "localhost"),
            "port": int(env_values.get("MYSQL_PORT", "3306")),
            "user": env_values.get("MYSQL_USER"),
            "password": env_values.get("MYSQL_PASSWORD"),
            "database": env_values.get("MYSQL_DB", "chess_db"),
        }
        self._redis_config = {
            "host": env_values.get("REDIS_HOST", "localhost"),
            "port": int(env_values.get("REDIS_PORT", "6379")),
            "password": env_values.get("REDIS_PASSWORD"),
            "db": int(env_values.get("REDIS_DB", "0")),
            "decode_responses": True,
        }
        self._validate_secrets()
        self._ensure_tables()

    def _mysql_connection(self) -> mysql.connector.MySQLConnection:
        try:
            return mysql.connector.connect(**self._mysql_config)
        except mysql.connector.Error as exc:
            raise RuntimeError("MySQL 連線失敗，請檢查 .env 設定。") from exc

    def _redis_client(self) -> redis.Redis:
        return redis.Redis(**self._redis_config)

    def check_cache(self, fen: str) -> Optional[Tuple[str, float]]:
        try:
            client = self._redis_client()
            raw = client.get(self._cache_key(fen))
            if not raw:
                return None
            payload = json.loads(raw)
            move = payload.get("move")
            score = payload.get("score")
            if move is None or score is None:
                return None
            return move, float(score)
        except (redis.RedisError, ValueError, TypeError, json.JSONDecodeError) as exc:
            print(f"Redis 讀取失敗：{exc}")
            return None

    def save_analysis(
        self,
        opp_id: str,
        fen: str,
        move: str,
        score: float,
        game_id: Optional[str] = None,
    ) -> bool:
        cached = self._save_cache(fen, move, score)
        stored = self._save_mysql(opp_id, fen, move, score, game_id)
        return cached and stored

    def get_opponent_history(self, opponent_id: str) -> List[Dict[str, Any]]:
        try:
            with self._mysql_connection() as connection:
                cursor = connection.cursor(dictionary=True)
                cursor.execute(
                    """
                    SELECT move_made AS move, COUNT(*) AS move_count, AVG(score) AS avg_score
                    FROM move_history
                    WHERE opponent_id = %s
                    GROUP BY move_made
                    ORDER BY move_count DESC
                    """,
                    (opponent_id,),
                )
                rows = cursor.fetchall()
                cursor.close()
                return rows
        except (mysql.connector.Error, RuntimeError) as exc:
            print(f"MySQL read failed: {exc}")
            return []

    def _save_cache(self, fen: str, move: str, score: float) -> bool:
        try:
            client = self._redis_client()
            payload = json.dumps({"move": move, "score": score})
            client.setex(self._cache_key(fen), 3600, payload)
            return True
        except redis.RedisError as exc:
            print(f"Redis 寫入失敗：{exc}")
            return False

    def _save_mysql(
        self,
        opp_id: str,
        fen: str,
        move: str,
        score: float,
        game_id: Optional[str],
    ) -> bool:
        try:
            with self._mysql_connection() as connection:
                cursor = connection.cursor()
                cursor.execute(
                    """
                    INSERT INTO move_history
                        (game_id, opponent_id, fen, move_made, score)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (game_id, opp_id, fen, move, score),
                )
                connection.commit()
                cursor.close()
            return True
        except (mysql.connector.Error, RuntimeError) as exc:
            print(f"MySQL 寫入失敗：{exc}")
            return False

    def _ensure_tables(self) -> None:
        try:
            with self._mysql_connection() as connection:
                cursor = connection.cursor()
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS move_history (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        game_id VARCHAR(50) NULL,
                        opponent_id VARCHAR(50) NOT NULL,
                        fen TEXT NOT NULL,
                        move_made VARCHAR(10),
                        score FLOAT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        INDEX idx_move_history_fen (fen(30))
                    )
                    """
                )
                connection.commit()
                cursor.close()
        except (mysql.connector.Error, RuntimeError) as exc:
            print(f"MySQL 初始化失敗：{exc}")

    def _validate_secrets(self) -> None:
        missing = []
        if not self._mysql_config.get("user"):
            missing.append("MYSQL_USER")
        if not self._mysql_config.get("password"):
            missing.append("MYSQL_PASSWORD")
        if self._redis_config.get("password") is None:
            missing.append("REDIS_PASSWORD")
        if missing:
            raise RuntimeError(".env 缺少必要欄位：" + ", ".join(missing))

    def get_opponent_opening(self, opp_id: str) -> Dict[str, Any]:
        try:
            with self._mysql_connection() as connection:
                cursor = connection.cursor(dictionary=True)
                cursor.execute(
                    """
                    SELECT fen, move_made, COUNT(*) AS move_count
                    FROM move_history
                    WHERE opponent_id = %s
                    GROUP BY fen, move_made
                    """,
                    (opp_id,),
                )
                rows = cursor.fetchall()
                cursor.close()
        except (mysql.connector.Error, RuntimeError) as exc:
            print(f"MySQL 讀取失敗：{exc}")
            return {}

        def _fullmove(fen: str) -> int:
            parts = fen.split()
            if len(parts) < 6:
                return 999
            try:
                return int(parts[5])
            except ValueError:
                return 999

        filtered = [row for row in rows if _fullmove(row.get("fen", "")) <= 5]
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
