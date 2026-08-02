"""CSI Terminal Chat - server.

Thread-per-client TCP server with authentication, rooms, private
messages, security event logging, and graceful shutdown.
"""

import socket
import threading
import sys
import os
import re
import argparse
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.protocol import send_msg, recv_msg, ProtocolError
from common import colors as C
from logger import log
import auth
import db

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 5000

USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,20}$")
ROOM_RE = re.compile(r"^[A-Za-z0-9_-]{1,20}$")
MAX_PASSWORD_LEN = 128
DEFAULT_ROOM = "general"
IDLE_TIMEOUT = 300

clients = {}                      # username -> socket
rooms = {DEFAULT_ROOM: set()}     # room name -> set of usernames
user_rooms = {}                   # username -> room name
clients_lock = threading.Lock()

_shutting_down = threading.Event()


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
        log.warning(f"REGISTER rejected (bad username) ip={addr[0]}")
        send_msg(sock, {"type": "auth_error",
                        "reason": "Username must be 3-20 chars: letters, digits, underscore"})
        return None

    if not valid_password(password):
        send_msg(sock, {"type": "auth_error",
                        "reason": "Password must be at least 6 characters"})
        return None

    salt_hex, hash_hex = auth.hash_password(password)

    if not db.create_user(username, salt_hex, hash_hex):
        log.warning(f"REGISTER refused (taken) user={username} ip={addr[0]}")
        send_msg(sock, {"type": "auth_error", "reason": "Username already taken"})
        return None

    log.info(f"REGISTER ok user={username} ip={addr[0]}")
    send_msg(sock, {"type": "auth_ok", "username": username})
    return None


def handle_login(sock, msg, addr):
    username = msg.get("username")
    password = msg.get("password")

    if not valid_username(username) or not isinstance(password, str):
        log.warning(f"LOGIN FAILED (malformed) ip={addr[0]}")
        send_msg(sock, {"type": "auth_error", "reason": "Invalid username or password"})
        return None

    row = db.get_user(username)

    if row is None or not auth.verify_password(password, row[0], row[1]):
        log.warning(f"LOGIN FAILED user={username} ip={addr[0]}")
        send_msg(sock, {"type": "auth_error", "reason": "Invalid username or password"})
        return None

    with clients_lock:
        if username in clients:
            log.warning(f"LOGIN refused (already online) user={username} ip={addr[0]}")
            send_msg(sock, {"type": "auth_error", "reason": "User already connected"})
            return None
        clients[username] = sock
        rooms.setdefault(DEFAULT_ROOM, set()).add(username)
        user_rooms[username] = DEFAULT_ROOM
        online = len(clients)

    log.info(f"LOGIN ok user={username} ip={addr[0]} online={online}")
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

    log.info(f"ROOM user={username} from={old_room} to={room}")

    if old_room:
        broadcast_room(old_room, {"type": "system", "text": f"{username} left #{old_room}"})

    send_msg(sock, {"type": "system", "text": f"You joined #{room}"})
    broadcast_room(room, {"type": "system", "text": f"{username} joined #{room}"},
                   exclude=username)


def handle_rooms(sock):
    with clients_lock:
        listing = {name: len(members) for name, members in rooms.items()}

    lines = [f"  #{name} ({count} online)" for name, count in sorted(listing.items())]
    send_msg(sock, {"type": "system", "text": "Rooms:\n" + "\n".join(lines)})


def handle_who(sock, username):
    with clients_lock:
        room = user_rooms.get(username, DEFAULT_ROOM)
        members = sorted(rooms.get(room, set()))

    send_msg(sock, {"type": "system",
                    "text": f"In #{room} ({len(members)}): " + ", ".join(members)})


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

    log.info(f"PM from={sender} to={target}")

    try:
        send_msg(target_sock, {"type": "private", "from": sender, "text": text})
        send_msg(sock, {"type": "private_sent", "to": target, "text": text})
    except Exception:
        send_msg(sock, {"type": "error", "reason": f"Could not deliver to {target}"})


