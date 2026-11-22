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
import mimetypes
import os
import secrets
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from . import names, remote, tags
from .audio import describe
from .audio import peaks, tempo
from .manifest import diff
from .repo import DIR, KitError, Repo, _default_author, clean_name

PAGE = Path(__file__).with_name("ui.html")
MAX_RECENT = 12


def config_dir() -> Path:
    """Where the app keeps its own small settings, outside any kit folder."""
    if os.environ.get("KIT_CONFIG_DIR"):  # lets the tests use a throwaway folder
        return Path(os.environ["KIT_CONFIG_DIR"])
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home()) / "Kit"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Kit"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "kit"


# What the "show the file" button is called on each system.
REVEAL_LABEL = {"darwin": "Show in Finder", "win32": "Show in Explorer"}.get(sys.platform, "Show in folder")


def reveal(path: Path) -> None:
    """Open the system's file browser with this file selected, so it can be dragged anywhere.

    The path goes to the program as one argument, never through a shell, so a
    file name with spaces or quotes cannot turn into a different command.
    """
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])  # -R: reveal in Finder instead of opening
    elif sys.platform == "win32":
        subprocess.Popen(["explorer", f"/select,{path}"])
    else:
        subprocess.Popen(["xdg-open", str(path.parent)])  # Linux file managers cannot select a file


def downloads_folder() -> Path:
    """Where exported zips go: the Downloads folder, where people look for them."""
    downloads = Path.home() / "Downloads"
    return downloads if downloads.is_dir() else Path.home()


