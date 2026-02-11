from typing import Optional, Tuple

import chess
from stockfish import Stockfish


class ChessAnalyzer:
    def __init__(self, stockfish_path: str, default_depth: int = 15) -> None:
        self._engine = Stockfish(path=stockfish_path)
        self._default_depth = default_depth

    def get_best_move(self, fen: str, depth: Optional[int] = None) -> Tuple[str, int]:
        board = chess.Board(fen)
        self._engine.set_fen_position(board.fen())
        self._engine.set_depth(depth or self._default_depth)
        best_move = self._engine.get_best_move()
        evaluation = self._engine.get_evaluation()
        score = self._normalize_score(evaluation)
        return best_move, score

    @staticmethod
    def _normalize_score(evaluation: dict) -> int:
        if evaluation.get("type") == "cp":
            return int(evaluation.get("value", 0))
        if evaluation.get("type") == "mate":
            value = int(evaluation.get("value", 0))
            return 100000 if value > 0 else -100000
        return 0
