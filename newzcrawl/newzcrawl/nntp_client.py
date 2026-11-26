"""NNTP client (RFC 5538) — pure standard library.

Supports: AUTHINFO USER/PASS (RFC 4643), CAPABILITIES, LIST GROUPS /
LIST NEWSGROUPS / LIST OVERVIEW.FMT, XOVER, XHDR, ARTICLE/BODY/HEAD
fetching, STAT, POST, SELECT/SLAVE (parallel connections), and simple
reconnect/timeout handling.

The client is *not* thread-safe; the queue module gives each worker its
own client connection (mirroring NewsLeecher's parallel connection
model).
"""

from __future__ import annotations

import email
import fnmatch
import logging
import socket
import time
from dataclasses import dataclass, field
from email.header import decode_header, make_header
from typing import Callable, Iterable

log = logging.getLogger("newzcrawl.nntp")

DEFAULT_TIMEOUT = 30.0
MAX_LINE = 1_000_000


def _has_body(code: int, message: str, expect: bool) -> bool:
    """Decide whether a reply carries a lone-'.'-terminated body.

    1xx intermediate responses always do. When a body was *not* expected
    the only reply that can carry one is a spontaneous notice whose
    message ends in ``...follows`` (e.g. a MOTD). When a body *was*
    expected, a 2xx success carries it but an error (3xx/4xx/5xx, e.g.
    ``420 no such article``) never does — reading one anyway would block
    forever.
    """
    if 100 <= code <= 199:
        return True
    if not expect:
        return "follows" in message.lower()
    return (200 <= code <= 299) or ("follows" in message.lower())


class NNTPError(Exception):
    """An NNTP-level error (5xx response or protocol violation)."""

    def __init__(self, code: int, message: str):
        super().__init__(f"NNTP {code}: {message}")
        self.code = code
        self.message = message


class ConnectionError_(NNTPError):
    pass


@dataclass
class Article:
    message_id: str
    group: str
    subject: str = ""
    author: str = ""
    date: str = ""
    size: int = 0
    references: list[str] = field(default_factory=list)
    headers: dict = field(default_factory=dict)
    body: bytes = b""
    has_yenc: bool = False
    decoded: bytes | None = None
    crc_ok: bool | None = None


def decode_mime_header(value: str) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


