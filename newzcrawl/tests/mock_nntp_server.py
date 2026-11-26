"""Threaded in-process NNTP server for tests and offline demos.

Serves a small set of yEnc binary files + a text article across a few
newsgroups, supporting: greeting, AUTHINFO, CAPABILITIES, LIST GROUPS /
LIST NEWSGROUPS / LIST OVERVIEW.FMT, GROUP, SELECT, SLAVE, STAT, LAST,
XOVER, XHDR, BODY/HEAD, POST, QUIT.

Usage in tests:
    srv = MockNNTPServer()
    srv.add_file("binary.bin", b"...")
    srv.start()
    client = NNTPClient("127.0.0.1", srv.port, username="u", password="p")
    ...
Also usable standalone:  python3 tests/mock_nntp_server.py 9090
"""

from __future__ import annotations

import fnmatch
import re
import socket
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class MockArticle:
    message_id: str
    group: str
    subject: str
    author: str
    date: str
    body: bytes
    yenc: bool = False
    references: list[str] = field(default_factory=list)


@dataclass
class MockFile:
    """A logical file made of one or more yEnc articles (parts)."""
    name: str
    data: bytes
    parts: list[str]  # message ids, in order


class MockNNTPServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 0,
                 require_auth: bool = True,
                 username: str = "test", password: str = "test"):
        self.host = host
        self._port = port
        self.require_auth = require_auth
        self.username = username
        self.password = password
        self.articles: dict[str, MockArticle] = {}
        self.groups: dict[str, int] = {}  # name -> article count
        self.ordered: dict[str, list[str]] = {}  # group -> [message ids]
        self.overview: dict[str, list[str]] = {}  # msgid -> overview fields
        self.received_posts: list[str] = []
        self._srv: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.port = 0

    # -- setup ---------------------------------------------------------
    def add_file(self, name: str, data: bytes, group: str = "bin.test",
                 part_size: int = 64 * 1024) -> MockFile:
        from newzcrawl import yenc
        parts = []
        base_id = re.sub(r"[^A-Za-z0-9]", "", name)
        bodies = yenc.encode_multipart(data, name, part_size=part_size)
        for i, body in enumerate(bodies, 1):
            mid = f"part{i:03d}-{base_id.lower()}-mock@newzcrawl.test"
            self._add_article(MockArticle(
                message_id=mid, group=group,
                subject=f"[{name}] part {i}/{len(bodies)}",
                author="mock@newzcrawl.test",
                date=datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000"),
                body=body, yenc=True,
            ))
            parts.append(mid)
        return MockFile(name=name, data=data, parts=parts)

    def add_text(self, message_id: str, group: str, subject: str, body: str):
        self._add_article(MockArticle(
            message_id=message_id, group=group, subject=subject,
            author="poster@newzcrawl.test",
            date=datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000"),
            body=body.encode(), yenc=False,
        ))

    def _add_article(self, art: MockArticle):
        self.articles[art.message_id] = art
        self.groups[art.group] = self.groups.get(art.group, 0) + 1
        self.ordered.setdefault(art.group, []).append(art.message_id)
        refs = " ".join(art.references)
        self.overview[art.message_id] = [
            art.message_id, refs, art.subject, art.author, art.date,
            str(len(art.body)),
        ]

    # -- lifecycle -----------------------------------------------------
    def start(self) -> None:
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind((self.host, self._port))
        self.port = self._srv.getsockname()[1]
        self._srv.listen(16)
        self._stop.clear()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._srv:
            self._srv.close()
        if self._thread:
            self._thread.join(timeout=5)

    def __enter__(self) -> "MockNNTPServer":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    # -- protocol ------------------------------------------------------
    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._srv.accept()
            except OSError:
                break
            t = threading.Thread(target=self._handle, args=(conn,), daemon=True)
            t.start()

    def _handle(self, conn: socket.socket) -> None:
        state = {"authed": not self.require_auth, "pending_user": None}
        reader = _BufReader(conn)
        try:
            conn.settimeout(30)
            conn.sendall(b"200 mock-nntp ready\r\n")
            while True:
                line = reader.readline()
                if line is None:
                    break
                cmd = line.decode("ascii", "replace").strip()
                if not cmd:
                    continue
                kw, _, arg = cmd.partition(" ")
                kw = kw.upper()
                resp = self._dispatch(kw, arg, state, reader)
                if resp is None:
                    break
                if isinstance(resp, tuple):
                    conn.sendall(resp[0].encode() + b"\r\n")
                    for ln in resp[1]:
                        conn.sendall(ln + b"\r\n")
                    conn.sendall(b".\r\n")
                else:
                    conn.sendall(resp.encode() + b"\r\n")
                if kw == "QUIT":
                    break
        except (OSError, ConnectionError):
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass


    def _resolve_range(self, rng: str, state: dict) -> list[str]:
        """Resolve an XHDR/XOVER range operand to a list of message-ids.

        Handles the dash numeric form (``12-20``), the space numeric form
        (``12 20``), a single number, and a space/comma message-id list.
        """
        rng = rng.strip()
        # numeric: "lo" or "lo-hi" or "lo hi"; message-ids may contain '-'
        # so a non-numeric dash token is treated as a single message-id.
        if "-" in rng and " " not in rng and "," not in rng:
            a, _, b = rng.partition("-")
            if a.isdigit() and b.isdigit():
                lo, hi = int(a), int(b)
            else:
                return [rng] if rng in self.articles else []
        elif " " in rng or "," in rng:
            toks = [t for t in rng.replace(",", " ").split() if t]
            if all(t.isdigit() for t in toks):
                lo, hi = int(toks[0]), int(toks[-1])
            else:
                return [m for m in toks if m in self.articles]
        else:
            if not rng.isdigit():
                return [rng] if rng in self.articles else []
            lo = hi = int(rng)
        g = state.get("group")
        mids = self.ordered.get(g, [])
        lo = max(1, lo)
        hi = min(len(mids), hi)
        if lo > hi:
            return []
        return mids[lo - 1:hi]

    def _artnum(self, mid: str, state: dict) -> str:
        """1-based article number of ``mid`` (current group preferred)."""
        groups = []
        g = state.get("group")
        if g:
            groups.append(g)
        groups.extend(x for x in self.ordered if x != g)
        for grp in groups:
            ordered = self.ordered.get(grp, [])
            if mid in ordered:
                return str(ordered.index(mid) + 1)
        return "0"

    def _dispatch(self, kw: str, arg: str,
                  state: dict, reader: "_BufReader") -> str | tuple[str, list[bytes]] | None:
        A = self.articles
        if self.require_auth and not state["authed"] and kw not in ("AUTHINFO", "QUIT"):
            return "502 authentication required"
        if kw == "AUTHINFO":
            # handled in two steps: AUTHINFO USER x / AUTHINFO PASS y
            if arg.upper().startswith("USER "):
                state["pending_user"] = arg[5:].strip()
                return "381 send password"
            if arg.upper().startswith("PASS "):
                pw = arg[5:].strip()
                user = state.get("pending_user") or ""
                if user == self.username and pw == self.password:
                    state["authed"] = True
                    return "281 authentication accepted"
                return "501 authentication failed"
            return "500 bad AUTHINFO"
        if kw == "CAPABILITIES":
            return ("101 capabilities follow",
                    [b"reading", b"posting", b"authinfo", b"xover", b"xhdr",
                     b"select", b"slave", b"list.overview.fmt"])
        if kw == "LIST":
            sub, _, a2 = arg.partition(" ")
            sub = sub.upper()
            if sub in ("GROUPS", "NEWSGROUPS"):
                pat = a2.strip().strip('"') or "*"
                rows = [f"{g}\t*\ty".encode() for g in sorted(self.groups)
                        if fnmatch.fnmatch(g, pat)]
                return ("211 list of groups", rows)
            if sub == "OVERVIEW.FMT":
                return ("214 overview format follows",
                        [b"Message-ID References Subject From Date Bytes"])
            if sub == "":
                rows = [f"{g} * y".encode() for g in sorted(self.groups)]
                return ("215 active file follows", rows)
            return "504 unknown LIST"
        if kw == "GROUP":
            g = arg.strip()
            if g in self.groups:
                state["group"] = g
                return f"211 {self.groups[g]} {g}"
            return f"404 no such group {g}"
        if kw in ("SELECT", "SLAVE"):
            g = arg.strip()
            if g in self.groups:
                state["group"] = g
                return f"201 {g} selected"
            return f"404 no such group {g}"
        if kw == "STAT":
            mid = arg.strip()
            if mid in A:
                a = A[mid]
                return f"231 {len(a.body)} {mid}"
            return f"420 no such article {mid}"
        if kw == "LAST":
            g = state.get("group")
            ordered = self.ordered.get(g, []) if g else []
            last = len(ordered)
            return f"269 {last}"
        if kw == "XOVER":
            mids = self._resolve_range(arg, state)
            if not mids:
                return "420 no such article"
            # Real protocol: "<artnum>\\t<f1>\\t<f2>..." with fields in
            # OVERVIEW.FMT order (Message-ID References Subject From Date Bytes).
            rows = []
            for m in mids:
                artnum = self._artnum(m, state)
                rows.append(
                    (f"{artnum}\t" + "\t".join(self.overview[m])).encode())
            return ("224 overview data follows", rows)
        if kw == "XHDR":
            name, _, rng = arg.partition(" ")
            name = name.strip()
            mids = self._resolve_range(rng, state)
            if not mids:
                return "420 no such article"
            rows = []
            for m in mids:
                a = A[m]
                val = {"message-id": m, "subject": a.subject,
                       "from": a.author, "date": a.date,
                       "bytes": str(len(a.body)),
                       "references": " ".join(a.references)}.get(name.lower(), "")
                rows.append(f"{self._artnum(m, state)} {val}".encode())
            return ("221 header data follows", rows)
        if kw in ("BODY", "HEAD", "ARTICLE"):
            mid = arg.strip()
            a = A.get(mid)
            if not a:
                return f"420 no such article {mid}"
            body_lines = a.body.replace(b"\r\n", b"\n").split(b"\n")
            # RFC 5538 dot-folding: prefix EVERY line that starts with a dot
            body_lines = [b"." + l if l.startswith(b".") else l for l in body_lines]
            if kw == "BODY":
                return ("222 body follows", body_lines)
            headers = (
                f"Message-ID: {a.message_id}\r\n"
                f"Newsgroups: {a.group}\r\n"
                f"Subject: {a.subject}\r\n"
                f"From: {a.author}\r\n"
                f"Date: {a.date}\r\n"
                f"Content-Length: {len(a.body)}\r\n"
                + ("Content-Transfer-Encoding: yEnc\r\n" if a.yenc else "")
            )
            hdr_lines = headers.rstrip("\r\n").encode().split(b"\r\n")
            if kw == "HEAD":
                return ("221 head and body follow", hdr_lines + [b""] + body_lines)
            # ARTICLE
            return ("220 article follows", hdr_lines + [b""] + body_lines)
        if kw == "POST":
            # POST body arrives on the following lines until a lone "."
            lines = []
            while True:
                data = reader.readline()
                if data is None:
                    return None
                if data == b".":
                    break
                if data.startswith(b".."):
                    data = data[1:]
                lines.append(data)
            self.received_posts.append(b"\n".join(lines).decode("utf-8", "replace"))
            return "240 article posted"
        if kw == "QUIT":
            return "208 closing connection"
        return f"500 unknown command {kw}"


class _BufReader:
    """Persistent line reader so data buffered for one command (e.g. the
    POST body that follows the POST line in the same TCP segment) is not
    lost. ``readline`` returns the line bytes (no CRLF) or None on EOF."""

    def __init__(self, conn: socket.socket):
        self.conn = conn
        self.buf = b""

    def readline(self) -> bytes | None:
        while b"\r\n" not in self.buf:
            try:
                chunk = self.conn.recv(65536)
            except OSError:
                return None
            if not chunk:
                return None
            self.buf += chunk
        line, self.buf = self.buf.split(b"\r\n", 1)
        return line


if __name__ == "__main__":
    import sys
    import os
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9090
    srv = MockNNTPServer(port=port)
    srv.add_file("demo.bin", os.urandom(256 * 1024))
    srv.add_text("<text-1@newzcrawl.test>", "news.test", "Hello", "plain text article")
    srv.start()
    print(f"mock NNTP on 127.0.0.1:{srv.port} (user test/test)")
    try:
        import time
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        srv.stop()
