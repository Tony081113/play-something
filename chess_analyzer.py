from dataclasses import dataclass
from typing import Optional, Tuple

import chess
from stockfish import Stockfish


@dataclass(frozen=True)
class AnalysisResult:
    move: str
    score: int
    mate_in: Optional[int]


class ChessAnalyzer:
    def __init__(self, stockfish_path: str, default_depth: int = 15) -> None:
        self._engine = Stockfish(path=stockfish_path)
        self._default_depth = default_depth

    def get_best_move(self, fen: str, depth: Optional[int] = None) -> Tuple[str, int]:
        result = self.analyze_position(fen, depth=depth)
        return result.move, result.score

    def analyze_position(self, fen: str, depth: Optional[int] = None) -> AnalysisResult:
        board = chess.Board(fen)
        self._engine.set_fen_position(board.fen())
        self._engine.set_depth(depth or self._default_depth)
        best_move = self._engine.get_best_move()
        evaluation = self._engine.get_evaluation()
        score = self._normalize_score(evaluation)
        return AnalysisResult(best_move, score, self._mate_in(evaluation))

    def recommend_reply(
        self, fen: str, opponent_move: str, depth: Optional[int] = None
    ) -> Optional[AnalysisResult]:
        board = chess.Board(fen)
        try:
            move = chess.Move.from_uci(opponent_move)
        except ValueError:
            return None
        if move not in board.legal_moves:
            return None
        board.push(move)
        return self.analyze_position(board.fen(), depth=depth)

    @staticmethod
    def _normalize_score(evaluation: dict) -> int:
        if evaluation.get("type") == "cp":
            return int(evaluation.get("value", 0))
        if evaluation.get("type") == "mate":
            value = int(evaluation.get("value", 0))
            return 100000 if value > 0 else -100000
        return 0

    @staticmethod
    def _mate_in(evaluation: dict) -> Optional[int]:
        if evaluation.get("type") != "mate":
            return None
        try:
            return int(evaluation.get("value", 0))
        except (TypeError, ValueError):
            return None
