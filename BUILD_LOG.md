# Build Log

Phase-by-phase record of the build: what was completed, what was decided
and why, and what went wrong along the way.

---

## Status

| Phase | Description | Status |
|---|---|---|
| 0 | Lab environment (VM, network, firewall, git) | Complete |
| 1 | Framing protocol + echo server | Complete |
| 2 | Concurrency: thread-per-client, broadcast | Complete |
| 3 | Authentication: PBKDF2 + SQLite | Complete |
| 4 | Rooms: join, list, who | Complete |
| 5 | Private messaging | Complete |
| 6 | Security event logging | Complete |
| 7 | Graceful shutdown, idle timeout, CLI args | Complete |
| 8 | Documentation | Complete |

---

## Phase 0 — Lab environment

**Completed**

- Ubuntu Server 24.04 LTS in VMware, 2 GB RAM, 2 vCPU, 20 GB disk
- VMware NAT (vmnet8), static IP `192.168.119.50/24`, gateway `192.168.119.2`
- OpenSSH server; development conducted over SSH from the Windows host
- `ufw` default-deny inbound, SSH and 5000/tcp allowed
- `git init` before the first line of code

**Decisions**

*NAT over bridged or host-only.* Bridged depends on the physical router's
DHCP and behaves unpredictably on shared or restricted networks. Host-only
has no internet access, which makes `apt` and `git push` painful. NAT gives
host-to-VM reachability, VM-to-VM reachability, and outbound internet.

*Static IP rather than DHCP.* VMware's NAT DHCP tends to reissue the same
lease, so DHCP would probably have worked. `192.168.119.50` was chosen
deliberately below the DHCP pool (which begins around `.128`) so the
address can never be handed to another machine. Five minutes of work to
remove a class of failure during a demo.

*Port 5000.* Above 1024, so the server binds without root.

**Blockers hit**

- `git remote add origin` and the URL were entered on separate lines,
  producing a usage error followed by bash attempting to execute the URL
  as a command. Both are one command.
- GitHub username was transposed (`abdallahrafik` vs `rafikabdallah`),
  producing `ERROR: Repository not found`. GitHub returns the same message
  for "does not exist" and "exists but you lack access", deliberately, so
  that private repository names cannot be probed. Fixed with
  `git remote set-url`.
- First `git push` failed with `src refspec main does not match any` —
  the branch existed by name but had no commit to point at. A branch is a
  pointer to a commit; with zero commits there is nothing to push.

---

## Phase 1 — Framing protocol

**Completed**

`common/protocol.py`: `send_msg`, `recv_exact`, `recv_msg`, a
`ProtocolError` hierarchy, and the `HEADER_SIZE` / `MAX_MSG_SIZE`
constants. Echo server and single client, verified over loopback and then
over the LAN interface.

**Decisions**

*Length-prefix framing over delimiters.* Binary-safe, requires no
escaping, and no byte-by-byte scanning. The receiver learns the exact
payload size before reading it.

*Big-endian.* Network byte order; independent of either endpoint's CPU.

*`protocol.py` performs no socket creation.* It reads and writes on
sockets handed to it and never binds, connects, or listens. This keeps
the module unit-testable without a network.

*`sendall`, not `send`.* `send` may transmit only part of the buffer and
report how much — a truncation bug that stays invisible until messages
grow.

**Traps encountered**

- `recv()` returning `b''` means the peer has closed, not that no data has
  arrived yet. Without an explicit check, `recv_exact` spins forever on a
  dead socket.
- `struct.unpack` returns a tuple even for a single value; `[0]` is
  required.
- The length header must be validated against `MAX_MSG_SIZE` *before*
  allocating, not after. See ARCHITECTURE.md §5.3.

---

## Phase 2 — Concurrency

**Completed**

Accept loop spawning one daemon thread per connection; shared client
registry guarded by a single lock; broadcast to all clients but the
sender. Client rewritten with a second thread so that receiving no longer
waits on the keyboard.

**Decisions**

*Thread-per-client over event-driven I/O.* Simpler control flow, natural
failure containment, and adequate for a LAN chat server. Trade-off
recorded in ARCHITECTURE.md §3.1.

*Snapshot the target list under the lock, send outside it.* `send_msg`
can block on a full TCP send buffer; holding the lock across that call
stalls every other thread behind the slowest client.

*Daemon threads.* Client threads block on `recv` indefinitely. Without
daemon status, `Ctrl+C` on the server hangs waiting for threads that will
never finish.

**Observed behaviour worth noting**

Before the client was threaded, messages from other users sat in the TCP
receive buffer until the local user pressed Enter — a clean demonstration
that the server was routing correctly and the limitation was entirely
client-side.

---

## Phase 3 — Authentication

**Completed**

`server/auth.py` (PBKDF2-HMAC-SHA256, 200 000 iterations, 16-byte random
salt, constant-time verification) and `server/db.py` (SQLite, one `users`
table, parameterised queries). Connection state gated: unauthenticated
sockets accept only `register` and `login`.

**Decisions**

*PBKDF2 over SHA-256, and over Argon2/bcrypt.* Reasoning in
ARCHITECTURE.md §5.1 — briefly: a fast hash is the wrong primitive, and
the memory-hard alternatives are not in the standard library.

*`os.urandom`, not `random`.* The `random` module is a Mersenne Twister:
fast, and fully predictable from a modest number of observed outputs.

*`hmac.compare_digest`, not `==`.* String equality short-circuits and
leaks position information through timing.

*Uniqueness enforced by the `UNIQUE` constraint*, not by a check-then-insert
in Python, which leaves a TOCTOU window between the two statements.

*A fresh SQLite connection per call.* Connections are not thread-safe by
default. Auth happens once per session, so the overhead is irrelevant and
an entire class of concurrency bug disappears.

