import socket
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.protocol import send_msg, recv_msg, ProtocolError

HOST = "0.0.0.0"
PORT = 5000


def main():
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    server_sock.bind((HOST, PORT))
    server_sock.listen(5)
    print(f"[SERVER] Listening on {HOST}:{PORT}")

    client_sock, client_addr = server_sock.accept()
    print(f"[SERVER] Client connected from {client_addr}")

    try:
        while True:
            msg = recv_msg(client_sock)
            print(f"[SERVER] Received: {msg}")
            send_msg(client_sock, msg)
    except ProtocolError as e:
        print(f"[SERVER] Connection ended: {e}")
    finally:
        client_sock.close()
        server_sock.close()


if __name__ == "__main__":
    main()
