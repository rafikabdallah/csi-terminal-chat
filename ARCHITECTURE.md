# Architecture

Design decisions, protocol specification, and security analysis for
CSI Terminal Chat.

---

## 1. Overview

A central server accepts TCP connections from terminal clients on a LAN.
Clients never communicate directly — all traffic is relayed by the server,
which owns authentication, room membership, and message routing.

```
   ┌────────────┐        ┌────────────┐        ┌────────────┐
   │  client A  │        │  client B  │        │  client C  │
   └─────┬──────┘        └─────┬──────┘        └─────┬──────┘
         │                     │                     │
         └──────────── TCP :5000 ────────────────────┘
                              │
                     ┌────────▼────────┐
                     │     server      │
                     │  auth · rooms   │
                     │  routing · log  │
                     └────────┬────────┘
                              │
                     ┌────────▼────────┐
                     │  SQLite · log   │
                     └─────────────────┘
```

Relaying everything through the server is what makes authentication
meaningful: identity is established once, server-side, and every message
is attributed by the server rather than by the sender.

---

## 2. Protocol specification

### 2.1 Framing

TCP is a byte stream. It preserves order and delivery, but **not message
boundaries**. A single `recv()` may return half a message, two messages,
or one and a fragment of the next. Message boundaries must therefore be
imposed by the application.

Every message on the wire:

```
┌──────────────┬───────────────────────────┐
│   4 bytes    │        N bytes            │
│  N (uint32)  │      UTF-8 JSON           │
│  big-endian  │                           │
└──────────────┴───────────────────────────┘
```

**Why length-prefix over a delimiter.** Delimiter framing (terminating
each message with `\n`) requires that the delimiter never appear in the
payload, which means escaping, which means scanning every byte. Length
prefixing is binary-safe, requires no scanning, and the receiver knows
exactly how many bytes to read before it reads them.

**Why big-endian.** Network byte order is the convention for wire
protocols, so the format is independent of the endianness of either
endpoint's CPU.

**Maximum message size** is 65 536 bytes, enforced on both send and
receive. See §5.3.

### 2.2 Message types

Every message is a JSON object with a `type` field.

**Client to server**

| Type | Fields | Notes |
|---|---|---|
| `register` | `username`, `password` | Pre-auth only |
| `login` | `username`, `password` | Pre-auth only |
| `chat` | `text` | Requires auth |
| `join` | `room` | Requires auth |
| `rooms` | — | Requires auth |
| `who` | — | Requires auth |
| `private` | `to`, `text` | Requires auth |

**Server to client**

| Type | Fields | Notes |
|---|---|---|
| `auth_ok` | `username`, `room` | `room` present only on login |
| `auth_error` | `reason` | Deliberately generic — see §5.6 |
| `chat` | `from`, `room`, `text` | `from` is set server-side |
| `private` | `from`, `text` | `from` is set server-side |
| `private_sent` | `to`, `text` | Delivery confirmation to sender |
| `system` | `text` | Joins, departures, shutdown notice |
| `error` | `reason` | Post-auth errors |

### 2.3 Connection lifecycle

```
   connect
      │
      ▼
 UNAUTHENTICATED ──── register ──→ (account created, stay here)
      │
      │ login succeeds
      ▼
 AUTHENTICATED (in #general)
      │
      ├── chat / join / rooms / who / private
      │
      ▼
 disconnect ──→ removed from clients and rooms, socket closed
```

Any message other than `register` or `login` sent while unauthenticated
is refused and logged as a `UNAUTH` warning.

---

## 3. Threading model

### 3.1 Server

One thread accepts connections. Each accepted connection is handed to its
own thread, which owns that client for its lifetime.

```
   MAIN THREAD                    CLIENT THREADS
   ┌──────────────┐
   │ while True:  │
   │   accept() ──┼──── client A ──→ blocking recv loop
   │              ├──── client B ──→ blocking recv loop
   │              └──── client C ──→ blocking recv loop
   └──────────────┘
```

**Why thread-per-client.** The code reads sequentially: each thread
handles one conversation from start to finish, which makes the control
flow obvious and the error handling local. A failure in one client's
thread is naturally contained and cannot reach another's.

**Trade-off.** Each thread carries its own stack (roughly 8 MB of virtual
address space on Linux), so the model does not scale to tens of thousands
of concurrent connections. An event-driven approach — `select`, `epoll`,
or `asyncio` — multiplexes all sockets in a single thread and scales far
better, at the cost of inverting the control flow into callbacks or
coroutines and making the code substantially harder to follow. For a LAN
chat server with a realistic ceiling of dozens of users, thread-per-client
is the correct trade: the scalability that is given up is scalability that
would never be used.

