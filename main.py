import argparse
import os
import sys

from chess_analyzer import ChessAnalyzer
from db_envoy import DBEnvoy


def _load_dotenv(dotenv_path: str = ".env") -> None:
    if not os.path.isfile(dotenv_path):
        return
    with open(dotenv_path, "r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"").strip("'"))


def _load_stockfish_path() -> str:
    stockfish_path = os.getenv("STOCKFISH_PATH")
    if stockfish_path and os.path.isfile(stockfish_path):
        return stockfish_path

    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(os.path.join(getattr(sys, "_MEIPASS", ""), "stockfish-windows-x86-64-universal.exe"))
        candidates.append(os.path.join(os.path.dirname(sys.executable), "stockfish-windows-x86-64-universal.exe"))
    candidates.append(
        os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "stockfish",
            "stockfish-windows-x86-64-universal.exe",
        )
    )

    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate

    raise RuntimeError("找不到 Stockfish 執行檔，請將 stockfish-windows-x86-64-universal.exe 放在程式旁邊。")


def _run_cli() -> None:
    print("請輸入 FEN：")
    fen = input().strip()
    print("請輸入對手 ID：")
    opponent_id = input().strip()
    if not opponent_id:
        print("對手 ID 必填。")
        return
    print("請輸入對局 ID（可留空）：")
    game_id = input().strip() or None

    db_envoy = DBEnvoy()
    cached = db_envoy.check_cache(fen)
    if cached:
        move, score = cached
        print(f"快取最佳走法：{move}（分數：{score}）")
        return

    history = db_envoy.get_opponent_history(opponent_id)
    if history:
        print("對手歷史走法：")
        for row in history:
            print(f"- {row['move']}: {row['move_count']}（平均 {row['avg_score']:.2f}）")

    analyzer = ChessAnalyzer(_load_stockfish_path())
    best_move, score = analyzer.get_best_move(fen)
    db_envoy.save_analysis(opponent_id, fen, best_move, score, game_id=game_id)
    print(f"最佳走法：{best_move}（分數：{score}）")


