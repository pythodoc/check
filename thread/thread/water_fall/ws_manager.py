# ws_manager.py

import socket
import threading
import json
from greeksoft_1 import GreekAPI

HOST = "127.0.0.1"
PORT = 5000

clients = {}
clients_lock = threading.Lock()

api = GreekAPI(
    user="G911",
    s_pwd="greek@123",
    pwd="g@4444444444",
    procli="2",
    ac_no="",
    is_secure=False,
    is_base_64=True,
    rest_ip="dev.greeksoft.in",
    rest_port="3333"
)

api.start_apollo([], "")
# time.sleep(2)

subscribed_tokens = set()
token_ref_count = {}


def extract_token(data):
    """Normalize token from incoming tick payloads."""
    if isinstance(data, tuple) and data:
        return str(data[0])

    if isinstance(data, dict):
        for key in ("symbol", "token", "tk", "instrument_token"):
            value = data.get(key)
            if value not in (None, ""):
                return str(value)

    return None


def handle_client(conn):
    print("New client connected")

    buffer = ""

    while True:
        try:
            chunk = conn.recv(4096).decode()
            if not chunk:
                break

            buffer += chunk

            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)

                if not line.strip():
                    continue

                msg = json.loads(line)

                msg_type = msg.get("type", "subscribe")

                if msg_type == "subscribe":
                    strategy_name = msg.get("strategy", "unknown")
                    tokens = set(str(t) for t in msg.get("tokens", []))
                    update_client_subscription(conn, strategy_name, tokens)
                elif msg_type == "place_order":
                    strategy_name = msg.get("strategy", "unknown")
                    params = msg.get("order", {})
                    # Order execution is delegated to GreekAPI.place_order only.
                    order_resp = api.place_order(
                        params.get("tokenno", ""),
                        params.get("symbol", ""),
                        params.get("lot", "1"),
                        params.get("qty", "1"),
                        params.get("price", "0"),
                        params.get("buysell", "BUY"),
                        params.get("ordtype", "M"),
                        params.get("trigprice", "0"),
                        params.get("exchange", "NSE"),
                        params.get("validity", "0"),
                        strategy_name
                    )
                    response = {
                        "__type": "order_response",
                        "strategy": strategy_name,
                        "request": params,
                        "response": order_resp
                    }
                    conn.sendall((json.dumps(response) + "\n").encode())
                else:
                    err = {"__type": "error", "message": f"Unsupported message type: {msg_type}"}
                    conn.sendall((json.dumps(err) + "\n").encode())

        except Exception as e:
            print("Error:", e)
            break

    remove_client(conn)
    conn.close()



# def market_data_router():
#     print("Router started")
#
#     for data in api.data_stream():
#
#         if isinstance(data, tuple):
#             token = str(data[0])
#         else:
#             token = str(data.get("symbol"))
#
#         for strategy, info in list(clients.items()):
#             if token in info["tokens"]:
#                 try:
#                     msg = json.dumps(data) + "\n"
#                     info["conn"].sendall(msg.encode())
#                 except:
#                     print("Client disconnected:", strategy)
#                     del clients[strategy]

def market_data_router():
    print("Router started")

    for data in api.data_stream():
        token = extract_token(data)
        dead_connections = []
        with clients_lock:
            snapshot = list(clients.items())

        for conn, info in snapshot:
            if token and token not in info["tokens"]:
                continue
            try:
                conn.sendall((json.dumps(data) + "\n").encode())
            except Exception as e:
                print(f"Client send failed for {info['name']}: {e}")
                dead_connections.append(conn)

        for conn in dead_connections:
            remove_client(conn)
            try:
                conn.close()
            except Exception:
                pass


def update_client_subscription(conn, strategy_name, new_tokens):
    with clients_lock:
        previous_info = clients.get(conn)
        previous_tokens = previous_info["tokens"] if previous_info else set()
        added_tokens = new_tokens - previous_tokens
        removed_tokens = previous_tokens - new_tokens

        for token in added_tokens:
            current_count = token_ref_count.get(token, 0)
            if current_count == 0:
                api.subscribe_token([token])
                subscribed_tokens.add(token)
                print("Subscribed:", token)
            token_ref_count[token] = current_count + 1

        for token in removed_tokens:
            current_count = token_ref_count.get(token, 0)
            if current_count <= 1:
                token_ref_count.pop(token, None)
                subscribed_tokens.discard(token)
            else:
                token_ref_count[token] = current_count - 1

        clients[conn] = {
            "name": strategy_name,
            "tokens": new_tokens
        }
    print("Register:", strategy_name, sorted(new_tokens))
    try:
        ack = {"__type": "ack", "strategy": strategy_name, "tokens": sorted(new_tokens)}
        conn.sendall((json.dumps(ack) + "\n").encode())
        print(f"ACK sent to {strategy_name}")
    except Exception as e:
        print(f"ACK send failed for {strategy_name}: {e}")


def remove_client(conn):
    with clients_lock:
        info = clients.pop(conn, None)
        if not info:
            return
        for token in info["tokens"]:
            current_count = token_ref_count.get(token, 0)
            if current_count <= 1:
                token_ref_count.pop(token, None)
                subscribed_tokens.discard(token)
            else:
                token_ref_count[token] = current_count - 1
    print("Removed client:", info["name"])





def start_server():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((HOST, PORT))
    server.listen()

    print("WebSocket Manager Running...")

    threading.Thread(target=market_data_router, daemon=True).start()

    while True:
        conn, addr = server.accept()
        threading.Thread(target=handle_client, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    start_server()
