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
from array import array
import os
import secrets
import subprocess
import sys
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from . import diagnostics, kinds, loudness, mp3, names, problems, remote, tags, updates
from .audio import describe
from .audio import key, key_of, note, note_of, peaks, tempo, tempo_of
from .manifest import diff
from .repo import DIR, KitError, Repo, _default_author, clean_name

PAGE = Path(__file__).with_name("ui.html")
MAX_RECENT = 12
HEARD_LIMIT = 30 * 48000 * 4  # 30 seconds of 32-bit samples, at up to 48 kHz


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
        self.window = "browser"  # or "own window": `kit app` sets it, for problem reports
        self.token = secrets.token_urlsafe(16)
        self.lock = threading.RLock()  # one action at a time
        self._peaks: dict[str, list[float] | None] = {}
        self._tempo: dict[str, float | None] = {}
        self._note: dict[str, str | None] = {}
        self._heard: dict[str, dict] = {}
        self._key: dict[str, str | None] = {}
        self.progress = {"files": 0, "files_total": 0, "bytes": 0, "bytes_total": 0}
        self.update: str | None = None  # a newer version on GitHub, once the check has found one
        self.update_checked = False
        self.auto_compact = False  # compress saved WAV files in the background; on in the real app
        self.compacting = False
        self.auto_sort = False  # sort sounds by kind in the background; on in the real app
        self.sorting = False
        self.compacted: dict | None = None  # what the last background run saved, for the page
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
        self._save_recent([str(self.folder)] + [path for path in self._recent_paths() if path != str(self.folder)])

    def forget(self, folder: str) -> str:
        """Take a kit off the home screen's list. The folder itself is not touched."""
        paths = self._recent_paths()
        kept = [path for path in paths if path != folder]
        if len(kept) == len(paths):
            raise KitError(f"{folder} is not in the list")
        self._save_recent(kept)
        return Path(folder).name

    def _save_recent(self, paths: list[str]) -> None:
        try:
            config_dir().mkdir(parents=True, exist_ok=True)
            (config_dir() / "recent.json").write_text(json.dumps(paths[:MAX_RECENT]))
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

    def check_for_update(self) -> None:
        """Ask GitHub whether a newer Kit is out. Runs on its own thread at start-up."""
        self.update = updates.newer_version(config_dir() / "update.json")
        self.update_checked = True

    def start_update_check(self) -> None:
        threading.Thread(target=self.check_for_update, daemon=True).start()

    def state(self) -> dict:
        """Everything the screen shows, plus a newer version of Kit if there is one."""
        return {**self._state(), "update": self.update}

    def _state(self) -> dict:
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
        repo.follow_renames(changes)
        stars = repo.stars()
        notes = repo.notes()
        by_hash = {saved[path]["hash"]: commit for path, commit in repo.credits().items()}
        sounds = self._sounds(working, changes, by_hash, labels,
                              locate=lambda path, entry: repo.folder / path, key="path", before=saved)
        known = kinds.known(repo, working)
        found = problems.known(repo, working, {path for path, kind in known.items() if kind == "Loop"})
        for sound in sounds:
            sound["starred"] = sound["path"] in stars
            sound["note"] = notes.get(sound["path"])
            sound["kind"] = known.get(sound["path"])
            # Its kind was heard, not read from its name: "Name by sound" can name it.
            sound["unnamed"] = bool(sound["kind"]) and kinds.kind_from_name(sound["path"]) is None
            sound["problems"] = [problem["text"] for problem in found.get(sound["path"], [])]
            sound["fixable"] = any(problem["kind"] in ("late", "click") for problem in found.get(sound["path"], []))
        self._levels(repo, working, known, sounds)
        return {
            "repo": True,
            "name": self.folder.name,
            "folder": str(self.folder),
            "author": repo.author,
            "remote": repo.remote(),
            "versions": [
                {"label": labels[c.id], "id": c.id, "author": c.author, "message": c.message,
                 "time": c.time, "merge": bool(c.merged), "names": repo.names_of(c.id)}
                for c in history
            ],
            "changes": [vars(change) for change in changes],
            "sounds": sounds,
            "removed": [c.path for c in changes if c.kind == "removed"],
            "reveal": REVEAL_LABEL,
            "warnings": names.warnings(working),
            "conflicts": repo.conflicts(),
            "compacting": self.compacting,
            "sorting": self.sorting,
            "compacted": self.compacted if self.compacted and self.compacted["folder"] == str(self.folder) else None,
            "sync": self._sync(repo),
        }

    def version(self, ref: str, against: str | None = None) -> dict:
        """Everything the screen for one saved version shows.

        Its changes are measured against `against` (any version), or by
        default against the version before it. The first version has nothing
        before it, so all of its sounds are new.
        """
        repo = self.repo()
        history = repo.log()
        labels = {c.id: f"v{len(history) - i}" for i, c in enumerate(history)}
        commit = repo.load_commit(repo.resolve(ref))
        manifest = repo.manifest_of(commit.id)
        base = repo.resolve(against) if against else commit.parent
        before = repo.manifest_of(base)
        changes = diff(before, manifest)
        by_hash = {manifest[path]["hash"]: c for path, c in repo.credits(commit.id).items()}
        sounds = self._sounds(manifest, changes, by_hash, labels,
                              locate=lambda path, entry: repo.store.file_for(entry["hash"]), key="hash", before=before)
        notes = repo.notes()
        known = kinds.known(repo, manifest)
        for sound in sounds:
            sound["note"] = notes.get(sound["path"])  # notes belong to the kit, not to one version
            sound["kind"] = known.get(sound["path"])
        return {
            "label": labels.get(commit.id, commit.id[:8]),
            "id": commit.id, "author": commit.author, "message": commit.message,
            "time": commit.time, "latest": commit.id == repo.head(), "names": repo.names_of(commit.id),
            "sounds": sounds,
            "removed": [c.path for c in changes if c.kind == "removed"],
            "changes": [vars(change) for change in changes],
            "against": {"id": base, "label": labels.get(base, base[:8])} if base else None,
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
        """The file behind an audio or waveform request, and its real name.

        `kit` asks for a sound in another kit (from "More like this"). Only
        kits opened before can be asked for, never any folder on the computer.
        """
        rel = query.get("path", "")
        if any(part.startswith(".") for part in rel.split("/")):
            raise KitError("no such sound")  # never serve .kit or other hidden files
        if "kit" in query:
            folder = Path(query["kit"]).resolve()
            if folder != self.folder and folder not in {Path(p).resolve() for p in self._recent_paths()}:
                raise KitError("no such sound")
            path = (folder / rel).resolve()
            if not path.is_relative_to(folder) or not path.is_file():
                raise KitError("no such sound")
            return path, rel
        repo = self.repo()
        if "hash" in query:
            return repo.store.file_for(query["hash"]), query.get("name", "")
        path = repo._safe_path(rel)
        if not path.is_file():
            raise KitError("no such sound")
        return path, rel

    def _levels(self, repo: Repo, working: dict, known: dict, sounds: list[dict]) -> None:
        """Each sound's loudness and peak, and whether it is far louder or quieter than the
        other sounds of its kind (a hat is meant to be quieter than an 808)."""
        measured = loudness.known(repo, working)
        middles = loudness.middles(measured, known)
        for sound in sounds:
            level = measured.get(sound["path"])
            sound["lufs"] = level["lufs"] if level else None
            sound["peak"] = level["peak"] if level else None
            sound["level"] = loudness.apart(level["lufs"], middles.get(known.get(sound["path"]))) if level else None
            sound["middle"] = middles.get(known.get(sound["path"]))

    def duplicates(self) -> dict:
        """Sounds that are in more than one place across every kit opened before (and the open one)."""
        from .fingerprint import find_duplicates

        folders = [Path(p) for p in self._recent_paths() if Path(p).is_dir()]
        if self.folder and all(Path(p).resolve() != self.folder.resolve() for p in folders):
            folders.insert(0, self.folder)
        if not folders:
            raise KitError("open a kit first: duplicates are looked for across the kits you have opened")

        def progress(done: int, total: int) -> None:
            self.progress = {"files": done, "files_total": total, "bytes": 0, "bytes_total": 0}

        groups = find_duplicates(folders, progress=progress)
        return {
            "kits": len(folders),
            "extra": sum(group.extra_bytes for group in groups),
            "groups": [{
                "exact": group.exact,
                "extra": group.extra_bytes,
                "places": [{"kit": place.kit, "folder": place.folder, "path": place.path, "size": place.size,
                            "source": f"kit={_quote(place.folder)}&path={_quote(place.path)}"}
                           for place in group.places],
            } for group in groups],
        }

    def rename_plan(self) -> list[dict]:
        """New names for the sounds whose names say nothing, from what Kit heard. Nothing is renamed here."""
        from .rename import suggest

        repo = self.repo()
        with self.lock:
            working = repo.working()
        sorted_ = kinds.sort_kit(repo, working=working)  # quick for sounds listened to before
        heard = {path: kind["kind"] for path, kind in sorted_.items() if kind["from"] == "sound"}
        return suggest(working, heard, lambda path: self.listen({"path": path}))

    def _fix(self, repo: Repo, paths: list[str]) -> list[tuple[Path, list[str]]]:
        """Check each sound again (the file may have changed) and fix what can be fixed."""
        from .fix import fix

        known = kinds.known(repo, repo.working())
        done = []
        for path in paths:
            source, name = self.locate({"path": path})
            loop = known.get(path) == "Loop"
            found = problems.check(source, name, loop) or []
            done.append(fix(repo, path, found, loop))
        return done

    def similar(self, query: dict) -> list[dict]:
        """The 10 sounds most like one in the open kit, from it and every kit opened before."""
        from .fingerprint import most_like

        self.locate(query)  # the sound must exist
        folders = [self.folder] + [Path(p) for p in self._recent_paths()
                                   if Path(p).is_dir() and Path(p).resolve() != self.folder.resolve()]

        def progress(done: int, total: int) -> None:
            self.progress = {"files": done, "files_total": total, "bytes": 0, "bytes_total": 0}

        try:
            found = most_like(folders, self.folder, query["path"], progress=progress)
        except ValueError as err:
            raise KitError(str(err)) from None
        here = self.folder.resolve()
        return [{
            "path": place.path,
            "kit": place.kit,
            "label": "in this kit" if Path(place.folder) == here else f"in {place.kit}",
            "source": f"path={_quote(place.path)}" if Path(place.folder) == here
                      else f"kit={_quote(place.folder)}&path={_quote(place.path)}",
            "distance": round(score, 2),
        } for place, score in found]

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

    def note(self, query: dict) -> str | None:
        """The note a one-shot plays, from its audio (remembered like the tempo)."""
        path, name = self.locate(query)
        stat = path.stat()
        key = f"{path}:{stat.st_size}:{stat.st_mtime_ns}"
        if key not in self._note:
            self._note[key] = note(path, name)
        return self._note[key]

    def key(self, query: dict) -> str | None:
        """The key of a melodic loop, from its audio (about half a second of work, so it is remembered)."""
        path, name = self.locate(query)
        stat = path.stat()
        cache = f"{path}:{stat.st_size}:{stat.st_mtime_ns}"
        if cache not in self._key:
            self._key[cache] = key(path, name)
        return self._key[cache]

    def listen(self, query: dict) -> dict:
        """What the audio says about a sound that its name does not: a tempo, a key, or a note.

        A key needs at least three notes sounding (see audio.key_of), so loops
        and chords get one and one-shots do not. A one-shot gets the note it
        plays instead. Anything with a beat or a key never gets a note: a
        melody has a pitch at every moment, but its first note says nothing.
        """
        _, name = self.locate(query)
        named = tags.from_name(name)
        bpm = None if named["bpm"] else self.tempo(query)
        found_key = None if named["key"] else self.key(query)
        busy = bpm or named["bpm"] or found_key or named["key"]
        return {"bpm": bpm, "key": found_key, "note": None if busy else self.note(query)}

    def heard(self, query: dict, samples: array) -> dict:
        """`listen` for a sound the page decoded (MP3, AAC): it sends the samples.

        They come mono, at `rate` samples a second (the page sends 22050).
        `seconds` is the whole sound's length as the page measured it; for an
        MP3 the length from its headers is used instead, as it is exact, and
        the tempo search depends on it.
        """
        path, name = self.locate(query)  # the sound must exist; its file is not read again
        stat = path.stat()
        cache = f"{path}:{stat.st_size}:{stat.st_mtime_ns}"
        if cache not in self._heard:
            rate = int(query.get("rate", 0))
            if not 8000 <= rate <= 96000:
                raise KitError("rate must be between 8000 and 96000")
            info = mp3.read(path) if name.lower().endswith(".mp3") else None
            seconds = info.seconds if info else float(query.get("seconds", 0))
            named = tags.from_name(name)  # the same rules as `listen`
            bpm = None if named["bpm"] else tempo_of(samples, rate, seconds)
            found_key = None if named["key"] else key_of(samples, rate)
            busy = bpm or named["bpm"] or found_key or named["key"]
            self._heard[cache] = {"bpm": bpm, "key": found_key, "note": None if busy else note_of(list(samples), rate)}
        return self._heard[cache]

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

    # Actions after which there may be new WAV files in .kit to compress.
    COMPACT_AFTER = {"open", "commit", "pull", "clone"}

    def act(self, action: str, body: dict) -> dict:
        """Run one action from the page and say what happened."""
        result = self._act(action, body)
        if action in self.COMPACT_AFTER:
            self.compact_soon()
            self.sort_soon()
        return result

    def sort_soon(self) -> None:
        """Listen to the open kit's sounds whose names say nothing, and check every
        sound for problems (clipping, a late start, a click), on a background thread.

        The folder is read under the lock, so it never runs at the same time as
        a save; the listening (a fraction of a second per sound) is not.
        """
        if not self.auto_sort or self.sorting or self.folder is None or not (self.folder / DIR).is_dir():
            return
        repo = self.repo()
        with self.lock:
            working = repo.working()
        loops = {path for path, kind in kinds.known(repo, working).items() if kind == "Loop"}
        if (not kinds.unsorted(repo, working) and not problems.unchecked(repo, working, loops)
                and not loudness.unmeasured(repo, working)):
            return
        self.sorting = True

        def work() -> None:
            try:
                sorted_ = kinds.sort_kit(repo, working=working)
                # Loops are checked differently from one-shots, so sort first.
                problems.check_kit(repo, {path for path, kind in sorted_.items() if kind["kind"] == "Loop"},
                                   working=working)
                loudness.measure_kit(repo, working=working)
            except Exception:
                diagnostics.record(config_dir(), f"Sorting sounds in {repo.folder}\n{traceback.format_exc()}")
            finally:
                self.sorting = False

        threading.Thread(target=work, daemon=True).start()

    def compact_soon(self) -> None:
        """Start `kit compact` for the open kit on a background thread, unless one is running.

        It encodes with no lock held, so saving, playing and syncing carry on
        meanwhile; only the moment a packed copy replaces an original is locked.
        """
        if not self.auto_compact or self.compacting or self.folder is None or not (self.folder / DIR).is_dir():
            return
        repo = self.repo()
        self.compacting = True

        def work() -> None:
            try:
                files, before, after = repo.store.compact(lock=self.lock)
                if files:
                    self.compacted = {"folder": str(repo.folder), "files": files, "saved": before - after}
            except Exception:  # never let a background job take the app down; keep the details
                diagnostics.record(config_dir(), f"Compressing {repo.folder}\n{traceback.format_exc()}")
            finally:
                self.compacting = False

        threading.Thread(target=work, daemon=True).start()

    def _act(self, action: str, body: dict) -> dict:
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
        if action == "note":
            repo = self.repo()
            repo.note(body.get("path") or "", str(body.get("text") or ""))
            return {"note": repo.notes().get(body.get("path") or "")}
        if action == "star":
            self.repo().star(body.get("path") or "", bool(body.get("on", True)))
            return {"message": ""}
        if action == "report":
            return {"text": diagnostics.report(config_dir(), self.window)}
        if action == "log":
            # An error inside the page itself (its script), sent here so it reaches the report.
            diagnostics.record(config_dir(), "In the page: " + str(body.get("message", ""))[:2000])
            return {"message": ""}
        if action == "download":
            webbrowser.open(updates.DOWNLOAD_PAGE)  # the user's own browser, even from the app window
            return {"message": ""}
        if action == "forget":
            name = self.forget(body.get("folder") or "")
            return {"message": f"Removed {name} from the list. The folder and its sounds are untouched."}
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
            # Refuses .kit and paths outside the folder; `kit` (from Duplicates) only for a kit opened before.
            query = {"path": body.get("path", ""), **({"kit": body["kit"]} if body.get("kit") else {})}
            path, _ = self.locate(query)
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
                message = (f"Pushed {result.commits} version(s)." if result.commits
                           else "The shared folder already has every version.")
                return {"message": f"{message} {remote.names_note(result, sent=True)}".strip()}
            result = remote.pull(repo, target)
            if result.outcome == "up-to-date":
                return {"message": f"Already up to date. {remote.names_note(result, sent=False)}".strip()}
            message = f"Pulled {result.commits} version(s). {remote.names_note(result, sent=False)}".strip()
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
        if action == "tune":
            from .tune import tune

            path = body.get("path") or ""
            self.locate({"path": path})  # the sound must exist
            out, played, shift = tune(repo, path, str(body.get("note") or ""))
            if out is None:
                return {"message": f"{Path(path).name} is already in {body.get('note')}."}
            way = "up" if shift > 0 else "down"
            return {"message": f"Tuned {Path(path).name} from {played} {way} {abs(shift):.1f} semitones: "
                               f"{out.name}. Save a version to keep it."}
        if action == "build-pack":
            from .packs import build

            paths = body.get("paths") or sorted(repo.stars() & repo.working().keys())
            out, count = build(repo, paths, downloads_folder(), body.get("name"),
                               listen=lambda path: self.listen({"path": path}))
            try:
                reveal(out)
            except OSError:
                pass
            return {"message": f"Saved {out.name} ({count} sound{'s' if count != 1 else ''}, sorted by kind) in {out.parent.name}."}
        if action == "changelog":
            from .changelog import write_changelog

            out = write_changelog(repo, body.get("version") or "latest", downloads_folder(), body.get("since"))
            try:
                reveal(out)
            except OSError:
                pass
            return {"message": f"Saved {out.name} in {out.parent.name}. Exported zips include it as Changes.txt."}
        if action == "chop":
            from .chop import chop

            path, name = self.locate({"path": body.get("path") or ""})
            files = chop(path, name)
            return {"message": f"Cut {name} into {len(files)} pieces, in "
                               f"{files[0].parent.relative_to(repo.folder).as_posix()}/. Save a version to keep them."}
        if action == "fix":
            paths = body.get("paths") or [body.get("path") or ""]
            fixed = self._fix(repo, paths)
            self.sort_soon()  # check the fixed files again
            if len(paths) == 1:
                out, done = fixed[0]
                where = "" if out.name == Path(paths[0]).name else f", as {out.name}"
                return {"message": f"Fixed {Path(paths[0]).name}{where}: {' and '.join(done)}. "
                                   + ("Before plays the original." if not where else "Save a version to keep it.")}
            return {"message": f"Fixed {len(fixed)} sounds. Save a version to keep the fixes; Before plays the originals."}
        if action == "rename-heard":
            from .rename import apply

            plan = body.get("plan") or []
            count = apply(repo.folder, [{"from": str(m["from"]), "to": str(m["to"])} for m in plan])
            return {"message": f"Renamed {count} sound{'s' if count != 1 else ''}. Save a version to keep the new names;"
                               " credits follow the sounds."}
        if action == "level":
            from .level import describe, level

            paths = body.get("paths") or [body.get("path") or ""]
            working = repo.working()
            known = kinds.known(repo, working)
            middles = loudness.middles(loudness.known(repo, working), known)
            lines = []
            for path in paths:
                self.locate({"path": path})  # the sound must exist
                target = body.get("to")
                if target is None:
                    target = middles.get(known.get(path))
                    if target is None:
                        raise KitError(f"there are not 3 measured {known.get(path) or 'sounds'} of this kind "
                                       f"to level {Path(path).name} to")
                out, gain, result = level(repo, path, float(target))
                lines.append(describe(path, out, gain, result, float(target)))
            self.sort_soon()  # measure the levelled files
            if len(lines) == 1:
                return {"message": lines[0] + ". Before plays the original." if "Turned" in lines[0] else lines[0] + "."}
            return {"message": f"Levelled {len(paths)} sounds to the middle of their kinds. Before plays the originals."}
        if action == "split-sheet":
            from .splits import write_split_sheet

            out = write_split_sheet(repo, body.get("version") or "latest", downloads_folder())
            try:
                reveal(out)
            except OSError:
                pass
            return {"message": f"Saved {out.name} in {out.parent.name}."}
        if action == "name-version":
            repo.name_version(body.get("name", ""), body.get("version", ""))
            return {"message": f"Named it {body.get('name', '').strip()}."}
        if action == "unname":
            repo.remove_name(body.get("name", ""))
            return {"message": f"Removed the name {body.get('name', '').strip()}."}
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
        except Exception:  # a bug: keep the details, and tell the person where to find them
            diagnostics.record(config_dir(), f"{self.command} {self.path.split('?')[0]}\n{traceback.format_exc()}")
            self._json(500, {"error": "Something went wrong inside Kit. The details are saved: "
                                      "Report a problem, on the All kits screen, shows them to send."})

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
            return self._run(lambda: self.app.version(query.get("ref", "latest"), query.get("against")))
        if url.path == "/api/progress":
            # No lock: this answers while a long scan holds it.
            return self._json(200, self.app.progress)
        if url.path == "/api/update":
            # No lock: it only reads two values set by the start-up check.
            return self._json(200, {"checked": self.app.update_checked, "version": self.app.update})
        if url.path == "/api/tempo":
            return self._run(lambda: self.app.listen(query), lock=False)
        if url.path == "/api/dupes":
            # No lock: the first look at a big collection listens to every sound.
            return self._run(self.app.duplicates, lock=False)
        if url.path == "/api/rename-preview":
            # No lock: listening to every unnamed sound can take a while (the folder is read under it).
            return self._run(lambda: {"plan": self.app.rename_plan()}, lock=False)
        if url.path == "/api/similar":
            # No lock: making fingerprints for kits not yet listened to takes a while.
            return self._run(lambda: {"sounds": self.app.similar(query)}, lock=False)
        if url.path == "/api/peaks":
            # No lock either: a FLAC file is decoded in Python, which can take
            # seconds for a long one, and that must not hold up saving.
            return self._run(lambda: {"peaks": self.app.peaks(query)}, lock=False)
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
        if url.path == "/api/heard":
            return self._heard(url)
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._json(400, {"error": "bad request"})
        self._run(lambda: self.app.act(url.path[len("/api/"):], body))

    def _heard(self, url) -> None:
        """Decoded samples from the page: 32-bit floats, raw, not JSON (a few MB as JSON)."""
        query = {key: values[0] for key, values in parse_qs(url.query).items()}
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 < length <= HEARD_LIMIT:
            return self._json(400, {"error": "send between 1 byte and 30 seconds of samples"})
        raw = self.rfile.read(length)
        samples = array("f")
        samples.frombytes(raw[:len(raw) // 4 * 4])
        if sys.byteorder == "big":
            samples.byteswap()  # the page sends little-endian, like every computer Kit runs on
        # No lock, like /api/tempo: this is read-only and must not hold up saving.
        self._run(lambda: self.app.heard(query, samples), lock=False)

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
    app.auto_compact = app.auto_sort = True
    app.compact_soon()  # a kit saved before compression existed is compressed now
    app.sort_soon()
    if not dev:
        app.start_update_check()
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
