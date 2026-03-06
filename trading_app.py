import json
import queue
import threading as mt
import traceback
from collections import deque
from datetime import datetime

from greek_api_duplicate import GreekAPI

try:
    from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPointF, Qt, QTimer, Signal, QObject
    from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
    from PySide6.QtWidgets import (
        QApplication,
        QButtonGroup,
        QCheckBox,
        QFormLayout,
        QGroupBox,
        QHBoxLayout,
        QHeaderView,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMessageBox,
        QPlainTextEdit,
        QPushButton,
        QSplitter,
        QTableView,
        QTabWidget,
        QVBoxLayout,
        QWidget,
    )
except ImportError as exc:
    raise ImportError("PySide6 is required. Install with: pip install PySide6") from exc


def _to_float(value):
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_number(value, decimals=2):
    num = _to_float(value)
    if num is None:
        return "-" if value in (None, "") else str(value)
    return f"{num:,.{decimals}f}"


class SignalBus(QObject):
    log = Signal(str)
    error = Signal(str, str)
    status = Signal(str)
    account_text = Signal(str)
    clear_watch = Signal()
    remove_tokens = Signal(list)


class MarketTableModel(QAbstractTableModel):
    columns = ("token", "name", "ltp", "change", "ltt", "lut", "volume", "oi")

    def __init__(self):
        super().__init__()
        self._rows = []
        self._index = {}

    def rowCount(self, parent=QModelIndex()):
        if parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        if parent.isValid():
            return 0
        return len(self.columns)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None

        row = self._rows[index.row()]
        col = index.column()
        value = row[col]

        if role == Qt.DisplayRole:
            if col == 2:
                return _format_number(value, 2)
            if col == 3:
                change = _to_float(value)
                if change is None:
                    return "-"
                return f"{change:+,.2f}"
            if col in (6, 7):
                return _format_number(value, 0)
            return "-" if value in (None, "") else str(value)

        if role == Qt.TextAlignmentRole:
            if col == 1:
                return Qt.AlignLeft | Qt.AlignVCenter
            return Qt.AlignCenter

        if role == Qt.ForegroundRole and col in (2, 3):
            change = _to_float(row[3])
            if change is None:
                return QColor("#8b949e")
            if change > 0:
                return QColor("#22c55e")
            if change < 0:
                return QColor("#ef4444")
            return QColor("#c9d1d9")

        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            return self.columns[section].upper()
        return str(section + 1)

    def clear(self):
        self.beginResetModel()
        self._rows.clear()
        self._index.clear()
        self.endResetModel()

    def remove_tokens(self, tokens):
        token_set = {str(token) for token in tokens}
        if not token_set:
            return

        filtered = [row for row in self._rows if str(row[0]) not in token_set]
        self.beginResetModel()
        self._rows = filtered
        self._index = {str(row[0]): i for i, row in enumerate(self._rows)}
        self.endResetModel()

    def upsert_many(self, normalized_ticks):
        if not normalized_ticks:
            return

        changed_rows = []
        inserted_rows = []

        for token, tick in normalized_ticks.items():
            idx = self._index.get(token)
            if idx is None:
                ltp = _to_float(tick[2])
                new_row = [tick[0], tick[1], ltp if ltp is not None else tick[2], 0.0, tick[3], tick[4], tick[5], tick[6]]
                inserted_rows.append(new_row)
            else:
                row = self._rows[idx]
                prev_ltp = _to_float(row[2])
                new_ltp = _to_float(tick[2])
                if new_ltp is None:
                    new_ltp = prev_ltp
                if prev_ltp is None or new_ltp is None:
                    change = row[3]
                else:
                    change = new_ltp - prev_ltp

                row[0] = tick[0]
                row[1] = tick[1] if tick[1] not in (None, "") else row[1]
                row[2] = new_ltp if new_ltp is not None else tick[2]
                row[3] = change
                row[4] = tick[3]
                row[5] = tick[4]
                row[6] = tick[5]
                row[7] = tick[6]
                changed_rows.append(idx)

        if inserted_rows:
            start = len(self._rows)
            end = start + len(inserted_rows) - 1
            self.beginInsertRows(QModelIndex(), start, end)
            self._rows.extend(inserted_rows)
            self.endInsertRows()
            for i in range(start, end + 1):
                self._index[str(self._rows[i][0])] = i

        for row_idx in changed_rows:
            left = self.index(row_idx, 0)
            right = self.index(row_idx, len(self.columns) - 1)
            self.dataChanged.emit(left, right, [Qt.DisplayRole, Qt.ForegroundRole])

    def token_at_row(self, row_idx):
        if row_idx < 0 or row_idx >= len(self._rows):
            return None
        return str(self._rows[row_idx][0])

    def find_row_by_token(self, token):
        return self._index.get(str(token))

    def row_by_token(self, token):
        idx = self._index.get(str(token))
        if idx is None:
            return None
        return self._rows[idx]


class PriceChartWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumHeight(420)
        self._active_token = None
        self._history = deque(maxlen=320)
        self._timeframe = "5m"

    def set_active_token(self, token):
        token = str(token) if token else None
        if token != self._active_token:
            self._active_token = token
            self._history.clear()
            self.update()

    def set_timeframe(self, tf):
        self._timeframe = tf
        size_map = {"1m": 180, "5m": 320, "15m": 480, "1h": 650}
        max_points = size_map.get(tf, 320)
        self._history = deque(list(self._history)[-max_points:], maxlen=max_points)
        self.update()

    def push_price(self, token, ltp):
        if str(token) != str(self._active_token):
            return
        price = _to_float(ltp)
        if price is None:
            return
        self._history.append(price)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#0b1220"))

        margin_left = 58
        margin_right = 18
        margin_top = 18
        margin_bottom = 30

        w = max(1, self.width() - margin_left - margin_right)
        h = max(1, self.height() - margin_top - margin_bottom)
        left = margin_left
        top = margin_top
        right = left + w
        bottom = top + h

        grid_pen = QPen(QColor("#1f2a3a"), 1)
        painter.setPen(grid_pen)
        for i in range(6):
            y = top + i * (h / 5)
            painter.drawLine(left, int(y), right, int(y))
        for i in range(8):
            x = left + i * (w / 7)
            painter.drawLine(int(x), top, int(x), bottom)

        if not self._active_token:
            painter.setPen(QColor("#8b949e"))
            painter.drawText(self.rect(), Qt.AlignCenter, "Select a token from Watchlist to view chart")
            return

        if len(self._history) < 2:
            painter.setPen(QColor("#8b949e"))
            painter.drawText(self.rect(), Qt.AlignCenter, f"{self._active_token} waiting for live ticks...")
            return

        prices = list(self._history)
        min_price = min(prices)
        max_price = max(prices)
        if max_price <= min_price:
            max_price += 1.0
            min_price -= 1.0

        pad = (max_price - min_price) * 0.08
        max_price += pad
        min_price -= pad
        span = max_price - min_price

        poly = QPolygonF()
        for i, price in enumerate(prices):
            x = left + (i / (len(prices) - 1)) * w
            y = top + ((max_price - price) / span) * h
            poly.append(QPointF(x, y))

        line_pen = QPen(QColor("#22d3ee"), 2)
        painter.setPen(line_pen)
        painter.drawPolyline(poly)

        last = prices[-1]
        last_y = top + ((max_price - last) / span) * h
        painter.setPen(QPen(QColor("#f59e0b"), 1, Qt.DashLine))
        painter.drawLine(left, int(last_y), right, int(last_y))

        painter.setPen(QColor("#9fb3c8"))
        painter.drawText(10, top + 8, _format_number(max_price, 2))
        painter.drawText(10, bottom, _format_number(min_price, 2))

        painter.setPen(QColor("#d0e0f2"))
        painter.drawText(left, 16, f"{self._active_token}  |  {self._timeframe}")
        painter.drawText(right - 180, 16, f"Last: {_format_number(last, 2)}")

        painter.setPen(QColor("#334155"))
        painter.drawText(right - 84, bottom + 18, "GREEKVIEW")


class TradingWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("GreekView Pro Terminal")
        self.resize(1680, 980)

        self.bus = SignalBus()
        self.bus.log.connect(self._append_log)
        self.bus.error.connect(self._show_error)
        self.bus.status.connect(self._set_status)
        self.bus.account_text.connect(self.account_output_set_text)
        self.bus.clear_watch.connect(self._clear_watch_ui)
        self.bus.remove_tokens.connect(self.model_remove_tokens)

        self.model = MarketTableModel()
        self.api = None
        self.streaming_active = False
        self.stream_thread = None
        self.ui_queue = queue.Queue(maxsize=30000)
        self.current_tokens = set()
        self.active_token = None
        self._perf_counter = 0

        self._build_ui()

        self.ui_timer = QTimer(self)
        self.ui_timer.timeout.connect(self._drain_ui_queue)
        self.ui_timer.start(40)

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        base = QVBoxLayout(root)
        base.setContentsMargins(8, 8, 8, 8)
        base.setSpacing(8)

        base.addWidget(self._build_top_bar())

        vertical = QSplitter(Qt.Vertical)
        upper = QSplitter(Qt.Horizontal)

        upper.addWidget(self._build_watchlist_panel())
        upper.addWidget(self._build_chart_panel())
        upper.addWidget(self._build_trade_panel())
        upper.setSizes([430, 890, 360])

        vertical.addWidget(upper)
        vertical.addWidget(self._build_bottom_console())
        vertical.setSizes([730, 250])

        base.addWidget(vertical, 1)

        self.setStyleSheet(
            """
            QWidget {
                background-color: #0f1724;
                color: #d6deeb;
                font-size: 13px;
            }
            QGroupBox {
                background-color: #131d2b;
                border: 1px solid #223046;
                border-radius: 8px;
                margin-top: 10px;
                font-weight: 700;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 6px 0 6px;
                color: #9fb3c8;
            }
            QLineEdit, QPlainTextEdit {
                background-color: #0c1420;
                border: 1px solid #26374f;
                border-radius: 6px;
                color: #e2e8f0;
                padding: 6px;
                selection-background-color: #1d4ed8;
            }
            QTabWidget::pane {
                border: 1px solid #223046;
                background-color: #0f1724;
            }
            QTabBar::tab {
                background: #111a28;
                border: 1px solid #26374f;
                padding: 8px 12px;
                margin-right: 2px;
                color: #9fb3c8;
            }
            QTabBar::tab:selected {
                background: #1b2a3d;
                color: #e2e8f0;
            }
            QTableView {
                background-color: #0d1624;
                alternate-background-color: #0f1b2a;
                gridline-color: #223046;
                border: 1px solid #223046;
                selection-background-color: #1f3b62;
                selection-color: #e2e8f0;
            }
            QHeaderView::section {
                background-color: #132033;
                color: #9fb3c8;
                border: 1px solid #223046;
                padding: 6px;
                font-weight: 700;
            }
            QPushButton {
                background-color: #1a2638;
                border: 1px solid #31435d;
                border-radius: 6px;
                color: #dce6f2;
                font-weight: 600;
                padding: 7px 10px;
            }
            QPushButton:hover {
                background-color: #22334a;
            }
            QPushButton:pressed {
                background-color: #1a2a3f;
            }
            QPushButton#primary {
                background-color: #1d4ed8;
                border: 1px solid #2563eb;
                color: white;
            }
            QPushButton#primary:hover {
                background-color: #1e40af;
            }
            QPushButton#danger {
                background-color: #b91c1c;
                border: 1px solid #dc2626;
                color: white;
            }
            QPushButton#buy {
                background-color: #15803d;
                border: 1px solid #22c55e;
                color: white;
            }
            QPushButton#sell {
                background-color: #b91c1c;
                border: 1px solid #ef4444;
                color: white;
            }
            QPushButton[checked="true"] {
                background-color: #1d4ed8;
                border: 1px solid #2563eb;
                color: white;
            }
            """
        )

    def _build_top_bar(self):
        bar = QGroupBox("Workspace")
        row = QHBoxLayout(bar)

        logo = QLabel("GREEKVIEW PRO")
        logo.setStyleSheet("font-size: 19px; font-weight: 800; color: #dbeafe; letter-spacing: 1px;")
        row.addWidget(logo)

        self.status_label = QLabel("Disconnected")
        self.status_label.setStyleSheet(
            "padding: 4px 10px; border-radius: 10px; background: #3f1a1a; color: #fecaca; font-weight: 700;"
        )
        row.addWidget(self.status_label)

        row.addSpacing(16)
        row.addWidget(QLabel("Focus Token"))
        self.focus_token_input = QLineEdit()
        self.focus_token_input.setPlaceholderText("Type token and press Enter")
        self.focus_token_input.returnPressed.connect(self.focus_token_from_toolbar)
        self.focus_token_input.setFixedWidth(180)
        row.addWidget(self.focus_token_input)

        btn_focus = QPushButton("Go")
        btn_focus.clicked.connect(self.focus_token_from_toolbar)
        row.addWidget(btn_focus)

        row.addSpacing(12)
        self.btn_perf = QPushButton("Perf Stats")
        self.btn_perf.clicked.connect(self.show_perf_stats)
        row.addWidget(self.btn_perf)

        self.perf_badge = QLabel("ticks/s: -  backlog: -")
        self.perf_badge.setStyleSheet("color: #93c5fd; font-weight: 600;")
        row.addWidget(self.perf_badge)
        row.addStretch(1)
        return bar

    def _build_watchlist_panel(self):
        panel = QGroupBox("Watchlist")
        layout = QVBoxLayout(panel)

        self.token_input = QPlainTextEdit()
        self.token_input.setPlaceholderText("Enter tokens separated by comma/newline")
        self.token_input.setFixedHeight(90)
        layout.addWidget(self.token_input)

        controls = QHBoxLayout()
        self.btn_start_stream = QPushButton("Start Stream")
        self.btn_start_stream.setObjectName("primary")
        self.btn_start_stream.clicked.connect(self.start_stream)
        self.btn_subscribe = QPushButton("Subscribe")
        self.btn_subscribe.clicked.connect(self.subscribe_tokens)
        self.btn_unsubscribe = QPushButton("Unsubscribe Selected")
        self.btn_unsubscribe.clicked.connect(self.unsubscribe_selected)
        self.btn_clear = QPushButton("Clear")
        self.btn_clear.clicked.connect(self.clear_watch)

        controls.addWidget(self.btn_start_stream)
        controls.addWidget(self.btn_subscribe)
        controls.addWidget(self.btn_unsubscribe)
        controls.addWidget(self.btn_clear)
        layout.addLayout(controls)

        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableView.SelectRows)
        self.table.setSelectionMode(QTableView.ExtendedSelection)
        self.table.setSortingEnabled(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.clicked.connect(self._on_table_clicked)
        layout.addWidget(self.table, 1)

        return panel

    def _build_chart_panel(self):
        panel = QGroupBox("Chart")
        layout = QVBoxLayout(panel)

        top = QHBoxLayout()
        self.active_symbol_label = QLabel("Token: -")
        self.active_symbol_label.setStyleSheet("font-size: 15px; font-weight: 800; color: #dbeafe;")
        self.ltp_label = QLabel("LTP: -")
        self.chg_label = QLabel("Change: -")
        self.vol_label = QLabel("Volume: -")
        self.oi_label = QLabel("OI: -")
        self.time_label = QLabel("LUT: -")
        self.ltp_label.setStyleSheet("font-size: 15px; font-weight: 800; color: #fef08a;")
        self.chg_label.setStyleSheet("font-size: 14px; font-weight: 700; color: #cbd5e1;")

        for widget in (self.active_symbol_label, self.ltp_label, self.chg_label, self.vol_label, self.oi_label, self.time_label):
            top.addWidget(widget)
            top.addSpacing(10)

        top.addStretch(1)

        tf_group = QButtonGroup(self)
        for tf in ("1m", "5m", "15m", "1h"):
            btn = QPushButton(tf)
            btn.setCheckable(True)
            if tf == "5m":
                btn.setChecked(True)
            btn.clicked.connect(lambda checked, val=tf: self.set_timeframe(val))
            tf_group.addButton(btn)
            top.addWidget(btn)

        layout.addLayout(top)
        self.chart = PriceChartWidget()
        layout.addWidget(self.chart, 1)
        return panel

    def _build_trade_panel(self):
        container = QWidget()
        col = QVBoxLayout(container)
        col.setContentsMargins(0, 0, 0, 0)

        conn = QGroupBox("Connection")
        form = QFormLayout(conn)

        self.username = QLineEdit()
        self.session_pwd = QLineEdit()
        self.session_pwd.setEchoMode(QLineEdit.Password)
        self.user_pwd = QLineEdit()
        self.user_pwd.setEchoMode(QLineEdit.Password)
        self.procli = QLineEdit("0")
        self.ac_no = QLineEdit()
        self.rest_ip = QLineEdit("127.0.0.1")
        self.rest_port = QLineEdit("80")
        self.req_data = QLineEdit("ltp")
        self.ping_interval = QLineEdit("20")
        self.ping_timeout = QLineEdit("10")
        self.is_secure = QCheckBox("Secure")
        self.is_base64 = QCheckBox("Base64")
        self.iris = QCheckBox("IRIS")
        self.iris.setChecked(True)

        form.addRow("Username", self.username)
        form.addRow("Session Password", self.session_pwd)
        form.addRow("Trading Password", self.user_pwd)
        form.addRow("Pro Client", self.procli)
        form.addRow("Account Number", self.ac_no)
        form.addRow("REST IP", self.rest_ip)
        form.addRow("REST Port", self.rest_port)
        form.addRow("Req Data", self.req_data)
        form.addRow("Ping Interval", self.ping_interval)
        form.addRow("Ping Timeout", self.ping_timeout)
        form.addRow("", self.is_secure)
        form.addRow("", self.is_base64)
        form.addRow("", self.iris)

        btn_row = QHBoxLayout()
        btn_connect = QPushButton("Connect")
        btn_connect.setObjectName("primary")
        btn_connect.clicked.connect(self.connect_api)
        btn_disconnect = QPushButton("Disconnect")
        btn_disconnect.setObjectName("danger")
        btn_disconnect.clicked.connect(self.disconnect_api)
        btn_time = QPushButton("Server Time")
        btn_time.clicked.connect(self.fetch_server_time)
        btn_row.addWidget(btn_connect)
        btn_row.addWidget(btn_disconnect)
        btn_row.addWidget(btn_time)
        form.addRow(btn_row)

        col.addWidget(conn)

        ticket = QGroupBox("Order Ticket")
        order_form = QFormLayout(ticket)

        self.ord_token = QLineEdit()
        self.ord_symbol = QLineEdit()
        self.ord_lot = QLineEdit("1")
        self.ord_qty = QLineEdit("1")
        self.ord_price = QLineEdit("0")
        self.ord_buysell = QLineEdit("BUY")
        self.ord_type = QLineEdit("LIMIT")
        self.ord_trig = QLineEdit("0")
        self.ord_exchange = QLineEdit("NSE")
        self.ord_validity = QLineEdit("0")
        self.ord_strategy = QLineEdit("GreekViewPro")
        self.cancel_order_id = QLineEdit()

        order_form.addRow("Token", self.ord_token)
        order_form.addRow("Symbol", self.ord_symbol)
        order_form.addRow("Lot", self.ord_lot)
        order_form.addRow("Qty", self.ord_qty)
        order_form.addRow("Price", self.ord_price)
        order_form.addRow("Buy/Sell", self.ord_buysell)
        order_form.addRow("Order Type", self.ord_type)
        order_form.addRow("Trigger", self.ord_trig)
        order_form.addRow("Exchange", self.ord_exchange)
        order_form.addRow("Validity", self.ord_validity)
        order_form.addRow("Strategy", self.ord_strategy)

        action_row = QHBoxLayout()
        buy_btn = QPushButton("Quick BUY")
        buy_btn.setObjectName("buy")
        buy_btn.clicked.connect(lambda: self.place_order(side_override="BUY"))
        sell_btn = QPushButton("Quick SELL")
        sell_btn.setObjectName("sell")
        sell_btn.clicked.connect(lambda: self.place_order(side_override="SELL"))
        action_row.addWidget(buy_btn)
        action_row.addWidget(sell_btn)
        order_form.addRow(action_row)

        place_row = QHBoxLayout()
        place_btn = QPushButton("Place Order")
        place_btn.setObjectName("primary")
        place_btn.clicked.connect(self.place_order)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.cancel_order)
        self.cancel_order_id.setPlaceholderText("Order ID")
        place_row.addWidget(place_btn)
        place_row.addWidget(cancel_btn)
        order_form.addRow("Cancel ID", self.cancel_order_id)
        order_form.addRow(place_row)

        col.addWidget(ticket)
        col.addStretch(1)
        return container

    def _build_bottom_console(self):
        tabs = QTabWidget()

        account_tab = QWidget()
        account_layout = QVBoxLayout(account_tab)
        action_row = QHBoxLayout()
        for text, handler in (
            ("Orderbook All", self.fetch_orderbook_all),
            ("Orderbook Traded", self.fetch_orderbook_traded),
            ("Orderbook Pending", self.fetch_orderbook_pending),
            ("Net Positions", self.fetch_positions),
            ("Margin", self.fetch_margin),
            ("Holdings", self.fetch_holdings),
        ):
            btn = QPushButton(text)
            btn.clicked.connect(handler)
            action_row.addWidget(btn)
        action_row.addStretch(1)
        account_layout.addLayout(action_row)
        self.account_output = QPlainTextEdit()
        self.account_output.setReadOnly(True)
        account_layout.addWidget(self.account_output, 1)

        logs_tab = QWidget()
        logs_layout = QVBoxLayout(logs_tab)
        self.log_output = QPlainTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setStyleSheet("background-color: #0a111d; color: #9fd3ff;")
        logs_layout.addWidget(self.log_output, 1)

        tabs.addTab(account_tab, "Account")
        tabs.addTab(logs_tab, "Logs")
        return tabs

    def _append_log(self, message):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_output.appendPlainText(f"[{ts}] {message}")

    def _show_error(self, title, text):
        QMessageBox.critical(self, title, text)

    def _set_status(self, status):
        label = status.lower()
        if label.startswith("connected"):
            style = "padding: 4px 10px; border-radius: 10px; background: #133a2a; color: #86efac; font-weight: 700;"
        elif label.startswith("streaming"):
            style = "padding: 4px 10px; border-radius: 10px; background: #172554; color: #93c5fd; font-weight: 700;"
        else:
            style = "padding: 4px 10px; border-radius: 10px; background: #3f1a1a; color: #fecaca; font-weight: 700;"
        self.status_label.setStyleSheet(style)
        self.status_label.setText(status)

    def _clear_watch_ui(self):
        self.model.clear()
        self.chart.set_active_token(None)
        self.active_token = None
        self.current_tokens.clear()
        self._set_quote_strip(None)

    def model_remove_tokens(self, tokens):
        self.model.remove_tokens(tokens)
        if self.active_token and self.active_token in {str(token) for token in tokens}:
            self.active_token = None
            self.chart.set_active_token(None)
            self._set_quote_strip(None)

    def account_output_set_text(self, text):
        self.account_output.setPlainText(text)

    def _set_quote_strip(self, row):
        if not row:
            self.active_symbol_label.setText("Token: -")
            self.ltp_label.setText("LTP: -")
            self.chg_label.setText("Change: -")
            self.vol_label.setText("Volume: -")
            self.oi_label.setText("OI: -")
            self.time_label.setText("LUT: -")
            self.chg_label.setStyleSheet("font-size: 14px; font-weight: 700; color: #cbd5e1;")
            return

        token, name, ltp, change, _, lut, volume, oi = row
        self.active_symbol_label.setText(f"Token: {token}  |  {name or '-'}")
        self.ltp_label.setText(f"LTP: {_format_number(ltp, 2)}")
        chg = _to_float(change)
        if chg is None:
            self.chg_label.setText("Change: -")
            self.chg_label.setStyleSheet("font-size: 14px; font-weight: 700; color: #cbd5e1;")
        else:
            self.chg_label.setText(f"Change: {chg:+,.2f}")
            if chg > 0:
                self.chg_label.setStyleSheet("font-size: 14px; font-weight: 700; color: #22c55e;")
            elif chg < 0:
                self.chg_label.setStyleSheet("font-size: 14px; font-weight: 700; color: #ef4444;")
            else:
                self.chg_label.setStyleSheet("font-size: 14px; font-weight: 700; color: #cbd5e1;")

        self.vol_label.setText(f"Volume: {_format_number(volume, 0)}")
        self.oi_label.setText(f"OI: {_format_number(oi, 0)}")
        self.time_label.setText(f"LUT: {lut if lut not in (None, '') else '-'}")

    def _run_bg(self, fn, name):
        def runner():
            try:
                fn()
            except Exception as exc:
                tb = traceback.format_exc(limit=3)
                self.bus.log.emit(f"{name} failed: {exc}\n{tb}")
                self.bus.error.emit(f"{name} Error", str(exc))

        mt.Thread(target=runner, daemon=True).start()

    def _parse_tokens(self):
        raw = self.token_input.toPlainText().strip()
        if not raw:
            return []
        tokens = []
        for item in raw.replace("\n", ",").split(","):
            token = item.strip()
            if token:
                tokens.append(token)
        return list(dict.fromkeys(tokens))

    def _normalize_tick(self, tick):
        if isinstance(tick, dict):
            token = tick.get("symbol")
            if token is None:
                return None
            return (
                str(token),
                tick.get("name", ""),
                tick.get("ltp"),
                tick.get("ltt"),
                tick.get("lut"),
                tick.get("tot_vol"),
                tick.get("currentOI") or tick.get("oi"),
            )

        if not isinstance(tick, (list, tuple)) or len(tick) == 0:
            return None

        token = tick[0]
        if token is None:
            return None

        return (
            str(token),
            tick[1] if len(tick) > 1 else "",
            tick[2] if len(tick) > 2 else None,
            tick[3] if len(tick) > 3 else None,
            tick[4] if len(tick) > 4 else None,
            tick[5] if len(tick) > 5 else None,
            tick[6] if len(tick) > 6 else None,
        )

    def focus_token_from_toolbar(self):
        token = self.focus_token_input.text().strip()
        if not token:
            return
        row = self.model.find_row_by_token(token)
        if row is None:
            self.bus.log.emit(f"Token {token} not found in watchlist.")
            return
        self.table.selectRow(row)
        self.table.scrollTo(self.model.index(row, 0))
        self._activate_token(token)

    def _on_table_clicked(self, index):
        if not index.isValid():
            return
        token = self.model.token_at_row(index.row())
        if token:
            self._activate_token(token)

    def _activate_token(self, token):
        token = str(token)
        self.active_token = token
        self.focus_token_input.setText(token)
        self.chart.set_active_token(token)
        row = self.model.row_by_token(token)
        self._set_quote_strip(row)
        if row:
            self.ord_token.setText(str(row[0]))
            self.ord_symbol.setText(str(row[1] or ""))

    def set_timeframe(self, tf):
        self.chart.set_timeframe(tf)

    def connect_api(self):
        def task():
            self.api = GreekAPI(
                user=self.username.text().strip(),
                s_pwd=self.session_pwd.text().strip(),
                pwd=self.user_pwd.text().strip(),
                procli=self.procli.text().strip(),
                ac_no=self.ac_no.text().strip(),
                is_secure=self.is_secure.isChecked(),
                is_base_64=self.is_base64.isChecked(),
                rest_ip=self.rest_ip.text().strip(),
                rest_port=self.rest_port.text().strip(),
                iris=self.iris.isChecked(),
            )
            self.bus.status.emit("Connected")
            self.bus.log.emit("Connected successfully.")

        self._run_bg(task, "Connect")

    def disconnect_api(self):
        self.streaming_active = False

        def task():
            if self.api:
                try:
                    self.api.close_connection()
                except Exception as exc:
                    self.bus.log.emit(f"Disconnect warning: {exc}")
            self.api = None
            self.bus.clear_watch.emit()
            self.bus.status.emit("Disconnected")
            self.bus.log.emit("Disconnected.")

        self._run_bg(task, "Disconnect")

    def start_stream(self):
        if not self.api:
            self.bus.error.emit("Not Connected", "Connect first.")
            return

        tokens = self._parse_tokens()
        if not tokens:
            self.bus.error.emit("No Tokens", "Provide token list.")
            return

        self.streaming_active = True
        self.current_tokens = set(tokens)

        def task():
            self.api.start_apollo(
                token_list=tokens,
                req_data=self.req_data.text().strip() or "ltp",
                ping_interval=int(self.ping_interval.text().strip() or "20"),
                ping_timeout=int(self.ping_timeout.text().strip() or "10"),
            )
            self._start_stream_consumer()
            self.bus.status.emit("Streaming")
            self.bus.log.emit(f"Streaming started for {len(tokens)} tokens.")

        self._run_bg(task, "Start Stream")

    def _start_stream_consumer(self):
        if self.stream_thread and self.stream_thread.is_alive():
            return

        def consume():
            for batch in self.api.data_stream_batch(batch_size=500):
                if not self.streaming_active or not self.api:
                    return
                try:
                    self.ui_queue.put_nowait(batch)
                except queue.Full:
                    try:
                        self.ui_queue.get_nowait()
                        self.ui_queue.put_nowait(batch)
                    except Exception:
                        pass

        self.stream_thread = mt.Thread(target=consume, daemon=True)
        self.stream_thread.start()

    def subscribe_tokens(self):
        if not self.api:
            self.bus.error.emit("Not Connected", "Connect first.")
            return
        tokens = self._parse_tokens()
        if not tokens:
            self.bus.error.emit("No Tokens", "Provide token list.")
            return

        def task():
            self.api.subscribe_token(tokens)
            self.current_tokens.update(tokens)
            self.bus.log.emit(f"Subscribed {len(tokens)} token(s).")

        self._run_bg(task, "Subscribe")

    def unsubscribe_selected(self):
        if not self.api:
            return
        indexes = self.table.selectionModel().selectedRows()
        tokens = []
        for idx in indexes:
            token = self.model.token_at_row(idx.row())
            if token:
                tokens.append(token)
        if not tokens:
            self.bus.error.emit("No selection", "Select rows to unsubscribe.")
            return

        def task():
            for token in tokens:
                try:
                    self.api.unsubscribe_token(token)
                except Exception as exc:
                    self.bus.log.emit(f"Unsubscribe warning for {token}: {exc}")
                self.current_tokens.discard(token)
            self.bus.remove_tokens.emit(tokens)
            self.bus.log.emit(f"Unsubscribed {len(tokens)} token(s).")

        self._run_bg(task, "Unsubscribe")

    def clear_watch(self):
        self.current_tokens.clear()
        self.bus.clear_watch.emit()
        self.bus.log.emit("Watchlist cleared.")

    def _drain_ui_queue(self):
        latest = {}
        drained_batches = 0

        while drained_batches < 70:
            try:
                batch = self.ui_queue.get_nowait()
            except queue.Empty:
                break

            drained_batches += 1
            for tick in batch:
                normalized = self._normalize_tick(tick)
                if not normalized:
                    continue
                token = normalized[0]
                if token not in self.current_tokens:
                    continue
                latest[token] = normalized

        if not latest:
            return

        self.model.upsert_many(latest)

        if self.active_token:
            row = self.model.row_by_token(self.active_token)
            if row:
                self.chart.push_price(self.active_token, row[2])
                self._set_quote_strip(row)

        self._perf_counter += 1
        if self.api and self._perf_counter % 10 == 0:
            stats = self.api.get_performance_stats()
            tps = _format_number(stats.get("messages_per_second"), 0)
            backlog = _format_number(stats.get("raw_buffer_size"), 0)
            dropped = _format_number(stats.get("raw_messages_dropped"), 0)
            self.perf_badge.setText(f"ticks/s: {tps}  backlog: {backlog}  dropped: {dropped}")

    def place_order(self, side_override=None):
        if not self.api:
            self.bus.error.emit("Not Connected", "Connect first.")
            return

        def task():
            side = side_override if side_override else self.ord_buysell.text().strip()
            resp = self.api.place_order(
                tokenno=self.ord_token.text().strip(),
                symbol=self.ord_symbol.text().strip(),
                lot=self.ord_lot.text().strip(),
                qty=self.ord_qty.text().strip(),
                price=self.ord_price.text().strip(),
                buysell=side,
                ordtype=self.ord_type.text().strip(),
                trigprice=self.ord_trig.text().strip(),
                exchange=self.ord_exchange.text().strip(),
                validity=self.ord_validity.text().strip(),
                strategyname=self.ord_strategy.text().strip(),
            )
            self.bus.log.emit(f"Place order response: {resp}")

        self._run_bg(task, "Place Order")

    def cancel_order(self):
        if not self.api:
            self.bus.error.emit("Not Connected", "Connect first.")
            return
        order_id = self.cancel_order_id.text().strip()
        if not order_id:
            self.bus.error.emit("Missing Order ID", "Enter order ID to cancel.")
            return

        def task():
            self.api.cancel_order(order_id)
            self.bus.log.emit(f"Cancel order requested for {order_id}")

        self._run_bg(task, "Cancel Order")

    def fetch_server_time(self):
        if not self.api:
            self.bus.error.emit("Not Connected", "Connect first.")
            return

        def task():
            server_time = self.api.server_time()
            self.bus.log.emit(f"Server time: {server_time}")

        self._run_bg(task, "Server Time")

    def show_perf_stats(self):
        if not self.api:
            self.bus.error.emit("Not Connected", "Connect first.")
            return

        def task():
            stats = self.api.get_performance_stats()
            self.bus.log.emit(f"Performance stats: {json.dumps(stats, default=str)}")

        self._run_bg(task, "Performance Stats")

    def _fetch_account_view(self, title, fn):
        if not self.api:
            self.bus.error.emit("Not Connected", "Connect first.")
            return

        def task():
            data = fn()
            if isinstance(data, (dict, list)):
                content = json.dumps(data, indent=2, default=str)
            else:
                content = str(data)
            self.bus.account_text.emit(f"{title}\n{'=' * len(title)}\n{content}")
            self.bus.log.emit(f"{title} fetched.")

        self._run_bg(task, title)

    def fetch_orderbook_all(self):
        self._fetch_account_view("Orderbook All", lambda: self.api.Orderbook_All())

    def fetch_orderbook_traded(self):
        self._fetch_account_view("Orderbook Traded", lambda: self.api.Orderbook_Traded())

    def fetch_orderbook_pending(self):
        self._fetch_account_view("Orderbook Pending", lambda: self.api.all_pending_order())

    def fetch_positions(self):
        self._fetch_account_view("Net Positions", lambda: self.api.Net_Position_request())

    def fetch_margin(self):
        self._fetch_account_view("Margin", lambda: self.api.get_margin_details())

    def fetch_holdings(self):
        self._fetch_account_view("Holdings", lambda: self.api.get_holding_details())

    def closeEvent(self, event):
        self.streaming_active = False
        if self.api:
            try:
                self.api.close_connection()
            except Exception:
                pass
        super().closeEvent(event)


def main():
    app = QApplication([])
    window = TradingWindow()
    window.show()
    app.exec()


if __name__ == "__main__":
    main()