def handle_client(client_sock, addr):
    """One thread per connection."""
    log.info(f"CONNECT ip={addr[0]} port={addr[1]}")
    client_sock.settimeout(IDLE_TIMEOUT)
    username = None
    room = None

    try:
        while not _shutting_down.is_set():
            msg = recv_msg(client_sock)
            mtype = msg.get("type")

            if username is None:
                if mtype == "register":
                    handle_register(client_sock, msg, addr)
                elif mtype == "login":
                    username = handle_login(client_sock, msg, addr)
                else:
                    log.warning(f"UNAUTH action type={mtype} ip={addr[0]}")
                    send_msg(client_sock, {"type": "auth_error",
                                           "reason": "Please /login or /register first"})
                continue

            if mtype == "chat":
                text = msg.get("text", "")
                if not isinstance(text, str) or not text.strip():
                    continue

                with clients_lock:
                    room = user_rooms.get(username, DEFAULT_ROOM)

                log.info(f"CHAT room={room} user={username} len={len(text)}")
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
                log.warning(f"UNKNOWN type={mtype} user={username} ip={addr[0]}")
                send_msg(client_sock, {"type": "error",
                                       "reason": f"Unknown message type: {mtype}"})

    except socket.timeout:
        log.warning(f"TIMEOUT ip={addr[0]} user={username} after={IDLE_TIMEOUT}s")
    except ProtocolError as e:
        log.info(f"DISCONNECT ip={addr[0]} user={username} reason={e}")
    except Exception as e:
        log.error(f"HANDLER ERROR ip={addr[0]} user={username}: {e}")
    finally:
        left_room = None
        if username:
            with clients_lock:
                clients.pop(username, None)
                left_room = user_rooms.pop(username, None)
                if left_room and left_room in rooms:
                    rooms[left_room].discard(username)
                    if not rooms[left_room] and left_room != DEFAULT_ROOM:
                        del rooms[left_room]
                online = len(clients)

            if left_room and not _shutting_down.is_set():
                broadcast_room(left_room,
                               {"type": "system", "text": f"{username} left the chat"})
            log.info(f"CLEANUP user={username} online={online}")

        try:
            client_sock.close()
        except Exception:
            pass


def shutdown(server_sock):
    """Notify every connected client, then close all sockets."""
    _shutting_down.set()

    with clients_lock:
        socks = list(clients.values())

    if socks:
        log.info(f"Notifying {len(socks)} client(s) of shutdown")

    for sock in socks:
        try:
            send_msg(sock, {"type": "system", "text": "Server is shutting down"})
            sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        finally:
            try:
                sock.close()
            except Exception:
                pass

    try:
        server_sock.close()
    except Exception:
        pass

    log.info("Server stopped")


def parse_args():
    p = argparse.ArgumentParser(description="CSI Terminal Chat server")
    p.add_argument("--host", default=DEFAULT_HOST,
                   help=f"interface to bind (default {DEFAULT_HOST})")
    p.add_argument("--port", type=int, default=DEFAULT_PORT,
                   help=f"TCP port (default {DEFAULT_PORT})")
    return p.parse_args()


def main():
    args = parse_args()

    print(C.BANNER)
    print(f"{C.GREY}  starting up{C.RESET}", end="", flush=True)
    for _ in range(3):
        time.sleep(0.15)
        print(f"{C.GREY}.{C.RESET}", end="", flush=True)
    print()

    db.init_db()

    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    try:
        server_sock.bind((args.host, args.port))
    except OSError as e:
        print(f"{C.RED}  cannot bind {args.host}:{args.port} - {e}{C.RESET}")
        return 1

    server_sock.listen(5)

    print(f"{C.GREEN}  ● listening on {args.host}:{args.port}{C.RESET}")
    print(f"{C.GREY}  Ctrl+C to stop{C.RESET}\n")
    log.info(f"Server listening on {args.host}:{args.port}")

    try:
        while True:
            client_sock, addr = server_sock.accept()
            threading.Thread(target=handle_client, args=(client_sock, addr),
                             daemon=True).start()
    except KeyboardInterrupt:
        print(f"\n{C.YELLOW}  shutdown requested{C.RESET}")
        log.info("Shutdown requested")
    finally:
        shutdown(server_sock)

    return 0


if __name__ == "__main__":
    sys.exit(main())