*Registration does not auto-login.* Two distinct operations, easier to
reason about and to demonstrate.

**Blockers hit**

A test account created while verifying `db.py` persisted into the real
database and appeared to be a registration bug. It was not — validation
had correctly rejected the short password before reaching the database.
The account was left over from the test. Resolved by deleting `chat.db`.

Worth recording as a genuine lesson: test data leaking into a live
database is a routine mistake with real consequences, and test accounts
with known credentials surviving into production is a recurring pentest
finding. A separate test database file would prevent it.

---

## Phase 4 — Rooms

**Completed**

`rooms` (name → member set) and `user_rooms` (user → room) with
create-on-demand and delete-when-empty; `/join`, `/rooms`, `/who`;
broadcast scoped to a single room.

**Decisions**

*Room state kept in `server.py` rather than a separate `rooms.py`.*
Deliberate trade against the time budget. The room maps and the client
registry share one lock, and separating them across modules would mean
passing the lock across a module boundary. The stated architecture
specified `rooms.py`; this is a documented deviation, not an oversight.

*Both maps updated inside a single lock acquisition.* They are two views
of the same fact and must never disagree; a user in `rooms["general"]`
recorded as being in `dev` is a ghost that breaks broadcast and cleanup.

*Empty rooms deleted, except `general`.* `rooms` grows from network
input, so without this every `/join <randomname>` leaves permanent
residue — an unbounded structure driven by untrusted input.

*Room names length-capped and regex-constrained*, same reasoning as
usernames.

---

## Phase 5 — Private messaging

**Completed**

`/msg <user> <text>`, delivered to the recipient's socket only, with a
confirmation echo to the sender. Self-messaging and offline recipients
rejected with explicit errors.

**Decisions**

*`from` populated server-side*, never from the client's message. Same
rule as broadcast, and more important here: a private message carries no
surrounding context that would expose an impersonation.

*Private messages ignore rooms entirely.* They are addressed by identity,
not location, so two users in different rooms can exchange them.

*Metadata logged, content not.* `PM from=alice to=bob` records that a
message occurred without recording what it said.

*"Private" means routed to one recipient, not encrypted.* The server sees
every message in plaintext, as does anyone observing the network. Stated
explicitly in ARCHITECTURE.md so the term is not overread.

---

## Phase 6 — Logging

**Completed**

`server/logger.py`: rotating file handler (1 MB, 3 backups) plus console,
shared formatter, guarded against double handler registration. All
`print` calls in the server replaced with levelled log calls in
`key=value` format.

**Decisions**

*`WARNING` reserved for security-relevant events*, `INFO` for normal
operation, so that `grep WARNING` produces an incident view. Severity as
a filter, not decoration.

*Source IP on every authentication event.* "Failed login for alice" is
not actionable; "five failures from 192.168.119.77 in ten seconds" is.

*`UNAUTH action` and `UNKNOWN type` logged as warnings.* Neither is
reachable through the supplied client, so their appearance indicates
someone speaking the protocol with a hand-written tool.

*No secrets and no content in the log.* Chat records `len=` rather than
the message. Limits the damage if the log file is exposed.

*Rotation as a security control.* Unbounded logs are a denial-of-service
vector for anyone able to trigger log lines.

---

## Phase 7 — Shutdown and hardening

**Completed**

Shutdown routine notifying every connected client before closing;
`threading.Event` to suppress spurious departure broadcasts during
shutdown; 300-second idle receive timeout; `argparse` for `--host` and
`--port`; ANSI colour output and startup banner via `common/colors.py`.

**Decisions**

*`sock.shutdown(SHUT_RDWR)` before `close()`.* `close()` releases the
local descriptor; `shutdown()` sends the FIN immediately so the client's
blocked `recv` returns at once rather than hanging.

*Idle timeout as a security control, not a convenience.* A connection
opened and left silent holds a thread indefinitely; enough of them
exhaust the server without authenticating or sending payload —
slowloris-shaped resource exhaustion.

*`socket.timeout` caught before the generic `except Exception`.* It
subclasses `OSError`, so a broader handler placed first would swallow it
and lose the warning.

*`argparse` rather than editing constants.* This is what makes the
project a tool rather than a script — the same binary runs against any
server address.

*Colour disabled automatically when stdout is not a TTY.* Without the
`isatty()` check, redirecting output to a file fills it with escape
sequences.

---

## Phase 8 — Documentation and verification

**Completed**

README.md, ARCHITECTURE.md, this log. Cross-machine verification: Ubuntu
Server VM running the server, with clients connected simultaneously from
the VM itself (`127.0.0.1`) and from the Windows 11 host
(`192.168.119.1`), confirmed in the server log.

**Note on verification**

Development was initially conducted entirely on the VM, so all early
testing ran over loopback. Loopback exercises the socket API but not the
network stack in any meaningful sense. Running a Windows client against
the Linux server was what actually validated the cross-platform claim and
the `0.0.0.0` bind.

---

## Tooling notes

Pasting large files into `nano` over SSH produced corruption — duplicated
content and stray bracketed-paste escape sequences (`1~`) embedded in the
source. Switching to a shell heredoc (`cat > file << 'EOF'`) resolved it:
no editor is involved, so nothing reinterprets the input. Quoting the
delimiter prevents the shell expanding `$` and backticks inside the
pasted code.

`python3 -m py_compile <file>` after every paste caught these failures
immediately rather than at runtime.

---

## If the project were continued

Priority order is recorded in ARCHITECTURE.md §7. The first item — TLS
via the standard library `ssl` module — is the only one that changes the
project's deployability rather than its feature set, and is the
prerequisite for running it anywhere other than a trusted network.
