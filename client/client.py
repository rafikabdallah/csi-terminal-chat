"""CSI Terminal Chat - client.

Two threads: one blocking on stdin, one blocking on recv.
"""

import socket
import threading
import sys
import os
import getpass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.protocol import send_msg, recv_msg, ProtocolError

HOST = "192.168.119.50"
PORT = 5000

running = True


def receive_loop(sock):
    """Print everything the server sends."""
    global running

    try:
        while running:
            msg = recv_msg(sock)
            mtype = msg.get("type")

            if mtype == "chat":
                print(f"\r[#{msg.get('room')}] {msg.get('from')}: {msg.get('text')}\n> ", end="")
            elif mtype == "system":
                print(f"\r*** {msg.get('text')}\n> ", end="")
            elif mtype == "auth_ok":
                room = msg.get("room")
                if room:
                    print(f"\r*** Logged in as {msg.get('username')} in #{room}\n> ", end="")
                else:
                    print(f"\r*** Account created. Now use /login\n> ", end="")
            elif mtype in ("auth_error", "error"):
                print(f"\r!!! {msg.get('reason')}\n> ", end="")
            else:
                print(f"\r??? {msg}\n> ", end="")

    except ProtocolError:
        print("\r*** Connection to server lost.")
    except Exception:
        pass
    finally:
        running = False


def print_help():
    print("Commands:")
    print("  /register <username>  - create an account")
    print("  /login <username>     - log in")
    print("  /join <room>          - switch room")
    print("  /rooms                - list rooms")
    print("  /who                  - who is in your room")
    print("  /help                 - this list")
    print("  /quit                 - disconnect")
    print("  anything else         - send as chat")


def main():
    global running

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    try:
        sock.connect((HOST, PORT))
    except OSError as e:
        print(f"Could not connect to {HOST}:{PORT} - {e}")
        return

    print(f"*** Connected to {HOST}:{PORT}")
    print_help()

    threading.Thread(target=receive_loop, args=(sock,), daemon=True).start()

    try:
        while running:
            line = input("> ").strip()

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
                room = line.split(maxsplit=1)[1].strip()
                send_msg(sock, {"type": "join", "room": room})
                continue

            if line.startswith("/register ") or line.startswith("/login "):
                parts = line.split(maxsplit=1)
                command = parts[0][1:]
                username = parts[1].strip()
                password = getpass.getpass("Password: ")

                send_msg(sock, {"type": command,
                                "username": username,
                                "password": password})
                continue

            if line.startswith("/"):
                print("!!! Unknown command. Try /help")
                continue

            send_msg(sock, {"type": "chat", "text": line})

    except (KeyboardInterrupt, EOFError):
        print("\n*** Disconnecting.")
    except ProtocolError:
        print("\n*** Connection lost.")
    finally:
        running = False
        sock.close()


if __name__ == "__main__":
    main()
