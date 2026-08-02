# CSI Terminal Chat

A multi-client group chat server over raw TCP sockets. Terminal clients, no web framework, no HTTP, no external dependencies — Python standard library only.

Built as a portfolio project to demonstrate socket programming, concurrency, and applied security engineering.

CSI Terminal Chat

---

## Features

- **Raw TCP sockets** with a custom length-prefixed binary protocol  
- **Thread-per-client** concurrency with lock-guarded shared state  
- **PBKDF2-HMAC-SHA256** password hashing with per-user random salts  
- **Rooms** — join, list, and see who is present  
- **Private messaging** between any two online users  
- **Structured security logging** with rotation and severity levels  
- **Graceful shutdown** — abrupt client death never affects other clients

---

## Requirements

- Python 3.11 or newer  
- No third-party packages

Verified on Ubuntu Server 24.04 (server) and Windows 11 (client).

---

## Running it

Clone on both machines:

git clone https://github.com/rafikabdallah/csi-terminal-chat.git

cd csi-terminal-chat

**Server** — binds to all interfaces on port 5000 by default:

python3 server/server.py

python3 server/server.py \--host 0.0.0.0 \--port 5000    \# explicit

**Client** — point it at the server's address:

python3 client/client.py \--host 192.168.119.50

python client\\client.py \--host 192.168.119.50          \# Windows

If the server is on the same machine, `--host` can be omitted.

---

## Commands

| Command | Description |
| :---- | :---- |
| `/register <username>` | Create an account (password prompted, never echoed) |
| `/login <username>` | Log in |
| `/join <room>` | Switch room — created on demand |
| `/rooms` | List active rooms and occupant counts |
| `/who` | List users in your current room |
| `/msg <user> <text>` | Send a private message |
| `/help` | Show the command list |
| `/quit` | Disconnect |

Anything not starting with `/` is sent as a chat message to your room.

Usernames are 3–20 characters, letters/digits/underscore. Passwords are a minimum of 6 characters and are never transmitted on the command line or echoed to the terminal.

---

## Lab environment

Developed and tested across two real machines rather than loopback:

   Ubuntu Server 24.04 VM          Windows 11 host

   192.168.119.50                  192.168.119.1

   server \+ client                 client

            └──── VMware NAT (vmnet8) ────┘

The server binds `0.0.0.0` so it is reachable on the LAN. `ufw` is set to default-deny inbound with only SSH and port 5000 explicitly allowed. Port 5000 is above 1024, so the service runs unprivileged.

---

## Protocol

Every message is a JSON object framed with a 4-byte big-endian length header:

┌──────────────┬───────────────────────────┐

│   4 bytes    │        N bytes            │

│  N (uint32)  │      UTF-8 JSON           │

└──────────────┴───────────────────────────┘

TCP is a byte stream with no message boundaries. Length-prefix framing makes each message unambiguous and is binary-safe, unlike delimiter-based framing. Full specification in [ARCHITECTURE.md](http://ARCHITECTURE.md).

---

## Project structure

common/

  protocol.py     framing, encode/decode, size limits

  colors.py       ANSI output, auto-disabled when piped

server/

  server.py       accept loop, client threads, rooms, routing

  auth.py         PBKDF2 hashing and constant-time verification

  db.py           SQLite persistence, parameterised queries

  logger.py       rotating file \+ console logging

client/

  client.py       two-threaded terminal client

---

## Security

This is a security portfolio piece, and the design decisions behind it — password hashing choice, input validation, untrusted-length handling, impersonation defence, auth-failure logging as a detection signal — are documented in [ARCHITECTURE.md](http://ARCHITECTURE.md), along with the known limitations.

**Note:** the protocol is unencrypted. Credentials and messages cross the network in plaintext. This is acceptable on an isolated lab network and documented as such; TLS is a prerequisite for any internet-facing deployment.

---

## Documentation

- [ARCHITECTURE.md](http://ARCHITECTURE.md) — protocol spec, threading model, security analysis, limitations  
- [BUILD\_LOG.md](http://BUILD_LOG.md) — phase-by-phase build record and decisions

---

## Licence

MIT  
