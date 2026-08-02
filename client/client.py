import socket
import threading
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.protocol import send_msg, recv_msg, ProtocolError

HOST = "192.168.119.50"
PORT = 5000

running = True


def receive_loop(sock):
    """Runs in its own thread. Prints anything the server sends."""
    global running
    try:
        while running:
            msg = recv_msg(sock)
            if msg.get("type") == "chat":
                print(f"\n[{msg.get('from')}] {msg.get('text')}\n> ", end="")
            else:
                print(f"\n[SERVER] {msg}\n> ", end="")
    except ProtocolError:
        print("\n[CLIENT] Connection to server lost.")
    except Exception:
        pass
    finally:
        running = False


def main():
    global running

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((HOST, PORT))
    print(f"[CLIENT] Connected to {HOST}:{PORT}")

    receiver = threading.Thread(target=receive_loop, args=(sock,), daemon=True)
    receiver.start()

    try:
        while running:
            text = input("> ")
            if not text:
                continue
            send_msg(sock, {"type": "chat", "text": text})

    except (KeyboardInterrupt, EOFError):
        print("\n[CLIENT] Disconnecting.")
    except ProtocolError:
        print("\n[CLIENT] Connection lost.")
    finally:
        running = False
        sock.close()


if __name__ == "__main__":
    main()
