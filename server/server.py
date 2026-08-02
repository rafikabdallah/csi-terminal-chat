"""CSI Terminal Chat - server.

Thread-per-client TCP server with authentication, rooms, and private messages.
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
ROOM_RE = re.compile(r"^[A-Za-z0-9_-]{1,20}$")
MAX_PASSWORD_LEN = 128
DEFAULT_ROOM = "general"

clients = {}                      # username -> socket
rooms = {DEFAULT_ROOM: set()}     # room name -> set of usernames
user_rooms = {}                   # username -> room name
clients_lock = threading.Lock()


def broadcast_room(room, message, exclude=None):
    """Send a message to everyone in `room` except `exclude`."""
    with clients_lock:
        members = [(u, clients[u])
                   for u in rooms.get(room, set())
                   if u != exclude and u in clients]

    for username, sock in members:
        try:
            send_msg(sock, message)
        except Exception:
            pass


def valid_username(name):
    return isinstance(name, str) and USERNAME_RE.match(name) is not None


def valid_room(name):
    return isinstance(name, str) and ROOM_RE.match(name) is not None


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
    return None


def handle_login(sock, msg, addr):
    username = msg.get("username")
    password = msg.get("password")

    if not valid_username(username) or not isinstance(password, str):
        send_msg(sock, {"type": "auth_error", "reason": "Invalid username or password"})
        return None

    row = db.get_user(username)

    if row is None or not auth.verify_password(password, row[0], row[1]):
        print(f"[AUTH] FAILED login for '{username}' from {addr[0]}")
        send_msg(sock, {"type": "auth_error", "reason": "Invalid username or password"})
        return None

    with clients_lock:
        if username in clients:
            send_msg(sock, {"type": "auth_error", "reason": "User already connected"})
            return None
        clients[username] = sock
        rooms.setdefault(DEFAULT_ROOM, set()).add(username)
        user_rooms[username] = DEFAULT_ROOM

    print(f"[AUTH] Login OK: {username} from {addr[0]}")
    send_msg(sock, {"type": "auth_ok", "username": username, "room": DEFAULT_ROOM})
    broadcast_room(DEFAULT_ROOM,
                   {"type": "system", "text": f"{username} joined #{DEFAULT_ROOM}"},
                   exclude=username)
    return username


def handle_join(sock, username, msg):
    room = msg.get("room")

    if not valid_room(room):
        send_msg(sock, {"type": "error",
                        "reason": "Room name must be 1-20 chars: letters, digits, _ or -"})
        return

    with clients_lock:
        old_room = user_rooms.get(username)
        if old_room == room:
            send_msg(sock, {"type": "error", "reason": f"Already in #{room}"})
            return

        if old_room and old_room in rooms:
            rooms[old_room].discard(username)
            if not rooms[old_room] and old_room != DEFAULT_ROOM:
                del rooms[old_room]

        rooms.setdefault(room, set()).add(username)
        user_rooms[username] = room

    print(f"[ROOM] {username}: {old_room} -> {room}")

    if old_room:
        broadcast_room(old_room, {"type": "system", "text": f"{username} left #{old_room}"})

    send_msg(sock, {"type": "system", "text": f"You joined #{room}"})
    broadcast_room(room, {"type": "system", "text": f"{username} joined #{room}"},
                   exclude=username)


def handle_rooms(sock):
    with clients_lock:
        listing = {name: len(members) for name, members in rooms.items()}

    lines = [f"  #{name} ({count})" for name, count in sorted(listing.items())]
    send_msg(sock, {"type": "system", "text": "Rooms:\n" + "\n".join(lines)})


def handle_who(sock, username):
    with clients_lock:
        room = user_rooms.get(username, DEFAULT_ROOM)
        members = sorted(rooms.get(room, set()))

    send_msg(sock, {"type": "system", "text": f"In #{room}: " + ", ".join(members)})


def handle_private(sock, sender, msg):
    """Deliver a direct message to one online user."""
    target = msg.get("to")
    text = msg.get("text", "")

    if not valid_username(target):
        send_msg(sock, {"type": "error", "reason": "Invalid username"})
        return

    if not isinstance(text, str) or not text.strip():
        return

    if target == sender:
        send_msg(sock, {"type": "error", "reason": "You cannot message yourself"})
        return

    with clients_lock:
        target_sock = clients.get(target)

    if target_sock is None:
        send_msg(sock, {"type": "error", "reason": f"{target} is not online"})
        return

    print(f"[PM] {sender} -> {target}")

    try:
        send_msg(target_sock, {"type": "private", "from": sender, "text": text})
        send_msg(sock, {"type": "private_sent", "to": target, "text": text})
    except Exception:
        send_msg(sock, {"type": "error", "reason": f"Could not deliver to {target}"})


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

                with clients_lock:
                    room = user_rooms.get(username, DEFAULT_ROOM)

                print(f"[CHAT] #{room} {username}: {text}")
                broadcast_room(room, {"type": "chat", "from": username,
                                      "room": room, "text": text},
                               exclude=username)

            elif mtype == "join":
                handle_join(client_sock, username, msg)

            elif mtype == "rooms":
                handle_rooms(client_sock)

            elif mtype == "who":
                handle_who(client_sock, username)

            elif mtype == "private":
                handle_private(client_sock, username, msg)

            else:
                send_msg(client_sock, {"type": "error",
                                       "reason": f"Unknown message type: {mtype}"})

    except ProtocolError as e:
        print(f"[SERVER] {addr[0]} disconnected: {e}")
    except Exception as e:
        print(f"[SERVER] {addr[0]} error: {e}")
    finally:
        room = None
        if username:
            with clients_lock:
                clients.pop(username, None)
                room = user_rooms.pop(username, None)
                if room and room in rooms:
                    rooms[room].discard(username)
                    if not rooms[room] and room != DEFAULT_ROOM:
                        del rooms[room]

            if room:
                broadcast_room(room, {"type": "system", "text": f"{username} left the chat"})
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
            threading.Thread(target=handle_client, args=(client_sock, addr),
                             daemon=True).start()
    except KeyboardInterrupt:
        print("\n[SERVER] Shutting down.")
    finally:
        server_sock.close()


if __name__ == "__main__":
    main()
