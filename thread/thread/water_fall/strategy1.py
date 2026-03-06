import socket
import json

HOST = "127.0.0.1"
PORT = 5000

strategy_name = "strategy1"
tokens = ["101000025", "101013061", "101002885"]

# Safety switch. Keep False until your trigger/quantity is validated.
ENABLE_ORDER = False
order_sent = False


def send_subscribe(client):
    msg = {
        "type": "subscribe",
        "strategy": strategy_name,
        "tokens": tokens
    }
    client.sendall((json.dumps(msg) + "\n").encode())


def send_order_request(client, tick):
    order = {
        "tokenno": str(tick.get("symbol", "")),
        "symbol": str(tick.get("name", "")),
        "lot": "1",
        "qty": "1",
        "price": str(tick.get("ltp", "0")),
        "buysell": "BUY",
        "ordtype": "M",
        "trigprice": "0",
        "exchange": str(tick.get("exch", "NSE")),
        "validity": "0"
    }
    msg = {
        "type": "place_order",
        "strategy": strategy_name,
        "order": order
    }
    client.sendall((json.dumps(msg) + "\n").encode())
    print("Strategy1 Order Request Sent:", order)


client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
client.connect((HOST, PORT))
send_subscribe(client)
print("Strategy1 Connected")

buffer = ""
while True:
    chunk = client.recv(4096).decode()
    if not chunk:
        break

    buffer += chunk
    while "\n" in buffer:
        line, buffer = buffer.split("\n", 1)
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as e:
            print("Strategy1 JSON decode error:", e, "LINE:", line)
            continue

        if msg.get("__type") == "ack":
            print("Strategy1 ACK:", msg)
            continue
        if msg.get("__type") == "order_response":
            print("Strategy1 Order Response:", msg)
            continue
        if msg.get("__type") == "error":
            print("Strategy1 Error:", msg)
            continue

        print("Strategy1 Tick:", msg)

        # Example trigger: send one order when configured token tick arrives.
        if (
            ENABLE_ORDER
            and not order_sent
            and str(msg.get("symbol", "")) == "101000025"
        ):
            send_order_request(client, msg)
            order_sent = True
