import socket
import threading
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.protocol import send_msg, recv_msg, ProtocolError

HOST = "0.0.0.0"
PORT = 5000

clients = []
clients_lock = threading.Lock()


def broadcast(message, sender_sock=None):
    """Send a message to every connected client except the sender."""
    with clients_lock:
        targets = list(clients)

    for sock in targets:
        if sock is sender_sock:
            continue
        try:
            send_msg(sock, message)
        except Exception:
            pass


def handle_client(client_sock, addr):
    """Run in its own thread. Owns one client connection."""
    print(f"[SERVER] Client connected from {addr}")

    with clients_lock:
        clients.append(client_sock)

    try:
        while True:
            msg = recv_msg(client_sock)
            print(f"[SERVER] {addr} -> {msg}")

            broadcast({
                "type": "chat",
                "from": f"{addr[0]}:{addr[1]}",
                "text": msg.get("text", "")
            }, sender_sock=client_sock)

    except ProtocolError as e:
        print(f"[SERVER] {addr} disconnected: {e}")
    except Exception as e:
        print(f"[SERVER] {addr} error: {e}")
    finally:
        with clients_lock:
            if client_sock in clients:
                clients.remove(client_sock)
        client_sock.close()
        print(f"[SERVER] {addr} cleaned up. Clients online: {len(clients)}")


def main():
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind((HOST, PORT))
    server_sock.listen(5)
    print(f"[SERVER] Listening on {HOST}:{PORT}")

    try:
        while True:
            client_sock, addr = server_sock.accept()
            thread = threading.Thread(
                target=handle_client,
                args=(client_sock, addr),
                daemon=True
            )
            thread.start()
    except KeyboardInterrupt:
        print("\n[SERVER] Shutting down.")
    finally:
        server_sock.close()


if __name__ == "__main__":
    main()
