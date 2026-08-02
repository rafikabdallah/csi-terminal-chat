"""CSI Terminal Chat - client.

Two threads: one blocking on stdin, one blocking on recv.
"""

import socket
import threading
import sys
import os
import getpass
import argparse
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.protocol import send_msg, recv_msg, ProtocolError
from common import colors as C

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5000

running = True
current_room = "general"
current_user = None


def stamp():
    return f"{C.GREY}{datetime.now().strftime('%H:%M')}{C.RESET}"


def prompt():
    if current_user:
        return f"{C.CYAN}{current_user}{C.GREY}@{C.MAGENTA}#{current_room}{C.RESET} > "
    return f"{C.GREY}(not logged in){C.RESET} > "


def show(line):
    """Print an incoming line above the input prompt."""
    sys.stdout.write("\r\033[K" + line + "\n" + prompt())
    sys.stdout.flush()


def receive_loop(sock):
    global running, current_room, current_user

    try:
        while running:
            msg = recv_msg(sock)
            mtype = msg.get("type")

            if mtype == "chat":
                show(f"{stamp()} {C.MAGENTA}#{msg.get('room')}{C.RESET} "
                     f"{C.BOLD}{C.CYAN}{msg.get('from')}{C.RESET}: {msg.get('text')}")

            elif mtype == "private":
                show(f"{stamp()} {C.YELLOW}{C.BOLD}[PM ← {msg.get('from')}]{C.RESET} "
                     f"{C.YELLOW}{msg.get('text')}{C.RESET}")

            elif mtype == "private_sent":
                show(f"{stamp()} {C.YELLOW}[PM → {msg.get('to')}]{C.RESET} "
                     f"{C.DIM}{msg.get('text')}{C.RESET}")

            elif mtype == "system":
                text = msg.get("text", "")
                if text.startswith("You joined #"):
                    current_room = text.split("#", 1)[1].strip()
                show(f"{C.GREY}── {text}{C.RESET}")

            elif mtype == "auth_ok":
                room = msg.get("room")
                if room:
                    current_user = msg.get("username")
                    current_room = room
                    show(f"{C.GREEN}✔ logged in as {current_user} in #{room}{C.RESET}")
                else:
                    show(f"{C.GREEN}✔ account created — now use /login{C.RESET}")

            elif mtype in ("auth_error", "error"):
                show(f"{C.RED}✘ {msg.get('reason')}{C.RESET}")

            else:
                show(f"{C.GREY}? {msg}{C.RESET}")

    except ProtocolError:
        show(f"{C.RED}✘ connection to server lost{C.RESET}")
    except Exception:
        pass
    finally:
        running = False


def print_help():
    rows = [
        ("/register <user>", "create an account"),
        ("/login <user>", "log in"),
        ("/join <room>", "switch room"),
        ("/rooms", "list rooms"),
        ("/who", "who is in your room"),
        ("/msg <user> <text>", "private message"),
        ("/help", "this list"),
        ("/quit", "disconnect"),
    ]
    print(f"\n{C.BOLD}  commands{C.RESET}")
    for cmd, desc in rows:
        print(f"  {C.CYAN}{cmd:<20}{C.RESET}{C.GREY}{desc}{C.RESET}")
    print(f"  {C.GREY}{'anything else':<20}send as chat{C.RESET}\n")


def connect(host, port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    print(f"{C.GREY}  connecting to {host}:{port}{C.RESET}", end="", flush=True)

    for _ in range(3):
        time.sleep(0.15)
        print(f"{C.GREY}.{C.RESET}", end="", flush=True)

    try:
        sock.connect((host, port))
    except OSError as e:
        print(f"\n{C.RED}  ✘ could not connect: {e}{C.RESET}")
        return None

    print(f"\n{C.GREEN}  ● connected{C.RESET}")
    return sock


def parse_args():
    p = argparse.ArgumentParser(description="CSI Terminal Chat client")
    p.add_argument("--host", default=DEFAULT_HOST,
                   help=f"server address (default {DEFAULT_HOST})")
    p.add_argument("--port", type=int, default=DEFAULT_PORT,
                   help=f"server port (default {DEFAULT_PORT})")
    return p.parse_args()


def main():
    global running

    args = parse_args()
    print(C.BANNER)

    sock = connect(args.host, args.port)
    if sock is None:
        return 1

    print_help()

    threading.Thread(target=receive_loop, args=(sock,), daemon=True).start()

    try:
        while running:
            line = input(prompt()).strip()

            if not line:
                continue

            if line == "/quit":
                break

            if line == "/help":
                print_help()
                continue

            if line == "/rooms":
                send_msg(sock, {"type": "rooms"})
                continue

            if line == "/who":
                send_msg(sock, {"type": "who"})
                continue

            if line.startswith("/join "):
                send_msg(sock, {"type": "join",
                                "room": line.split(maxsplit=1)[1].strip()})
                continue

            if line.startswith("/msg "):
                parts = line.split(maxsplit=2)
                if len(parts) < 3:
                    print(f"{C.RED}  usage: /msg <user> <message>{C.RESET}")
                    continue
                send_msg(sock, {"type": "private", "to": parts[1], "text": parts[2]})
                continue

            if line.startswith("/register ") or line.startswith("/login "):
                parts = line.split(maxsplit=1)
                command = parts[0][1:]
                username = parts[1].strip()
                password = getpass.getpass(f"{C.GREY}  password: {C.RESET}")

                send_msg(sock, {"type": command,
                                "username": username,
                                "password": password})
                continue

            if line.startswith("/"):
                print(f"{C.RED}  unknown command — try /help{C.RESET}")
                continue

            send_msg(sock, {"type": "chat", "text": line})

    except (KeyboardInterrupt, EOFError):
        print(f"\n{C.GREY}  disconnecting{C.RESET}")
    except ProtocolError:
        print(f"\n{C.RED}  connection lost{C.RESET}")
    finally:
        running = False
        try:
            sock.close()
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
