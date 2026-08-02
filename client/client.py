import socket
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.protocol import send_msg, recv_msg, ProtocolError

HOST = "192.168.119.50"
PORT = 5000


def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((HOST, PORT))
    print(f"[CLIENT] Connected to {HOST}:{PORT}")

    try:
        while True:
            text = input("> ")
            if not text:
                continue

            send_msg(sock, {"type": "echo", "text": text})
            reply = recv_msg(sock)
            print(f"[CLIENT] Server replied: {reply}")

    except (ProtocolError, KeyboardInterrupt, EOFError):
        print("\n[CLIENT] Disconnecting.")
    finally:
        sock.close()


if __name__ == "__main__":
    main()
