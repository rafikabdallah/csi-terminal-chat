"""CSI Terminal Chat - server.

Thread-per-client TCP server with authentication.
"""

import socket
import threading
import sys
import os
import re

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.protocol import send_msg, recv_msg, ProtocolError
import auth
import db

HOST = "0.0.0.0"
PORT = 5000

USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,20}$")
MAX_PASSWORD_LEN = 128

clients = {}
clients_lock = threading.Lock()


def broadcast(message, exclude=None):
    """Send a message to all authenticated clients except `exclude`."""
    with clients_lock:
        targets = [(u, s) for u, s in clients.items() if u != exclude]

    for username, sock in targets:
        try:
            send_msg(sock, message)
        except Exception:
            pass


def valid_username(name):
    return isinstance(name, str) and USERNAME_RE.match(name) is not None


def valid_password(pw):
    return isinstance(pw, str) and 6 <= len(pw) <= MAX_PASSWORD_LEN


def handle_register(sock, msg, addr):
    username = msg.get("username")
    password = msg.get("password")

    if not valid_username(username):
        send_msg(sock, {"type": "auth_error",
                        "reason": "Username must be 3-20 chars: letters, digits, underscore"})
        return None

    if not valid_password(password):
        send_msg(sock, {"type": "auth_error",
                        "reason": "Password must be at least 6 characters"})
        return None

    salt_hex, hash_hex = auth.hash_password(password)

    if not db.create_user(username, salt_hex, hash_hex):
        print(f"[AUTH] Registration refused (taken): {username} from {addr[0]}")
        send_msg(sock, {"type": "auth_error", "reason": "Username already taken"})
        return None

    print(f"[AUTH] Registered: {username} from {addr[0]}")
    send_msg(sock, {"type": "auth_ok", "username": username})
    return username


def handle_login(sock, msg, addr):
    username = msg.get("username")
    password = msg.get("password")

    if not valid_username(username) or not isinstance(password, str):
        send_msg(sock, {"type": "auth_error",
                        "reason": "Invalid username or password"})
        return None

    row = db.get_user(username)

    if row is None or not auth.verify_password(password, row[0], row[1]):
        print(f"[AUTH] FAILED login for '{username}' from {addr[0]}")
        send_msg(sock, {"type": "auth_error",
                        "reason": "Invalid username or password"})
        return None

    with clients_lock:
        if username in clients:
            send_msg(sock, {"type": "auth_error",
                            "reason": "User already connected"})
            return None
        clients[username] = sock

    print(f"[AUTH] Login OK: {username} from {addr[0]}")
    send_msg(sock, {"type": "auth_ok", "username": username})
    broadcast({"type": "system", "text": f"{username} joined the chat"},
              exclude=username)
    return username


def handle_client(client_sock, addr):
    """One thread per connection."""
    print(f"[SERVER] Connection from {addr[0]}:{addr[1]}")
    username = None

    try:
        while True:
            msg = recv_msg(client_sock)
            mtype = msg.get("type")

            if username is None:
                if mtype == "register":
                    handle_register(client_sock, msg, addr)
                elif mtype == "login":
                    username = handle_login(client_sock, msg, addr)
                else:
                    send_msg(client_sock, {"type": "auth_error",
                                           "reason": "Please /login or /register first"})
                continue

            if mtype == "chat":
                text = msg.get("text", "")
                if not isinstance(text, str) or not text.strip():
                    continue
                print(f"[CHAT] {username}: {text}")
                broadcast({"type": "chat", "from": username, "text": text},
                          exclude=username)
            else:
                send_msg(client_sock, {"type": "error",
                                       "reason": f"Unknown message type: {mtype}"})

    except ProtocolError as e:
        print(f"[SERVER] {addr[0]} disconnected: {e}")
    except Exception as e:
        print(f"[SERVER] {addr[0]} error: {e}")
    finally:
        if username:
            with clients_lock:
                clients.pop(username, None)
            broadcast({"type": "system", "text": f"{username} left the chat"})
            print(f"[SERVER] {username} cleaned up")
        client_sock.close()


def main():
    db.init_db()

    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind((HOST, PORT))
    server_sock.listen(5)
    print(f"[SERVER] Listening on {HOST}:{PORT}")

    try:
        while True:
            client_sock, addr = server_sock.accept()
            threading.Thread(target=handle_client,
                             args=(client_sock, addr),
                             daemon=True).start()
    except KeyboardInterrupt:
        print("\n[SERVER] Shutting down.")
    finally:
        server_sock.close()


if __name__ == "__main__":
    main()
