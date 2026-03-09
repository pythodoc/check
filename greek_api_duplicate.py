import pandas as pd
import requests as req
import hashlib
import base64
import json
import time
import threading as mt
import os
import websocket as wb
import ssl
from collections import deque
import ujson  # Ultra-fast JSON library

class GreekAPI:
    def __init__(self, user, s_pwd, pwd, procli, ac_no, is_secure, is_base_64, rest_ip, rest_port,iris):
        wb.enableTrace(False)
        self.username = user
        self.session_pwd = s_pwd
        self.userpwd = pwd
        self.gscid = self.username
        self.procli = procli
        self.ac_no = ac_no
        self.url_session_token = 'http://greekapi.greeksoft.in:3001'
        self.is_secure = is_secure
        self.is_base64 = is_base_64
        self.rest_ip = rest_ip
        self.rest_port = rest_port
        self.ssl_verify = False
        self.iris=iris

        # Protocol headers
        self.wbhd = "https://" if is_secure else "http://"
        self.wshd = "wss://" if is_secure else "ws://"

        # ===== PERFORMANCE OPTIMIZATIONS =====

        # Use deque instead of Queue for faster operations
        self.data_buffer = deque(maxlen=10000000)  # Circular buffer to prevent memory issues
        self.order_buffer = deque(maxlen=10000)
        self.raw_msg_buffer = deque(maxlen=300000)  # Raw Apollo messages for async parser workers
        # Lock-free data structure for latest values
        self.latest_data = {}  # token -> latest data mapping
        self.data_callback = None

        # WebSocket related
        self.ws_apollo = None
        self.apollo_port = None
        self.ws_iris = None
        self.iris_port = None
        self.o_i_cache = {}  # Cache OI data by token
        self.hb_stop_event_apollo = mt.Event()
        self.hb_stop_event_iris = mt.Event()
        self.parser_stop_event = mt.Event()
        self.apollo_login_ready = mt.Event()
        self.token_list = []
        self.token_counter = {}
        self.req_data = None
        self.parser_workers = []
        self.raw_trim_lock = mt.Lock()

        # Performance monitoring
        self.message_count = 0
        self.last_message_time = time.time()
        self.parse_error_count = 0
        self.raw_msg_drop_count = 0

        # Tuning knobs for high-token, low-latency operation
        cpu_count = os.cpu_count() or 4
        self.parser_worker_count = max(2, min(8, cpu_count))
        self.raw_backlog_soft_limit = 150000
        self.raw_backlog_target = 100000
        self.login_subscribe_wait_seconds = 1.0
        self.batch_subscribe_size = 75
        self.batch_subscribe_delay = 0.01

        # Initialize connection
        self.session_token = self.get_session_token()
        self.gcid = self.getlogininfo()
        self.session_id, error_code = self._jlogin_new()

        self._handle_login_error(error_code)

    @staticmethod
    def _normalize_allresp_payload(data):
        payload = dict(data or {})
        payload.pop('level2', None)
        schema_defaults = {
            'symbol': '',
            'name': '',
            'exch': '',
            'asset_type': '',
            'ltp': '0',
            'open': '0',
            'high': '0',
            'low': '0',
            'close': '0',
            'change': '0',
            'p_change': '0',
            'tot_vol': '0',
            'tot_buyQty': '0',
            'tot_sellQty': '0',
            'ltq': '0',
            'bid': '0',
            'ask': '0',
            'bidqty': '0',
            'askqty': '0',
            'oi': '0',
            'ltt': '',
            'lut': '',
            'atp': '0',
            'taq': '0',
            'tbq': '0',
            'h52w': '0',
            'l52w': '0',
        }
        for key, default in schema_defaults.items():
            payload.setdefault(key, default)
        return payload

    def _handle_login_error(self, error_code):
        """Centralized error handling for login"""
        error_messages = {
            1: "Password has expired.",
            2: "Invalid password.",
            3: "Failure occurred.",
            4: "Duplicate password not allowed.",
            5: "Max attempts exceeded for wrong password.",
            6: "Inactive user.",
            7: "Inactive user.",
            8: "Invalid 2FA answer.",
            9: "Same ID password.",
            10: "Same login and transaction passwords.",
            11: "Guest not registered.",
            12: "Guest already registered.",
            13: "Retailer does not exist.",
            14: "Version mismatch.",
            17: "Account locked, please contact admin and change password.",
            18: "Login & transaction password expired.",
        }

        if error_code in error_messages:
            print(error_messages[error_code])
            if self.session_id is None:
                print('Please Check or Change the password and Try Again!')
                self.close_connection()
        else:
            print('Connection Has Been Established Successfully!')
            print(f"Session ID: {self.session_id}")

    # ===== ULTRA-FAST JSON METHODS =====

    def base64_to_json(self, coded_string):
        """Ultra-fast base64 to JSON using ujson"""
        string = base64.b64decode(coded_string).decode('utf-8')
        return ujson.loads(string)  # ujson is 2-3x faster than json

    def json_to_base64(self, obj):
        """Ultra-fast JSON to base64 using ujson"""
        string = ujson.dumps(obj)
        return base64.b64encode(string.encode('utf-8')).decode('utf-8')

    def get_session_token(self):
        """Get session token from auth server"""
        url = f'{self.url_session_token}/auth/greek/sessiontoken'
        myobj = {
            "username": str(self.username),
            "password": str(self.session_pwd),
            "validFor": "1d"
        }
        response = req.post(url, json=myobj)
        response.raise_for_status()
        return response.json().get('sessionToken')

    def get_url(self, servicename):
        """Build service URL"""
        return f"{self.wbhd}{self.rest_ip}:{self.rest_port}/{servicename}"

    def _make_request(self, url, params, method='POST'):
        """Centralized HTTP request handler"""
        headers = {
            "Authorization": str(self.session_token),
            "charset": "utf-8",
            "Content-Type": "application/json"
        }

        if self.is_base64:
            data = self.json_to_base64(params)
            response = req.request(method, url, data=data, headers=headers, verify=self.ssl_verify)
            return self.base64_to_json(response.text)
        else:
            response = req.request(method, url, json=params, headers=headers, verify=self.ssl_verify)
            return response.json()

    def getlogininfo(self):
        """Get login info including GCID"""
        svc_req = {
            "request": {
                "svcVersion": "1.0.0",
                "svcGroup": "Login",
                "svcName": "getlogininfo",
                "data": {"gscid": str(self.username)}
            }
        }
        url = self.get_url("getLoginInfo")
        result = self._make_request(url, svc_req)
        return result['response']['data']['gcid']

    def _jlogin_new(self):
        """Login and get session details"""
        url = f"http://{self.rest_ip}:{self.rest_port}/jloginNew"
        pwdhash = hashlib.md5(self.userpwd.encode()).hexdigest()

        svc_req = {
            "request": {
                "data": {
                    "gscid": str(self.username),
                    "deviceDetails": "",
                    "deviceType": "0",
                    "pass": str(pwdhash),
                    "transPass": "",
                    "userType": "Customer",
                    "brokerid": "1",
                    "passType": "0",
                    "version_no": "1.0.1.10",
                    "encryptionType": "1"
                },
                "svcName": "jloginNew",
                "svcGroup": "Login"
            }
        }

        result = self._make_request(url, svc_req)

        data = result['response']['data']
        self.apollo_port = data.get('Apollo_Port')
        self.iris_port = data.get('Iris_Port')

        return result['response']['sessionId'], result['response']['ErrorCode']

    # ===== LIGHTNING FAST WEBSOCKET IMPLEMENTATION =====

    def on_open(self, ws):
        """WebSocket on_open callback."""
        print("WebSocket Connection opened")

        apollo_login_req = {
            "request": {
                "data": {
                    "gscid": str(self.username),
                    "gcid": str(self.gcid),
                    "sessionId": str(self.session_id),
                    "device_type": "0"
                },
                "response_format": "json",
                "request_type": "subscribe",
                "streaming_type": "login"
            }
        }

        payload = self.json_to_base64(apollo_login_req) if self.is_base64 else ujson.dumps(apollo_login_req)
        ws.send(payload)

        # Start subscription only after login-ack (with timeout fallback).
        if self.token_list:
            mt.Thread(target=self._wait_for_login_and_subscribe, daemon=True).start()

        # Start heartbeat in separate thread
        mt.Thread(target=self._heartbeat_loop, args=(ws,), daemon=True).start()

    def _wait_for_login_and_subscribe(self):
        """Wait for login-ack, then subscribe; fallback by timeout to avoid deadlock."""
        self.apollo_login_ready.wait(timeout=self.login_subscribe_wait_seconds)
        if self.token_list:
            self._batch_subscribe_tokens(self.token_list)

    def on_message(self, ws, message):
        """
        Apollo receiver callback.
        Keep this method minimal so the websocket thread never blocks on parsing.
        """
        if len(self.raw_msg_buffer) == self.raw_msg_buffer.maxlen:
            self.raw_msg_drop_count += 1
        self.raw_msg_buffer.append(message)

    def _trim_raw_backlog(self):
        """Drop stale raw messages when parser falls behind to preserve live-ness."""
        backlog = len(self.raw_msg_buffer)
        if backlog <= self.raw_backlog_soft_limit:
            return

        if not self.raw_trim_lock.acquire(blocking=False):
            return

        try:
            while len(self.raw_msg_buffer) > self.raw_backlog_target:
                try:
                    self.raw_msg_buffer.popleft()
                    self.raw_msg_drop_count += 1
                except IndexError:
                    break
        finally:
            self.raw_trim_lock.release()

    def _process_apollo_message(self, message):
        """Decode and process a single Apollo message."""
        apollo_res = self.base64_to_json(message) if self.is_base64 else ujson.loads(message)
        resp = apollo_res.get('response')
        if not resp:
            return

        service_name = resp.get('svcName')
        streaming = resp.get('streaming_type')

        # Login-ack used for reliable subscription timing.
        if streaming == 'login' or service_name == 'login':
            self.apollo_login_ready.set()
            return

        if service_name == 'OpenInterest':
            data = resp.get('data')
            if data:
                tkn_raw = data.get('gtoken')
                if tkn_raw is not None:
                    self.o_i_cache[str(tkn_raw)] = data.get('currentOI')
            return

        if service_name != 'Broadcast' or streaming != 'marketPicture':
            return

        data = resp.get('data')
        if not data:
            return

        tkn_raw = data.get('symbol')
        if tkn_raw is None:
            return
        tkn = str(tkn_raw)
        # For allresp mode, push every tick immediately.
        if self.req_data != 'allresp':
            counter = self.token_counter.get(tkn, 3)
            if counter < 3:
                self.token_counter[tkn] = counter + 1
                return

        sym = data.get('name')
        ltp = data.get('ltp')
        ltt = data.get('ltt')
        lut = data.get('lut')
        oi = self.o_i_cache.get(tkn)

        if self.req_data == 'depth':
            packed_data = (tkn, sym, ltp, data.get('level2'), ltt, lut, oi)
        elif self.req_data == 'ask/bid':
            packed_data = (tkn, sym, data.get('bid'), data.get('ask'), ltt, lut, oi)
        elif self.req_data == 'allresp':
            packed_data = self._normalize_allresp_payload(data)
        else:
            packed_data = (tkn, sym, ltp, ltt, lut, data.get('tot_vol'), oi)

        self.data_buffer.append(packed_data)
        self.latest_data[tkn] = packed_data

        if self.data_callback:
            try:
                self.data_callback(tkn, packed_data)
            except Exception:
                pass

        self.message_count += 1

    def _parser_worker(self):
        """Parser worker thread for high-throughput Apollo decode."""
        while not self.parser_stop_event.is_set():
            self._trim_raw_backlog()

            try:
                message = self.raw_msg_buffer.popleft()
            except IndexError:
                time.sleep(0.0001)
                continue

            try:
                self._process_apollo_message(message)
            except Exception:
                self.parse_error_count += 1

    def _start_parser_workers(self):
        """Start parser workers once per active Apollo session."""
        if self.parser_workers:
            return

        self.parser_stop_event.clear()
        worker_count = self.parser_worker_count
        for _ in range(worker_count):
            worker = mt.Thread(target=self._parser_worker, daemon=True)
            worker.start()
            self.parser_workers.append(worker)

    def _stop_parser_workers(self):
        """Signal parser workers to stop."""
        if not self.parser_workers:
            self.parser_stop_event.set()
            return

        self.parser_stop_event.set()
        for worker in self.parser_workers:
            try:
                worker.join(timeout=0.2)
            except Exception:
                pass
        self.parser_workers = []

    def on_error(self, ws, error):
        """WebSocket error callback"""
        print(f"WebSocket Error: {error}")

    def on_close(self, ws, close_status_code, close_msg):
        """WebSocket close callback"""
        print(f"WebSocket Connection closed: {close_status_code} - {close_msg}")

    def on_open_iris(self,ws):
        """WebSocket on_open callback - OPTIMIZED"""
        print("IRIS WebSocket Connection opened")

        iris_login_req = {
            "request": {
                "data": {
                    "gscid": str(self.username),
                    "gcid": str(self.gcid),
                    "sessionId": str(self.session_id),
                    "device_type": "0"
                },
                "response_format": "json",
                "request_type": "subscribe",
                "streaming_type": "login"
            }
        }

        payload = self.json_to_base64(iris_login_req) if self.is_base64 else ujson.dumps(iris_login_req)
        ws.send(payload)

        # Start heartbeat in separate thread
        mt.Thread(target=self._heartbeat_loop_iris, args=(ws,), daemon=True).start()

    def on_message_iris(self, ws, message):
        try:
            iris_res = self.base64_to_json(message) if self.is_base64 else ujson.loads(message) # ujson is faster
            # print(iris_res)

            resp = iris_res.get('response')
            if not resp:
                return

            if resp.get('svcName') == 'order' and resp.get('streaming_type') == 'RmsRejectionResponse':

                data = resp.get('data')
                if data:
                    self.order_buffer.append(data)

        except Exception:
            pass

    def start_apollo(self, token_list, req_data='ltp', ping_interval=20, ping_timeout=10):
        """
        Start Apollo WebSocket with performance optimizations

        Args:
            token_list: List of tokens to subscribe
            req_data: Type of data to receive ('ltp', 'depth', 'ask/bid', 'allresp')
            ping_interval: WebSocket ping interval (lower = faster disconnect detection)
            ping_timeout: WebSocket ping timeout
        """
        self._stop_parser_workers()
        self.apollo_login_ready.clear()
        self.hb_stop_event_apollo.clear()
        self.hb_stop_event_iris.clear()
        self.raw_msg_buffer.clear()
        self.raw_msg_drop_count = 0
        self.parse_error_count = 0
        self.message_count = 0
        self.last_message_time = time.time()

        self.token_list = list(dict.fromkeys(str(t) for t in token_list))
        self.req_data = req_data

        # Pre-initialize counter to 3 for existing tokens to skip warmup
        self.token_counter = {t: 3 for t in self.token_list}
        self._start_parser_workers()

        apollo_ws_url = f"{self.wshd}{self.rest_ip}:{self.apollo_port}"

        # ===== PERFORMANCE WEBSOCKET OPTIONS =====
        self.ws_apollo = wb.WebSocketApp(
            apollo_ws_url,
            on_open=self.on_open,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close
        )


        # Start in separate thread with optimizations
        thread = mt.Thread(
            target=self.ws_apollo.run_forever,
            kwargs={
                "sslopt": {"cert_reqs": ssl.CERT_NONE},
                "ping_interval": ping_interval,  # Faster ping
                "ping_timeout": ping_timeout,
                "skip_utf8_validation": True  # Skip validation for speed
            }
        )
        thread.daemon = True
        thread.start()

        print(f"Apollo WebSocket started for {len(self.token_list)} tokens")
        if self.iris:
            iris_url = f"{self.wshd}{self.rest_ip}:{self.iris_port}"
            self.ws_iris = wb.WebSocketApp(
                iris_url,
                on_open=self.on_open_iris,
                on_message=self.on_message_iris,
                on_error=self.on_error,
                on_close=self.on_close
            )
            mt.Thread(
                target=self.ws_iris.run_forever,
                kwargs={
                    "sslopt": {"cert_reqs": ssl.CERT_NONE},
                    "ping_interval": 20,
                    "ping_timeout": 10
                },
                daemon=True
            ).start()
            print(f"Iris WebSocket started for Order Response")

    def _heartbeat_loop(self, ws):
        """Optimized heartbeat with pre-compiled message"""
        # Pre-compile heartbeat message (don't recreate every time)
        apollo_hb_req = {
            "request": {
                "data": {
                    "gcid": str(self.gcid),
                    "sessionId": str(self.session_id)
                },
                "response_format": "json",
                "request_type": "subscribe",
                "streaming_type": "HeartBeat"
            }
        }

        # Pre-encode once
        payload = self.json_to_base64(apollo_hb_req) if self.is_base64 else ujson.dumps(apollo_hb_req)

        while not self.hb_stop_event_apollo.is_set():
            try:
                ws.send(payload)
                time.sleep(30)
            except Exception as e:
                print(f"Heartbeat error: {e}")
                break

    def _heartbeat_loop_iris(self, ws):
        """Optimized heartbeat with pre-compiled message"""
        # Pre-compile heartbeat message (don't recreate every time)
        iris_hb_req = {
            "request": {
                "data": {
                    "gcid": str(self.gcid),
                    "sessionId": str(self.session_id)
                },
                "response_format": "json",
                "request_type": "subscribe",
                "streaming_type": "HeartBeat"
            }
        }

        # Pre-encode once
        payload_iris = self.json_to_base64(iris_hb_req) if self.is_base64 else ujson.dumps(iris_hb_req)

        while not self.hb_stop_event_iris.is_set():
            try:
                ws.send(payload_iris)
                time.sleep(60)
            except Exception as e:
                print(f"Heartbeat error: {e}")
                break

    def _batch_subscribe_tokens(self, tokens, batch_size=None):
        """
        Batch subscribe to multiple tokens at once for faster initial setup

        Args:
            tokens: List of tokens
            batch_size: Number of tokens to subscribe in one message
        """
        if not self.ws_apollo:
            return

        batch_size = batch_size or self.batch_subscribe_size
        tokens = list(dict.fromkeys(str(t) for t in tokens))

        # Split into batches
        for i in range(0, len(tokens), batch_size):
            batch = tokens[i:i + batch_size]
            symbols = [{"symbol": str(tkn)} for tkn in batch]

            subscribe_req = {
                "request": {
                    "data": {"symbols": symbols},
                    "response_format": "json",
                    "gscid": str(self.username),
                    "gcid": str(self.gcid),
                    "request_type": "subscribe",
                    "streaming_type": "marketPicture"
                }
            }

            payload = self.json_to_base64(subscribe_req) if self.is_base64 else ujson.dumps(subscribe_req)
            try:
                self.ws_apollo.send(payload)
            except Exception:
                continue

            # Small delay between batches to avoid overwhelming the server
            if i + batch_size < len(tokens):
                time.sleep(self.batch_subscribe_delay)

        print(f"Batch subscribed to {len(tokens)} tokens in {(len(tokens) + batch_size - 1) // batch_size} batches")

    def subscribe_token(self, tokens):
        """
        Subscribe to token(s) - OPTIMIZED

        Args:
            tokens: Single token or list of tokens
        """
        if not isinstance(tokens, list):
            tokens = [tokens]
        tokens = [str(t) for t in tokens]

        # Keep token list in sync for reconnects.
        existing_tokens = set(self.token_list)
        for tkn in tokens:
            if tkn not in existing_tokens:
                self.token_list.append(tkn)
                existing_tokens.add(tkn)

        # Initialize counter for new tokens
        for tkn in tokens:
            if tkn not in self.token_counter:
                self.token_counter[tkn] = 0

        if not self.apollo_login_ready.is_set():
            print(f"Queued {len(tokens)} token(s) for subscribe (waiting for login ack)")
            return

        # Use batch subscribe for multiple tokens
        if len(tokens) > self.batch_subscribe_size:
            self._batch_subscribe_tokens(tokens)
        else:
            # For small numbers, subscribe individually
            for tkn in tokens:
                subscribe_req = {
                    "request": {
                        "data": {"symbols": [{"symbol": str(tkn)}]},
                        "response_format": "json",
                        "gscid": str(self.username),
                        "gcid": str(self.gcid),
                        "request_type": "subscribe",
                        "streaming_type": "marketPicture"
                    }
                }

                payload = self.json_to_base64(subscribe_req) if self.is_base64 else ujson.dumps(subscribe_req)
                self.ws_apollo.send(payload)

        print(f"Subscribed to {len(tokens)} token(s)")

    def unsubscribe_token(self, token):
        """Unsubscribe from token - OPTIMIZED"""
        str_token = str(token)

        if not self.ws_apollo:
            self.token_counter.pop(str_token, None)
            self.latest_data.pop(str_token, None)
            self.o_i_cache.pop(str_token, None)
            self.token_list = [t for t in self.token_list if t != str_token]
            return

        unsubscribe_req = {
            "request": {
                "data": {"symbols": [{"symbol": str_token}]},
                "response_format": "json",
                "gscid": str(self.username),
                "gcid": str(self.gcid),
                "request_type": "unsubscribe",
                "streaming_type": "marketPicture"
            }
        }

        payload = self.json_to_base64(unsubscribe_req) if self.is_base64 else ujson.dumps(unsubscribe_req)
        self.ws_apollo.send(payload)

        # Clean up token data
        self.token_counter.pop(str_token, None)
        self.latest_data.pop(str_token, None)
        self.o_i_cache.pop(str_token, None)
        self.token_list = [t for t in self.token_list if t != str_token]

        print(f"Unsubscribed from token: {token}")

    # ===== ULTRA-FAST DATA ACCESS METHODS =====

    def get_latest_data(self, token=None):
        """
        Get latest data for a token (FASTEST - O(1) lookup)

        Args:
            token: Token to get data for, or None for all tokens

        Returns:
            Latest data for the token or dict of all latest data
        """
        if token:
            return self.latest_data.get(str(token))
        return self.latest_data.copy()

    def data_stream_fast(self):
        """
        FASTEST data streaming - Direct deque access
        Yields data as fast as it arrives
        """
        while True:
            try:
                if self.data_buffer:
                    yield self.data_buffer.popleft()
                # if self.order_buffer:
                #     yield self.order_buffer.popleft()
                else:
                    time.sleep(0.0001)  # Minimal sleep to prevent CPU spinning
            except IndexError:
                time.sleep(0.0001)

    def data_stream_batch(self, batch_size=100):
        """
        Batch data streaming - Get multiple messages at once
        More efficient for high-frequency data

        Args:
            batch_size: Number of messages to yield at once
        """
        while True:
            batch = []
            try:
                # Collect batch_size messages
                for _ in range(batch_size):
                    if self.data_buffer:
                        batch.append(self.data_buffer.popleft())
                    else:
                        break

                if batch:
                    yield batch
                else:
                    time.sleep(0.001)
            except Exception as e:
                if batch:
                    yield batch
                time.sleep(0.001)

    def get_buffer_size(self):
        """Get current buffer size for monitoring"""
        return len(self.data_buffer)

    def clear_buffer(self):
        """Clear data buffer"""
        self.data_buffer.clear()
        print("Data buffer cleared")

    def get_performance_stats(self):
        """Get WebSocket performance statistics"""
        current_time = time.time()
        elapsed = current_time - self.last_message_time

        stats = {
            'total_messages': self.message_count,
            'messages_per_second': self.message_count / elapsed if elapsed > 0 else 0,
            'buffer_size': len(self.data_buffer),
            'raw_buffer_size': len(self.raw_msg_buffer),
            'raw_messages_dropped': self.raw_msg_drop_count,
            'parse_errors': self.parse_error_count,
            'parser_workers': len(self.parser_workers),
            'subscribed_tokens': len(self.token_counter),
            'cached_oi_tokens': len(self.o_i_cache),
            'latest_data_tokens': len(self.latest_data)
        }

        return stats

    def reset_performance_stats(self):
        """Reset performance counters"""
        self.message_count = 0
        self.last_message_time = time.time()
        self.raw_msg_drop_count = 0
        self.parse_error_count = 0

    # ===== CALLBACK-BASED STREAMING (ZERO-COPY) =====

    def set_data_callback(self, callback_func):
        """
        Set a callback function for real-time data (ZERO latency)
        This is the FASTEST method - data is processed immediately

        Args:
            callback_func: Function that takes (token, data) as arguments

        Example:
            def my_callback(token, data):
                print(f"Token {token}: {data}")

            api.set_data_callback(my_callback)
        """
        self.data_callback = callback_func

    def on_message_with_callback(self, ws, message):
        """
        Compatibility wrapper: callback mode uses the same async parser pipeline.
        """
        self.on_message(ws, message)

    def start_apollo_with_callback(self, token_list, callback_func, req_data):
        """
        Start Apollo WebSocket with callback.

        Args:
            token_list: List of tokens to subscribe
            callback_func: Function to call on each data update
            req_data: Type of data to receive

        Example:
            def process_data(token, data):
                print(f"Token: {token}, LTP: {data[2]}")

            api.start_apollo_with_callback(['12345'], process_data)
        """
        self.data_callback = callback_func
        self.start_apollo(token_list, req_data=req_data)

    # ===== PANDAS DATAFRAME CONVERSION (BATCH) =====

    def get_dataframe_batch(self, max_rows=1000):
        """
        Get buffered data as pandas DataFrame (efficient batch conversion)

        Args:
            max_rows: Maximum number of rows to convert

        Returns:
            pandas DataFrame with latest data
        """
        batch = []
        for _ in range(min(max_rows, len(self.data_buffer))):
            try:
                batch.append(self.data_buffer.popleft())
            except IndexError:
                break

        if not batch:
            return pd.DataFrame()

        # Convert based on data format
        if self.req_data == 'allresp':
            return pd.DataFrame(batch)
        elif self.req_data == 'depth':
            return pd.DataFrame(batch, columns=['token', 'symbol', 'ltp', 'depth', 'last_trade_time', 'last_update_time', 'oi'])
        elif self.req_data == 'ask/bid':
            return pd.DataFrame(batch, columns=['token', 'symbol', 'bid', 'ask', 'last_trade_time', 'last_update_time', 'oi'])
        else:
            return pd.DataFrame(batch, columns=['token', 'symbol', 'ltp', 'last_trade_time', 'last_update_time', 'volume', 'oi'])

    def get_latest_dataframe(self):
        """
        Get latest data for all tokens as DataFrame
        Useful for snapshot views
        """
        if not self.latest_data:
            return pd.DataFrame()

        data_list = list(self.latest_data.values())

        if self.req_data == 'allresp':
            return pd.DataFrame(data_list)
        elif self.req_data == 'depth':
            return pd.DataFrame(data_list, columns=['token', 'symbol', 'ltp', 'depth', 'last_trade_time', 'last_update_time', 'oi'])
        elif self.req_data == 'ask/bid':
            return pd.DataFrame(data_list, columns=['token', 'symbol', 'bid', 'ask', 'last_trade_time', 'last_update_time', 'oi'])
        else:
            return pd.DataFrame(data_list, columns=['token', 'symbol', 'ltp', 'last_trade_time', 'last_update_time', 'volume', 'oi'])

    # ===== REST API METHODS (keeping previous implementations) =====

    def token_broadcast(self, tokenno, assettype):
        """Get quote for a single symbol"""
        params = {
            "request": {
                "data": {
                    "token": tokenno,
                    "assetType": assettype,
                    "gscid": str(self.username),
                    "gcid": str(self.gcid),
                },
                "svcName": "getQuoteForSingleSymbol_V2",
                "svcGroup": "Markets"
            }
        }

        url = self.get_url("getQuoteForSingleSymbol_V2")
        result = self._make_request(url, params)
        return result.get('response', {}).get('data')
    
    def multiple_token_broadcast(self,asset_type,exchange,token_list):
        exchange_tokens = [
            {
                "asset_type": str(asset_type),
                "exchange":str(exchange),
                "token": str(token)
            }
            for token in token_list
        ]
        params={
            "request": {
                "data": {
                    "symbolList": exchange_tokens
                },
                "svcName": "getQuoteForMultipleSymbols",
                "svcGroup": "Markets"
            }
        }
        svcname='getQuoteForMultipleSymbols'
        url_mtb = self.get_url(svcname)
        if self.is_base64:
            params = self.json_to_base64(params)
            stoken = self.session_token
            headers = { "Authorization":""+str(stoken) ,"charset": "utf-8", "Content-Type": "application/json" }
            y1 =  req.post(url_mtb,data=params,headers=headers ,verify=self.ssl_verify )
            y1 = y1.text
            z1 = self.base64_to_json(y1)
            response = z1.get('response',{}).get('data',{}).get('quotelist')
        else:
            stoken = self.session_token
            headers = { "Authorization":""+str(stoken) ,"charset": "utf-8", "Content-Type": "application/json" }
            y1 =  req.post(url_mtb,json=params,headers=headers ,verify=self.ssl_verify )
            z1 = y1.json()
            response = z1.get('response',{}).get('data',{}).get('quotelist')
        return response

    def MBP_data(self, exchange, token_list):
        """Get Market By Price data for multiple tokens"""
        exchange_tokens = [
            {"exchange": str(exchange), "token": str(token)}
            for token in token_list
        ]

        params = {
            "request": {
                "data": {
                    "mode": "FULL",
                    "exchangeTokens": exchange_tokens
                },
                "svcName": "getToken_OnlyMbpData",
                "svcGroup": "Markets"
            }
        }

        url = self.get_url("getToken_OnlyMbpData")
        result = self._make_request(url, params, method='GET')
        return result.get('response', {}).get('data', {}).get('fetched', [])

    def server_time(self):
        """Get server time"""
        params = {
            "request": {
                "data": {
                    "token": '101999957',
                    "assetType": 'INDEX',
                    "gscid": str(self.username),
                    "gcid": str(self.gcid),
                },
                "svcName": "getQuoteForSingleSymbol_V2",
                "svcGroup": "Markets"
            }
        }

        url = self.get_url("getQuoteForSingleSymbol_V2")
        data = self._make_request(url,params).get('response',{})
        if data:
            server_time = data.get('serverTime')
            if server_time:
                return pd.to_datetime(server_time, unit='s', errors='coerce') .tz_localize('UTC').tz_convert("Asia/Kolkata").strftime('%d-%m-%Y %H:%M:%S')
        return None

    # ===== ORDER MANAGEMENT METHODS =====

    def place_order(self, tokenno, symbol, lot, qty, price, buysell, ordtype, trigprice, exchange, validity, strategyname):
        """Place a new order"""
        if self.session_id is None:
            print('Order cannot be placed! Session not established.')
            return None

        params = {
            "request": {
                "data": {
                    "trigger_price": str(trigprice),
                    "gtoken": str(tokenno),
                    "side": str(buysell),
                    "gcid": str(self.gcid),
                    "validity": str(validity),
                    "price": str(price),
                    "exchange": str(exchange),
                    "disclosed_qty": "0",
                    "tradeSymbol": str(symbol),
                    "lot": str(lot),
                    "iprocli": str(self.procli),
                    "order_type": str(ordtype),
                    "product": "0",
                    "qty": str(qty),
                    "corderid": "3",
                    "amo": "0",
                    "is_restapi": "1",
                    "gtdExpiry": 0,
                    "is_post_closed": "0",
                    "is_preopen_order": "0",
                    "isSqOffOrder": "false",
                    "offline": "0",
                    "strategyName": str(strategyname),
                    "strategyNo": "124"
                },
                "response_format": "json",
                "request_type": "subscribe",
                "streaming_type": "NewOrderRequest"
            }
        }

        if self.procli == "1":
            params["request"]["data"]["AccountNumber"] = str(self.ac_no)

        url = self.get_url("NewOrderRequest")
        result = self._make_request(url, params)
        return result.get('response')

    def modify_order(self, price, lot, qty, ordtype, gorderid):
        """Modify an existing order"""
        params = {
            "request": {
                "data": {
                    "trigger_price": "0",
                    "gcid": str(self.gcid),
                    "validity": "0",
                    "price": str(price),
                    "gorderid": str(gorderid),
                    "order_type": str(ordtype),
                    "lot": str(lot),
                    "qty": str(qty),
                    "disclosed_qty": "0",
                    "amo": "0",
                    "sl_price": "0",
                    "gtdExpiry": "0"
                },
                "response_format": "json",
                "request_type": "subscribe",
                "streaming_type": "ModifyOrderRequest"
            }
        }

        url = self.get_url("SmallModifyOrderRequest")
        result = self._make_request(url, params)
        return result.get('response')

    def cancel_order(self,ord_id):
        stoken = self.session_token
        svcname = 'Order/'
        url_cancel_ord = self.get_url(svcname)
        url_cancel_ord = url_cancel_ord + str(ord_id)

        headers = { "Authorization":""+str(stoken) ,"charset": "utf-8", "Content-Type": "application/json" }
        params = ""
        if self.is_base64:
            can_response = req.request("DELETE", url_cancel_ord, data=params, headers=headers, verify=self.ssl_verify)
            can_response=self.base64_to_json(can_response.text)
            message=can_response.get('success')
            if message=='true':
                print(f'Order_No: {ord_id}, has been Cancelled!')
            else:
                print(f'Error While Cancelling Order_No: {ord_id} !')
        else:
            can_response = req.request("DELETE", url_cancel_ord, json=params, headers=headers, verify=self.ssl_verify)
            message=can_response.json().get('success')
            if can_response.json().get('ErrorCode')==0 and message=='true':
                print(f'Order_No: {ord_id}, has been Cancelled!')
            else:
                print(f'Error While Cancelling Order_No: {ord_id} !')

    def Order_Trade_status(self, ord_id):
        """Get order trade status"""
        url = self.get_url("getOrderDetail?")
        text = f"greekOrderNo={ord_id}&gscid={self.username}"

        if self.is_base64:
            coded_string = base64.b64encode(text.encode('utf-8')).decode('utf-8')
            url += coded_string
        else:
            url += text

        headers = {
            "Authorization": str(self.session_token),
            "charset": "utf-8",
            "Content-Type": "application/json"
        }

        response = req.get(url, headers=headers, verify=self.ssl_verify)
        result = self.base64_to_json(response.text) if self.is_base64 else response.json()
        data=result.get('data')
        if not data:
            print(f'Data is Not Present for G-ORDER-ID,{ord_id}')
            return None
        else:
            return result.get('data', [{}])[0]

    def _get_orderbook(self, order_status='ALL'):
        """Generic method to get orderbook with different filters"""
        url = self.get_url("getOrderBookDetailWithLegV2?")
        text = f"exchangeType=ALL&ClientCode={self.gcid}&Order_Status={order_status}&Ordertype=ALL&gscid={self.username}"

        if self.is_base64:
            coded_string = base64.b64encode(text.encode('utf-8')).decode('utf-8')
            url += coded_string
        else:
            url += text

        headers = {
            "Authorization": str(self.session_token),
            "charset": "utf-8",
            "Content-Type": "application/json"
        }

        response = req.get(url, headers=headers, verify=self.ssl_verify)
        result = self.base64_to_json(response.text) if self.is_base64 else response.json()
        return result.get('data')

    def Orderbook_All(self):
        """Get all orders"""
        return self._get_orderbook('ALL')

    def Orderbook_Traded(self):
        """Get traded orders"""
        return self._get_orderbook('TRADED')

    def Orderbook_Rejected(self):
        """Get rejected orders"""
        return self._get_orderbook('RMS_REJECTED')

    def all_pending_order(self):
        """Get all pending orders"""
        return self._get_orderbook('Pending')

    def Orderbook_lite(self, order_id):
        """Get lite order details"""
        url = self.get_url("getOrderBookDetail_Lite?")
        text = f"greekOrderNo={order_id}&gscid={self.username}"

        if self.is_base64:
            coded_string = base64.b64encode(text.encode('utf-8')).decode('utf-8')
            url += coded_string
        else:
            url += text

        headers = {
            "Authorization": str(self.session_token),
            "charset": "utf-8",
            "Content-Type": "application/json"
        }

        response = req.get(url, headers=headers, verify=self.ssl_verify)
        result = self.base64_to_json(response.text) if self.is_base64 else response.json()
        return result.get('data')

    # ===== POSITION MANAGEMENT =====

    def Net_Position_request(self):
        """Get net position"""
        params = {
            "request": {
                "FormFactor": "M",
                "data": {"gscid": str(self.username)},
                "svcGroup": "portfolio",
                "svcVersion": "1.0.0",
                "streaming_type": "NPRequest",
                "request_type": "subscribe"
            }
        }

        url = self.get_url("NPRequest")
        result = self._make_request(url, params)
        return result.get('response', {}).get('stockDetails')

    def Net_position_Detailed(self):
        """Get detailed net position"""
        params = {
            "request": {
                "FormFactor": "M",
                "data": {"gscid": str(self.username)},
                "svcGroup": "portfolio",
                "svcVersion": "1.0.0",
                "streaming_type": "NPDetailRequest",
                "request_type": "subscribe"
            }
        }

        url = self.get_url("NPDetailRequest")
        result = self._make_request(url, params)
        return result.get('response', {}).get('stockDetails')

    def Net_Position_Details_strategywise(self, type='ALL'):
        """Get strategy-wise net position"""
        url = self.get_url("getStrategyNameWiseNetPositionDetail?")
        text = f"gscid={self.username}"

        if type == 'EXP':
            text += "&type=expiry"

        if self.is_base64:
            coded_string = base64.b64encode(text.encode('utf-8')).decode('utf-8')
            url += coded_string
        else:
            url += text

        headers = {
            "Authorization": str(self.session_token),
            "charset": "utf-8",
            "Content-Type": "application/json"
        }

        response = req.get(url, headers=headers, verify=self.ssl_verify)
        result = self.base64_to_json(response.text) if self.is_base64 else response.json()
        return result.get('data')

    def get_margin_details(self):
        """Get margin details"""
        params = {
            "request": {
                "FormFactor": "M",
                "data": {
                    "gcid": str(self.gcid),
                    "sessionId": str(self.session_id),
                    "segment": 2,
                    "exchange_type": "-1"
                },
                "svcGroup": "portfolio",
                "svcVersion": "1.0.0",
                "streaming_type": "MarginDetailRequest",
                "request_type": "subscribe"
            }
        }

        url = self.get_url("MarginDetailRequest")
        result = self._make_request(url, params)
        data = result.get('response', {}).get('data')
        return [data] if data else []

    def get_holding_details(self):
        """Get holding details"""
        params = {
            "request": {
                "FormFactor": "M",
                "data": {
                    "gscid": str(self.username),
                    "gcid": str(self.gcid),
                    "sessionId": str(self.session_id),
                },
                "svcGroup": "portfolio",
                "svcVersion": "1.0.0",
                "streaming_type": "HoldingDetailsInfo",
                "request_type": "subscribe"
            }
        }

        url = self.get_url("HoldingDetailsInfo")
        result = self._make_request(url, params)
        return result.get('response', {}).get('data', {}).get('stockDetails')

    # ===== HISTORICAL DATA =====

    def get_ohlc_data(self, token, date, interval, max_retries=5, timeout=60):
        """
        Get OHLC data with retry logic

        Args:
            token: Token ID
            date: Date in format 'YYYYMMDD' (e.g., '20231218')
            interval: Interval in minutes
            max_retries: Maximum retry attempts
            timeout: Request timeout in seconds

        Returns:
            OHLC data list or None
        """
        params = {
            "request": {
                "FormFactor": "M",
                "data": {
                    "gscid": str(self.username),
                    "token": int(token),
                    "interval": int(interval),
                    "date": str(date),
                    "noofdays": 1
                },
                "svcGroup": "portfolio",
                "svcVersion": "1.0.0",
                "svcName": "jhistorical_New",
                "requestType": "U"
            }
        }

        url = self.get_url('get_ohlc')
        # headers = {
        #     "Authorization": str(self.session_token),
        #     "charset": "utf-8",
        #     "Content-Type": "application/json"
        # }

        for attempt in range(1, max_retries + 1):
            try:
                result=self._make_request(url,params)

                response_data = result.get('response', {}).get('data', {}).get('data')

                if response_data and len(response_data) > 5:
                    return response_data
                else:
                    print(f'Insufficient data for date {date}, retrying... (Attempt {attempt}/{max_retries})')
                    time.sleep(10)

            except Exception as e:
                print(f'Error getting OHLC data (Attempt {attempt}/{max_retries}): {e}')
                if attempt < max_retries:
                    time.sleep(10)
                else:
                    print(f'Failed to get OHLC data after {max_retries} attempts')
                    return None

        return None

    def get_contract_data(self):
        """Get all contract data"""
        url = self.get_url("getAllContract")
        headers = {
            "Authorization": str(self.session_token),
            "charset": "utf-8",
            "Content-Type": "application/json"
        }

        response = req.get(url, headers=headers, verify=self.ssl_verify)
        lines = response.text.strip().split('\n')

        if not lines:
            return pd.DataFrame()

        headers_list = lines[0].split(',')
        json_list = []

        for row in lines[1:]:
            values = row.split(',')
            item = dict(zip(headers_list, values))
            json_list.append(item)

        return pd.DataFrame(json_list)

    # ===== CONNECTION MANAGEMENT =====

    def close_connection(self):
        """Close WebSocket connection and stop heartbeat"""
        print("Closing WebSocket connection and stopping heartbeat...")
        self.hb_stop_event_apollo.set()
        self.hb_stop_event_iris.set()
        self.apollo_login_ready.clear()
        self._stop_parser_workers()

        if self.ws_apollo:
            try:
                self.ws_apollo.close()
            except Exception as e:
                print(f"Error closing Apollo WebSocket: {e}")
            finally:
                self.ws_apollo = None

        if self.ws_iris:
            try:
                self.ws_iris.close()
            except Exception as e:
                print(f"Error closing Iris WebSocket: {e}")
            finally:
                self.ws_iris = None

        # Clean up resources
        self.raw_msg_buffer.clear()
        self.data_buffer.clear()
        self.order_buffer.clear()
        self.latest_data.clear()
        self.o_i_cache.clear()
        self.token_counter.clear()
        self.token_list = []

        print("Connection closed successfully!")