class App:
    """The state behind the page: one kit folder, and the actions on it."""

    def __init__(self, folder=None, dev: bool = False):
        self.folder: Path | None = None  # None means the home screen: no kit is open
        self.dev = dev  # development mode: the page reloads itself when ui.html is saved
        self.token = secrets.token_urlsafe(16)
        self.lock = threading.RLock()  # one action at a time
        self._peaks: dict[str, list[float] | None] = {}
        self._tempo: dict[str, float | None] = {}
        self.progress = {"files": 0, "files_total": 0, "bytes": 0, "bytes_total": 0}
        if folder is not None:
            if not Path(folder).is_dir():
                raise KitError(f"there is no folder at {folder}")
            self.open(folder)

    def open(self, folder) -> None:
        """Switch to a kit folder and remember it for the home screen."""
        try:
            self.folder = Repo.find(folder).folder  # a subfolder opens the kit it belongs to
        except KitError:
            self.folder = Path(folder).resolve()  # not a repo yet; the page will say so
        recent = [str(self.folder)] + [path for path in self._recent_paths() if path != str(self.folder)]
        try:
            config_dir().mkdir(parents=True, exist_ok=True)
            (config_dir() / "recent.json").write_text(json.dumps(recent[:MAX_RECENT]))
        except OSError:
            pass  # the list of recent kits is a convenience, never a reason to fail

    def name(self) -> str:
        """The producer name for new kits: the one set in the app, else the computer's user name."""
        try:
            saved = json.loads((config_dir() / "settings.json").read_text()).get("name")
        except (OSError, ValueError, AttributeError):
            saved = None
        return saved if isinstance(saved, str) and saved.strip() else _default_author()

    def set_name(self, name: str) -> str:
        name = clean_name(name)
        try:
            config_dir().mkdir(parents=True, exist_ok=True)
            (config_dir() / "settings.json").write_text(json.dumps({"name": name}))
        except OSError:
            pass  # the open kit still gets the name below; only the default for new kits is lost
        return name

    def _recent_paths(self) -> list[str]:
        try:
            paths = json.loads((config_dir() / "recent.json").read_text())
        except (OSError, ValueError):
            return []
        return [path for path in paths if isinstance(path, str)] if isinstance(paths, list) else []

    def recent(self) -> list[dict]:
        """Kits opened before, newest first, leaving out folders that no longer exist."""
        return [{"name": Path(path).name, "folder": path, "tracked": (Path(path) / DIR).is_dir()}
                for path in self._recent_paths() if Path(path).is_dir()]

    def repo(self) -> Repo:
        if self.folder is None:
            raise KitError("open a kit folder first")
        if not (self.folder / DIR).is_dir():
            raise KitError("this folder is not a Kit repo yet")
        repo = Repo(self.folder)
        repo.on_progress = self._report
        return repo

    def _report(self, files: int, files_total: int, done: int, total: int) -> None:
        # Read by /api/progress while the scan runs. A dict replaced whole, so a
        # reader on another thread always sees one consistent set of numbers.
        self.progress = {"files": files, "files_total": files_total, "bytes": done, "bytes_total": total}

    # ---- reading ---------------------------------------------------------

    def state(self) -> dict:
        """Everything the "Now" screen shows, or the home screen when no kit is open."""
        if self.folder is None:
            return {"home": True, "recent": self.recent(), "name": self.name()}
        if not (self.folder / DIR).is_dir():
            return {"repo": False, "name": self.folder.name, "folder": str(self.folder),
                    "author": self.name()}
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
                                   locate=lambda path, entry: repo.folder / path, key="path", before=saved),
            "removed": [c.path for c in changes if c.kind == "removed"],
            "reveal": REVEAL_LABEL,
            "warnings": names.warnings(working),
            "conflicts": repo.conflicts(),
            "sync": self._sync(repo),
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
                                   key="hash", before=repo.manifest_of(commit.parent)),
            "removed": [c.path for c in changes if c.kind == "removed"],
        }

    def _sounds(self, manifest, changes, by_hash, labels, locate, key, before=None) -> list[dict]:
        """The sounds of `manifest`, with what the page needs to show and play each one.

        `before` is the version the changes are measured against. A modified
        sound gets a "before" source too, pointing at its previous audio in the
        store, so the page can play the two one after the other.
        """
        before = before or {}
        changed = {c.path: c for c in changes if c.kind != "removed"}
        sounds = []
        for path, entry in sorted(manifest.items()):
            change = changed.get(path)
            credit = by_hash.get(entry["hash"])
            sounds.append({
                "path": path,
                **tags.from_name(path),  # "bpm" and "key", when the file name says
                "size": entry["size"],
                # How the page asks for this sound's audio and waveform: by
                # path for the working folder, by hash for a saved version.
                "source": f"path={_quote(path)}" if key == "path"
                          else f"hash={entry['hash']}&name={_quote(path)}",
                "info": describe(locate(path, entry), path),
                "change": change.kind if change else None,
                "before": f"hash={before[path]['hash']}&name={_quote(path)}"
                          if change and change.kind == "modified" and path in before else None,
                "was": change.old_path if change else None,
                "by": credit.author if credit else None,
                "since": labels.get(credit.id, credit.id[:8]) if credit else None,
            })
        return sounds

    def locate(self, query: dict) -> tuple[Path, str]:
        """The file behind an audio or waveform request, and its real name."""
        repo = self.repo()
        if "hash" in query:
            return repo.store.path_for(query["hash"]), query.get("name", "")
        rel = query.get("path", "")
        if any(part.startswith(".") for part in rel.split("/")):
            raise KitError("no such sound")  # never serve .kit or other hidden files
        path = repo._safe_path(rel)
        if not path.is_file():
            raise KitError("no such sound")
        return path, rel

    def peaks(self, query: dict) -> list[float] | None:
        path, name = self.locate(query)
        stat = path.stat()
        key = f"{path}:{stat.st_size}:{stat.st_mtime_ns}"
        if key not in self._peaks:
            self._peaks[key] = peaks(path, name)
        return self._peaks[key]

    def tempo(self, query: dict) -> float | None:
        """A loop's tempo estimated from its audio (about 0.2 s of work, so it is remembered)."""
        path, name = self.locate(query)
        stat = path.stat()
        key = f"{path}:{stat.st_size}:{stat.st_mtime_ns}"
        if key not in self._tempo:
            self._tempo[key] = tempo(path, name)
        return self._tempo[key]

    # ---- actions ---------------------------------------------------------

    def _sync(self, repo: Repo) -> dict | None:
        """What there is to push and pull, for a shared folder.

        Only for folder remotes: they are files on this computer (or a synced
        Drive folder), so checking is quick. An S3 bucket would mean network
        requests every time the screen is redrawn, so it is not checked.
        """
        spec = repo.remote()
        if not spec or spec.startswith("s3://"):
            return None
        try:
            return remote.compare(repo, remote.open_remote(spec))
        except (OSError, ValueError, KitError):
            return None  # the shared folder is offline or unreadable right now

    def _clone(self, body: dict) -> dict:
        """Get a kit someone shared: copy it from the shared folder into a new folder, and open it."""
        spec = (body.get("remote") or "").strip()
        into = (body.get("into") or "").strip()
        if not spec:
            raise KitError("choose the shared folder first")
        if not into:
            raise KitError("choose where to put the kit")
        spec = remote.find_shared(remote.normalize(spec))
        name = spec.rstrip("/").rsplit("/", 1)[-1].rsplit("\\", 1)[-1] or "shared kit"
        repo, result = remote.clone(spec, Path(into).expanduser() / name, author=self.name())
        self.open(repo.folder)
        return {"message": f"Got {name}: {result.commits} version(s)."}

    def act(self, action: str, body: dict) -> dict:
        """Run one action from the page and say what happened."""
        if action == "open":
            text = (body.get("folder") or "").strip()
            if not text:
                raise KitError("choose a folder first")
            folder = Path(text).expanduser()
            if not folder.is_dir():
                raise KitError(f"there is no folder at {folder}")
            self.open(folder)
            return {"message": f"Opened {self.folder.name}"}
        if action == "close":
            self.folder = None
            return {"message": ""}
        if action == "clone":
            return self._clone(body)
        if action == "name":
            name = self.set_name(body.get("name", ""))
            if self.folder is not None and (self.folder / DIR).is_dir():
                self.repo().set_author(name)
            return {"message": f"You'll be credited as {name}."}
        if self.folder is None:
            raise KitError("open a kit folder first")
        if action == "reveal":
            path, _ = self.locate({"path": body.get("path", "")})  # refuses .kit and paths outside the folder
            try:
                reveal(path)
            except OSError as err:  # e.g. no file manager installed
                raise KitError(f"could not open the file browser ({err.strerror or err})") from err
            return {"message": ""}
        if action == "init":
            name = self.set_name(body.get("author") or self.name())
            Repo.init(self.folder, author=name)
            return {"message": f"Started tracking this folder. Sounds you add are credited to {name}."}
        repo = self.repo()
        if action == "commit":
            saved = repo.commit(body.get("message", ""))
            return {"message": f"Saved v{len(repo.log())}: {saved.commit.message}"}
        if action == "restore":
            changes = repo.restore(body.get("version", ""), force=bool(body.get("force")))
            return {"message": f"Put {len(changes)} file(s) back. Save a version to keep it this way."
                    if changes else "The folder already matches that version."}
        if action == "remote":
            spec = (body.get("remote") or "").strip()
            if not spec:
                raise KitError("type a folder path or s3://bucket/name first")
            chosen = remote.normalize(spec)
            repo.set_remote(remote.place_to_share(spec, repo))
            if repo.remote() != chosen:
                return {"message": f"Sharing in {repo.remote()}, a folder of its own inside the one you chose. "
                                   "Push to send your versions there."}
            return {"message": f"Sharing in {repo.remote()}. Push to send your versions there."}
        if action in ("push", "pull"):
            if not repo.remote():
                raise KitError("set a remote first: a shared folder or s3://bucket/name")
            target = remote.open_remote(repo.remote())
            if action == "push":
                result = remote.push(repo, target)
                return {"message": f"Pushed {result.commits} version(s)." if result.commits
                        else "The remote already has every version."}
            result = remote.pull(repo, target)
            if result.outcome == "up-to-date":
                return {"message": "Already up to date."}
            message = f"Pulled {result.commits} version(s)."
            if result.outcome == "merge":
                message += " Both sides had new versions, so they were merged."
            if result.conflicts:
                message += " Some sounds were changed on both sides: listen to both and choose, at the top."
            return {"message": message}
        if action == "export":
            from .export import export

            out, sounds, _ = export(repo, body.get("version") or "latest", downloads_folder())
            try:
                reveal(out)  # show it in Finder, ready to send
            except OSError:
                pass  # the zip is there either way; the message says where
            return {"message": f"Saved {out.name} ({sounds} sounds) in {out.parent.name}."}
        if action == "resolve":
            done = repo.settle(body.get("path", ""), body.get("keep", ""))
            return {"message": done + " Save a version to share the choice."}
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

    def _run(self, work, lock: bool = True) -> None:
        """Run an endpoint, turning problems into a JSON error the page can show.

        Actions take the lock so only one runs at a time. Slow read-only work
        (tempo) can skip it, so it never holds up saving or the other requests.
        """
        try:
            if lock:
                with self.app.lock:
                    self._json(200, work())
            else:
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
            page = (PAGE.read_text(encoding="utf-8")  # read every time, so a saved edit shows on reload
                    .replace("__KIT_TOKEN__", self.app.token)
                    .replace("__KIT_DEV__", "true" if self.app.dev else "false"))
            return self._send(200, page.encode(), "text/html; charset=utf-8")
        if url.path == "/favicon.ico":
            return self._send(204, b"", "image/x-icon")  # browsers always ask; there is no icon
        if not self._allowed(query):
            return self._json(403, {"error": "forbidden"})
        if url.path == "/api/state":
            return self._run(self.app.state)
        if url.path == "/api/version":
            return self._run(lambda: self.app.version(query.get("ref", "latest")))
        if url.path == "/api/progress":
            # No lock: this answers while a long scan holds it.
            return self._json(200, self.app.progress)
        if url.path == "/api/tempo":
            return self._run(lambda: {"bpm": self.app.tempo(query)}, lock=False)
        if url.path == "/api/peaks":
            return self._run(lambda: {"peaks": self.app.peaks(query)})
        if url.path == "/api/audio":
            return self._audio(query)
        if url.path == "/api/dev" and self.app.dev:
            # When ui.html was last saved. The page asks every second and reloads when it changes.
            return self._json(200, {"page": PAGE.stat().st_mtime_ns})
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

    def _audio(self, query: dict) -> None:
        """Send a sound. Safari will not play audio unless byte ranges are supported."""
        try:
            path, name = self.app.locate(query)
            size = path.stat().st_size
        except (KitError, ValueError, OSError):
            return self._json(404, {"error": "no such sound"})
        start, end = 0, size - 1
        header = self.headers.get("Range") or ""
        partial = header.startswith("bytes=")
        if partial:
            first, _, last = header[len("bytes="):].split(",")[0].partition("-")
            try:
                if first:
                    start, end = int(first), int(last) if last else size - 1
                else:  # "bytes=-500" means the last 500 bytes
                    start = max(0, size - int(last))
            except ValueError:
                partial = False
            end = min(end, size - 1)
            if start > end:
                return self._send(416, b"", "text/plain", {"Content-Range": f"bytes */{size}"})
        with open(path, "rb") as f:
            f.seek(start)
            data = f.read(end - start + 1)
        kind = mimetypes.guess_type(name)[0] or "application/octet-stream"
        extra = {"Accept-Ranges": "bytes"}
        if partial:
            extra["Content-Range"] = f"bytes {start}-{end}/{size}"
        self._send(206 if partial else 200, data, kind, extra)


def make_server(folder=None, port: int = 0, dev: bool = False) -> tuple[ThreadingHTTPServer, App]:
    """Build the server without starting it. Port 0 picks any free port.

    With no folder the app starts on its home screen, which is what the
    double-click app does: there is no "current folder" to assume.
    """
    app = App(folder, dev=dev)
    handler = type("KitHandler", (Handler,), {"app": app})
    return ThreadingHTTPServer(("127.0.0.1", port), handler), app


def serve(folder=".", port: int = 0, open_browser: bool = True, dev: bool = False) -> None:
    server, app = make_server(folder, port, dev=dev)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"Kit is running for {app.folder or 'your kits'}")
    print(f"Open {url}  (press Ctrl+C here to stop)")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
