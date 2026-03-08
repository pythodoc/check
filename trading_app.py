import json
import queue
import threading as mt
import traceback
from collections import deque
from datetime import datetime, timedelta, timezone

from greek_api_duplicate import GreekAPI
import pandas as pd

try:
    from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPointF, Qt, QTimer, Signal, QObject
    from PySide6.QtGui import QColor, QKeySequence, QPainter, QPen, QPolygonF, QShortcut
    from PySide6.QtWidgets import (
        QApplication,
        QButtonGroup,
        QCheckBox,
        QComboBox,
        QDialog,
        QDialogButtonBox,
        QFormLayout,
        QGroupBox,
        QHBoxLayout,
        QHeaderView,
        QInputDialog,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMessageBox,
        QPlainTextEdit,
        QPushButton,
        QSplitter,
        QTableView,
        QVBoxLayout,
        QWidget,
    )
    try:
        from PySide6.QtWebEngineWidgets import QWebEngineView
        HAS_WEBENGINE = True
    except ImportError:
        QWebEngineView = None
        HAS_WEBENGINE = False
    try:
        from lightweight_charts.widgets import QtChart as LWQtChart
        HAS_LW_PY = True
    except Exception:
        LWQtChart = None
        HAS_LW_PY = False
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


def _format_ws_time(value):
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 10_000_000_000:
            ts = ts / 1000.0
        try:
            return datetime.fromtimestamp(ts, tz=IST).strftime("%d-%m-%Y %H:%M:%S")
        except Exception:
            return str(value)
    text = str(value).strip()
    if text.isdigit():
        ts = float(text)
        if ts > 10_000_000_000:
            ts = ts / 1000.0
        try:
            return datetime.fromtimestamp(ts, tz=IST).strftime("%d-%m-%Y %H:%M:%S")
        except Exception:
            return text
    return text


IST = timezone(timedelta(hours=5, minutes=30))


def _to_epoch_seconds_ist(value):
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 10_000_000_000:
            ts = ts / 1000.0
        return int(ts)
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        ts = float(text)
        if ts > 10_000_000_000:
            ts = ts / 1000.0
        return int(ts)

    for fmt in ("%d-%m-%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%d/%m/%Y %H:%M:%S"):
        try:
            dt = datetime.strptime(text, fmt).replace(tzinfo=IST)
            return int(dt.timestamp())
        except Exception:
            pass

    iso = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=IST)
        else:
            dt = dt.astimezone(IST)
        return int(dt.timestamp())
    except Exception:
        return None


class SignalBus(QObject):
    log = Signal(str)
    error = Signal(str, str)
    status = Signal(str)
    login_state = Signal(bool)
    session_info = Signal(str)
    ohlc_series = Signal(str, list, list, list)
    account_table = Signal(str, list, list)
    refresh_orderbook = Signal()
    account_text = Signal(str)
    clear_watch = Signal()
    remove_tokens = Signal(list)
    contract_ready = Signal()
    strategy_rows = Signal(list)


class MarketTableModel(QAbstractTableModel):
    default_columns = [
        "symbol",
        "name",
        "ltp",
        "change",
        "ltt",
        "lut",
        "tot_vol",
        "oi",
        "open",
        "high",
        "low",
        "close",
        "p_change",
        "bid",
        "ask",
        "bidqty",
        "askqty",
        "tot_buyQty",
        "tot_sellQty",
        "ltq",
        "exch",
        "asset_type",
        "atp",
        "taq",
        "tbq",
        "h52w",
        "l52w",
    ]

    def __init__(self):
        super().__init__()
        self._columns = list(self.default_columns)
        self._rows = []
        self._index = {}

    def rowCount(self, parent=QModelIndex()):
        if parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        if parent.isValid():
            return 0
        return len(self._columns)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None

        row = self._rows[index.row()]
        col = index.column()
        key = self._columns[col]
        value = row.get(key)

        if role == Qt.DisplayRole:
            if key in ("ltp", "open", "high", "low", "close", "bid", "ask", "atp", "h52w", "l52w"):
                return _format_number(value, 2)
            if key in ("change", "p_change"):
                change = _to_float(value)
                if change is None:
                    return "-"
                return f"{change:+,.2f}"
            if key in ("tot_vol", "oi", "bidqty", "askqty", "tot_buyQty", "tot_sellQty", "ltq", "taq", "tbq"):
                return _format_number(value, 0)
            return "-" if value in (None, "") else str(value)

        if role == Qt.TextAlignmentRole:
            if key in ("name", "ltt", "lut", "exch", "asset_type"):
                return Qt.AlignLeft | Qt.AlignVCenter
            return Qt.AlignCenter

        if role == Qt.ForegroundRole and key in ("ltp", "change", "p_change"):
            change = _to_float(row.get("change"))
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
            return self._columns[section].upper()
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

        filtered = [row for row in self._rows if str(row.get("symbol")) not in token_set]
        self.beginResetModel()
        self._rows = filtered
        self._index = {str(row.get("symbol")): i for i, row in enumerate(self._rows)}
        self.endResetModel()

    def _ensure_columns(self, keys):
        existing = set(self._columns)
        new_cols = [k for k in keys if k not in existing]
        if not new_cols:
            return
        self.beginResetModel()
        self._columns.extend(new_cols)
        self.endResetModel()

    def upsert_many(self, normalized_ticks):
        if not normalized_ticks:
            return

        dynamic_keys = set()
        for tick in normalized_ticks.values():
            if isinstance(tick, dict):
                dynamic_keys.update(tick.keys())
        dynamic_keys.discard("level2")
        self._ensure_columns(dynamic_keys)

        changed_rows = []
        inserted_rows = []

        for token, tick in normalized_ticks.items():
            idx = self._index.get(token)
            if idx is None:
                new_row = dict(tick)
                new_row["symbol"] = str(token)
                ltp = _to_float(new_row.get("ltp"))
                if ltp is not None:
                    new_row["ltp"] = ltp
                if "oi" not in new_row and "currentOI" in new_row:
                    new_row["oi"] = new_row.get("currentOI")
                new_row["change"] = _to_float(new_row.get("change")) or 0.0
                inserted_rows.append(new_row)
            else:
                row = self._rows[idx]
                prev_ltp = _to_float(row.get("ltp"))
                new_ltp = _to_float(tick.get("ltp"))
                if new_ltp is None:
                    new_ltp = prev_ltp
                if prev_ltp is None or new_ltp is None:
                    change = _to_float(row.get("change")) or 0.0
                else:
                    change = new_ltp - prev_ltp

                row.update(tick)
                row["symbol"] = str(token)
                if row.get("name") in (None, ""):
                    row["name"] = tick.get("name", "")
                if new_ltp is not None:
                    row["ltp"] = new_ltp
                if "oi" not in row and "currentOI" in row:
                    row["oi"] = row.get("currentOI")
                row["change"] = change
                changed_rows.append(idx)

        if inserted_rows:
            start = len(self._rows)
            end = start + len(inserted_rows) - 1
            self.beginInsertRows(QModelIndex(), start, end)
            self._rows.extend(inserted_rows)
            self.endInsertRows()
            for i in range(start, end + 1):
                self._index[str(self._rows[i].get("symbol"))] = i

        for row_idx in changed_rows:
            left = self.index(row_idx, 0)
            right = self.index(row_idx, len(self._columns) - 1)
            self.dataChanged.emit(left, right, [Qt.DisplayRole, Qt.ForegroundRole])

    def token_at_row(self, row_idx):
        if row_idx < 0 or row_idx >= len(self._rows):
            return None
        return str(self._rows[row_idx].get("symbol"))

    def find_row_by_token(self, token):
        return self._index.get(str(token))

    def row_by_token(self, token):
        idx = self._index.get(str(token))
        if idx is None:
            return None
        return self._rows[idx]


class AccountTableModel(QAbstractTableModel):
    def __init__(self):
        super().__init__()
        self._columns = []
        self._rows = []

    def rowCount(self, parent=QModelIndex()):
        if parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        if parent.isValid():
            return 0
        return len(self._columns)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        if role in (Qt.DisplayRole, Qt.EditRole):
            value = self._rows[index.row()].get(self._columns[index.column()])
            return "-" if value in (None, "") else str(value)
        if role == Qt.TextAlignmentRole:
            return Qt.AlignCenter
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            if section < len(self._columns):
                return str(self._columns[section]).upper()
            return ""
        return str(section + 1)

    def clear(self):
        self.beginResetModel()
        self._columns = []
        self._rows = []
        self.endResetModel()

    def set_records(self, columns, rows):
        self.beginResetModel()
        self._columns = list(columns)
        self._rows = list(rows)
        self.endResetModel()

    def row_at(self, row_idx):
        if row_idx < 0 or row_idx >= len(self._rows):
            return None
        return self._rows[row_idx]


class PriceChartWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumHeight(420)
        self._active_token = None
        self._history = deque(maxlen=320)  # close prices for line mode
        self._candles = deque(maxlen=320)  # tuples: (open, high, low, close)
        self._times = deque(maxlen=320)    # display timestamps aligned with points
        self._timeframe = "5m"
        self._chart_type = "line"

    def set_active_token(self, token):
        token = str(token) if token else None
        if token != self._active_token:
            self._active_token = token
            self._history.clear()
            self._candles.clear()
            self._times.clear()
            self.update()

    def set_timeframe(self, tf):
        self._timeframe = tf
        size_map = {"1m": 180, "5m": 320, "15m": 480, "1h": 650}
        max_points = size_map.get(tf, 320)
        self._history = deque(list(self._history)[-max_points:], maxlen=max_points)
        self._candles = deque(list(self._candles)[-max_points:], maxlen=max_points)
        self._times = deque(list(self._times)[-max_points:], maxlen=max_points)
        self.update()

    def set_chart_type(self, chart_type):
        self._chart_type = "candlestick" if chart_type == "candlestick" else "line"
        self.update()

    def push_price(self, token, ltp, ts=None):
        if str(token) != str(self._active_token):
            return
        price = _to_float(ltp)
        if price is None:
            return
        self._history.append(price)
        self._candles.append((price, price, price, price))
        self._times.append(str(ts or ""))
        self.update()

    def set_history(self, token, prices, candles=None, times=None):
        if str(token) != str(self._active_token):
            return
        normalized = []
        for value in prices:
            num = _to_float(value)
            if num is not None:
                normalized.append(num)
        self._history.clear()
        if normalized:
            self._history.extend(normalized[-self._history.maxlen :])

        self._candles.clear()
        if candles:
            valid = []
            for item in candles:
                if not isinstance(item, (list, tuple)) or len(item) < 4:
                    continue
                opn = _to_float(item[0])
                high = _to_float(item[1])
                low = _to_float(item[2])
                close = _to_float(item[3])
                if None in (opn, high, low, close):
                    continue
                valid.append((opn, high, low, close))
            self._candles.extend(valid[-self._candles.maxlen :])

        self._times.clear()
        if isinstance(times, list) and times:
            cleaned = [str(t) if t is not None else "" for t in times]
            self._times.extend(cleaned[-self._times.maxlen :])
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

        prices = list(self._history)
        candles = list(self._candles)
        times = list(self._times)
        has_line = len(prices) >= 2
        has_candle = len(candles) >= 2
        use_candle = self._chart_type == "candlestick"

        if (use_candle and not has_candle) and not has_line:
            painter.setPen(QColor("#8b949e"))
            painter.drawText(self.rect(), Qt.AlignCenter, f"{self._active_token} waiting for live ticks...")
            return

        if (not use_candle) and not has_line:
            painter.setPen(QColor("#8b949e"))
            painter.drawText(self.rect(), Qt.AlignCenter, f"{self._active_token} waiting for live ticks...")
            return

        if use_candle and has_candle:
            min_price = min(c[2] for c in candles)
            max_price = max(c[1] for c in candles)
        else:
            min_price = min(prices)
            max_price = max(prices)
        if max_price <= min_price:
            max_price += 1.0
            min_price -= 1.0

        pad = (max_price - min_price) * 0.08
        max_price += pad
        min_price -= pad
        span = max_price - min_price

        if use_candle and has_candle:
            count = len(candles)
            step = w / max(1, count)
            body_width = max(3.0, min(16.0, step * 0.7))
            for i, (opn, high, low, close) in enumerate(candles):
                x_center = left + (i + 0.5) * step
                y_high = top + ((max_price - high) / span) * h
                y_low = top + ((max_price - low) / span) * h
                y_open = top + ((max_price - opn) / span) * h
                y_close = top + ((max_price - close) / span) * h

                up = close >= opn
                body_color = QColor("#22c55e" if up else "#ef4444")
                wick_pen = QPen(body_color, 1)
                painter.setPen(wick_pen)
                painter.drawLine(int(x_center), int(y_high), int(x_center), int(y_low))

                body_top = min(y_open, y_close)
                body_height = max(1.0, abs(y_close - y_open))
                painter.fillRect(int(x_center - body_width / 2), int(body_top), int(body_width), int(body_height), body_color)
            last = candles[-1][3]
        else:
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

        # Render simple time axis labels when timestamps are available.
        if times:
            t_first = times[0] if len(times) > 0 else ""
            t_mid = times[len(times) // 2] if len(times) > 2 else ""
            t_last = times[-1] if len(times) > 1 else ""
            painter.setPen(QColor("#93a4b8"))
            if t_first:
                painter.drawText(left, bottom + 18, t_first)
            if t_mid:
                painter.drawText(int((left + right) / 2) - 70, bottom + 18, t_mid)
            if t_last:
                painter.drawText(right - 140, bottom + 18, t_last)

        painter.setPen(QColor("#334155"))
        painter.drawText(right - 84, bottom + 34, "GREEKVIEW")


class LightweightChartWidget(QWidget):
    def __init__(self):
        super().__init__()
        self._active_token = None
        self._timeframe = "5m"
        self._chart_type = "line"
        self._loaded = False
        self._last_history = None
        self._fallback = None
        self._view = None
        self._qt_chart = None
        self._qt_line = None
        self._qt_has_data = False
        self._qt_last_prices = []
        self._qt_last_candles = []
        self._qt_last_times = []
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)

        if HAS_LW_PY:
            try:
                self._qt_chart = LWQtChart(self)
                self._layout.addWidget(self._qt_chart.webview, 1)
                self._qt_chart.time_scale(time_visible=True, seconds_visible=True)
                self._qt_has_data = False
                return
            except Exception:
                self._qt_chart = None

        if not HAS_WEBENGINE:
            self._fallback = PriceChartWidget()
            self._layout.addWidget(self._fallback, 1)
            return

        self._view = QWebEngineView()
        self._view.loadFinished.connect(self._on_loaded)
        self._view.setHtml(
            """
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <style>
    html, body, #chart { width: 100%; height: 100%; margin: 0; background: #0b1220; color: #cbd5e1; }
    #msg { position: absolute; inset: 0; display: none; align-items: center; justify-content: center; color: #94a3b8; }
  </style>
  <script src="https://unpkg.com/lightweight-charts/dist/lightweight-charts.standalone.production.js"></script>
</head>
<body>
  <div id="chart"></div>
  <div id="msg">Lightweight Charts JS not loaded. Falling back...</div>
  <script>
    const root = document.getElementById('chart');
    const msg = document.getElementById('msg');
    if (typeof LightweightCharts === 'undefined') {
      window.__lw_ready = false;
      msg.style.display = 'flex';
    } else {
      window.__lw_ready = true;
    }
    if (window.__lw_ready) {
    const chart = LightweightCharts.createChart(root, {
      layout: { background: { color: '#0b1220' }, textColor: '#cbd5e1' },
      grid: { vertLines: { color: '#1f2a3a' }, horzLines: { color: '#1f2a3a' } },
      rightPriceScale: { borderColor: '#334155' },
      timeScale: {
        borderColor: '#334155',
        timeVisible: true,
        secondsVisible: true
      },
      localization: {
        timeFormatter: (time) => {
          const d = new Date((Number(time) || 0) * 1000);
          return new Intl.DateTimeFormat('en-IN', {
            timeZone: 'Asia/Kolkata',
            year: 'numeric',
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit',
            hour12: false
          }).format(d);
        }
      }
    });

    const lineSeries = chart.addLineSeries({ color: '#22d3ee', lineWidth: 2 });
    const candleSeries = chart.addCandlestickSeries({
      upColor: '#22c55e',
      downColor: '#ef4444',
      borderVisible: false,
      wickUpColor: '#22c55e',
      wickDownColor: '#ef4444',
    });

    window.__mode = 'line';
    window.setMode = function(mode) {
      window.__mode = (mode === 'candlestick') ? 'candlestick' : 'line';
      if (window.__mode === 'candlestick') {
        lineSeries.applyOptions({ visible: false });
        candleSeries.applyOptions({ visible: true });
      } else {
        lineSeries.applyOptions({ visible: true });
        candleSeries.applyOptions({ visible: false });
      }
    };

    window.setSeriesData = function(payload) {
      lineSeries.setData(payload.line || []);
      candleSeries.setData(payload.candles || []);
      chart.timeScale().fitContent();
      window.setMode(window.__mode);
    };

    window.updateLive = function(point) {
      if (point.line) lineSeries.update(point.line);
      if (point.candle) candleSeries.update(point.candle);
    };

    new ResizeObserver(() => {
      chart.applyOptions({ width: root.clientWidth, height: root.clientHeight });
    }).observe(root);
    }
  </script>
</body>
</html>
            """
        )
        self._layout.addWidget(self._view, 1)

    def _on_loaded(self, ok):
        self._loaded = bool(ok)
        if not self._loaded:
            self._switch_to_fallback()
            return
        self._view.page().runJavaScript("window.__lw_ready === true", self._after_ready_check)

    def _after_ready_check(self, ready):
        if ready is not True:
            self._switch_to_fallback()
            return
        self._run_js(f"window.setMode('{self._chart_type}')")
        if self._last_history:
            self._run_js(f"window.setSeriesData({json.dumps(self._last_history)})")

    def _switch_to_fallback(self):
        if self._fallback is not None:
            return
        self._fallback = PriceChartWidget()
        self._fallback.set_timeframe(self._timeframe)
        self._fallback.set_chart_type(self._chart_type)
        self._fallback.set_active_token(self._active_token)
        if self._view is not None:
            self._view.setParent(None)
        self._layout.addWidget(self._fallback, 1)
        if self._last_history and self._active_token:
            self._fallback.set_history(
                self._active_token,
                [point.get("value") for point in self._last_history.get("line", [])],
                [(c.get("open"), c.get("high"), c.get("low"), c.get("close")) for c in self._last_history.get("candles", [])],
                [str(p.get("time", "")) for p in self._last_history.get("line", [])],
            )

    def _run_js(self, script):
        if HAS_WEBENGINE and self._loaded:
            self._view.page().runJavaScript(script)

    def set_active_token(self, token):
        token = str(token) if token else None
        if token != self._active_token:
            self._active_token = token
            self._qt_has_data = False
            if self._qt_chart:
                try:
                    self._qt_chart.set(pd.DataFrame(columns=["time", "open", "high", "low", "close"]))
                    if self._qt_line:
                        self._qt_line.set(pd.DataFrame(columns=["time", "line"]))
                except Exception:
                    pass
            if self._fallback:
                self._fallback.set_active_token(token)
            else:
                self._last_history = {"line": [], "candles": []}
                self._run_js("window.setSeriesData({line: [], candles: []})")

    def set_timeframe(self, tf):
        self._timeframe = tf
        if self._qt_chart:
            try:
                self._qt_chart.time_scale(time_visible=True, seconds_visible=True)
            except Exception:
                pass
        if self._fallback:
            self._fallback.set_timeframe(tf)

    def set_chart_type(self, chart_type):
        self._chart_type = "candlestick" if chart_type == "candlestick" else "line"
        if self._qt_chart:
            if self._active_token and (self._qt_last_prices or self._qt_last_candles):
                self.set_history(self._active_token, self._qt_last_prices, self._qt_last_candles, self._qt_last_times)
            return
        if self._fallback:
            self._fallback.set_chart_type(self._chart_type)
        else:
            self._run_js(f"window.setMode('{self._chart_type}')")

    def set_history(self, token, prices, candles=None, times=None):
        if str(token) != str(self._active_token):
            return
        self._qt_last_prices = list(prices or [])
        self._qt_last_candles = list(candles or [])
        self._qt_last_times = list(times or [])
        if self._qt_chart:
            ts_list = times if isinstance(times, list) else []
            step_map = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600}
            step = step_map.get(self._timeframe, 300)
            base_epoch = int(datetime.now(tz=IST).timestamp())
            count = max(len(prices or []), len(candles or []), 1)
            fallback_start = base_epoch - step * count

            candle_rows = []
            if candles:
                for i, c in enumerate(candles):
                    if not isinstance(c, (list, tuple)) or len(c) < 4:
                        continue
                    t = _to_epoch_seconds_ist(ts_list[i] if i < len(ts_list) else None)
                    if t is None:
                        t = fallback_start + (i + 1) * step
                    candle_rows.append(
                        {
                            "time": datetime.fromtimestamp(int(t), tz=IST).replace(tzinfo=None),
                            "open": float(c[0]),
                            "high": float(c[1]),
                            "low": float(c[2]),
                            "close": float(c[3]),
                        }
                    )

            line_rows = []
            for i, p in enumerate(prices or []):
                v = _to_float(p)
                if v is None:
                    continue
                t = _to_epoch_seconds_ist(ts_list[i] if i < len(ts_list) else None)
                if t is None:
                    t = fallback_start + (i + 1) * step
                line_rows.append(
                    {"time": datetime.fromtimestamp(int(t), tz=IST).replace(tzinfo=None), "line": float(v)}
                )

            try:
                if self._chart_type == "candlestick":
                    df_c = pd.DataFrame(candle_rows, columns=["time", "open", "high", "low", "close"])
                    self._qt_chart.set(df_c)
                    if self._qt_line:
                        self._qt_line.set(pd.DataFrame(columns=["time", "line"]))
                else:
                    # Keep candles empty for pure line mode.
                    self._qt_chart.set(pd.DataFrame(columns=["time", "open", "high", "low", "close"]))
                    if self._qt_line is None:
                        self._qt_line = self._qt_chart.create_line("line")
                    df_l = pd.DataFrame(line_rows, columns=["time", "line"])
                    self._qt_line.set(df_l)
                self._qt_has_data = True
            except Exception:
                pass
            return
        if self._fallback:
            self._fallback.set_history(token, prices, candles, times)
            return

        line_data = []
        candle_data = []
        ts_list = times if isinstance(times, list) else []
        step_map = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600}
        step = step_map.get(self._timeframe, 300)
        base_epoch = int(datetime.now(tz=IST).timestamp())
        fallback_start = base_epoch - step * max(len(prices or []), len(candles or []), 1)
        if candles:
            for i, c in enumerate(candles):
                if not isinstance(c, (list, tuple)) or len(c) < 4:
                    continue
                t = _to_epoch_seconds_ist(ts_list[i] if i < len(ts_list) else None)
                if t is None:
                    t = fallback_start + (i + 1) * step
                candle_data.append({"time": int(t), "open": float(c[0]), "high": float(c[1]), "low": float(c[2]), "close": float(c[3])})
        for i, p in enumerate(prices or []):
            v = _to_float(p)
            if v is None:
                continue
            t = _to_epoch_seconds_ist(ts_list[i] if i < len(ts_list) else None)
            if t is None:
                t = fallback_start + (i + 1) * step
            line_data.append({"time": int(t), "value": float(v)})

        self._last_history = {"line": line_data, "candles": candle_data}
        self._run_js(f"window.setSeriesData({json.dumps(self._last_history)})")

    def push_price(self, token, ltp, ts=None):
        if str(token) != str(self._active_token):
            return
        if self._qt_chart:
            value = _to_float(ltp)
            t = _to_epoch_seconds_ist(ts)
            if value is None:
                return
            if t is None:
                t = int(datetime.now(tz=IST).timestamp())
            dt = datetime.fromtimestamp(int(t), tz=IST).replace(tzinfo=None)
            try:
                if self._chart_type == "candlestick":
                    if not self._qt_has_data:
                        seed = pd.DataFrame([{"time": dt, "open": value, "high": value, "low": value, "close": value}])
                        self._qt_chart.set(seed)
                        self._qt_has_data = True
                    else:
                        self._qt_chart.update_from_tick(pd.Series({"time": dt, "price": value}))
                else:
                    if self._qt_line is None:
                        self._qt_line = self._qt_chart.create_line("line")
                    if not self._qt_has_data:
                        self._qt_line.set(pd.DataFrame([{"time": dt, "line": value}]))
                        self._qt_has_data = True
                    else:
                        self._qt_line.update(pd.Series({"time": dt, "line": value}))
            except Exception:
                pass
            return
        if self._fallback:
            self._fallback.push_price(token, ltp, ts)
            return
        value = _to_float(ltp)
        t = _to_epoch_seconds_ist(ts)
        if value is None or t is None:
            return
        point = {
            "line": {"time": int(t), "value": float(value)},
            "candle": {"time": int(t), "open": float(value), "high": float(value), "low": float(value), "close": float(value)},
        }
        self._run_js(f"window.updateLive({json.dumps(point)})")


class LoginDialog(QDialog):
    def __init__(self, parent=None, defaults=None):
        super().__init__(parent)
        self.setWindowTitle("Login")
        self.setModal(True)
        self.setMinimumWidth(440)
        cfg = defaults or {}

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.username = QLineEdit(cfg.get("user", ""))
        self.session_pwd = QLineEdit(cfg.get("s_pwd", ""))
        self.session_pwd.setEchoMode(QLineEdit.Password)
        self.user_pwd = QLineEdit(cfg.get("pwd", ""))
        self.user_pwd.setEchoMode(QLineEdit.Password)
        self.user_pwd.setMaxLength(12)
        self.procli = QLineEdit(cfg.get("procli", "0"))
        self.ac_no = QLineEdit(cfg.get("ac_no", ""))
        self.rest_ip = QLineEdit(cfg.get("rest_ip", "127.0.0.1"))
        self.rest_port = QLineEdit(cfg.get("rest_port", "80"))
        self.is_secure = QCheckBox("Secure")
        self.is_secure.setChecked(bool(cfg.get("is_secure", False)))
        self.is_base64 = QCheckBox("Base64")
        self.is_base64.setChecked(bool(cfg.get("is_base_64", False)))
        self.iris = QCheckBox("IRIS")
        self.iris.setChecked(bool(cfg.get("iris", True)))

        form.addRow("Username", self.username)
        form.addRow("Session Password", self.session_pwd)
        form.addRow("Trading Password", self.user_pwd)
        form.addRow("Pro Client", self.procli)
        form.addRow("Account Number", self.ac_no)
        form.addRow("REST IP", self.rest_ip)
        form.addRow("REST Port", self.rest_port)
        form.addRow("", self.is_secure)
        form.addRow("", self.is_base64)
        form.addRow("", self.iris)

        layout.addLayout(form)

        self.btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.btns.button(QDialogButtonBox.Ok).setText("Login")
        self.btns.accepted.connect(self._validate_and_accept)
        self.btns.rejected.connect(self.reject)
        layout.addWidget(self.btns)

    def _validate_and_accept(self):
        required = {
            "Username": self.username.text().strip(),
            "Session Password": self.session_pwd.text().strip(),
            "Trading Password": self.user_pwd.text().strip(),
            "Account Number": self.ac_no.text().strip(),
        }
        missing = [label for label, value in required.items() if not value]
        if missing:
            QMessageBox.warning(self, "Missing Fields", f"Please fill: {', '.join(missing)}")
            return
        self.accept()

    def get_credentials(self):
        return {
            "user": self.username.text().strip(),
            "s_pwd": self.session_pwd.text().strip(),
            "pwd": self.user_pwd.text().strip(),
            "procli": self.procli.text().strip() or "0",
            "ac_no": self.ac_no.text().strip(),
            "is_secure": self.is_secure.isChecked(),
            "is_base_64": self.is_base64.isChecked(),
            "rest_ip": self.rest_ip.text().strip() or "127.0.0.1",
            "rest_port": self.rest_port.text().strip() or "80",
            "iris": self.iris.isChecked(),
        }


