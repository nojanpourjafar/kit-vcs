"""`kit ui`: Kit as an app in the browser.

A small web server runs on this computer only (127.0.0.1) and serves one page,
ui.html. The page calls the JSON endpoints below, and each endpoint is a thin
wrapper around the same Repo methods the command line uses.

Because a local server can be reached by any web page open in the same
browser, three checks guard it:
  * it listens on 127.0.0.1 only, so other machines cannot connect;
  * every request must carry a random token that only our own page knows;
  * the Host header must be this server, which blocks DNS-rebinding tricks.
"""

from __future__ import annotations

import json
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from .audio import describe
from .manifest import diff
from .repo import DIR, KitError, Repo, _default_author

PAGE = Path(__file__).with_name("ui.html")


class App:
    """The state behind the page: one kit folder, and the actions on it."""

    def __init__(self, folder):
        try:
            self.folder = Repo.find(folder).folder
        except KitError:
            self.folder = Path(folder).resolve()  # not a repo yet; the page will say so
        self.token = secrets.token_urlsafe(16)
        self.lock = threading.RLock()  # one action at a time

    def repo(self) -> Repo:
        if not (self.folder / DIR).is_dir():
            raise KitError("this folder is not a Kit repo yet")
        return Repo(self.folder)

    # ---- reading ---------------------------------------------------------

    def state(self) -> dict:
        """Everything the "Now" screen shows."""
        if not (self.folder / DIR).is_dir():
            return {"repo": False, "name": self.folder.name, "folder": str(self.folder),
                    "author": _default_author()}
        repo = self.repo()
        history = repo.log()
        labels = {c.id: f"v{len(history) - i}" for i, c in enumerate(history)}
        saved = repo.manifest_of(repo.head())
        working = repo.working()
        changes = diff(saved, working)
        by_hash = {saved[path]["hash"]: commit for path, commit in repo.credits().items()}
        return {
            "repo": True,
            "name": self.folder.name,
            "folder": str(self.folder),
            "author": repo.author,
            "remote": repo.remote(),
            "versions": [
                {"label": labels[c.id], "id": c.id, "author": c.author, "message": c.message,
                 "time": c.time, "merge": bool(c.merged)}
                for c in history
            ],
            "changes": [vars(change) for change in changes],
            "sounds": self._sounds(working, changes, by_hash, labels,
                                   locate=lambda path, entry: repo.folder / path, key="path"),
            "removed": [c.path for c in changes if c.kind == "removed"],
        }

    def version(self, ref: str) -> dict:
        """Everything the screen for one saved version shows."""
        repo = self.repo()
        history = repo.log()
        labels = {c.id: f"v{len(history) - i}" for i, c in enumerate(history)}
        commit = repo.load_commit(repo.resolve(ref))
        manifest = repo.manifest_of(commit.id)
        changes = diff(repo.manifest_of(commit.parent), manifest)
        by_hash = {manifest[path]["hash"]: c for path, c in repo.credits(commit.id).items()}
        return {
            "label": labels.get(commit.id, commit.id[:8]),
            "id": commit.id, "author": commit.author, "message": commit.message,
            "time": commit.time, "latest": commit.id == repo.head(),
            "sounds": self._sounds(manifest, changes, by_hash, labels,
                                   locate=lambda path, entry: repo.store.path_for(entry["hash"]),
                                   key="hash"),
            "removed": [c.path for c in changes if c.kind == "removed"],
        }

    def _sounds(self, manifest, changes, by_hash, labels, locate, key) -> list[dict]:
        changed = {c.path: c for c in changes if c.kind != "removed"}
        sounds = []
        for path, entry in sorted(manifest.items()):
            change = changed.get(path)
            credit = by_hash.get(entry["hash"])
            sounds.append({
                "path": path,
                "size": entry["size"],
                # How the page asks for this sound's audio and waveform: by
                # path for the working folder, by hash for a saved version.
                "source": f"path={_quote(path)}" if key == "path"
                          else f"hash={entry['hash']}&name={_quote(path)}",
                "info": describe(locate(path, entry), path),
                "change": change.kind if change else None,
                "was": change.old_path if change else None,
                "by": credit.author if credit else None,
                "since": labels.get(credit.id, credit.id[:8]) if credit else None,
            })
        return sounds

    # ---- actions ---------------------------------------------------------

    def act(self, action: str, body: dict) -> dict:
        """Run one action from the page and say what happened."""
        if action == "init":
            Repo.init(self.folder, author=(body.get("author") or "").strip() or None)
            return {"message": "Started tracking this folder."}
        repo = self.repo()
        if action == "commit":
            saved = repo.commit(body.get("message", ""))
            return {"message": f"Saved v{len(repo.log())}: {saved.commit.message}"}
        if action == "restore":
            changes = repo.restore(body.get("version", ""), force=bool(body.get("force")))
            return {"message": f"Put {len(changes)} file(s) back. Save a version to keep it this way."
                    if changes else "The folder already matches that version."}
        raise KitError(f"unknown action: {action}")


def _quote(text: str) -> str:
    return quote(text, safe="")


class Handler(BaseHTTPRequestHandler):
    app: App  # set by serve()

    def log_message(self, *args) -> None:  # keep the terminal quiet
        pass

    # ---- plumbing --------------------------------------------------------

    def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, data) -> None:
        self._send(status, json.dumps(data).encode(), "application/json")

    def _allowed(self, query: dict) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost"):
            return False
        token = self.headers.get("X-Kit-Token") or query.get("t", "")
        return secrets.compare_digest(token, self.app.token)

    def _run(self, work) -> None:
        """Run an endpoint, turning problems into a JSON error the page can show."""
        try:
            with self.app.lock:
                self._json(200, work())
        except KitError as err:
            self._json(400, {"error": str(err)})
        except (ValueError, OSError) as err:
            self._json(400, {"error": f"{type(err).__name__}: {err}"})

    # ---- routes ----------------------------------------------------------

    def do_GET(self) -> None:
        url = urlparse(self.path)
        query = {key: values[0] for key, values in parse_qs(url.query).items()}
        if url.path == "/":
            host = (self.headers.get("Host") or "").split(":")[0]
            if host not in ("127.0.0.1", "localhost"):
                return self._json(403, {"error": "forbidden"})
            page = PAGE.read_text(encoding="utf-8").replace("__KIT_TOKEN__", self.app.token)
            return self._send(200, page.encode(), "text/html; charset=utf-8")
        if url.path == "/favicon.ico":
            return self._send(204, b"", "image/x-icon")  # browsers always ask; there is no icon
        if not self._allowed(query):
            return self._json(403, {"error": "forbidden"})
        if url.path == "/api/state":
            return self._run(self.app.state)
        if url.path == "/api/version":
            return self._run(lambda: self.app.version(query.get("ref", "latest")))
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        url = urlparse(self.path)
        if not self._allowed({}) or not url.path.startswith("/api/"):
            return self._json(403, {"error": "forbidden"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._json(400, {"error": "bad request"})
        self._run(lambda: self.app.act(url.path[len("/api/"):], body))

def make_server(folder, port: int = 0) -> tuple[ThreadingHTTPServer, App]:
    """Build the server without starting it. Port 0 picks any free port."""
    app = App(folder)
    handler = type("KitHandler", (Handler,), {"app": app})
    return ThreadingHTTPServer(("127.0.0.1", port), handler), app


def serve(folder=".", port: int = 0, open_browser: bool = True) -> None:
    server, app = make_server(folder, port)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"Kit is running for {app.folder}")
    print(f"Open {url}  (press Ctrl+C here to stop)")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
