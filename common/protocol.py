import json
import struct

HEADER_SIZE = 4
MAX_MSG_SIZE = 65536


class ProtocolError(Exception):
    """Base class for protocol failures."""


class ConnectionClosedError(ProtocolError):
    """The peer closed the connection."""


def recv_exact(sock, n):
    """Read exactly n bytes from the socket, or raise."""
    buffer = bytearray()

    while len(buffer) < n:
        chunk = sock.recv(n - len(buffer))

        if not chunk:
            raise ConnectionClosedError("Peer closed the connection")

        buffer.extend(chunk)

    return bytes(buffer)

def send_msg(sock, obj):
    """Send one dict as a length-prefixed JSON message."""
    payload = json.dumps(obj).encode("utf-8")
    length = len(payload)

    if length > MAX_MSG_SIZE:
        raise ValueError(f"Message too large: {length} bytes")

    header = struct.pack(">I", length)
    sock.sendall(header + payload)


def recv_msg(sock):
    """Read one length-prefixed JSON message and return it as a dict."""
    header = recv_exact(sock, HEADER_SIZE)
    length = struct.unpack(">I", header)[0]

    if length > MAX_MSG_SIZE:
        raise ProtocolError(f"Declared message size too large: {length}")

    payload = recv_exact(sock, length)

    try:
        obj = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ProtocolError(f"Malformed payload: {e}")

    if not isinstance(obj, dict):
        raise ProtocolError("Message must be a JSON object")

    return obj