class NNTPClient:
    def __init__(self, host: str, port: int = 119, *, timeout: float = DEFAULT_TIMEOUT,
                 socks: tuple[str, int] | None = None,
                 read_timeout: float | None = None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.read_timeout = read_timeout or timeout
        self.socks = socks
        self.sock: socket.socket | None = None
        self._buf = b""
        self.authenticated = False
        self.caps: list[str] = []
        self.server_name: str = ""
        self.current_group: str | None = None
        self._ovfmt: list[str] | None = None  # cached overview field names

    # -- connection ----------------------------------------------------
    def connect(self) -> None:
        if self.socks:
            self.sock = _socks5_connect(self.socks[0], self.socks[1],
                                        self.host, self.port, self.timeout)
        else:
            self.sock = socket.create_connection((self.host, self.port),
                                                 self.timeout)
        self.sock.settimeout(self.read_timeout)
        code, msg = self._read_response()
        if code != 200:
            raise NNTPError(code, f"unexpected greeting: {msg}")
        self.server_name = msg.split(" ", 1)[-1]

    def close(self) -> None:
        if self.sock:
            try:
                self.send("QUIT")
            except Exception:
                pass
            try:
                self.sock.close()
            finally:
                self.sock = None

    def __enter__(self) -> "NNTPClient":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- low-level protocol -------------------------------------------
    def _recv_until(self, term: bytes = b"\r\n") -> bytes:
        assert self.sock is not None
        while term not in self._buf:
            if len(self._buf) > MAX_LINE:
                raise NNTPError(0, "line too long")
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError_(0, "connection closed by server")
            self._buf += chunk
        line, self._buf = self._buf.split(term, 1)
        return line + term

    def _read_response(self) -> tuple[int, str]:
        line = self._recv_until().rstrip(b"\r\n").decode("ascii", "replace")
        code = int(line[:3])
        return code, line[4:]

    def _read_body_lines(self) -> list[bytes]:
        """Read a response body terminated by a lone '.' line."""
        lines: list[bytes] = []
        while True:
            line = self._recv_until().rstrip(b"\r\n")
            if line == b".":
                break
            if line.startswith(b".."):
                line = line[1:]
            lines.append(line)
        return lines

    def _send_command(self, cmd: str, expect_body: bool = False,
                      allow: tuple[int, ...] = (200,)) -> tuple[int, str | list[bytes]]:
        assert self.sock is not None, "not connected"
        self.sock.sendall(cmd.encode("ascii", "replace") + b"\r\n")
        code, msg = self._read_response()
        # Some servers interleave a spontaneous "201 ... MOTD follows"
        # before the real reply; drain any such notice. (A plain "201
        # group selected" has no body, so it is not consumed here.)
        while code == 201 and "follows" in msg.lower():
            self._read_body_lines()
            code, msg = self._read_response()
        # Consume the body (if any) BEFORE deciding the code is valid:
        # an unexpected code that still carries a body (e.g. "215 active
        # file follows") must be drained or the connection desyncs.
        body: list[bytes] | None = None
        if _has_body(code, msg, expect_body):
            body = self._read_body_lines()
        if code not in allow:
            raise NNTPError(code, msg or cmd)
        return (code, body) if body is not None else (code, msg)

    def send(self, cmd: str, expect_body: bool = False,
             allow: tuple[int, ...] | None = None) -> tuple[int, str | list[bytes]]:
        allow = allow or (200, 221, 222, 223)
        return self._send_command(cmd, expect_body=expect_body, allow=allow)

    # -- commands ------------------------------------------------------
    def authenticate(self, username: str, password: str) -> None:
        # NewsLeecher enforced lowercase-only server passwords.
        if password != password.lower():
            log.warning("NNTP password contains uppercase; server may reject it")
        # 381 = "send password" (expected intermediate response)
        self.send(f"AUTHINFO USER {username}", allow=(381,))
        code, _ = self.send(f"AUTHINFO PASS {password}",
                            allow=(281, 282, 285, 286))
        self.authenticated = code in (281, 282, 285, 286)

    def capabilities(self) -> list[str]:
        _, msg = self.send("CAPABILITIES", allow=(101, 200))
        # 101 multi-line bodies arrive as a list of lines
        if isinstance(msg, list):
            tokens = " ".join(ln.decode("ascii", "replace") for ln in msg).split()
        else:
            tokens = str(msg).replace("\n", " ").split()
        self.caps = [t.strip() for t in tokens if t.strip()]
        return self.caps

    def list_groups(self, pattern: str = "*") -> list[tuple[str, str, str]]:
        """Return (group, lowmark, highmark) tuples matching ``pattern``.

        Falls back to the ACTIVE file (bare LIST) with client-side
        filtering when the server rejects the pattern form (501).
        """
        try:
            _, lines = self.send(f"LIST GROUPS {pattern}", expect_body=True,
                                 allow=(100, 211))
        except NNTPError as e:
            if e.code == 501:
                return [a for a in self.list_active()
                        if fnmatch.fnmatch(a[0], pattern)]
            raise
        out = []
        for ln in lines:
            parts = ln.decode("ascii", "replace").split()
            if len(parts) >= 3:
                out.append((parts[0], parts[1], parts[2]))
        return out

    def list_active(self) -> list[tuple[str, str, str]]:
        """Return (group, lowmark, highmark) from the ACTIVE file.

        Some servers (e.g. nntpswitch) answer bare LIST with code 215
        "active file follows"; others use 211. Both are handled.
        """
        _, lines = self.send("LIST", expect_body=True, allow=(100, 211, 215))
        out = []
        for ln in lines:
            parts = ln.decode("ascii", "replace").split()
            if len(parts) >= 3:
                out.append((parts[0], parts[1], parts[2]))
        return out

    def list_newsgroups(self, pattern: str = "*") -> list[tuple[str, str, str]]:
        try:
            _, lines = self.send(f"LIST NEWSGROUPS {pattern}", expect_body=True,
                                 allow=(100, 211))
        except NNTPError as e:
            if e.code == 501:
                return [a for a in self.list_active()
                        if fnmatch.fnmatch(a[0], pattern)]
            raise
        out = []
        for ln in lines:
            parts = ln.decode("ascii", "replace").rsplit(None, 2)
            if len(parts) >= 3:
                out.append((parts[0], parts[1], parts[2]))
        return out

    def list_overview_fmt(self) -> list[str]:
        """Return the ordered overview field names (cached on the client).

        RFC 5538 uses code 214 for this but some servers (e.g.
        nntpswitch) answer with 215, so both are accepted.
        """
        if self._ovfmt is not None:
            return self._ovfmt
        _, lines = self.send("LIST OVERVIEW.FMT", expect_body=True,
                             allow=(100, 214, 215))
        self._ovfmt = [t for ln in lines
                       for t in ln.decode("ascii", "replace").split()]
        return self._ovfmt

    def group(self, name: str) -> int:
        _, msg = self.send(f"GROUP {name}", allow=(201, 211))
        self.current_group = name
        return int(str(msg).split()[0])

    def select(self, name: str) -> str:
        """SELECT: switch this connection to a group (parallel fetch)."""
        _, msg = self.send(f"SELECT {name}", allow=(201,))
        self.current_group = name
        return name

    def slave(self, name: str) -> str:
        """SLAVE: like SELECT but marks the connection as a slave."""
        _, msg = self.send(f"SLAVE {name}", allow=(201,))
        self.current_group = name
        return name

    def stat(self, message_id: str) -> int:
        _, msg = self.send(f"STAT {message_id}", allow=(231,))
        return int(str(msg).split()[0])

    def last(self, message_id: str | None = None) -> int:
        cmd = "LAST" if message_id is None else f"LAST {message_id}"
        _, msg = self.send(cmd, allow=(230, 269))
        return int(str(msg).split()[0])

    @staticmethod
    def _range(loid: str, hid: str | None) -> str:
        """Build a range operand. Numeric ranges use the ``first-last``
        dash form (widely supported, e.g. nntpswitch); message-id lists
        use space separation."""
        if hid is None:
            return loid
        if loid.isdigit() and hid.isdigit():
            return f"{loid}-{hid}"
        return f"{loid} {hid}"

    def xover(self, msgid_lo: str, msgid_hi: str | None = None) -> dict[str, list[str]]:
        cmd = f"XOVER {self._range(msgid_lo, msgid_hi)}"
        _, lines = self.send(cmd, expect_body=True, allow=(100, 224))
        return {ln.decode("ascii", "replace").split(None, 1)[0]:
                ln.decode("ascii", "replace").split(None, 1)[1].split("\t")
                for ln in lines}

    def xover_dict(self, msgid_lo: str,
                   msgid_hi: str | None = None) -> dict[str, dict[str, str]]:
        """XOVER returning rows keyed by the server's overview field names.

        Servers disagree on the overview column order (some use the
        standard 6, others a wider 8-column layout with Path/Xref), so
        the names are read from ``LIST OVERVIEW.FMT`` rather than assumed.
        """
        fmt = [f.rstrip(":") for f in self.list_overview_fmt()]
        out: dict[str, dict[str, str]] = {}
        for num, fields in self.xover(msgid_lo, msgid_hi).items():
            row = {fmt[i]: (fields[i] if i < len(fields) else "")
                   for i in range(len(fmt))}
            out[num] = row
        return out

    def xhdr(self, name: str, msgid_lo: str, msgid_hi: str | None = None) -> dict[str, str]:
        cmd = f"XHDR {name} {self._range(msgid_lo, msgid_hi)}"
        _, lines = self.send(cmd, expect_body=True, allow=(100, 221))
        out: dict[str, str] = {}
        for ln in lines:
            key, _, val = ln.decode("ascii", "replace").partition(" ")
            out[key] = val
        return out

    def fetch(self, message_id: str, group: str | None = None,
              body_only: bool = False, decode_yenc: bool = True
              ) -> Article:
        """Fetch an article and build an Article.

        Uses ARTICLE (headers + body) by default so that
        Content-Transfer-Encoding can be detected. ``body_only=True``
        uses BODY and skips header parsing / yEnc auto-decode (call
        :meth:`NNTPClient.decode_body` on the returned bytes if needed).
        """
        if body_only:
            code, lines = self.send(f"BODY {message_id}", expect_body=True,
                                    allow=(100, 222))
            body_part = b"\r\n".join(lines)
            parsed: dict[str, str] = {}
        else:
            code, lines = self.send(f"ARTICLE {message_id}", expect_body=True,
                                    allow=(100, 220))
            raw = b"\r\n".join(lines)
            sep = raw.find(b"\r\n\r\n")
            if sep == -1:
                parsed, body_part = {}, raw
            else:
                parsed = _parse_headers(raw[:sep])
                body_part = raw[sep + 4:]
        art = Article(
            message_id=message_id,
            group=group or parsed.get("Newsgroups", "") or self.current_group or "",
            subject=decode_mime_header(parsed.get("Subject", "")),
            author=decode_mime_header(parsed.get("From", "")),
            date=parsed.get("Date", ""),
            size=len(body_part),
            headers=parsed,
            body=body_part,
        )
        art.references = [r for r in
                          art.headers.get("References", "").split() if r]
        if decode_yenc and self._is_yenc(art, body_part):
            from newzcrawl import yenc
            art.has_yenc = True
            try:
                art.decoded, crc = yenc.decode(body_part)
                art.crc_ok = yenc.check_crc(art.decoded, crc)
            except ValueError:
                art.decoded = None
                art.crc_ok = False
        return art

    @staticmethod
    def _is_yenc(art: Article, body: bytes) -> bool:
        """Detect a yEnc payload from headers or the ``=y`` magic."""
        cte = art.headers.get("Content-Transfer-Encoding", "")
        if cte.strip().lower() == "yenc":
            return True
        return body.lstrip()[:2] == b"=y"

    def decode_body(self, body: bytes, verify: bool = True
                    ) -> tuple[bytes, bool]:
        """Decode a yEnc article body; returns (raw, crc_ok)."""
        from newzcrawl import yenc
        raw, crc = yenc.decode(body)
        return raw, (yenc.check_crc(raw, crc) if verify else True)

    def post(self, raw_article: str) -> None:
        body = raw_article.replace("\r\n", "\n").replace("\r", "\n")
        lines = body.split("\n")
        if lines and lines[-1] == "":
            lines = lines[:-1]  # drop artifact of trailing newline
        # fold every line with a leading dot (RFC 5538)
        folded = "\r\n".join("." + ln if ln.startswith(".") else ln
                             for ln in lines)
        self.sock.sendall(b"POST\r\n" + folded.encode("utf-8") + b"\r\n.\r\n")
        code, msg = self._read_response()
        if code not in (240, 241):
            raise NNTPError(code, msg)


def _parse_headers(raw: bytes) -> dict[str, str]:
    """Parse RFC822 headers (continuation lines folded)."""
    text = raw.decode("utf-8", "replace")
    headers: dict[str, str] = {}
    current = None
    for line in text.split("\n"):
        line = line.rstrip("\r")
        if not line:
            continue
        if line[0] in " \t" and current:
            headers[current] += " " + line.strip()
        else:
            key, _, val = line.partition(":")
            current = key.strip()
            headers[current] = val.strip()
    return headers


def _socks5_connect(proxy_host: str, proxy_port: int,
                    dst_host: str, dst_port: int, timeout: float) -> socket.socket:
    """Minimal SOCKS5 (no auth) connect, stdlib only."""
    s = socket.create_connection((proxy_host, proxy_port), timeout)
    s.settimeout(timeout)
    dst = dst_host.encode("idna")
    if len(dst) > 255:
        raise NNTPError(0, "hostname too long for SOCKS5")
    s.sendall(b"\x05\x01\x00")  # version, 1 method: no-auth
    resp = s.recv(2)
    if resp != b"\x05\x00":
        raise NNTPError(0, "SOCKS5 proxy rejected (no-auth not accepted)")
    s.sendall(b"\x05\x01\x00\x03" + bytes([len(dst)]) + dst +
              dst_port.to_bytes(2, "big"))
    header = s.recv(4)
    if len(header) < 4 or header[1] != 0:
        raise NNTPError(0, f"SOCKS5 connect failed: {header[1]:#x}")
    return s


def iter_lines(lines: Iterable[bytes]) -> Iterable[str]:
    return (ln.decode("ascii", "replace") for ln in lines)