### 3.2 Shared state and locking

Three structures are shared across all client threads:

```python
clients    = {}                    # username -> socket
rooms      = {"general": set()}    # room name -> set of usernames
user_rooms = {}                    # username -> room name
```

All three are guarded by a single `threading.Lock`.

**One lock, not three.** `rooms` and `user_rooms` are two views of the
same fact and must never disagree — a user present in `rooms["general"]`
but recorded in `user_rooms` as being in `dev` is a ghost that breaks
both broadcast and cleanup. Updating both under one lock makes the pair
atomic. Separate locks would reintroduce the possibility of an
inconsistent intermediate state, and introduce lock-ordering deadlock
risk.

**`user_rooms` is redundant but deliberate.** The same information could
be recovered by scanning every room, but that scan would run on every
message. Storing the reverse mapping trades a small consistency burden
for constant-time lookup on the hot path.

**Locks are never held across I/O.** Broadcast builds its target list
inside the lock, releases it, and only then sends:

```python
with clients_lock:
    members = [(u, clients[u]) for u in rooms.get(room, set()) ...]

for username, sock in members:
    try:
        send_msg(sock, message)
    except Exception:
        pass
```

`send_msg` can block when a recipient's TCP send buffer is full. Holding
the lock across that call would stall every other thread behind the
slowest client — a self-inflicted denial of service. The `try/except`
inside the loop exists because a client may die between the snapshot and
the send; one dead socket must not abort delivery to everyone else.

**Per-connection state is local, not shared.** The authenticated username
lives in a local variable inside the client's thread function. Nothing
else can observe it, so it needs no lock. Isolation is preferred to
sharing wherever it is available.

### 3.3 Client

Two threads, because one thread can only block on one thing. A single
thread blocked on `input()` cannot read the socket; blocked on `recv()`
it cannot read the keyboard.

```
   ┌────────────────────────────┐
   │ main thread                │
   │   input()  ──→ send        │
   │                            │
   │ receiver thread (daemon)   │
   │   recv()   ──→ print       │
   └────────────────────────────┘
```

No lock is required. The threads share the socket, but one only writes
and the other only reads, and TCP treats the two directions
independently. Locks are for shared mutable state, not for the mere
existence of threads.

---

## 4. Persistence

SQLite, one table:

```sql
CREATE TABLE users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    salt          TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**A fresh connection per call.** SQLite connections are not thread-safe
by default, and the server has one thread per client. Opening and closing
a connection per operation removes an entire class of concurrency bug at
the cost of a small overhead that is irrelevant here, because database
access happens once per login rather than once per message. The
alternative — a single shared connection behind its own lock — is faster
but adds a second lock to reason about.

**Uniqueness is enforced by the database, not by application code.**
Checking whether a username exists and then inserting it leaves a window
in which another thread can insert the same name — a time-of-check to
time-of-use race. The `UNIQUE` constraint closes it at the only layer
where the check and the write can be atomic; the resulting
`IntegrityError` is caught and reported as "username already taken".

---

## 5. Security considerations

### 5.1 Password storage

Passwords are never stored. Each account holds a random 16-byte salt and
a PBKDF2-HMAC-SHA256 derivation over 200 000 iterations.

**Why not plain SHA-256.**

| | SHA-256 | PBKDF2 (200k) |
|---|---|---|
| Designed for | speed | deliberate slowness |
| Guesses/sec on commodity GPU | billions | thousands |
| Identical passwords → identical hashes | yes | no (salted) |
| Precomputed tables effective | yes | no |

SHA-256 is a general-purpose hash, and its speed is exactly what makes it
unsuitable here: an attacker holding the database can test billions of
candidates per second. PBKDF2 applies the hash repeatedly by design, so
200 000 iterations makes every guess roughly 200 000 times more
expensive. The cost to a legitimate login is around 100 ms — imperceptible
once per session, ruinous when multiplied across a dictionary.

**Why a per-user salt.** Without one, two users who choose the same
password produce the same stored hash, which is visible in the database,
and a single precomputed table cracks every account at once. A random
per-user salt forces the attacker to repeat the full 200 000-iteration
attack separately for each account. The salt is stored in plaintext
alongside the hash; it is not a secret, and its purpose is uniqueness
rather than concealment.

**Why not Argon2 or bcrypt.** Both are stronger — memory-hard, and
therefore far more resistant to GPU and ASIC parallelisation. Neither is
in the Python standard library, and this project has a hard
no-dependencies constraint. PBKDF2 is the correct choice under that
constraint, and would be the wrong choice without it.

**Iteration count is fixed and cannot be raised in place.** Increasing it
would invalidate every existing account, because verification recomputes
the derivation with the new count and no longer matches what is stored.
A production system stores the iteration count per user and transparently
upgrades the record on next successful login. That is a known omission
here, not an oversight.

### 5.2 Timing side-channels

Hash comparison uses `hmac.compare_digest`, never `==`.

String equality short-circuits at the first differing character, so a
comparison that fails at position 1 completes measurably faster than one
that fails at position 40. That difference is observable over a network
with enough samples, and it lets an attacker recover the correct value
one character at a time — converting an infeasible brute-force into a
linear search. `compare_digest` examines the full input regardless of
where the first difference occurs, so its runtime carries no information
about the content.

### 5.3 Untrusted length handling

The 4-byte length header arrives from the network and is therefore
attacker-controlled. It is validated against `MAX_MSG_SIZE` **before**
any buffer is allocated:

```python
length = struct.unpack(">I", header)[0]
if length > MAX_MSG_SIZE:
    raise ProtocolError(...)