def _run_gui() -> None:
    import chess
    import chess.svg
    from PyQt5 import QtCore, QtGui, QtSvg, QtWidgets

    class AiWorker(QtCore.QThread):
        finished = QtCore.pyqtSignal(str, float, bool)

        def __init__(
            self,
            analyzer: ChessAnalyzer,
            db_envoy: DBEnvoy,
            fen: str,
            depth: int,
            opponent_id: str,
        ) -> None:
            super().__init__()
            self._analyzer = analyzer
            self._db_envoy = db_envoy
            self._fen = fen
            self._depth = depth
            self._opponent_id = opponent_id

        def run(self) -> None:
            cached = self._db_envoy.check_cache(self._fen)
            if cached:
                move, score = cached
                self.finished.emit(move, score, True)
                return
            best_move, score = self._analyzer.get_best_move(self._fen, depth=self._depth)
            self._db_envoy.save_analysis(self._opponent_id, self._fen, best_move, score)
            self.finished.emit(best_move, score, False)

    class EvalBar(QtWidgets.QWidget):
        def __init__(self) -> None:
            super().__init__()
            self._score = 0.0
            self.setMinimumWidth(30)

        def set_score(self, score: float) -> None:
            self._score = score
            self.update()

        def paintEvent(self, event: QtCore.QEvent) -> None:
            painter = QtWidgets.QStylePainter(self)
            rect = self.rect()
            painter.fillRect(rect, QtCore.Qt.black)
            clamped = max(-1000.0, min(1000.0, float(self._score)))
            ratio = (clamped + 1000.0) / 2000.0
            white_height = int(rect.height() * ratio)
            white_rect = QtCore.QRect(rect.x(), rect.bottom() - white_height + 1, rect.width(), white_height)
            painter.fillRect(white_rect, QtCore.Qt.white)

    class BoardWidget(QtWidgets.QGraphicsView):
        boardChanged = QtCore.pyqtSignal()

        def __init__(self) -> None:
            super().__init__()
            self._scene = QtWidgets.QGraphicsScene(self)
            self.setScene(self._scene)
            self.setRenderHints(
                QtGui.QPainter.Antialiasing | QtGui.QPainter.SmoothPixmapTransform
            )
            self._board = chess.Board()
            self._selected_square = None
            self._last_move = None
            self._player_color = chess.WHITE
            self._square_size = 64
            self._square_items = {}
            self._piece_items = {}
            self._selected_overlay = None
            self._last_move_overlays = []
            self._check_overlay = None
            self._hint_items = []
            self._active_anim = None
            self.setFixedSize(self._square_size * 8 + 2, self._square_size * 8 + 2)
            self.setFrameStyle(QtWidgets.QFrame.NoFrame)
            self._draw_board()
            self._sync_pieces()

        def board(self) -> chess.Board:
            return self._board

        def set_board(self, board: chess.Board) -> None:
            self._board = board
            self._selected_square = None
            self._last_move = None
            self._clear_overlays()
            self._clear_hints()
            self._sync_pieces()
            self.boardChanged.emit()

        def set_player_color(self, color: bool) -> None:
            self._player_color = color

        def set_check_square(self, square: int) -> None:
            self._clear_check()
            if square is None:
                return
            rect = QtCore.QRectF(
                chess.square_file(square) * self._square_size,
                (7 - chess.square_rank(square)) * self._square_size,
                self._square_size,
                self._square_size,
            )
            overlay = self._scene.addRect(
                rect,
                QtGui.QPen(QtCore.Qt.NoPen),
                QtGui.QBrush(QtGui.QColor(255, 0, 0, 120)),
            )
            overlay.setZValue(1)
            self._check_overlay = overlay

        def apply_uci(self, uci: str) -> bool:
            try:
                move = chess.Move.from_uci(uci)
            except ValueError:
                return False
            if move not in self._board.legal_moves:
                return False
            return self._apply_move(move)

        def mousePressEvent(self, event: QtCore.QEvent) -> None:
            if self._board.turn != self._player_color:
                return
            pos = self.mapToScene(event.pos())
            square = self._square_at(pos)
            if square is None:
                return

            if self._selected_square is None:
                piece = self._board.piece_at(square)
                if piece and piece.color == self._board.turn:
                    self._selected_square = square
                    self._highlight_selected(square)
                    self._show_legal_moves(square)
                else:
                    self._clear_hints()
                return

            move = chess.Move(self._selected_square, square)
            if move not in self._board.legal_moves:
                move = chess.Move(self._selected_square, square, promotion=chess.QUEEN)

            if move in self._board.legal_moves:
                self._apply_move(move)
            self._selected_square = None
            self._clear_selected()
            self._clear_hints()

        def _apply_move(self, move: chess.Move) -> bool:
            from_square = move.from_square
            to_square = move.to_square
            piece_item = self._piece_items.get(from_square)
            if piece_item is None:
                return False

            captured = self._piece_items.get(to_square)
            if captured is not None:
                self._scene.removeItem(captured)
                del self._piece_items[to_square]

            is_castling = self._board.is_castling(move)
            is_en_passant = self._board.is_en_passant(move)
            self._board.push(move)
            self._last_move = move

            if is_castling or is_en_passant:
                self._sync_pieces()
                self._highlight_last_move(from_square, to_square)
                self.boardChanged.emit()
                return True

            del self._piece_items[from_square]
            self._piece_items[to_square] = piece_item

            moving_color = not self._board.turn
            if move.promotion:
                promoted = chess.Piece(move.promotion, moving_color)
                piece_item.setPixmap(self._piece_pixmap(promoted))

            start_pos = self._piece_pos(from_square)
            end_pos = self._piece_pos(to_square)
            self._animate_piece(piece_item, start_pos, end_pos)
            self._highlight_last_move(from_square, to_square)
            self._clear_hints()
            self.boardChanged.emit()
            return True

        def _draw_board(self) -> None:
            self._scene.clear()
            self._square_items.clear()
            light = QtGui.QColor("#D7D7D7")
            dark = QtGui.QColor("#4A4A4A")
            for rank in range(8):
                for file in range(8):
                    color = light if (rank + file) % 2 == 0 else dark
                    rect = QtCore.QRectF(
                        file * self._square_size,
                        (7 - rank) * self._square_size,
                        self._square_size,
                        self._square_size,
                    )
                    item = self._scene.addRect(
                        rect, QtGui.QPen(QtCore.Qt.NoPen), QtGui.QBrush(color)
                    )
                    square = chess.square(file, rank)
                    self._square_items[square] = item

        def _sync_pieces(self) -> None:
            for item in list(self._piece_items.values()):
                self._scene.removeItem(item)
            self._piece_items.clear()
            for square in chess.SQUARES:
                piece = self._board.piece_at(square)
                if piece is None:
                    continue
                pixmap = self._piece_pixmap(piece)
                item = QtWidgets.QGraphicsPixmapItem(pixmap)
                item.setZValue(2)
                item.setPos(self._piece_pos(square))
                self._scene.addItem(item)
                self._piece_items[square] = item
            if self._last_move is not None:
                self._highlight_last_move(
                    self._last_move.from_square, self._last_move.to_square
                )

        def _piece_pixmap(self, piece: chess.Piece) -> QtGui.QPixmap:
            size = self._square_size - 8
            svg = chess.svg.piece(piece, size=size)
            renderer = QtSvg.QSvgRenderer(QtCore.QByteArray(svg.encode("utf-8")))
            pixmap = QtGui.QPixmap(size, size)
            pixmap.fill(QtCore.Qt.transparent)
            painter = QtGui.QPainter(pixmap)
            renderer.render(painter)
            painter.end()
            return pixmap

        def _piece_pos(self, square: int) -> QtCore.QPointF:
            file = chess.square_file(square)
            rank = chess.square_rank(square)
            x = file * self._square_size + 4
            y = (7 - rank) * self._square_size + 4
            return QtCore.QPointF(x, y)

        def _square_at(self, pos: QtCore.QPointF) -> int:
            if pos.x() < 0 or pos.y() < 0:
                return None
            file = int(pos.x() // self._square_size)
            rank = 7 - int(pos.y() // self._square_size)
            if not (0 <= file <= 7 and 0 <= rank <= 7):
                return None
            return chess.square(file, rank)

        def _highlight_selected(self, square: int) -> None:
            self._clear_selected()
            rect = QtCore.QRectF(
                chess.square_file(square) * self._square_size,
                (7 - chess.square_rank(square)) * self._square_size,
                self._square_size,
                self._square_size,
            )
            overlay = self._scene.addRect(
                rect,
                QtGui.QPen(QtCore.Qt.NoPen),
                QtGui.QBrush(QtGui.QColor(0, 255, 204, 80)),
            )
            overlay.setZValue(1)
            self._selected_overlay = overlay

        def _highlight_last_move(self, from_square: int, to_square: int) -> None:
            self._clear_last_move()
            color = QtGui.QColor(0, 255, 204, 120)
            for square in (from_square, to_square):
                rect = QtCore.QRectF(
                    chess.square_file(square) * self._square_size,
                    (7 - chess.square_rank(square)) * self._square_size,
                    self._square_size,
                    self._square_size,
                )
                overlay = self._scene.addRect(
                    rect, QtGui.QPen(QtCore.Qt.NoPen), QtGui.QBrush(color)
                )
                overlay.setZValue(1)
                self._last_move_overlays.append(overlay)

        def _clear_selected(self) -> None:
            if self._selected_overlay is not None:
                self._scene.removeItem(self._selected_overlay)
                self._selected_overlay = None

        def _clear_last_move(self) -> None:
            for overlay in self._last_move_overlays:
                self._scene.removeItem(overlay)
            self._last_move_overlays = []

        def _clear_overlays(self) -> None:
            self._clear_selected()
            self._clear_last_move()
            self._clear_check()

        def _show_legal_moves(self, from_square: int) -> None:
            self._clear_hints()
            for move in self._board.legal_moves:
                if move.from_square != from_square:
                    continue
                to_square = move.to_square
                is_capture = self._board.is_capture(move)
                self._add_hint(to_square, is_capture)

        def _add_hint(self, square: int, is_capture: bool) -> None:
            center = self._square_center(square)
            radius = self._square_size * (0.26 if is_capture else 0.18)
            rect = QtCore.QRectF(
                center.x() - radius,
                center.y() - radius,
                radius * 2,
                radius * 2,
            )
            if is_capture:
                color = QtGui.QColor(160, 160, 160, 160)
                pen = QtGui.QPen(QtCore.Qt.NoPen)
                brush = QtGui.QBrush(color)
            else:
                color = QtGui.QColor(200, 200, 200, 120)
                pen = QtGui.QPen(QtCore.Qt.NoPen)
                brush = QtGui.QBrush(color)
            item = self._scene.addEllipse(rect, pen, brush)
            item.setZValue(1)
            self._hint_items.append(item)

        def _square_center(self, square: int) -> QtCore.QPointF:
            file = chess.square_file(square)
            rank = chess.square_rank(square)
            x = file * self._square_size + self._square_size / 2
            y = (7 - rank) * self._square_size + self._square_size / 2
            return QtCore.QPointF(x, y)

        def _clear_hints(self) -> None:
            for item in self._hint_items:
                self._scene.removeItem(item)
            self._hint_items = []

        def _clear_check(self) -> None:
            if self._check_overlay is not None:
                self._scene.removeItem(self._check_overlay)
                self._check_overlay = None

        def _animate_piece(
            self,
            item: QtWidgets.QGraphicsPixmapItem,
            start_pos: QtCore.QPointF,
            end_pos: QtCore.QPointF,
        ) -> None:
            item.setPos(start_pos)
            animation = QtCore.QVariantAnimation(self)
            animation.setDuration(220)
            animation.setStartValue(start_pos)
            animation.setEndValue(end_pos)
            animation.setEasingCurve(QtCore.QEasingCurve.InOutQuad)

            def _on_value_changed(value: QtCore.QPointF) -> None:
                item.setPos(value)

            animation.valueChanged.connect(_on_value_changed)
            self._active_anim = animation
            animation.start()

    class ChessGui(QtWidgets.QWidget):
        def __init__(self) -> None:
            super().__init__()
            self._db_envoy = DBEnvoy()
            self._analyzer = ChessAnalyzer(_load_stockfish_path())
            self._player_color = chess.WHITE
            self._ai_running = False
            self._game_over = False
            self._check_blink_timer = None
            self._check_blink_on = False
            self._init_ui()

        def _init_ui(self) -> None:
            self.setWindowTitle("西洋棋分析器")
            outer = QtWidgets.QHBoxLayout(self)

            self.board_widget = BoardWidget()
            self.board_widget.setMinimumSize(520, 520)
            self.board_widget.boardChanged.connect(self._refresh_fen)
            self.board_widget.boardChanged.connect(self._check_turn)
            self.board_widget.boardChanged.connect(self._check_game_status)
            self.board_widget.set_player_color(self._player_color)

            control_panel = QtWidgets.QVBoxLayout()

            self.fen_input = QtWidgets.QLineEdit()
            self.fen_input.setPlaceholderText("貼上 FEN 後點『載入 FEN』")

            load_button = QtWidgets.QPushButton("載入 FEN")
            load_button.clicked.connect(self._on_load_fen)

            reset_button = QtWidgets.QPushButton("重置棋盤")
            reset_button.clicked.connect(self._on_reset)

            self.fen_display = QtWidgets.QLineEdit()
            self.fen_display.setReadOnly(True)

            self.opponent_input = QtWidgets.QLineEdit()
            self.opponent_input.setPlaceholderText("對手 ID")

            self.game_input = QtWidgets.QLineEdit()
            self.game_input.setPlaceholderText("對局 ID（可留空）")

            self.depth_input = QtWidgets.QSpinBox()
            self.depth_input.setRange(1, 40)
            self.depth_input.setValue(15)

            self.output_label = QtWidgets.QLabel("就緒")
            self.output_label.setWordWrap(True)
            self._status_default_style = "color: #E0E0E0;"
            self.output_label.setStyleSheet(self._status_default_style)

            self.opening_label = QtWidgets.QLabel("")
            self.opening_label.setWordWrap(True)

            color_group = QtWidgets.QGroupBox("操作方")
            color_layout = QtWidgets.QVBoxLayout(color_group)
            self.white_radio = QtWidgets.QRadioButton("白棋")
            self.black_radio = QtWidgets.QRadioButton("黑棋")
            self.white_radio.setChecked(True)
            self.white_radio.toggled.connect(self._on_color_changed)
            self.black_radio.toggled.connect(self._on_color_changed)
            color_layout.addWidget(self.white_radio)
            color_layout.addWidget(self.black_radio)

            analyze_button = QtWidgets.QPushButton("分析")
            analyze_button.clicked.connect(self._on_analyze)

            control_panel.addWidget(QtWidgets.QLabel("FEN 輸入"))
            control_panel.addWidget(self.fen_input)
            control_panel.addWidget(load_button)
            control_panel.addWidget(reset_button)
            control_panel.addWidget(QtWidgets.QLabel("目前 FEN"))
            control_panel.addWidget(self.fen_display)
            control_panel.addWidget(self.opponent_input)
            control_panel.addWidget(self.game_input)
            control_panel.addWidget(QtWidgets.QLabel("深度"))
            control_panel.addWidget(self.depth_input)
            control_panel.addWidget(color_group)
            control_panel.addWidget(analyze_button)
            control_panel.addWidget(self.output_label)
            control_panel.addWidget(self.opening_label)
            control_panel.addStretch(1)

            self.eval_bar = EvalBar()

            outer.addWidget(self.board_widget, 2)
            outer.addWidget(self.eval_bar)
            outer.addLayout(control_panel, 1)

            self._apply_qss()
            self._refresh_fen()

        def _refresh_fen(self) -> None:
            fen = self.board_widget.board().fen()
            self.fen_display.setText(fen)

        def _on_load_fen(self) -> None:
            fen = self.fen_input.text().strip()
            if not fen:
                self.output_label.setText("請提供 FEN。")
                return
            try:
                board = chess.Board(fen)
            except ValueError:
                self.output_label.setText("FEN 格式無效。")
                return
            self.board_widget.set_board(board)
            self._refresh_fen()
            self.output_label.setText("FEN 已載入。")
            self._check_turn()

        def _on_reset(self) -> None:
            self._reset_game(show_message=True)

        def _on_color_changed(self) -> None:
            self._player_color = chess.WHITE if self.white_radio.isChecked() else chess.BLACK
            self.board_widget.set_player_color(self._player_color)
            self._check_turn()

        def _on_analyze(self) -> None:
            opponent_id = self.opponent_input.text().strip()
            game_id = self.game_input.text().strip() or None
            depth = self.depth_input.value()
            fen = self.board_widget.board().fen()

            if not opponent_id:
                self.output_label.setText("請提供對手 ID。")
                return

            cached = self._db_envoy.check_cache(fen)
            if cached:
                move, score = cached
                self.output_label.setText(f"快取最佳走法：{move}（分數：{score}）")
                self.eval_bar.set_score(score)
                return

            history = self._db_envoy.get_opponent_history(opponent_id)
            if history:
                top = history[0]
                self.output_label.setText(
                    f"對手常用走法：{top['move']}（{top['move_count']} 次）。"
                )

            opening = self._db_envoy.get_opponent_opening(opponent_id)
            if opening.get("move"):
                self.opening_label.setText(
                    f"提示：此對手習慣在目前局面下走 {opening['move']}。"
                )

            best_move, score = self._analyzer.get_best_move(fen, depth=depth)
            self._db_envoy.save_analysis(
                opponent_id, fen, best_move, score, game_id=game_id
            )
            self.output_label.setText(f"最佳走法：{best_move}（分數：{score}）")
            self.eval_bar.set_score(score)

        def _check_turn(self) -> None:
            if self._ai_running:
                return
            if self._game_over:
                return
            board = self.board_widget.board()
            self.board_widget.setEnabled(board.turn == self._player_color)
            if board.turn != self._player_color:
                QtCore.QTimer.singleShot(50, self._ai_turn)

        def _ai_turn(self) -> None:
            if self._ai_running:
                return
            if self._game_over:
                return
            opponent_id = self.opponent_input.text().strip()
            if not opponent_id:
                self.output_label.setText("請提供對手 ID。")
                return
            fen = self.board_widget.board().fen()
            depth = self.depth_input.value()
            self.output_label.setText("AI 計算中...")
            self._ai_running = True
            self.board_widget.setEnabled(False)
            self._ai_worker = AiWorker(self._analyzer, self._db_envoy, fen, depth, opponent_id)
            self._ai_worker.finished.connect(self._on_ai_finished)
            self._ai_worker.start()

        def _on_ai_finished(self, move: str, score: float, from_cache: bool) -> None:
            self._ai_running = False
            if self._game_over:
                return
            if not move:
                self.output_label.setText("AI 未能產生走法。")
                return
            if not self.board_widget.apply_uci(move):
                self.output_label.setText("AI 走法無效，請檢查棋盤狀態。")
                return
            self.eval_bar.set_score(score)
            if from_cache:
                self.output_label.setText(f"AI 使用快取：{move}（分數：{score}）。輪到你了")
            else:
                self.output_label.setText(f"AI 走法：{move}（分數：{score}）。輪到你了")
            self.board_widget.setEnabled(True)

        def _check_game_status(self) -> None:
            board = self.board_widget.board()
            if board.is_check():
                king_square = board.king(board.turn)
                self.board_widget.set_check_square(king_square)
            else:
                self.board_widget.set_check_square(None)

            if board.is_checkmate():
                self._game_over = True
                self._stop_check_blink()
                winner = "你贏了" if board.turn != self._player_color else "你輸了"
                self.output_label.setText(f"🏆 將殺！{winner}！")
                self._schedule_reset()
                return

            if board.is_stalemate():
                self._game_over = True
                self._stop_check_blink()
                self.output_label.setText("🏳️ 和棋！")
                self._schedule_reset()
                return

            if board.is_check():
                self._start_check_blink()
                self.output_label.setText("⚠️ 將軍中！")
            else:
                self._stop_check_blink()

        def _start_check_blink(self) -> None:
            if self._check_blink_timer is not None:
                return
            self._check_blink_timer = QtCore.QTimer(self)
            self._check_blink_timer.timeout.connect(self._toggle_check_blink)
            self._check_blink_timer.start(400)

        def _toggle_check_blink(self) -> None:
            self._check_blink_on = not self._check_blink_on
            if self._check_blink_on:
                self.output_label.setStyleSheet("color: #FF4D4D;")
            else:
                self.output_label.setStyleSheet(self._status_default_style)

        def _stop_check_blink(self) -> None:
            if self._check_blink_timer is None:
                return
            self._check_blink_timer.stop()
            self._check_blink_timer = None
            self._check_blink_on = False
            self.output_label.setStyleSheet(self._status_default_style)

        def _schedule_reset(self) -> None:
            QtCore.QTimer.singleShot(10000, self._reset_game)

        def _reset_game(self, show_message: bool = False) -> None:
            self._game_over = False
            self._ai_running = False
            self.board_widget.set_board(chess.Board())
            self._refresh_fen()
            self.eval_bar.set_score(0.0)
            if show_message:
                self.output_label.setText("棋盤已重置。")
            self._stop_check_blink()
            self._check_turn()

        def _apply_qss(self) -> None:
            self.setStyleSheet(
                """
                QWidget {
                    background-color: #121212;
                    color: #E0E0E0;
                    font-family: Consolas;
                }
                QGraphicsView {
                    background-color: #121212;
                    border: 1px solid #2A2A2A;
                }
                QLineEdit, QPlainTextEdit, QSpinBox {
                    background-color: #1E1E1E;
                    border: 1px solid #2A2A2A;
                    color: #E0E0E0;
                    padding: 6px;
                }
                QPushButton {
                    border: 1px solid #00FFCC;
                    color: #00FFCC;
                    padding: 8px 12px;
                    background-color: transparent;
                }
                QPushButton:hover {
                    background-color: #00FFCC;
                    color: #121212;
                }
                QGroupBox {
                    border: 1px solid #2A2A2A;
                    margin-top: 10px;
                }
                QGroupBox::title {
                    subcontrol-origin: margin;
                    subcontrol-position: top left;
                    padding: 0 6px;
                    color: #00FFCC;
                }
                QRadioButton {
                    padding: 4px;
                }
                """
            )

    app = QtWidgets.QApplication([])
    window = ChessGui()
    window.resize(980, 620)
    window.show()
    app.exec_()


def main() -> None:
    _load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--cli", action="store_true", help="Run in CLI mode")
    args = parser.parse_args()

    if args.cli:
        _run_cli()
    else:
        _run_gui()


if __name__ == "__main__":
    main()