class QuickOrderDialog(QDialog):
    def __init__(self, parent=None, side="BUY", token="", symbol="", ltp=None):
        super().__init__(parent)
        self.setModal(True)
        self.setWindowTitle(f"Quick {side}")
        self.resize(420, 280)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.side_label = QLabel(side)
        self.side_label.setStyleSheet("font-weight: 700; color: #22c55e;" if side.upper() == "BUY" else "font-weight: 700; color: #ef4444;")
        self.token_input = QLineEdit(str(token or ""))
        self.symbol_input = QLineEdit(str(symbol or ""))
        self.qty_input = QLineEdit("1")
        self.ordtype_combo = QComboBox()
        self.ordtype_combo.addItems(["MARKET", "LIMIT", "IOC"])
        self.price_input = QLineEdit("0")
        if ltp is not None:
            self.price_input.setText(f"{ltp:.2f}")
            self.ordtype_combo.setCurrentText("LIMIT")
        self.trigger_input = QLineEdit("0")

        self.ordtype_combo.currentTextChanged.connect(
            lambda text: self.price_input.setText("0") if str(text).upper() == "MARKET" else None
        )

        form.addRow("Side", self.side_label)
        form.addRow("Token", self.token_input)
        form.addRow("Symbol", self.symbol_input)
        form.addRow("Qty", self.qty_input)
        form.addRow("Order Type", self.ordtype_combo)
        form.addRow("Price", self.price_input)
        form.addRow("Trigger", self.trigger_input)
        layout.addLayout(form)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("Place Order")
        btns.accepted.connect(self._validate_and_accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _validate_and_accept(self):
        token = self.token_input.text().strip()
        qty = self.qty_input.text().strip()
        price = self.price_input.text().strip()
        ordtype = self.ordtype_combo.currentText().strip().upper()

        if not token:
            QMessageBox.warning(self, "Missing Token", "Token is required.")
            return
        try:
            qty_num = int(float(qty or "0"))
        except Exception:
            QMessageBox.warning(self, "Invalid Qty", "Qty must be numeric.")
            return
        if qty_num <= 0:
            QMessageBox.warning(self, "Invalid Qty", "Qty must be greater than 0.")
            return
        if ordtype != "MARKET":
            try:
                price_num = float(price or "0")
            except Exception:
                QMessageBox.warning(self, "Invalid Price", "Price must be numeric.")
                return
            if price_num <= 0:
                QMessageBox.warning(self, "Invalid Price", "Limit/IOC require price > 0.")
                return
        self.accept()

    def payload(self):
        ordtype = self.ordtype_combo.currentText().strip().upper()
        return {
            "token": self.token_input.text().strip(),
            "symbol": self.symbol_input.text().strip(),
            "qty": self.qty_input.text().strip() or "1",
            "ordtype": ordtype,
            "price": "0" if ordtype == "MARKET" else (self.price_input.text().strip() or "0"),
            "trigger": self.trigger_input.text().strip() or "0",
        }


class StrategyBasketDialog(QDialog):
    def __init__(self, parent=None, title="", legs=None):
        super().__init__(parent)
        self.setModal(True)
        self.setWindowTitle("Strategy Basket")
        self.resize(760, 460)
        self._legs = list(legs or [])

        layout = QVBoxLayout(self)
        if title:
            header = QLabel(title)
            header.setStyleSheet("font-size: 14px; font-weight: 700; color: #dbeafe;")
            layout.addWidget(header)

        self.table = QTableView()
        self.model = AccountTableModel()
        self.table.setModel(self.model)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Order Type"))
        self.ordtype_combo = QComboBox()
        self.ordtype_combo.addItems(["MARKET", "LIMIT", "IOC"])
        controls.addWidget(self.ordtype_combo)
        controls.addWidget(QLabel("Qty"))
        self.qty_input = QLineEdit("1")
        self.qty_input.setMaximumWidth(100)
        controls.addWidget(self.qty_input)
        controls.addStretch(1)
        layout.addLayout(controls)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("Place Basket")
        btns.accepted.connect(self._validate_and_accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

        rows = []
        for leg in self._legs:
            rows.append(
                {
                    "side": leg.get("side", ""),
                    "token": leg.get("token", ""),
                    "symbol": leg.get("symbol", ""),
                    "option": leg.get("option", ""),
                    "strike": leg.get("strike", ""),
                    "price": leg.get("price", ""),
                }
            )
        self.model.set_records(["side", "token", "symbol", "option", "strike", "price"], rows)

    def _validate_and_accept(self):
        try:
            qty = int(float(self.qty_input.text().strip() or "0"))
        except Exception:
            QMessageBox.warning(self, "Invalid Qty", "Qty must be numeric.")
            return
        if qty <= 0:
            QMessageBox.warning(self, "Invalid Qty", "Qty must be greater than 0.")
            return
        self.accept()

    def payload(self):
        return {
            "qty": str(int(float(self.qty_input.text().strip() or "1"))),
            "ordtype": self.ordtype_combo.currentText().strip().upper(),
            "legs": list(self._legs),
        }


class TradingWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("GreekView Pro Terminal")
        self.resize(1680, 980)

        self.bus = SignalBus()
        self.bus.log.connect(self._append_log)
        self.bus.error.connect(self._show_error)
        self.bus.status.connect(self._set_status)
        self.bus.login_state.connect(self._set_logged_in_state)
        self.bus.session_info.connect(self._set_session_info)
        self.bus.ohlc_series.connect(self._on_ohlc_series)
        self.bus.account_table.connect(self._set_account_table)
        self.bus.refresh_orderbook.connect(self.refresh_active_orderbook)
        self.bus.account_text.connect(self.account_output_set_text)
        self.bus.clear_watch.connect(self._clear_watch_ui)
        self.bus.remove_tokens.connect(self.model_remove_tokens)
        self.bus.contract_ready.connect(self._refresh_contract_filters)
        self.bus.contract_ready.connect(self._refresh_strategy_filters)
        self.bus.strategy_rows.connect(self._set_strategy_rows)

        self.model = MarketTableModel()
        self.account_model = AccountTableModel()
        self.account_popup = None
        self.account_popup_table = None
        self.api = None
        self.streaming_active = False
        self.stream_thread = None
        self.ui_queue = queue.Queue(maxsize=30000)
        self.current_tokens = set()
        self.stream_subscribed_tokens = set()
        self.active_token = None
        self._perf_counter = 0
        self.contract_df = None
        self.contract_colmap = {}
        self.contract_lookup = {}
        self.contract_cache = {}
        self.contract_row_by_token = {}
        self.token_metadata = {}
        self.primary_index_token = "101999957"
        self.index_tokens = {self.primary_index_token}
        self.didx_tokens = set()
        self._ohlc_request_key = None
        self.chart_popup = None
        self.chart_popup_widget = None
        self.popup_interval_combo = None
        self.popup_charttype_combo = None
        self.popup_ordtype_combo = None
        self.popup_qty_input = None
        self.popup_price_input = None
        self.popup_token_label = None
        self._watchlist_delete_shortcut = None
        self._watchlist_buy_shortcut = None
        self._watchlist_sell_shortcut = None
        self._contract_enter_shortcut = None
        self._contract_num_enter_shortcut = None
        self.login_config = {
            "user": "",
            "s_pwd": "",
            "pwd": "",
            "procli": "0",
            "ac_no": "",
            "is_secure": False,
            "is_base_64": False,
            "rest_ip": "127.0.0.1",
            "rest_port": "80",
            "iris": True,
        }
        self.timeframe_to_interval = {"1m": 1, "5m": 5, "15m": 15, "1h": 60}
        self.active_timeframe = "5m"
        self.chart_mode = "line"
        self.active_orderbook_key = "all"
        self.ws_server_time_text = "-"
        self.account_auto_refresh_timer = QTimer(self)
        self.account_auto_refresh_timer.setInterval(4000)
        self.account_auto_refresh_timer.timeout.connect(lambda: self.refresh_active_orderbook(auto=True))
        self._account_refresh_inflight = False
        self._pending_cancel_shortcut = None
        self._pending_modify_shortcut = None
        self._perf_shortcut = None
        self.index_popup = None
        self.index_popup_model = None
        self.index_popup_table = None
        self.index_latest = {}
        self.strategy_popup = None
        self.strategy_model = None
        self.strategy_table = None
        self.strategy_exchange_combo = None
        self.strategy_symbol_combo = None
        self.strategy_expiry_combo = None
        self.strategy_view_combo = None
        self.strategy_risk_input = None
        self.strategy_rows_cache = []
        self.ai_popup = None
        self.ai_model = None
        self.ai_table = None
        self.ai_summary = None
        self.ai_timer = None

        self._build_ui()
        self._set_logged_in_state(False)
        self._set_session_info("Not logged in")
        self._setup_shortcuts()

        self.ui_timer = QTimer(self)
        self.ui_timer.timeout.connect(self._drain_ui_queue)
        self.ui_timer.start(40)
        QTimer.singleShot(0, self.open_login_popup)

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        base = QVBoxLayout(root)
        base.setContentsMargins(8, 8, 8, 8)
        base.setSpacing(8)

        base.addWidget(self._build_top_bar())

        self.vertical_splitter = QSplitter(Qt.Vertical)
        self.vertical_splitter.setChildrenCollapsible(False)
        self.vertical_splitter.setHandleWidth(8)

        self.upper_splitter = QSplitter(Qt.Horizontal)
        self.upper_splitter.setChildrenCollapsible(False)
        self.upper_splitter.setHandleWidth(8)

        self.upper_splitter.addWidget(self._build_watchlist_panel())
        self.upper_splitter.setStretchFactor(0, 1)
        self.upper_splitter.setSizes([1500])

        self.vertical_splitter.addWidget(self.upper_splitter)
        self.vertical_splitter.addWidget(self._build_bottom_console())
        self.vertical_splitter.setStretchFactor(0, 3)
        self.vertical_splitter.setStretchFactor(1, 1)
        self.vertical_splitter.setSizes([760, 260])

        base.addWidget(self.vertical_splitter, 1)

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

        row.addSpacing(12)
        self.btn_index_popup = QPushButton("Index View")
        self.btn_index_popup.clicked.connect(self.show_index_popup)
        row.addWidget(self.btn_index_popup)
        self.btn_strategy = QPushButton("Strategy Finder")
        self.btn_strategy.clicked.connect(self.show_strategy_finder)
        row.addWidget(self.btn_strategy)
        self.btn_ai = QPushButton("AI Advisor")
        self.btn_ai.clicked.connect(self.show_ai_advisor)
        row.addWidget(self.btn_ai)

        self.perf_badge = QLabel("ticks/s: -  backlog: -")
        self.perf_badge.setStyleSheet("color: #93c5fd; font-weight: 600;")
        row.addWidget(self.perf_badge)
        self.server_time_label = QLabel("WS Time: -")
        self.server_time_label.setStyleSheet("color: #a7f3d0; font-weight: 600;")
        row.addWidget(self.server_time_label)
        row.addSpacing(10)
        row.addStretch(1)
        return bar

    def _build_watchlist_panel(self):
        panel = QGroupBox("Watchlist")
        panel.setMinimumWidth(360)
        self.watchlist_panel = panel
        layout = QVBoxLayout(panel)

        self.contract_filter_box = QGroupBox("Contract Filter (Press Enter to Add)")
        filter_layout = QVBoxLayout(self.contract_filter_box)
        primary_row = QHBoxLayout()
        primary_row.addWidget(QLabel("Exchange Segment"))
        self.contract_exchange_combo = QComboBox()
        self.contract_exchange_combo.currentTextChanged.connect(self._on_contract_exchange_changed)
        primary_row.addWidget(self.contract_exchange_combo, 1)
        primary_row.addWidget(QLabel("Symbol"))
        self.contract_symbol_combo = QComboBox()
        self.contract_symbol_combo.setEditable(True)
        self.contract_symbol_combo.currentTextChanged.connect(self._on_contract_symbol_changed)
        primary_row.addWidget(self.contract_symbol_combo, 1)
        filter_layout.addLayout(primary_row)

        self.contract_derivative_row = QWidget()
        der_row = QHBoxLayout(self.contract_derivative_row)
        der_row.setContentsMargins(0, 0, 0, 0)
        der_row.addWidget(QLabel("Series/InstType"))
        self.contract_inst_combo = QComboBox()
        self.contract_inst_combo.currentTextChanged.connect(self._on_contract_inst_changed)
        der_row.addWidget(self.contract_inst_combo, 1)
        der_row.addWidget(QLabel("Expiry"))
        self.contract_expiry_combo = QComboBox()
        self.contract_expiry_combo.currentTextChanged.connect(self._on_contract_expiry_changed)
        der_row.addWidget(self.contract_expiry_combo, 1)
        der_row.addWidget(QLabel("Strike"))
        self.contract_strike_combo = QComboBox()
        self.contract_strike_combo.currentTextChanged.connect(self._on_contract_strike_changed)
        der_row.addWidget(self.contract_strike_combo, 1)
        der_row.addWidget(QLabel("Option Type"))
        self.contract_option_combo = QComboBox()
        der_row.addWidget(self.contract_option_combo, 1)
        filter_layout.addWidget(self.contract_derivative_row)

        self.contract_filter_hint = QLabel("Select filter values and press Enter to subscribe GreekToken and add it to watchlist.")
        self.contract_filter_hint.setStyleSheet("color: #93c5fd;")
        self.contract_filter_hint.setWordWrap(True)
        filter_layout.addWidget(self.contract_filter_hint)

        layout.addWidget(self.contract_filter_box)
        self.contract_derivative_row.setVisible(False)
        self._contract_enter_shortcut = QShortcut(QKeySequence("Return"), self.contract_filter_box)
        self._contract_enter_shortcut.activated.connect(self._on_contract_enter_pressed)
        self._contract_num_enter_shortcut = QShortcut(QKeySequence("Enter"), self.contract_filter_box)
        self._contract_num_enter_shortcut.activated.connect(self._on_contract_enter_pressed)

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
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_table_right_click)
        self._watchlist_delete_shortcut = QShortcut(QKeySequence("Del"), self.table)
        self._watchlist_delete_shortcut.activated.connect(self.unsubscribe_selected)
        self._watchlist_buy_shortcut = QShortcut(QKeySequence("+"), self.table)
        self._watchlist_buy_shortcut.activated.connect(lambda: self.open_quick_order_for_selection("BUY"))
        self._watchlist_sell_shortcut = QShortcut(QKeySequence("-"), self.table)
        self._watchlist_sell_shortcut.activated.connect(lambda: self.open_quick_order_for_selection("SELL"))
        layout.addWidget(self.table, 1)

        return panel

    def _build_bottom_console(self):
        logs_box = QGroupBox("Logs")
        logs_layout = QVBoxLayout(logs_box)
        self.log_output = QPlainTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setStyleSheet("background-color: #0a111d; color: #9fd3ff;")
        logs_layout.addWidget(self.log_output, 1)
        return logs_box

    def _append_log(self, message):
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {message}"
        old = self.log_output.toPlainText()
        if old:
            # Newest-first log view for quicker issue triage.
            merged = f"{line}\n{old}"
            lines = merged.splitlines()
            if len(lines) > 2000:
                merged = "\n".join(lines[:2000])
            self.log_output.setPlainText(merged)
        else:
            self.log_output.setPlainText(line)

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

    def _set_session_info(self, text):
        if hasattr(self, "session_info_label") and self.session_info_label:
            self.session_info_label.setText(text)
            if text.lower().startswith("logged in"):
                self.session_info_label.setStyleSheet("font-weight: 700; color: #86efac;")
            else:
                self.session_info_label.setStyleSheet("font-weight: 700; color: #fca5a5;")

    def _set_logged_in_state(self, enabled):
        self.watchlist_panel.setEnabled(enabled)
        if hasattr(self, "contract_filter_box"):
            self.contract_filter_box.setEnabled(enabled)
        if not enabled:
            self.account_model.clear()
            if hasattr(self, "contract_exchange_combo"):
                self._refresh_contract_filters()

    def _clear_watch_ui(self):
        self.model.clear()
        if self.chart_popup_widget:
            self.chart_popup_widget.set_active_token(None)
        self.active_token = None
        self.current_tokens.clear()
        self.index_latest.clear()
        self._set_quote_strip(None)
        self.ws_server_time_text = "-"
        self.server_time_label.setText("WS Time: -")
        self._refresh_index_popup_table()

    def model_remove_tokens(self, tokens):
        self.model.remove_tokens(tokens)
        if self.active_token and self.active_token in {str(token) for token in tokens}:
            self.active_token = None
            if self.chart_popup_widget:
                self.chart_popup_widget.set_active_token(None)
            self._set_quote_strip(None)

    def account_output_set_text(self, text):
        return

    def _ensure_account_popup(self, title):
        if self.account_popup is None:
            self.account_popup = QDialog(self)
            self.account_popup.setModal(False)
            self.account_popup.resize(1200, 700)
            self.account_popup.finished.connect(lambda _: self.account_auto_refresh_timer.stop())
            layout = QVBoxLayout(self.account_popup)
            self.account_popup_table = QTableView()
            self.account_popup_table.setModel(self.account_model)
            self.account_popup_table.setAlternatingRowColors(True)
            self.account_popup_table.verticalHeader().setVisible(False)
            self.account_popup_table.setSelectionBehavior(QTableView.SelectRows)
            self.account_popup_table.setSelectionMode(QTableView.SingleSelection)
            self.account_popup_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
            self.account_popup_table.horizontalHeader().setStretchLastSection(True)
            layout.addWidget(self.account_popup_table, 1)
            self._pending_cancel_shortcut = QShortcut(QKeySequence("Del"), self.account_popup_table)
            self._pending_cancel_shortcut.activated.connect(self._cancel_selected_pending_order)
            self._pending_modify_shortcut = QShortcut(QKeySequence("M"), self.account_popup_table)
            self._pending_modify_shortcut.activated.connect(self._modify_selected_pending_order)
        self.account_popup.setWindowTitle(title)
        self.account_popup.show()
        self.account_popup.raise_()
        self.account_popup.activateWindow()
        if self.api and not self.account_auto_refresh_timer.isActive():
            self.account_auto_refresh_timer.start()

    def _set_account_table(self, title, columns, rows):
        self.account_model.set_records(columns, rows)
        self._ensure_account_popup(title)
        self.bus.log.emit(f"{title} table updated: {len(rows)} row(s).")

    def _ensure_index_popup(self):
        if self.index_popup is None:
            self.index_popup = QDialog(self)
            self.index_popup.setModal(False)
            self.index_popup.resize(700, 520)
            self.index_popup.setWindowTitle("Index View")
            layout = QVBoxLayout(self.index_popup)
            self.index_popup_table = QTableView()
            self.index_popup_model = AccountTableModel()
            self.index_popup_table.setModel(self.index_popup_model)
            self.index_popup_table.setAlternatingRowColors(True)
            self.index_popup_table.verticalHeader().setVisible(False)
            self.index_popup_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
            self.index_popup_table.horizontalHeader().setStretchLastSection(True)
            layout.addWidget(self.index_popup_table, 1)
        return self.index_popup

    def show_index_popup(self):
        if self.api and (not self.streaming_active or not self.stream_thread or not self.stream_thread.is_alive()):
            auto_index_tokens = sorted(set(self.didx_tokens))
            if auto_index_tokens:
                self._start_stream_for_tokens(auto_index_tokens, source_label="IndexViewAuto", track_watchlist=False)
            else:
                self._start_stream_for_tokens([self.primary_index_token], source_label="IndexViewAuto", track_watchlist=False)
        popup = self._ensure_index_popup()
        self._refresh_index_popup_table()
        popup.show()
        popup.raise_()
        popup.activateWindow()

    def _refresh_index_popup_table(self):
        if not self.index_popup_model:
            return
        rows = []
        target_tokens = set(self.didx_tokens) if self.didx_tokens else set(self.index_tokens)
        for token, row in self.index_latest.items():
            if token not in target_tokens:
                continue
            rows.append(
                {
                    "symbol": token,
                    "name": row.get("name", ""),
                    "ltp": _format_number(row.get("ltp"), 2),
                    "p_change": _format_number(row.get("p_change"), 2),
                    "change": _format_number(row.get("change"), 2),
                    "lut": _format_ws_time(row.get("lut") or row.get("ltt")) or "-",
                }
            )
        rows.sort(key=lambda r: _to_float(r.get("p_change")) or 0.0, reverse=True)
        self.index_popup_model.set_records(["symbol", "name", "ltp", "p_change", "change", "lut"], rows)

    def _strategy_fo_exchanges(self):
        exchanges = self.contract_cache.get("exchanges", []) if self.contract_cache else []
        return [ex for ex in exchanges if self._is_fo_segment(ex)]

    def _strategy_symbols_for_exchange(self, exchange):
        if not exchange:
            return []
        values = self.contract_cache.get("exchange_to_symbols", {}).get(exchange, [])
        return list(values)

    def _strategy_expiries(self, exchange, symbol):
        if not exchange or not symbol:
            return []
        expiry_set = set()
        fo_rows = self.contract_cache.get("fo_rows_by_exsyminst", {})
        for (ex, sym, inst), rows in fo_rows.items():
            if ex != exchange or sym != symbol or "OPT" not in str(inst).upper():
                continue
            for row in rows:
                exp = self._to_text(row.get("expiry"))
                if exp:
                    expiry_set.add(exp)
        return self._sort_mixed(expiry_set)

    def _ensure_strategy_popup(self):
        if self.strategy_popup is not None:
            return self.strategy_popup

        self.strategy_popup = QDialog(self)
        self.strategy_popup.setModal(False)
        self.strategy_popup.resize(1100, 700)
        self.strategy_popup.setWindowTitle("Strategy Finder")
        layout = QVBoxLayout(self.strategy_popup)

        filters = QHBoxLayout()
        filters.addWidget(QLabel("Exchange"))
        self.strategy_exchange_combo = QComboBox()
        self.strategy_exchange_combo.currentTextChanged.connect(self._on_strategy_exchange_changed)
        filters.addWidget(self.strategy_exchange_combo)
        filters.addWidget(QLabel("Symbol"))
        self.strategy_symbol_combo = QComboBox()
        self.strategy_symbol_combo.setEditable(True)
        self.strategy_symbol_combo.currentTextChanged.connect(self._on_strategy_symbol_changed)
        filters.addWidget(self.strategy_symbol_combo, 1)
        filters.addWidget(QLabel("Expiry"))
        self.strategy_expiry_combo = QComboBox()
        filters.addWidget(self.strategy_expiry_combo)
        filters.addWidget(QLabel("View"))
        self.strategy_view_combo = QComboBox()
        self.strategy_view_combo.addItems(["Range", "Bullish", "Bearish", "Volatility"])
        filters.addWidget(self.strategy_view_combo)
        filters.addWidget(QLabel("Risk Budget"))
        self.strategy_risk_input = QLineEdit("10000")
        self.strategy_risk_input.setMaximumWidth(120)
        filters.addWidget(self.strategy_risk_input)
        scan_btn = QPushButton("Scan")
        scan_btn.clicked.connect(self._scan_strategies)
        filters.addWidget(scan_btn)
        basket_btn = QPushButton("Load Basket")
        basket_btn.clicked.connect(self._load_selected_strategy_basket)
        filters.addWidget(basket_btn)
        layout.addLayout(filters)

        self.strategy_table = QTableView()
        self.strategy_model = AccountTableModel()
        self.strategy_table.setModel(self.strategy_model)
        self.strategy_table.setAlternatingRowColors(True)
        self.strategy_table.verticalHeader().setVisible(False)
        self.strategy_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.strategy_table.horizontalHeader().setStretchLastSection(True)
        self.strategy_table.doubleClicked.connect(lambda *_: self._load_selected_strategy_basket())
        layout.addWidget(self.strategy_table, 1)
        return self.strategy_popup

    def show_strategy_finder(self):
        popup = self._ensure_strategy_popup()
        self._refresh_strategy_filters()
        popup.show()
        popup.raise_()
        popup.activateWindow()

    def _refresh_strategy_filters(self):
        if not self.strategy_exchange_combo:
            return
        exchanges = self._strategy_fo_exchanges()
        self._combo_fill(self.strategy_exchange_combo, exchanges)
        self._combo_fill(self.strategy_symbol_combo, [])
        self._combo_fill(self.strategy_expiry_combo, [])

    def _on_strategy_exchange_changed(self, value):
        exchange = self._to_text(value).upper()
        symbols = self._strategy_symbols_for_exchange(exchange)
        self._combo_fill(self.strategy_symbol_combo, symbols)
        self._combo_fill(self.strategy_expiry_combo, [])

    def _on_strategy_symbol_changed(self, value):
        exchange = self._to_text(self.strategy_exchange_combo.currentText()).upper()
        symbol = self._to_text(value).upper()
        expiries = self._strategy_expiries(exchange, symbol)
        self._combo_fill(self.strategy_expiry_combo, expiries)

    def _quote_for_row(self, row, cache):
        token = str(row.get("token") or "").strip()
        if not token:
            return {}
        if token in cache:
            return cache[token]
        watch = self.model.row_by_token(token)
        if watch:
            out = {
                "ltp": _to_float(watch.get("ltp")),
                "bid": _to_float(watch.get("bid")),
                "ask": _to_float(watch.get("ask")),
                "tot_vol": _to_float(watch.get("tot_vol")),
                "oi": _to_float(watch.get("oi")),
            }
            cache[token] = out
            return out
        quote = {}
        try:
            raw = self.api.token_broadcast(token, row.get("inst") or row.get("asset_type") or "")
            quote = {
                "ltp": _to_float((raw or {}).get("ltp")),
                "bid": _to_float((raw or {}).get("bid")),
                "ask": _to_float((raw or {}).get("ask")),
                "tot_vol": _to_float((raw or {}).get("tot_vol")),
                "oi": _to_float((raw or {}).get("oi")),
            }
        except Exception:
            quote = {}
        cache[token] = quote
        return quote

    def _underlying_spot(self, symbol):
        eq = self.contract_cache.get("eq_row_by_exsym", {}).get(("NSEEQ", symbol)) or self.contract_cache.get("eq_row_by_exsym", {}).get(("BSEEQ", symbol))
        if not eq:
            return None
        token = str(eq.get("token") or "").strip()
        if not token:
            return None
        watch = self.model.row_by_token(token)
        if watch:
            v = _to_float(watch.get("ltp"))
            if v is not None and v > 0:
                return v
        try:
            q = self.api.token_broadcast(token, eq.get("asset_type") or "EQ")
            v = _to_float((q or {}).get("ltp"))
            if v is not None and v > 0:
                return v
        except Exception:
            return None
        return None

    @staticmethod
    def _safe_price(quote, side):
        if side == "buy":
            return _to_float(quote.get("ask")) or _to_float(quote.get("ltp")) or 0.0
        return _to_float(quote.get("bid")) or _to_float(quote.get("ltp")) or 0.0

    @staticmethod
    def _fmt_money(value):
        if isinstance(value, str):
            return value
        if value is None:
            return "-"
        return f"{value:,.2f}"

    @staticmethod
    def _order_exchange_from_segment(segment):
        text = str(segment or "").upper()
        if text.startswith("BSE"):
            return "BSE"
        return "NSE"

    def _selected_strategy_row(self):
        if not self.strategy_table or not self.strategy_model:
            return None
        model = self.strategy_table.selectionModel()
        if not model:
            return None
        rows = model.selectedRows()
        if not rows:
            return None
        return self.strategy_model.row_at(rows[0].row())

    def _parse_strategy_legs(self, legs_text):
        items = []
        text = str(legs_text or "").strip()
        if not text:
            return items
        for part in text.split("+"):
            p = part.strip()
            if not p:
                continue
            toks = [x for x in p.split(" ") if x]
            if len(toks) < 3:
                continue
            side = toks[0].strip().upper()
            option = toks[1].strip().upper()
            try:
                strike = float(toks[2])
            except Exception:
                continue
            if side in ("BUY", "SELL") and option in ("CE", "PE"):
                items.append({"side": side, "option": option, "strike": strike})
        return items

    def _resolve_strategy_leg_contracts(self, exchange, symbol, expiry, parsed_legs):
        rows = []
        fo_rows = self.contract_cache.get("fo_rows_by_exsyminst", {})
        by_key = {}
        for (ex, sym, inst), contracts in fo_rows.items():
            if ex != exchange or sym != symbol or "OPT" not in str(inst).upper():
                continue
            for row in contracts:
                row_exp = self._to_text(row.get("expiry"))
                row_opt = self._to_text(row.get("option")).upper()
                strike = _to_float(row.get("strike"))
                if row_exp == expiry and row_opt in ("CE", "PE") and strike is not None:
                    by_key[(row_opt, round(float(strike), 6))] = row
        for leg in parsed_legs:
            key = (leg["option"], round(float(leg["strike"]), 6))
            row = by_key.get(key)
            if not row:
                return []
            rows.append(row)
        return rows

    def _price_for_strategy_leg(self, leg_row, side):
        quote = self._quote_for_row(leg_row, {})
        if side == "BUY":
            price = _to_float(quote.get("ask")) or _to_float(quote.get("ltp"))
        else:
            price = _to_float(quote.get("bid")) or _to_float(quote.get("ltp"))
        if price is None or price <= 0:
            return None
        return float(price)

    def _load_selected_strategy_basket(self):
        if not self.api:
            self.bus.error.emit("Not Connected", "Login first.")
            return
        row = self._selected_strategy_row()
        if not row:
            self.bus.error.emit("No Selection", "Select a strategy row first.")
            return
        exchange = self._to_text(self.strategy_exchange_combo.currentText()).upper() if self.strategy_exchange_combo else ""
        symbol = self._to_text(self.strategy_symbol_combo.currentText()).upper() if self.strategy_symbol_combo else ""
        expiry = self._to_text(self.strategy_expiry_combo.currentText()) if self.strategy_expiry_combo else ""
        parsed = self._parse_strategy_legs(row.get("legs"))
        if not (exchange and symbol and expiry and parsed):
            self.bus.error.emit("Invalid Strategy", "Select valid strategy row with Exchange, Symbol, and Expiry.")
            return
        contracts = self._resolve_strategy_leg_contracts(exchange, symbol, expiry, parsed)
        if len(contracts) != len(parsed):
            self.bus.error.emit("Contracts Missing", "Could not map all strategy legs to option contracts.")
            return
        legs = []
        for leg, contract in zip(parsed, contracts):
            token = str(contract.get("token") or "").strip()
            if not token:
                self.bus.error.emit("Contract Error", "Missing token in selected strategy leg.")
                return
            price = self._price_for_strategy_leg(contract, leg["side"])
            if price is None:
                self.bus.error.emit("Price Error", f"Unable to fetch live price for {leg['option']} {leg['strike']}.")
                return
            legs.append(
                {
                    "side": leg["side"],
                    "option": leg["option"],
                    "strike": f"{leg['strike']:.2f}",
                    "token": token,
                    "symbol": contract.get("name") or symbol,
                    "exchange_segment": exchange,
                    "price": f"{price:.2f}",
                }
            )
        title = f"{row.get('strategy') or 'Strategy'} | {symbol} | {expiry}"
        dialog = StrategyBasketDialog(self, title=title, legs=legs)
        if dialog.exec() != QDialog.Accepted:
            return
        payload = dialog.payload()
        self._place_strategy_basket_orders(title, payload.get("legs", []), payload.get("qty", "1"), payload.get("ordtype", "MARKET"))

    def _place_strategy_basket_orders(self, strategy_name, legs, qty, ordtype):
        if not legs:
            return
        try:
            qty_num = int(float(qty or "0"))
        except Exception:
            self.bus.error.emit("Invalid Qty", "Qty must be numeric.")
            return
        if qty_num <= 0:
            self.bus.error.emit("Invalid Qty", "Qty must be greater than 0.")
            return
        try:
            ordtype_flag = self._map_ordtype_flag(ordtype)
        except Exception as exc:
            self.bus.error.emit("Invalid Order Type", str(exc))
            return

        def task():
            results = []
            for leg in legs:
                try:
                    side_flag = self._map_side_flag(leg.get("side"))
                    price = "0" if ordtype_flag == "2" else str(leg.get("price") or "0")
                    exchange = self._order_exchange_from_segment(leg.get("exchange_segment"))
                    resp = self.api.place_order(
                        tokenno=str(leg.get("token")),
                        symbol=str(leg.get("symbol") or ""),
                        lot="1",
                        qty=str(qty_num),
                        price=price,
                        buysell=side_flag,
                        ordtype=ordtype_flag,
                        trigprice="0",
                        exchange=exchange,
                        validity="0",
                        strategyname=str(strategy_name or "StrategyBasket"),
                    )
                    results.append({"token": leg.get("token"), "side": leg.get("side"), "status": "ok", "resp": resp})
                except Exception as exc:
                    results.append({"token": leg.get("token"), "side": leg.get("side"), "status": "error", "resp": str(exc)})
            ok_count = len([r for r in results if r.get("status") == "ok"])
            self.bus.log.emit(f"Strategy basket placed: {ok_count}/{len(results)} legs successful.")
            self.bus.log.emit(f"Basket result: {json.dumps(results, default=str)}")

        self._run_bg(task, "Place Strategy Basket")

    def _scan_strategies(self):
        if not self.api:
            self.bus.error.emit("Not Connected", "Login first.")
            return
        exchange = self._to_text(self.strategy_exchange_combo.currentText()).upper() if self.strategy_exchange_combo else ""
        symbol = self._to_text(self.strategy_symbol_combo.currentText()).upper() if self.strategy_symbol_combo else ""
        expiry = self._to_text(self.strategy_expiry_combo.currentText()) if self.strategy_expiry_combo else ""
        view = self._to_text(self.strategy_view_combo.currentText()).lower() if self.strategy_view_combo else "range"
        risk_budget = _to_float(self.strategy_risk_input.text()) if self.strategy_risk_input else None
        if not (exchange and symbol and expiry):
            self.bus.error.emit("Missing Filters", "Select Exchange, Symbol and Expiry.")
            return

        def task():
            rows = []
            fo_rows = self.contract_cache.get("fo_rows_by_exsyminst", {})
            for (ex, sym, inst), items in fo_rows.items():
                if ex != exchange or sym != symbol or "OPT" not in str(inst).upper():
                    continue
                for item in items:
                    if self._to_text(item.get("expiry")) == expiry:
                        opt = self._to_text(item.get("option")).upper()
                        strike = _to_float(item.get("strike"))
                        if opt in ("CE", "PE") and strike is not None:
                            row = dict(item)
                            row["opt"] = opt
                            row["strike_val"] = strike
                            rows.append(row)
            if not rows:
                self.bus.log.emit("Strategy scan: no option contracts found for selection.")
                self.bus.strategy_rows.emit([])
                return

            strike_map = {}
            for row in rows:
                strike_map.setdefault(row["strike_val"], {})[row["opt"]] = row
            valid_strikes = sorted([k for k, v in strike_map.items() if "CE" in v and "PE" in v])
            if len(valid_strikes) < 3:
                self.bus.log.emit("Strategy scan: insufficient CE/PE strikes for strategy generation.")
                self.bus.strategy_rows.emit([])
                return

            spot = self._underlying_spot(symbol)
            if spot is None:
                spot = valid_strikes[len(valid_strikes) // 2]
            atm = min(valid_strikes, key=lambda x: abs(x - spot))
            atm_idx = valid_strikes.index(atm)
            start = max(0, atm_idx - 3)
            end = min(len(valid_strikes), atm_idx + 4)
            work_strikes = valid_strikes[start:end]
            qcache = {}
            for strike in work_strikes:
                self._quote_for_row(strike_map[strike]["CE"], qcache)
                self._quote_for_row(strike_map[strike]["PE"], qcache)

            def ce(strike):
                return strike_map.get(strike, {}).get("CE")

            def pe(strike):
                return strike_map.get(strike, {}).get("PE")

            def q(row):
                return self._quote_for_row(row, qcache) if row else {}

            def liquidity(*quotes):
                vol = sum((_to_float(v.get("tot_vol")) or 0.0) for v in quotes)
                oi = sum((_to_float(v.get("oi")) or 0.0) for v in quotes)
                return min(40.0, (vol / 5000.0) + (oi / 20000.0))

            candidates = []
            if ce(atm) and pe(atm):
                ceq = q(ce(atm))
                peq = q(pe(atm))
                debit = self._safe_price(ceq, "buy") + self._safe_price(peq, "buy")
                credit = self._safe_price(ceq, "sell") + self._safe_price(peq, "sell")
                candidates.append(
                    {
                        "strategy": "Long Straddle",
                        "legs": f"BUY CE {atm} + BUY PE {atm}",
                        "cost": debit,
                        "max_profit": "Unlimited",
                        "max_loss": debit,
                        "breakeven": f"{atm - debit:.2f} / {atm + debit:.2f}",
                        "liq": liquidity(ceq, peq),
                    }
                )
                candidates.append(
                    {
                        "strategy": "Short Straddle",
                        "legs": f"SELL CE {atm} + SELL PE {atm}",
                        "cost": -credit,
                        "max_profit": credit,
                        "max_loss": "Unlimited",
                        "breakeven": f"{atm - credit:.2f} / {atm + credit:.2f}",
                        "liq": liquidity(ceq, peq),
                    }
                )

            if atm_idx + 1 < len(valid_strikes):
                k2 = valid_strikes[atm_idx + 1]
                if ce(atm) and ce(k2):
                    buyq = q(ce(atm))
                    sellq = q(ce(k2))
                    debit = self._safe_price(buyq, "buy") - self._safe_price(sellq, "sell")
                    maxp = max(0.0, (k2 - atm) - debit)
                    candidates.append(
                        {
                            "strategy": "Bull Call Spread",
                            "legs": f"BUY CE {atm} + SELL CE {k2}",
                            "cost": debit,
                            "max_profit": maxp,
                            "max_loss": max(0.0, debit),
                            "breakeven": f"{atm + debit:.2f}",
                            "liq": liquidity(buyq, sellq),
                        }
                    )
                if pe(atm) and pe(k2):
                    buyq = q(pe(k2))
                    sellq = q(pe(atm))
                    debit = self._safe_price(buyq, "buy") - self._safe_price(sellq, "sell")
                    maxp = max(0.0, (k2 - atm) - debit)
                    candidates.append(
                        {
                            "strategy": "Bear Put Spread",
                            "legs": f"BUY PE {k2} + SELL PE {atm}",
                            "cost": debit,
                            "max_profit": maxp,
                            "max_loss": max(0.0, debit),
                            "breakeven": f"{atm - debit:.2f}",
                            "liq": liquidity(buyq, sellq),
                        }
                    )

            if atm_idx - 1 >= 0 and atm_idx + 1 < len(valid_strikes):
                lp = valid_strikes[atm_idx - 1]
                hc = valid_strikes[atm_idx + 1]
                if pe(lp) and ce(hc):
                    peq = q(pe(lp))
                    ceq = q(ce(hc))
                    debit = self._safe_price(peq, "buy") + self._safe_price(ceq, "buy")
                    credit = self._safe_price(peq, "sell") + self._safe_price(ceq, "sell")
                    candidates.append(
                        {
                            "strategy": "Long Strangle",
                            "legs": f"BUY PE {lp} + BUY CE {hc}",
                            "cost": debit,
                            "max_profit": "Unlimited",
                            "max_loss": debit,
                            "breakeven": f"{lp - debit:.2f} / {hc + debit:.2f}",
                            "liq": liquidity(peq, ceq),
                        }
                    )
                    candidates.append(
                        {
                            "strategy": "Short Strangle",
                            "legs": f"SELL PE {lp} + SELL CE {hc}",
                            "cost": -credit,
                            "max_profit": credit,
                            "max_loss": "Unlimited",
                            "breakeven": f"{lp - credit:.2f} / {hc + credit:.2f}",
                            "liq": liquidity(peq, ceq),
                        }
                    )

            def view_bonus(name):
                n = name.lower()
                if view == "bullish":
                    return 12 if ("bull" in n or "long call" in n) else (-6 if "bear" in n else 0)
                if view == "bearish":
                    return 12 if ("bear" in n or "long put" in n) else (-6 if "bull" in n else 0)
                if view == "range":
                    return 10 if ("short straddle" in n or "short strangle" in n or "condor" in n) else 0
                if view == "volatility":
                    return 10 if ("long straddle" in n or "long strangle" in n) else 0
                return 0

            out = []
            for item in candidates:
                max_profit = item["max_profit"]
                max_loss = item["max_loss"]
                rr_bonus = 0.0
                if isinstance(max_profit, (int, float)) and isinstance(max_loss, (int, float)) and max_loss > 0:
                    rr_bonus = min(15.0, (max_profit / max_loss) * 5.0)
                elif isinstance(max_profit, str) and max_profit.lower().startswith("unlimited") and isinstance(max_loss, (int, float)):
                    rr_bonus = 8.0
                budget_bonus = 0.0
                if risk_budget and isinstance(max_loss, (int, float)) and max_loss > 0:
                    budget_bonus = 8.0 if max_loss <= risk_budget else -12.0
                score = 45.0 + item["liq"] + rr_bonus + budget_bonus + view_bonus(item["strategy"])
                out.append(
                    {
                        "strategy": item["strategy"],
                        "legs": item["legs"],
                        "expiry": expiry,
                        "atm": f"{atm:.2f}",
                        "cost": self._fmt_money(item["cost"]),
                        "max_profit": self._fmt_money(max_profit),
                        "max_loss": self._fmt_money(max_loss),
                        "breakeven": item["breakeven"],
                        "liquidity": f"{item['liq']:.1f}",
                        "score": f"{score:.1f}",
                    }
                )

            out.sort(key=lambda r: _to_float(r.get("score")) or 0.0, reverse=True)
            self.bus.strategy_rows.emit(out[:10])
            self.bus.log.emit(f"Strategy scan complete: {len(out[:10])} result(s).")

        self._run_bg(task, "Strategy Finder")

    def _set_strategy_rows(self, rows):
        if not self.strategy_model:
            return
        self.strategy_rows_cache = list(rows or [])
        columns = ["strategy", "legs", "expiry", "atm", "cost", "max_profit", "max_loss", "breakeven", "liquidity", "score"]
        self.strategy_model.set_records(columns, rows or [])
        if self.ai_popup and self.ai_popup.isVisible():
            self._refresh_ai_advisor()

    def _ensure_ai_popup(self):
        if self.ai_popup is not None:
            return self.ai_popup

        self.ai_popup = QDialog(self)
        self.ai_popup.setModal(False)
        self.ai_popup.resize(1120, 700)
        self.ai_popup.setWindowTitle("AI Advisor")
        self.ai_popup.finished.connect(lambda _: self.ai_timer.stop() if self.ai_timer else None)

        layout = QVBoxLayout(self.ai_popup)
        self.ai_summary = QLabel("AI advisor will analyze live market ticks and strategy data.")
        self.ai_summary.setWordWrap(True)
        self.ai_summary.setStyleSheet("color: #cbd5e1; padding: 4px 2px;")
        layout.addWidget(self.ai_summary)

        self.ai_table = QTableView()
        self.ai_model = AccountTableModel()
        self.ai_table.setModel(self.ai_model)
        self.ai_table.setAlternatingRowColors(True)
        self.ai_table.verticalHeader().setVisible(False)
        self.ai_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.ai_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.ai_table, 1)

        note = QLabel("Advisory only. This is model-driven assistance, not guaranteed financial advice.")
        note.setStyleSheet("color: #93c5fd;")
        layout.addWidget(note)

        if self.ai_timer is None:
            self.ai_timer = QTimer(self)
            self.ai_timer.setInterval(2500)
            self.ai_timer.timeout.connect(self._refresh_ai_advisor)
        return self.ai_popup

    def show_ai_advisor(self):
        popup = self._ensure_ai_popup()
        self._refresh_ai_advisor()
        popup.show()
        popup.raise_()
        popup.activateWindow()
        if self.ai_timer and not self.ai_timer.isActive():
            self.ai_timer.start()

    def _market_regime(self):
        target_tokens = set(self.didx_tokens) if self.didx_tokens else set(self.index_tokens)
        values = []
        for token, row in self.index_latest.items():
            if token not in target_tokens:
                continue
            pch = _to_float(row.get("p_change"))
            if pch is not None:
                values.append(pch)
        if not values:
            return {"mode": "neutral", "avg_p_change": 0.0, "vol": 0.0}
        avg = sum(values) / max(1, len(values))
        vol = max(values) - min(values) if len(values) > 1 else abs(avg)
        if avg > 0.5:
            mode = "bullish"
        elif avg < -0.5:
            mode = "bearish"
        else:
            mode = "neutral"
        if vol >= 1.8:
            mode = "volatile"
        return {"mode": mode, "avg_p_change": avg, "vol": vol}

    def _classify_segment(self, token, row):
        meta = self.contract_row_by_token.get(str(token), {})
        inst = str(meta.get("inst") or row.get("asset_type") or "").upper()
        exchange_seg = str(meta.get("exchange") or "").upper()
        if "OPT" in inst:
            return "OPTIONS"
        if "FUT" in inst:
            return "FUTURES"
        if exchange_seg.endswith("FO") and inst and inst != "DIDX":
            return "FUTURES"
        return "EQUITY"

    def _ai_equity_idea(self, row, regime):
        pch = _to_float(row.get("p_change"))
        change = _to_float(row.get("change"))
        vol = _to_float(row.get("tot_vol")) or 0.0
        if pch is None:
            return ("WATCH", 48, "Insufficient momentum data.", "Medium")
        score = 50.0 + (pch * 8.0) + (2.0 if change and change > 0 else -2.0) + min(10.0, vol / 200000.0)
        if regime["mode"] == "bearish":
            score -= 7.0
        if regime["mode"] == "volatile":
            score -= 10.0
        if score >= 62:
            return ("DO", min(95, int(score)), f"Trend positive ({pch:+.2f}%) with supportive volume.", "Use stop loss")
        if score <= 44:
            return ("DO NOT", max(5, int(score)), f"Weak/negative momentum ({pch:+.2f}%).", "Avoid fresh entry")
        return ("WATCH", int(score), f"Mixed signal ({pch:+.2f}%), wait for confirmation.", "Medium")

    def _ai_futures_idea(self, row, regime):
        pch = _to_float(row.get("p_change"))
        oi = _to_float(row.get("oi")) or 0.0
        if pch is None:
            return ("WATCH", 50, "No reliable momentum from live feed.", "High leverage risk")
        score = 50.0 + (pch * 7.5) + min(8.0, oi / 500000.0)
        if regime["mode"] == "bullish":
            score += 3.0 if pch > 0 else -4.0
        elif regime["mode"] == "bearish":
            score += 3.0 if pch < 0 else -4.0
        elif regime["mode"] == "volatile":
            score -= 8.0
        if score >= 64:
            side = "long" if pch > 0 else "short"
            return ("DO", min(95, int(score)), f"{side.title()} bias from momentum/OI ({pch:+.2f}%).", "Tight SL mandatory")
        if score <= 44:
            return ("DO NOT", max(5, int(score)), f"Poor setup for leveraged futures ({pch:+.2f}%).", "High risk")
        return ("WATCH", int(score), "Futures setup not clean yet.", "High leverage risk")

    def _ai_options_ideas(self, regime):
        out = []
        top = sorted(self.strategy_rows_cache or [], key=lambda r: _to_float(r.get("score")) or 0.0, reverse=True)[:4]
        for row in top:
            score = _to_float(row.get("score")) or 0.0
            action = "DO" if score >= 62 else ("DO NOT" if score <= 45 else "WATCH")
            if regime["mode"] == "volatile" and "Short" in str(row.get("strategy", "")):
                action = "DO NOT"
                score = min(score, 42.0)
            out.append(
                {
                    "segment": "OPTIONS",
                    "symbol": row.get("strategy", "-"),
                    "action": action,
                    "confidence": f"{int(max(5, min(95, score)))}",
                    "reason": f"{row.get('legs', '')} | Score {row.get('score', '-')}",
                    "risk": "Defined-risk preferred" if "Spread" in str(row.get("strategy", "")) else "Premium decay risk",
                }
            )
        return out

    def _refresh_ai_advisor(self):
        if not self.ai_model:
            return
        regime = self._market_regime()
        rows = []
        for row in getattr(self.model, "_rows", []):
            token = str(row.get("symbol") or "")
            segment = self._classify_segment(token, row)
            if segment == "OPTIONS":
                continue
            if segment == "EQUITY":
                action, conf, reason, risk = self._ai_equity_idea(row, regime)
            else:
                action, conf, reason, risk = self._ai_futures_idea(row, regime)
            rows.append(
                {
                    "segment": segment,
                    "symbol": row.get("name") or token,
                    "action": action,
                    "confidence": str(conf),
                    "reason": reason,
                    "risk": risk,
                }
            )
        rows.sort(key=lambda x: int(x.get("confidence") or "0"), reverse=True)
        rows = rows[:8] + self._ai_options_ideas(regime)
        rows = rows[:12]
        columns = ["segment", "symbol", "action", "confidence", "reason", "risk"]
        self.ai_model.set_records(columns, rows)
        if self.ai_summary:
            self.ai_summary.setText(
                f"Regime: {regime['mode'].upper()} | Index avg %: {regime['avg_p_change']:+.2f} | Vol spread: {regime['vol']:.2f} | Updated: {datetime.now().strftime('%H:%M:%S')}"
            )

    @staticmethod
    def _pick_from_row(row, candidates):
        if not isinstance(row, dict):
            return None
        if not row:
            return None
        lowered = {str(k).strip().lower(): k for k in row.keys()}
        for candidate in candidates:
            key = lowered.get(str(candidate).strip().lower())
            if key is not None:
                value = row.get(key)
                if value not in (None, ""):
                    return value
        return None

    def _selected_account_row(self):
        if not self.account_popup_table:
            return None
        model = self.account_popup_table.selectionModel()
        if not model:
            return None
        indexes = model.selectedRows()
        if not indexes:
            return None
        return self.account_model.row_at(indexes[0].row())

    def _resolve_pending_order_context(self, row):
        if not row:
            return None
        gorderid = self._pick_from_row(row, ("gorderid", "greekorderno", "orderid", "order_no", "orderno"))
        if not gorderid:
            return None
        qty = self._pick_from_row(row, ("qty", "quantity", "remainingqty", "remaining_qty")) or "1"
        lot = self._pick_from_row(row, ("lot", "lotsize", "lot_size")) or "1"
        ordtype = self._pick_from_row(row, ("order_type", "ordtype", "type", "ordertype")) or "1"
        token = self._pick_from_row(row, ("gtoken", "greektoken", "token", "tokenno", "symboltoken"))
        asset_type = self._pick_from_row(row, ("asset_type", "assettype", "series/insttype", "series"))
        ltp = self._pick_from_row(row, ("ltp", "last_price", "lastprice", "price"))
        return {
            "gorderid": str(gorderid).strip(),
            "qty": str(qty).strip() or "1",
            "lot": str(lot).strip() or "1",
            "ordtype": str(ordtype).strip() or "1",
            "token": str(token).strip() if token not in (None, "") else "",
            "asset_type": str(asset_type).strip() if asset_type not in (None, "") else "",
            "ltp": ltp,
        }

    def _latest_price_for_pending(self, ctx):
        token = str(ctx.get("token") or "").strip()
        if token:
            watch_row = self.model.row_by_token(token)
            if watch_row:
                live_ltp = _to_float(watch_row.get("ltp"))
                if live_ltp is not None and live_ltp > 0:
                    return live_ltp
        ltp = _to_float(ctx.get("ltp"))
        if ltp is not None and ltp > 0:
            return ltp
        if self.api and token:
            try:
                quote = self.api.token_broadcast(token, ctx.get("asset_type") or "")
                q_ltp = _to_float((quote or {}).get("ltp"))
                if q_ltp is not None and q_ltp > 0:
                    return q_ltp
            except Exception:
                pass
        return None

    def _cancel_selected_pending_order(self):
        if not self.api:
            self.bus.error.emit("Not Connected", "Login first.")
            return
        if (self.active_orderbook_key or "").lower() != "pending":
            self.bus.error.emit("Pending Only", "Open Pending Orderbook and select an order first.")
            return
        row = self._selected_account_row()
        ctx = self._resolve_pending_order_context(row)
        if not ctx or not ctx.get("gorderid"):
            self.bus.error.emit("No Order", "Selected row does not have gorderid.")
            return
        gorderid = ctx["gorderid"]
        ask = QMessageBox.question(
            self,
            "Cancel Pending Order",
            f"Cancel pending order {gorderid}?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ask != QMessageBox.Yes:
            return

        def task():
            self.api.cancel_order(gorderid)
            self.bus.log.emit(f"Cancel requested for gorderid {gorderid}.")
            self.fetch_orderbook_pending(log_fetch=False)

        self._run_bg(task, "Cancel Pending Order")

    def _modify_selected_pending_order(self):
        if not self.api:
            self.bus.error.emit("Not Connected", "Login first.")
            return
        if (self.active_orderbook_key or "").lower() != "pending":
            self.bus.error.emit("Pending Only", "Open Pending Orderbook and select an order first.")
            return
        row = self._selected_account_row()
        ctx = self._resolve_pending_order_context(row)
        if not ctx or not ctx.get("gorderid"):
            self.bus.error.emit("No Order", "Selected row does not have gorderid.")
            return

        latest_price = self._latest_price_for_pending(ctx)
        if latest_price is None:
            self.bus.error.emit("No Price", "Latest price not available for selected order.")
            return

        try:
            ordtype_flag = self._map_ordtype_flag(ctx.get("ordtype"))
        except Exception:
            ordtype_flag = "1"
        if ordtype_flag == "2":
            ordtype_flag = "1"

        gorderid = ctx["gorderid"]
        qty = ctx.get("qty") or "1"
        lot = ctx.get("lot") or "1"
        ask = QMessageBox.question(
            self,
            "Modify Pending Order",
            f"Modify order {gorderid} to latest price {latest_price:.2f}?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if ask != QMessageBox.Yes:
            return

        def task():
            resp = self.api.modify_order(
                price=f"{latest_price:.2f}",
                lot=str(lot),
                qty=str(qty),
                ordtype=str(ordtype_flag),
                gorderid=str(gorderid),
            )
            self.bus.log.emit(f"Modify requested for gorderid {gorderid}: {resp}")
            self.fetch_orderbook_pending(log_fetch=False)

        self._run_bg(task, "Modify Pending Order")

    @staticmethod
    def _rows_from_payload(data):
        if isinstance(data, list):
            rows = [item for item in data if isinstance(item, dict)]
            if rows:
                columns = list(dict.fromkeys(key for row in rows for key in row.keys()))
                return columns, rows
        if isinstance(data, dict):
            for key in ("data", "records", "rows", "orderbook", "orderBook", "stockDetails"):
                value = data.get(key)
                if isinstance(value, list) and value and isinstance(value[0], dict):
                    columns = list(dict.fromkeys(k for row in value for k in row.keys()))
                    return columns, value
        return [], []

    def refresh_active_orderbook(self, auto=False):
        if self._account_refresh_inflight:
            return
        key = (self.active_orderbook_key or "").lower()
        if key == "all":
            self.fetch_orderbook_all(log_fetch=not auto)
        elif key == "traded":
            self.fetch_orderbook_traded(log_fetch=not auto)
        elif key == "rejected":
            self.fetch_orderbook_rejected(log_fetch=not auto)
        elif key == "pending":
            self.fetch_orderbook_pending(log_fetch=not auto)
        elif key == "positions":
            self.fetch_positions(log_fetch=not auto)
        elif key == "holdings":
            self.fetch_holdings(log_fetch=not auto)
        elif key == "margin":
            self.fetch_margin(log_fetch=not auto)

    def _ensure_chart_popup(self):
        if self.chart_popup is None:
            self.chart_popup = QDialog(self)
            self.chart_popup.setModal(False)
            self.chart_popup.resize(1250, 760)
            self.chart_popup.setWindowTitle("Chart Popup")
            layout = QVBoxLayout(self.chart_popup)

            control_row = QHBoxLayout()
            self.popup_token_label = QLabel("Token: -")
            self.popup_token_label.setStyleSheet("font-weight: 700; color: #dbeafe;")
            control_row.addWidget(self.popup_token_label)
            control_row.addSpacing(10)
            control_row.addWidget(QLabel("Interval"))
            self.popup_interval_combo = QComboBox()
            self.popup_interval_combo.addItems(["1m", "5m", "15m", "1h"])
            self.popup_interval_combo.setCurrentText(self.active_timeframe)
            self.popup_interval_combo.currentTextChanged.connect(self.set_timeframe)
            control_row.addWidget(self.popup_interval_combo)
            control_row.addSpacing(10)
            control_row.addWidget(QLabel("Chart"))
            self.popup_charttype_combo = QComboBox()
            self.popup_charttype_combo.addItems(["Line", "Candlestick"])
            self.popup_charttype_combo.setCurrentText("Candlestick" if self.chart_mode == "candlestick" else "Line")
            self.popup_charttype_combo.currentTextChanged.connect(self.set_chart_type)
            control_row.addWidget(self.popup_charttype_combo)
            control_row.addSpacing(10)
            control_row.addWidget(QLabel("Order Type"))
            self.popup_ordtype_combo = QComboBox()
            self.popup_ordtype_combo.addItems(["LIMIT", "MARKET", "IOC"])
            self.popup_ordtype_combo.currentTextChanged.connect(
                lambda val: self.popup_price_input.setText("0") if str(val).upper() == "MARKET" else None
            )
            control_row.addWidget(self.popup_ordtype_combo)
            control_row.addWidget(QLabel("Qty"))
            self.popup_qty_input = QLineEdit("1")
            self.popup_qty_input.setMaximumWidth(80)
            control_row.addWidget(self.popup_qty_input)
            control_row.addWidget(QLabel("Price"))
            self.popup_price_input = QLineEdit("0")
            self.popup_price_input.setMaximumWidth(120)
            control_row.addWidget(self.popup_price_input)
            popup_buy_btn = QPushButton("BUY")
            popup_buy_btn.setObjectName("buy")
            popup_buy_btn.clicked.connect(lambda: self.place_order_from_popup("BUY"))
            popup_sell_btn = QPushButton("SELL")
            popup_sell_btn.setObjectName("sell")
            popup_sell_btn.clicked.connect(lambda: self.place_order_from_popup("SELL"))
            control_row.addWidget(popup_buy_btn)
            control_row.addWidget(popup_sell_btn)
            control_row.addStretch(1)
            layout.addLayout(control_row)

            self.chart_popup_widget = LightweightChartWidget()
            self.chart_popup_widget.set_timeframe(self.active_timeframe)
            self.chart_popup_widget.set_chart_type(self.chart_mode)
            layout.addWidget(self.chart_popup_widget, 1)
        self.chart_popup.show()
        self.chart_popup.raise_()
        self.chart_popup.activateWindow()

    def open_chart_popup_and_start_broadcast(self):
        if not self.api:
            self.bus.error.emit("Not Connected", "Login first.")
            return

        self._ensure_chart_popup()
        if self.active_token:
            self.chart_popup_widget.set_active_token(self.active_token)

        tokens = list(self.current_tokens)
        if not tokens:
            parsed, unresolved = self._parse_tokens()
            tokens.extend(parsed)
            if unresolved:
                self.bus.log.emit(f"Unresolved entries skipped: {', '.join(unresolved[:12])}")
        if self.active_token and self.active_token not in tokens:
            tokens.append(self.active_token)

        self._start_stream_for_tokens(tokens, source_label="F10")

    def _set_quote_strip(self, row):
        if not row:
            if self.popup_token_label:
                self.popup_token_label.setText(f"Token: {self.active_token or '-'} | LTP: - | WS: {self.ws_server_time_text}")
            return

        token = row.get("symbol")
        name = row.get("name")
        ltp = row.get("ltp")
        if self.popup_token_label:
            self.popup_token_label.setText(
                f"Token: {token} | {name or '-'} | LTP: {_format_number(ltp, 2)} | WS: {self.ws_server_time_text}"
            )

    def _update_ws_server_time(self, ltt, lut):
        text = _format_ws_time(lut) or _format_ws_time(ltt)
        if not text:
            return
        self.ws_server_time_text = text
        self.server_time_label.setText(f"WS Time: {text}")
        if self.popup_token_label and self.active_token:
            row = self.model.row_by_token(self.active_token)
            if row:
                self.popup_token_label.setText(
                    f"Token: {row.get('symbol')} | {row.get('name') or '-'} | LTP: {_format_number(row.get('ltp'), 2)} | WS: {text}"
                )

    def _run_bg(self, fn, name):
        def runner():
            try:
                fn()
            except Exception as exc:
                tb = traceback.format_exc(limit=3)
                self.bus.log.emit(f"{name} failed: {exc}\n{tb}")
                self.bus.error.emit(f"{name} Error", str(exc))

        mt.Thread(target=runner, daemon=True).start()

    def _setup_shortcuts(self):
        self._shortcuts = []
        mappings = (
            ("F3", self.fetch_orderbook_all),
            ("F4", self.fetch_orderbook_traded),
            ("F6", self.fetch_orderbook_pending),
            ("Alt+F6", self.fetch_positions),
            ("F7", self.fetch_holdings),
            ("F8", self.fetch_margin),
            ("F10", self.open_chart_popup_and_start_broadcast),
            ("Ctrl+P", self.show_perf_stats),
            ("Ctrl+I", self.show_ai_advisor),
        )
        for key, handler in mappings:
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(handler)
            self._shortcuts.append(shortcut)

    @staticmethod
    def _pick_column(columns, candidates):
        col_map = {str(col).strip().lower(): col for col in columns}
        for key in candidates:
            matched = col_map.get(key)
            if matched is not None:
                return matched
        return None

    def _build_contract_lookup(self, df):
        self.contract_df = df
        self.contract_colmap = {}
        self.contract_lookup.clear()
        self.contract_cache = {}
        self.contract_row_by_token.clear()
        self.token_metadata.clear()
        self.index_tokens.clear()
        self.index_tokens.add(self.primary_index_token)
        self.didx_tokens.clear()
        if df is None or getattr(df, "empty", True):
            return

        token_col = self._pick_column(df.columns, ("gtoken", "token", "symboltoken", "tokenno"))
        if token_col is None:
            token_col = self._pick_column(df.columns, ("greektoken",))
        symbol_col = self._pick_column(df.columns, ("symbol", "tradingsymbol", "tsym", "name", "sname"))
        exchange_col = self._pick_column(df.columns, ("exchange", "exch", "exch_seg", "exchange_segment"))
        if exchange_col is None:
            exchange_col = self._pick_column(df.columns, ("exchangesegment",))
        name_col = self._pick_column(df.columns, ("description", "name", "companyname", "symbol", "tradingsymbol"))
        asset_col = self._pick_column(df.columns, ("assettype", "asset_type", "instrumenttype", "segment", "series"))
        inst_col = self._pick_column(df.columns, ("series/insttype", "series", "insttype", "instrumenttype"))
        expiry_col = self._pick_column(df.columns, ("expirydate", "expiry", "expdate"))
        strike_col = self._pick_column(df.columns, ("strikeprice", "strike", "strike_price"))
        option_col = self._pick_column(df.columns, ("optiontype", "opttype", "option_type"))

        self.contract_colmap = {
            "token": token_col,
            "exchange": exchange_col,
            "symbol": symbol_col,
            "name": name_col,
            "asset": asset_col,
            "inst": inst_col,
            "expiry": expiry_col,
            "strike": strike_col,
            "option": option_col,
        }

        if token_col is None:
            self.bus.log.emit("Contract data loaded but token column not found.")
            return

        cache = {
            "exchanges": set(),
            "exchange_to_symbols": {},
            "fo_exsym_to_inst": {},
            "fo_rows_by_exsyminst": {},
            "fo_exsyminst_to_expiry": {},
            "fo_exsyminstexp_to_strike": {},
            "fo_exsyminstexpstrike_to_option": {},
            "eq_row_by_exsym": {},
            "fo_row_by_fullkey": {},
        }

        for _, row in df.iterrows():
            token = str(row.get(token_col, "")).strip()
            if not token:
                continue
            exchange = str(row.get(exchange_col, "")).strip().upper() if exchange_col else ""
            symbol = str(row.get(symbol_col, "")).strip().upper() if symbol_col else ""
            name = str(row.get(name_col, "")).strip() if name_col else symbol
            asset_type = str(row.get(asset_col, "")).strip().upper() if asset_col else ""
            inst = str(row.get(inst_col, "")).strip().upper() if inst_col else ""
            expiry = self._to_text(row.get(expiry_col)) if expiry_col else ""
            strike = self._to_text(row.get(strike_col)) if strike_col else ""
            option = self._to_text(row.get(option_col)).upper() if option_col else ""

            if symbol:
                key = f"{exchange}:{symbol}" if exchange else symbol
                self.contract_lookup[key] = token
                if symbol not in self.contract_lookup:
                    self.contract_lookup[symbol] = token
            self.token_metadata[token] = {"name": name or symbol, "exchange": exchange, "symbol": symbol}
            meta_text = f"{exchange} {symbol} {name} {asset_type}".upper()
            inst_text = inst.upper()
            if (
                ("INDEX" in meta_text)
                or any(idx in meta_text for idx in ("NIFTY", "BANKNIFTY", "SENSEX", "FINNIFTY"))
                or ("IDX" in inst_text and not expiry)
            ):
                self.index_tokens.add(token)
            if inst_text == "DIDX":
                self.didx_tokens.add(token)

            if exchange:
                cache["exchanges"].add(exchange)
            if exchange and symbol:
                cache["exchange_to_symbols"].setdefault(exchange, set()).add(symbol)

            row_data = {
                "token": token,
                "exchange": exchange,
                "symbol": symbol,
                "name": name or symbol,
                "inst": inst,
                "expiry": expiry,
                "strike": strike,
                "option": option,
                "asset_type": asset_type,
            }
            self.contract_row_by_token[token] = row_data
            exsym = (exchange, symbol)
            if self._is_fo_segment(exchange):
                if inst:
                    cache["fo_exsym_to_inst"].setdefault(exsym, set()).add(inst)
                    exsyminst = (exchange, symbol, inst)
                    cache["fo_rows_by_exsyminst"].setdefault(exsyminst, []).append(row_data)
                    if expiry:
                        cache["fo_exsyminst_to_expiry"].setdefault(exsyminst, set()).add(expiry)
                    exsyminstexp = (exchange, symbol, inst, expiry)
                    if strike:
                        cache["fo_exsyminstexp_to_strike"].setdefault(exsyminstexp, set()).add(strike)
                    exsyminstexpstrike = (exchange, symbol, inst, expiry, strike)
                    if option:
                        cache["fo_exsyminstexpstrike_to_option"].setdefault(exsyminstexpstrike, set()).add(option)
                    full_key = (exchange, symbol, inst, expiry, strike, option)
                    cache["fo_row_by_fullkey"][full_key] = row_data
            else:
                if exsym not in cache["eq_row_by_exsym"]:
                    cache["eq_row_by_exsym"][exsym] = row_data

        for key in (
            "exchange_to_symbols",
            "fo_exsym_to_inst",
            "fo_exsyminst_to_expiry",
            "fo_exsyminstexp_to_strike",
            "fo_exsyminstexpstrike_to_option",
        ):
            cache[key] = {k: self._sort_mixed(v) for k, v in cache[key].items()}
        cache["exchanges"] = sorted(cache["exchanges"])
        self.contract_cache = cache

    @staticmethod
    def _combo_fill(combo, values):
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("")
        for value in values:
            combo.addItem(str(value))
        combo.setCurrentIndex(0)
        combo.blockSignals(False)

    @staticmethod
    def _to_text(value):
        if value is None:
            return ""
        text = str(value).strip()
        return "" if text.lower() == "nan" else text

    @staticmethod
    def _is_fo_segment(exchange_segment):
        text = str(exchange_segment or "").upper()
        return text.endswith("FO") or "FO" in text

    @staticmethod
    def _inst_needs_expiry(inst):
        text = str(inst or "").upper()
        return text.startswith("FUT") or text.startswith("OPT")

    @staticmethod
    def _sort_mixed(values):
        def key_fn(item):
            text = str(item)
            try:
                return (0, float(text))
            except Exception:
                return (1, text)

        return sorted(values, key=key_fn)

    def _filtered_contract_df(self, exchange=None, symbol=None, inst=None, expiry=None, strike=None, option=None):
        df = self.contract_df
        if df is None or getattr(df, "empty", True):
            return None
        col = self.contract_colmap

        def apply_filter(frame, col_key, value):
            column = col.get(col_key)
            if not column or value in (None, ""):
                return frame
            return frame[frame[column].astype(str).str.strip().str.upper() == str(value).strip().upper()]

        out = df
        out = apply_filter(out, "exchange", exchange)
        out = apply_filter(out, "symbol", symbol)
        out = apply_filter(out, "inst", inst)
        out = apply_filter(out, "expiry", expiry)
        out = apply_filter(out, "strike", strike)
        out = apply_filter(out, "option", option)
        return out

    def _refresh_contract_filters(self):
        if not hasattr(self, "contract_exchange_combo"):
            return
        cache = self.contract_cache or {}
        if not cache:
            self._combo_fill(self.contract_exchange_combo, [])
            self._combo_fill(self.contract_symbol_combo, [])
            self._combo_fill(self.contract_inst_combo, [])
            self._combo_fill(self.contract_expiry_combo, [])
            self._combo_fill(self.contract_strike_combo, [])
            self._combo_fill(self.contract_option_combo, [])
            self.contract_derivative_row.setVisible(False)
            return

        exchanges = cache.get("exchanges", [])
        self._combo_fill(self.contract_exchange_combo, exchanges)
        self._combo_fill(self.contract_symbol_combo, [])
        self._combo_fill(self.contract_inst_combo, [])
        self._combo_fill(self.contract_expiry_combo, [])
        self._combo_fill(self.contract_strike_combo, [])
        self._combo_fill(self.contract_option_combo, [])
        self.contract_derivative_row.setVisible(False)

    def _on_contract_exchange_changed(self, value):
        exchange = str(value).strip().upper()
        symbols = []
        if exchange:
            symbols = self.contract_cache.get("exchange_to_symbols", {}).get((exchange), [])
        self._combo_fill(self.contract_symbol_combo, symbols)
        self._combo_fill(self.contract_inst_combo, [])
        self._combo_fill(self.contract_expiry_combo, [])
        self._combo_fill(self.contract_strike_combo, [])
        self._combo_fill(self.contract_option_combo, [])
        self.contract_derivative_row.setVisible(self._is_fo_segment(exchange))

    def _on_contract_symbol_changed(self, value):
        exchange = self._to_text(self.contract_exchange_combo.currentText()).upper()
        symbol = self._to_text(value).upper()
        if not self._is_fo_segment(exchange):
            return
        inst_values = []
        if symbol:
            inst_values = self.contract_cache.get("fo_exsym_to_inst", {}).get((exchange, symbol), [])
        self._combo_fill(self.contract_inst_combo, inst_values)
        self._combo_fill(self.contract_expiry_combo, [])
        self._combo_fill(self.contract_strike_combo, [])
        self._combo_fill(self.contract_option_combo, [])

    def _on_contract_inst_changed(self, value):
        exchange = self._to_text(self.contract_exchange_combo.currentText()).upper()
        symbol = self._to_text(self.contract_symbol_combo.currentText()).upper()
        inst = self._to_text(value).upper()
        rows = self.contract_cache.get("fo_rows_by_exsyminst", {}).get((exchange, symbol, inst), []) if inst else []
        expiry_values = []
        if rows:
            expiry_values = self.contract_cache.get("fo_exsyminst_to_expiry", {}).get((exchange, symbol, inst), [])
        self._combo_fill(self.contract_expiry_combo, expiry_values)
        if not expiry_values:
            strikes = self._sort_mixed({self._to_text(r.get("strike")) for r in rows if self._to_text(r.get("strike"))})
            options = sorted({self._to_text(r.get("option")).upper() for r in rows if self._to_text(r.get("option"))})
            self._combo_fill(self.contract_strike_combo, strikes)
            self._combo_fill(self.contract_option_combo, options)
        else:
            self._combo_fill(self.contract_strike_combo, [])
            self._combo_fill(self.contract_option_combo, [])

    def _on_contract_expiry_changed(self, value):
        exchange = self._to_text(self.contract_exchange_combo.currentText()).upper()
        symbol = self._to_text(self.contract_symbol_combo.currentText()).upper()
        inst = self._to_text(self.contract_inst_combo.currentText()).upper()
        expiry = self._to_text(value)
        strikes = []
        if expiry:
            strikes = self.contract_cache.get("fo_exsyminstexp_to_strike", {}).get((exchange, symbol, inst, expiry), [])
        self._combo_fill(self.contract_strike_combo, strikes)
        if not strikes and expiry:
            rows = self.contract_cache.get("fo_rows_by_exsyminst", {}).get((exchange, symbol, inst), [])
            options = sorted(
                {
                    self._to_text(r.get("option")).upper()
                    for r in rows
                    if self._to_text(r.get("expiry")) == expiry and self._to_text(r.get("option"))
                }
            )
            self._combo_fill(self.contract_option_combo, options)
        else:
            self._combo_fill(self.contract_option_combo, [])

    def _on_contract_strike_changed(self, value):
        exchange = self._to_text(self.contract_exchange_combo.currentText()).upper()
        symbol = self._to_text(self.contract_symbol_combo.currentText()).upper()
        inst = self._to_text(self.contract_inst_combo.currentText()).upper()
        expiry = self._to_text(self.contract_expiry_combo.currentText())
        strike = self._to_text(value)
        options = []
        if strike:
            key = (exchange, symbol, inst, expiry, strike)
            options = self.contract_cache.get("fo_exsyminstexpstrike_to_option", {}).get(key, [])
        self._combo_fill(self.contract_option_combo, options)

    def _on_contract_enter_pressed(self):
        if not self.api:
            self.bus.error.emit("Not Connected", "Login first.")
            return
        exchange = self._to_text(self.contract_exchange_combo.currentText()).upper()
        symbol = self._to_text(self.contract_symbol_combo.currentText()).upper()
        if not exchange or not symbol:
            self.bus.error.emit("Incomplete Filter", "Select Exchange Segment and Symbol.")
            return

        if self._is_fo_segment(exchange):
            inst = self._to_text(self.contract_inst_combo.currentText()).upper()
            expiry = self._to_text(self.contract_expiry_combo.currentText())
            strike = self._to_text(self.contract_strike_combo.currentText())
            option = self._to_text(self.contract_option_combo.currentText()).upper()
            if not inst:
                self.bus.error.emit(
                    "Incomplete Filter",
                    "For FO, select Series/InstType before Enter.",
                )
                return
            rows = self.contract_cache.get("fo_rows_by_exsyminst", {}).get((exchange, symbol, inst), [])
            if not rows:
                self.bus.error.emit("Not Found", "No contract matched selected filters.")
                return
            if self._inst_needs_expiry(inst):
                if not expiry:
                    self.bus.error.emit("Incomplete Filter", "Select Expiry Date for Futures/Options.")
                    return
                filtered = [r for r in rows if self._to_text(r.get("expiry")) == expiry]
                if strike:
                    filtered = [r for r in filtered if self._to_text(r.get("strike")) == strike]
                if option:
                    filtered = [r for r in filtered if self._to_text(r.get("option")).upper() == option]
                row = filtered[0] if filtered else None
            else:
                filtered = rows
                if strike:
                    filtered = [r for r in filtered if self._to_text(r.get("strike")) == strike]
                if option:
                    filtered = [r for r in filtered if self._to_text(r.get("option")).upper() == option]
                row = filtered[0] if filtered else rows[0]
        else:
            row = self.contract_cache.get("eq_row_by_exsym", {}).get((exchange, symbol))
        if not row:
            self.bus.error.emit("Not Found", "No contract matched selected filters.")
            return
        token = self._to_text(row.get("token"))
        if not token:
            self.bus.error.emit("Contract Error", "Selected contract has empty GreekToken.")
            return

        self._subscribe_from_contract_row(token, row)

    def _subscribe_from_contract_row(self, token, row):
        token = str(token).strip()
        if not token:
            return
        exchange = self._to_text(row.get("exchange"))
        symbol = self._to_text(row.get("symbol"))
        name = self._to_text(row.get("name")) or symbol
        inst = self._to_text(row.get("inst"))
        expiry = self._to_text(row.get("expiry"))
        strike = self._to_text(row.get("strike"))
        option = self._to_text(row.get("option"))

        preview_name = " ".join(part for part in (name, inst, expiry, strike, option) if part).strip()
        self.model.upsert_many(
            {
                token: {
                    "symbol": token,
                    "name": preview_name or name or symbol,
                    "exch": exchange,
                    "asset_type": inst,
                }
            }
        )
        self._start_stream_for_tokens([token], source_label="ContractFilter")
        row_idx = self.model.find_row_by_token(token)
        if row_idx is not None:
            self.table.selectRow(row_idx)
            self.table.scrollTo(self.model.index(row_idx, 0))
            self._activate_token(token, open_chart=False)
        self.bus.log.emit(f"Subscribed token {token} from contract filter.")

    def refresh_contract_data(self):
        if not self.api:
            self.bus.error.emit("Not Connected", "Connect first.")
            return

        def task():
            df = self.api.get_contract_data()
            self._build_contract_lookup(df)
            self.bus.contract_ready.emit()
            self.bus.log.emit(f"Contract data loaded: {len(self.token_metadata)} tokens.")

        self._run_bg(task, "Contract Data")

    def _resolve_tokens(self, entries):
        resolved = []
        unresolved = []
        default_exchange = "NSE"

        for raw in entries:
            item = str(raw).strip()
            if not item:
                continue

            if item.isdigit():
                resolved.append(item)
                continue

            exchange = default_exchange
            symbol = item
            if ":" in item:
                parts = item.split(":", 1)
                exchange = parts[0].strip().upper() or default_exchange
                symbol = parts[1].strip()
            symbol_key = symbol.upper()

            token = self.contract_lookup.get(f"{exchange}:{symbol_key}") or self.contract_lookup.get(symbol_key)
            if token:
                resolved.append(str(token))
            else:
                unresolved.append(item)

        return list(dict.fromkeys(resolved)), unresolved

    def _name_for_token(self, token):
        meta = self.token_metadata.get(str(token), {})
        return meta.get("name", "")

    def _parse_tokens(self):
        if not hasattr(self, "token_input"):
            return [], []
        raw = self.token_input.toPlainText().strip()
        if not raw:
            return [], []
        entries = []
        for item in raw.replace("\n", ",").split(","):
            value = item.strip()
            if value:
                entries.append(value)
        return self._resolve_tokens(entries)

    def _extract_ohlc_series(self, rows):
        closes = []
        candles = []
        times = []
        if not isinstance(rows, list):
            return closes, candles, times
        for row in rows:
            opn = high = low = close = None
            ts = None
            close = None
            if isinstance(row, dict):
                opn = row.get("open", row.get("o", row.get("Open")))
                high = row.get("high", row.get("h", row.get("High")))
                low = row.get("low", row.get("l", row.get("Low")))
                close = row.get("close", row.get("c", row.get("ltp", row.get("Close"))))
                ts = row.get("time", row.get("datetime", row.get("timestamp", row.get("lut", row.get("ltt")))))
            elif isinstance(row, (list, tuple)):
                if len(row) > 4:
                    opn, high, low, close = row[1], row[2], row[3], row[4]
                    ts = row[0]
                elif len(row) >= 4:
                    opn, high, low, close = row[0], row[1], row[2], row[3]
            elif isinstance(row, str):
                parts = row.split("|") if "|" in row else row.split(",")
                if len(parts) > 4:
                    opn, high, low, close = parts[1], parts[2], parts[3], parts[4]
                    ts = parts[0]
                elif len(parts) >= 4:
                    opn, high, low, close = parts[0], parts[1], parts[2], parts[3]
                elif parts:
                    close = parts[-1]

            c_val = _to_float(close)
            if c_val is not None:
                closes.append(c_val)
                times.append(str(ts) if ts is not None else "")

            o_val = _to_float(opn)
            h_val = _to_float(high)
            l_val = _to_float(low)
            if None not in (o_val, h_val, l_val, c_val):
                candles.append((o_val, h_val, l_val, c_val))

        return closes, candles, times

    def _fetch_ohlc_for_token(self, token):
        if not self.api or not token:
            return
        token = str(token)
        interval = self.timeframe_to_interval.get(self.active_timeframe, 5)
        date_key = datetime.now().strftime("%Y%m%d")
        request_key = f"{token}:{interval}:{date_key}"
        self._ohlc_request_key = request_key

        def task():
            rows = self.api.get_ohlc_data(token=token, date=date_key, interval=interval)
            closes, candles, times = self._extract_ohlc_series(rows)
            if closes:
                self.bus.ohlc_series.emit(request_key, closes, candles, times)
                self.bus.log.emit(f"OHLC loaded for token {token} ({self.active_timeframe}).")
            else:
                self.bus.log.emit(f"No OHLC data for token {token} ({self.active_timeframe}).")

        self._run_bg(task, "OHLC")

    def _on_ohlc_series(self, request_key, closes, candles, times):
        if request_key != self._ohlc_request_key:
            return
        token = request_key.split(":", 1)[0]
        if self.chart_popup_widget:
            self.chart_popup_widget.set_history(token, closes, candles, times)

    def _normalize_tick(self, tick):
        if isinstance(tick, dict):
            token = tick.get("symbol")
            if token is None:
                return None
            payload = dict(tick)
            payload.pop("level2", None)
            payload["symbol"] = str(token)
            payload["name"] = payload.get("name", "") or self._name_for_token(token)
            if "oi" not in payload and "currentOI" in payload:
                payload["oi"] = payload.get("currentOI")
            return payload

        if not isinstance(tick, (list, tuple)) or len(tick) == 0:
            return None

        token = tick[0]
        if token is None:
            return None

        name = tick[1] if len(tick) > 1 else ""
        if not name:
            name = self._name_for_token(token)

        return {
            "symbol": str(token),
            "name": name,
            "ltp": tick[2] if len(tick) > 2 else None,
            "ltt": tick[3] if len(tick) > 3 else None,
            "lut": tick[4] if len(tick) > 4 else None,
            "tot_vol": tick[5] if len(tick) > 5 else None,
            "oi": tick[6] if len(tick) > 6 else None,
        }

    def focus_token_from_toolbar(self):
        if not hasattr(self, "focus_token_input"):
            return
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
            with_chart = bool(QApplication.keyboardModifiers() & Qt.ControlModifier)
            self._activate_token(token, open_chart=with_chart)

    def _on_table_right_click(self, pos):
        index = self.table.indexAt(pos)
        if not index.isValid():
            return
        self.table.selectRow(index.row())
        token = self.model.token_at_row(index.row())
        if not token:
            return
        self._activate_token(token, open_chart=True)

    def _activate_token(self, token, open_chart=False):
        token = str(token)
        self.active_token = token
        if hasattr(self, "focus_token_input"):
            self.focus_token_input.setText(token)
        row = self.model.row_by_token(token)
        self._set_quote_strip(row)
        if open_chart:
            self.open_chart_popup_and_start_broadcast()
        elif self.chart_popup and self.chart_popup.isVisible() and self.chart_popup_widget:
            self.chart_popup_widget.set_active_token(token)
            self._fetch_ohlc_for_token(token)
        if row:
            if self.popup_token_label:
                self.popup_token_label.setText(f"Token: {row.get('symbol')} | {row.get('name') or '-'}")
            if self.popup_price_input:
                ltp = _to_float(row.get("ltp"))
                if ltp is not None and self.popup_ordtype_combo and self.popup_ordtype_combo.currentText() == "LIMIT":
                    self.popup_price_input.setText(f"{ltp:.2f}")
        elif self.popup_token_label:
            self.popup_token_label.setText(f"Token: {token}")

    def set_timeframe(self, tf):
        self.active_timeframe = tf
        if self.popup_interval_combo and self.popup_interval_combo.currentText() != tf:
            self.popup_interval_combo.setCurrentText(tf)
        if self.chart_popup_widget:
            self.chart_popup_widget.set_timeframe(tf)
        if self.active_token:
            self._fetch_ohlc_for_token(self.active_token)

    def set_chart_type(self, chart_type_text):
        mode = "candlestick" if str(chart_type_text).strip().lower().startswith("candle") else "line"
        self.chart_mode = mode
        if self.popup_charttype_combo:
            target = "Candlestick" if mode == "candlestick" else "Line"
            if self.popup_charttype_combo.currentText() != target:
                self.popup_charttype_combo.setCurrentText(target)
        if self.chart_popup_widget:
            self.chart_popup_widget.set_chart_type(mode)

    def place_order_from_popup(self, side):
        if not self.api:
            self.bus.error.emit("Not Connected", "Connect first.")
            return
        if not self.active_token:
            self.bus.error.emit("No Token", "Select a token from watchlist first.")
            return
        row = self.model.row_by_token(self.active_token)
        symbol = row.get("name") if row else ""
        order_type = self.popup_ordtype_combo.currentText().strip() if self.popup_ordtype_combo else "MARKET"
        qty = self.popup_qty_input.text().strip() if self.popup_qty_input else "1"
        price = self.popup_price_input.text().strip() if self.popup_price_input else "0"
        try:
            qty_num = int(float(qty or "0"))
        except Exception:
            self.bus.error.emit("Invalid Qty", "Qty must be a positive number.")
            return
        if qty_num <= 0:
            self.bus.error.emit("Invalid Qty", "Qty must be greater than 0.")
            return
        if order_type.upper() == "MARKET":
            price = "0"
        else:
            try:
                price_num = float(price or "0")
            except Exception:
                self.bus.error.emit("Invalid Price", "Price must be numeric.")
                return
            if price_num <= 0:
                self.bus.error.emit("Invalid Price", "Limit/IOC orders require price > 0.")
                return

        self._submit_order(
            token=str(self.active_token),
            symbol=str(symbol or ""),
            qty=str(qty_num),
            price=price or "0",
            side=side,
            ordtype=order_type,
            trigprice="0",
        )

    def _selected_watch_row(self):
        indexes = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not indexes:
            return None
        token = self.model.token_at_row(indexes[0].row())
        if not token:
            return None
        return self.model.row_by_token(token)

    def open_quick_order_for_selection(self, side):
        if not self.api:
            self.bus.error.emit("Not Connected", "Login first.")
            return
        row = self._selected_watch_row()
        if not row:
            self.bus.error.emit("No Selection", "Select a watchlist row first.")
            return
        token = str(row.get("symbol") or "")
        symbol = str(row.get("name") or "")
        ltp = _to_float(row.get("ltp"))
        dialog = QuickOrderDialog(self, side=side, token=token, symbol=symbol, ltp=ltp)
        if dialog.exec() != QDialog.Accepted:
            return
        payload = dialog.payload()
        self._submit_order(
            token=payload["token"],
            symbol=payload["symbol"],
            qty=payload["qty"],
            price=payload["price"],
            side=side,
            ordtype=payload["ordtype"],
            trigprice=payload["trigger"],
        )

    @staticmethod
    def _map_side_flag(value):
        text = str(value or "").strip().upper()
        if text in ("1", "BUY", "B"):
            return "1"
        if text in ("2", "SELL", "S"):
            return "2"
        raise ValueError("Invalid Buy/Sell. Use 1/BUY for buy or 2/SELL for sell.")

    @staticmethod
    def _map_ordtype_flag(value):
        text = str(value or "").strip().upper()
        if text in ("1", "LIMIT", "LMT"):
            return "1"
        if text in ("2", "MARKET", "MKT"):
            return "2"
        if text in ("3", "IOC"):
            return "3"
        raise ValueError("Invalid Order Type. Use 1/LIMIT, 2/MARKET, or 3/IOC.")

    def _submit_order(self, token, symbol, qty, price, side, ordtype, trigprice="0"):
        if not self.api:
            self.bus.error.emit("Not Connected", "Connect first.")
            return

        def task():
            side_flag = self._map_side_flag(side)
            ordtype_flag = self._map_ordtype_flag(ordtype)
            price_value = str(price)
            if ordtype_flag == "2":
                price_value = "0"
            resp = self.api.place_order(
                tokenno=str(token),
                symbol=str(symbol),
                lot="1",
                qty=str(qty),
                price=price_value,
                buysell=side_flag,
                ordtype=ordtype_flag,
                trigprice=str(trigprice),
                exchange="NSE",
                validity="0",
                strategyname="GreekViewPro",
            )
            self.bus.log.emit(f"Place order response: {resp}")

        self._run_bg(task, "Place Order")

    def open_login_popup(self):
        if self.api:
            self.bus.log.emit("Already connected.")
            return
        dialog = LoginDialog(self, defaults=self.login_config)
        if dialog.exec() != QDialog.Accepted:
            self.bus.log.emit("Login cancelled.")
            return
        self.connect_api(dialog.get_credentials())

    def connect_api(self, credentials):
        def task():
            try:
                self.api = GreekAPI(**credentials)
            except Exception:
                self.api = None
                self.bus.session_info.emit("Not logged in")
                self.bus.login_state.emit(False)
                self.bus.status.emit("Disconnected")
                raise
            self.login_config = dict(credentials)
            masked_user = credentials.get("user") or "-"
            account = credentials.get("ac_no") or "-"
            self.bus.status.emit("Connected")
            self.bus.session_info.emit(f"Logged in: {masked_user} | A/C: {account}")
            self.bus.login_state.emit(True)
            self.bus.log.emit("Login successful. Connected to API.")
            try:
                df = self.api.get_contract_data()
                self._build_contract_lookup(df)
                self.bus.contract_ready.emit()
                self.bus.log.emit(f"Contract data loaded: {len(self.token_metadata)} tokens.")
                auto_index_tokens = sorted(set(self.didx_tokens))
                if auto_index_tokens:
                    self._start_stream_for_tokens(auto_index_tokens, source_label="IndexViewAuto", track_watchlist=False)
                else:
                    self._start_stream_for_tokens([self.primary_index_token], source_label="IndexViewAuto", track_watchlist=False)
            except Exception as exc:
                self.bus.log.emit(f"Contract data load failed: {exc}")

        self.bus.status.emit("Connecting...")
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
            self.account_auto_refresh_timer.stop()
            self.contract_df = None
            self.contract_cache = {}
            self.contract_lookup.clear()
            self.contract_row_by_token.clear()
            self.token_metadata.clear()
            self.index_tokens.clear()
            self.didx_tokens.clear()
            self.stream_subscribed_tokens.clear()
            self.bus.session_info.emit("Not logged in")
            self.bus.login_state.emit(False)
            self.bus.status.emit("Disconnected")
            self.bus.log.emit("Disconnected.")

        self._run_bg(task, "Disconnect")

    def start_stream(self):
        if not self.api:
            self.bus.error.emit("Not Connected", "Connect first.")
            return

        tokens, unresolved = self._parse_tokens()
        if not tokens:
            self.bus.error.emit("No Tokens", "Provide valid token/symbol list.")
            return
        if unresolved:
            self.bus.log.emit(f"Unresolved entries skipped: {', '.join(unresolved[:12])}")

        self._start_stream_for_tokens(tokens, source_label="Manual")

    def _start_stream_for_tokens(self, tokens, source_label="Manual", track_watchlist=True):
        base_tokens = [str(t) for t in tokens if str(t).strip()]
        if self.streaming_active and self.stream_thread and self.stream_thread.is_alive() and self.api:
            incremental = [tok for tok in dict.fromkeys(base_tokens) if tok not in self.stream_subscribed_tokens]
            if self.primary_index_token not in self.stream_subscribed_tokens:
                incremental.append(self.primary_index_token)
            incremental = list(dict.fromkeys(incremental))
            if incremental:
                def task_sub():
                    self.api.subscribe_token(incremental)
                    self.stream_subscribed_tokens.update(incremental)
                    if track_watchlist:
                        self.current_tokens.update([tok for tok in incremental if tok != self.primary_index_token])
                    self.index_tokens.add(self.primary_index_token)
                    self.bus.log.emit(f"Stream already active. Added {len(incremental)} token(s).")
                self._run_bg(task_sub, f"Subscribe Stream {source_label}")
            else:
                self.bus.log.emit("Stream already active. No new tokens to add.")
            return

        self.streaming_active = True
        self.index_tokens.add(self.primary_index_token)
        stream_tokens = list(dict.fromkeys(base_tokens))
        if self.primary_index_token not in stream_tokens:
            stream_tokens.append(self.primary_index_token)
            self.bus.log.emit(f"Index token added for WS time: {self.primary_index_token}")
        self.stream_subscribed_tokens = set(stream_tokens)
        if track_watchlist:
            self.current_tokens.update(base_tokens)

        def task():
            self.api.start_apollo(
                token_list=stream_tokens,
                req_data="allresp",
            )
            self._start_stream_consumer()
            self.bus.status.emit("Streaming")
            self.bus.log.emit(f"Streaming started ({source_label}) for {len(stream_tokens)} tokens (RAW).")

        self._run_bg(task, f"Start Stream {source_label}")

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
        tokens, unresolved = self._parse_tokens()
        if not tokens:
            self.bus.error.emit("No Tokens", "Provide valid token/symbol list.")
            return
        if unresolved:
            self.bus.log.emit(f"Unresolved entries skipped: {', '.join(unresolved[:12])}")

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
                token = str(normalized.get("symbol"))
                if token not in self.current_tokens and token not in self.index_tokens:
                    continue
                if token in self.index_tokens:
                    self.index_latest[token] = normalized
                    self._update_ws_server_time(normalized.get("ltt"), normalized.get("lut"))
                if token in self.current_tokens:
                    latest[token] = normalized

        if not self.ws_server_time_text or self.ws_server_time_text == "-":
            for value in latest.values():
                self._update_ws_server_time(value.get("ltt"), value.get("lut"))
                if self.ws_server_time_text != "-":
                    break

        if not latest:
            return

        self.model.upsert_many(latest)
        if self.index_popup and self.index_popup.isVisible():
            self._refresh_index_popup_table()

        if self.active_token:
            row = self.model.row_by_token(self.active_token)
            if row:
                if self.chart_popup_widget:
                    self.chart_popup_widget.push_price(
                        self.active_token,
                        row.get("ltp"),
                        row.get("lut") or row.get("ltt"),
                    )
                self._set_quote_strip(row)

        self._perf_counter += 1
        if self.api and self._perf_counter % 10 == 0:
            stats = self.api.get_performance_stats()
            tps = _format_number(stats.get("messages_per_second"), 0)
            backlog = _format_number(stats.get("raw_buffer_size"), 0)
            dropped = _format_number(stats.get("raw_messages_dropped"), 0)
            self.perf_badge.setText(f"ticks/s: {tps}  backlog: {backlog}  dropped: {dropped}")

    def place_order(self, side_override=None):
        row = self._selected_watch_row()
        if not row:
            self.bus.error.emit("No Selection", "Select a watchlist row first.")
            return
        side = side_override if side_override else "BUY"
        self._submit_order(
            token=row.get("symbol"),
            symbol=row.get("name") or "",
            qty="1",
            price="0",
            side=side,
            ordtype="MARKET",
            trigprice="0",
        )

    def cancel_order(self):
        if not self.api:
            self.bus.error.emit("Not Connected", "Connect first.")
            return
        order_id, ok = QInputDialog.getText(self, "Cancel Order", "Order ID:")
        order_id = order_id.strip() if ok and order_id else ""
        if not order_id:
            return

        def task():
            self.api.cancel_order(order_id)
            self.bus.log.emit(f"Cancel order requested for {order_id}")
            self.bus.refresh_orderbook.emit()

        self._run_bg(task, "Cancel Order")

    def fetch_server_time(self):
        if self.ws_server_time_text and self.ws_server_time_text != "-":
            self.bus.log.emit(f"Server time (from WS index tick): {self.ws_server_time_text}")
            return
        self.bus.log.emit("Server time not available yet from websocket. Start stream and wait for index tick.")

    def show_perf_stats(self):
        if not self.api:
            self.bus.error.emit("Not Connected", "Connect first.")
            return

        def task():
            stats = self.api.get_performance_stats()
            text = json.dumps(stats, default=str, indent=2)
            self.bus.log.emit(f"Performance stats: {text}")
            QTimer.singleShot(0, lambda: QMessageBox.information(self, "Performance Stats", text))

        self._run_bg(task, "Performance Stats")

    def _fetch_account_view(self, title, fn, orderbook_key=None, log_fetch=True):
        if not self.api:
            self.bus.error.emit("Not Connected", "Connect first.")
            return

        def task():
            self._account_refresh_inflight = True
            try:
                data = fn()
                columns, rows = self._rows_from_payload(data)
                if columns and rows:
                    self.bus.account_table.emit(title, columns, rows)
                else:
                    self.bus.account_table.emit(title, [], [])
                if orderbook_key:
                    self.active_orderbook_key = orderbook_key
                if log_fetch:
                    self.bus.log.emit(f"{title} fetched.")
            finally:
                self._account_refresh_inflight = False

        self._run_bg(task, title)

    def fetch_orderbook_all(self, log_fetch=True):
        self._fetch_account_view("Orderbook All", lambda: self.api.Orderbook_All(), orderbook_key="all", log_fetch=log_fetch)

    def fetch_orderbook_traded(self, log_fetch=True):
        self._fetch_account_view("Orderbook Traded", lambda: self.api.Orderbook_Traded(), orderbook_key="traded", log_fetch=log_fetch)

    def fetch_orderbook_rejected(self, log_fetch=True):
        self._fetch_account_view("Orderbook Rejected", lambda: self.api.Orderbook_Rejected(), orderbook_key="rejected", log_fetch=log_fetch)

    def fetch_orderbook_pending(self, log_fetch=True):
        self._fetch_account_view("Orderbook Pending", lambda: self.api.all_pending_order(), orderbook_key="pending", log_fetch=log_fetch)

    def fetch_positions(self, log_fetch=True):
        self._fetch_account_view("Net Positions", lambda: self.api.Net_Position_request(), orderbook_key="positions", log_fetch=log_fetch)

    def fetch_margin(self, log_fetch=True):
        self._fetch_account_view("Margin", lambda: self.api.get_margin_details(), orderbook_key="margin", log_fetch=log_fetch)

    def fetch_holdings(self, log_fetch=True):
        self._fetch_account_view("Holdings", lambda: self.api.get_holding_details(), orderbook_key="holdings", log_fetch=log_fetch)

    def closeEvent(self, event):
        self.streaming_active = False
        self.account_auto_refresh_timer.stop()
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