payload = recv_exact(sock, length)
```

Without this check, a 4-byte packet declaring a length of 4 294 967 295
would drive the receiver into a loop attempting to collect 4 GB — memory
exhaustion from a trivially cheap request. This is the same class of flaw
as a classic buffer overflow: attacker controls a size field, program
trusts it. The general rule the code follows is that **no memory
operation is ever sized by an untrusted value**, and validation happens
before allocation rather than after.

`recv_exact` also treats an empty `recv()` return as connection closure
and raises, rather than looping forever on a dead socket.

### 5.4 Input validation at the boundary

All network input is validated where it enters the system, before it
reaches storage or shared state:

- Usernames: `^[A-Za-z0-9_]{3,20}$`
- Room names: `^[A-Za-z0-9_-]{1,20}$`
- Passwords: 6 to 128 characters
- Payload must decode as UTF-8, parse as JSON, and be a JSON **object** —
  `json.loads("42")` is valid JSON but not a valid message, and unchecked
  would produce a `TypeError` deep inside a handler on a path an attacker
  controls

Validating once at the boundary means every downstream function can rely
on the shape of what it holds.

**Rooms are created on demand from network input**, which makes the
`rooms` dictionary a structure that grows under attacker influence. Two
controls limit this: room names are length-capped, and empty rooms other
than `general` are deleted when the last occupant leaves.

### 5.5 SQL injection

Every query uses parameter placeholders:

```python
conn.execute("SELECT salt, password_hash FROM users WHERE username = ?",
             (username,))
```

Parameterised queries are not escaping. The driver transmits the SQL
statement and the values over separate channels, so a value is never
parsed as SQL and there is nothing to inject. String-formatted SQL —
`f"... WHERE username = '{username}'"` — would allow a username such as
`' OR '1'='1` to alter the meaning of the statement.

### 5.6 Server-side identity

**The server never trusts a client-supplied username.** The authenticated
username is established once, at login, from a credential check against
the database, and thereafter lives in a server-side variable belonging to
that connection's thread. Every outbound `from` field is populated from
that variable.

A client is free to send `{"type": "chat", "from": "admin", "text": "..."}`.
The field is simply ignored. Without this, any user could impersonate any
other — and in a private message, where the recipient has no surrounding
context to catch the inconsistency, impersonation is more damaging still.

This is the difference between authentication and decoration: an identity
that the client can assert is not an identity at all.

### 5.7 Username enumeration

Failed logins always return the same message — "Invalid username or
password" — whether the account does not exist or the password is wrong.

Distinguishing the two turns the login endpoint into an oracle: an
attacker submits candidate usernames, learns which accounts are real, and
concentrates the expensive part of the attack on those. Registration
necessarily leaks the same information by refusing taken names, which is
an accepted and generally unavoidable trade-off for usable signup; the
login path does not need to leak it as well.

### 5.8 Logging as a detection signal

Events are written to a rotating file and to the console, with severity
used as a filter rather than decoration. `INFO` records normal operation;
`WARNING` marks anything security-relevant, so `grep WARNING chat.log`
yields an incident view.

Logged at `WARNING`:

- `LOGIN FAILED` with username and source IP
- `REGISTER refused` (name taken, or malformed)
- `UNAUTH action` — a message sent before authenticating
- `UNKNOWN type` — a message type the protocol does not define
- `TIMEOUT` — an idle connection reclaimed

The last two matter most. Neither is reachable through the supplied
client. Their appearance means someone is speaking the protocol with a
hand-written tool, which is what reconnaissance looks like. Failed logins
carry the source IP because "failed login for alice" is not actionable
while "five failures from 192.168.119.77 in ten seconds" is.

Format is `key=value` throughout so that the log is greppable and
machine-parseable rather than merely readable.

**What is deliberately not logged.** No passwords, no hashes, no message
content. Chat records `len=` rather than the text; private messages
record the sender and recipient but never the body. This preserves an
audit trail without turning the log into a transcript of every
conversation on the server. Choosing what not to collect is a privacy
control in its own right, and one that limits the damage if the log file
itself is ever exposed.

**Rotation is a security control, not housekeeping.** The file is capped
at 1 MB with three retained backups. Unbounded logs are a denial-of-service
vector: an attacker who can trigger log lines can fill the disk.

### 5.9 Resource exhaustion

Client sockets carry a 300-second receive timeout. A connection that is
opened and then left silent otherwise holds a thread indefinitely;
several thousand such connections exhaust the server's capacity without
sending a single byte of payload or authenticating. This is the shape of
a slowloris-style attack, and the defence is to reclaim connections that
stop making progress.

### 5.10 Least privilege

- The service binds port 5000, above 1024, so it never requires root.
  A compromise of the chat process does not hand over the host.
- `ufw` is configured default-deny inbound, with only SSH and 5000
  explicitly allowed.
- Firewall rules allow SSH **before** the firewall is enabled. Reversing
  that order severs the administrator's own session — recoverable on a VM
  with console access, unrecoverable on a remote host.
- Binding `0.0.0.0` exposes the service on every interface. This is
  necessary for the lab topology and is deliberate, but it is attack
  surface: a production deployment would bind the single interface the
  service is meant to be reachable on.

### 5.11 Fail-closed behaviour

Where a security check can itself fail, it denies. A corrupted salt that
cannot be decoded causes `verify_password` to return `False` rather than
raising: a damaged record refuses access instead of crashing the handler.
When a control errors, the safe default is to deny.

---

## 6. Known limitations

**Transport is unencrypted.** Credentials and message content cross the
network in plaintext. Anyone positioned to observe LAN traffic can read
passwords during login. On an isolated lab network this is an accepted
and documented trade-off; on any untrusted network it is a vulnerability.
This is the single most significant limitation of the project.

**No rate limiting.** The auth failure logging detects brute-force
attempts but nothing throttles them. PBKDF2's cost makes online guessing
slow, but detection without response is only half a control.

**No message history.** Messages exist only in flight. A user who
disconnects sees nothing of what was said while they were away, and
private messages to offline users are refused rather than queued.

**Client input line is not fully redrawn.** An incoming message clears
and redraws the prompt, but text already typed is lost from the display.
Proper handling requires `curses` or full ANSI cursor management.

**Client-side `running` flag is checked only between inputs.** A main
thread blocked inside `input()` does not observe that the receiver thread
has set it, so the client requires one keypress to exit after the server
disappears.

**PBKDF2 iteration count is global and fixed.** See §5.1.

**Room state lives in `server.py` rather than a separate `rooms.py`.**
Room membership and the client registry share a single lock, and keeping
them in one module keeps that relationship visible. Extraction would
require passing the lock across a module boundary. This was a deliberate
trade against the time budget.

**No test suite.** `protocol.py` and `auth.py` are pure and have no
socket or database dependencies precisely so that they are unit-testable;
the tests were not written.

---

## 7. Future work

In rough order of value:

1. **TLS** via the standard library `ssl` module — wrapping the accepted
   socket server-side and the connecting socket client-side. This is the
   prerequisite for any deployment outside a trusted network, and closes
   the plaintext-credentials problem in §6.
2. **Rate limiting** on authentication attempts — per-IP backoff or
   temporary lockout, converting the existing detection signal into an
   active control.
3. **Per-user PBKDF2 iteration counts** with transparent upgrade on
   login, so the work factor can rise over time.
4. **Message history** persisted to SQLite, with offline private message
   delivery.
5. **systemd service unit** — start on boot, restart on failure, journald
   integration. The realistic deployment path for a Linux service.
6. **Unit tests** for framing and password verification.
7. **Admin commands** — kick, ban, room moderation.
8. **File transfer** over the existing framing, with size and type
   constraints.
