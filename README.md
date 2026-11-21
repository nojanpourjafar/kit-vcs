# Kit

Git-style version control for sample packs and loop kits.

Keep your kit folder exactly as it is. `kit` remembers every version of it, shows what changed between versions, rolls back, shares the kit with other producers, merges their changes into yours, and records who added each sound.

## Why

Producers share kits as zip files, so every update is a new zip and nobody can tell what changed. Kit gives a kit folder a real history: small, readable versions in place of `MyKit_v3_FINAL.zip`, updates that send only the new sounds, and credits that survive renames and collaboration.

## Install

Needs Python 3.10 or newer. No other dependencies.

```
git clone https://github.com/nojanpourjafar/kit-vcs.git
cd kit-vcs
pip install -e .
```

This gives you the `kit` command. To run it without installing, use `PYTHONPATH=src python -m kit ...` in place of `kit ...`.

## Quick start

```
python examples/make_demo_kit.py demo_kit    # 4 synthesized sounds to try it on
cd demo_kit

kit init --author "Nojan"
kit commit -m "first four sounds"

mv Kicks/kick_punch.wav Kicks/kick_01.wav    # rename a sample
kit status                                   # > Kicks/kick_01.wav  (was Kicks/kick_punch.wav)
kit commit -m "renamed the kick"             # Stored 0 new file(s), 0 bytes.

kit log                                      # v2, v1
kit diff v1 v2
kit restore v1                               # folder goes back; history stays
kit credits                                  # who first added each sound
kit show                                     # opens the version as a web page
```

## The app

If you would rather click than type, run this inside a kit folder:

```
kit ui
```

It opens Kit in your browser. The folder's sounds are listed with waveforms and play buttons, unsaved changes are marked, and there are buttons to save a version, look at or go back to an older one, and push and pull. Everything runs on your own computer: `kit ui` starts a small server that only your machine can reach, and stops when you press Ctrl+C in the terminal.

`kit ui` works on one folder. The **All kits** button at the top leads to a home screen that lists the kits opened before, so you can switch between them.

To have Kit in its own window, the way a desktop app looks, install one extra package and use `kit app`:

```
pip install -e ".[app]"
kit app
```

It starts on the home screen, with a button that opens the system's folder chooser. The window is the same page as `kit ui`, shown by [pywebview](https://pywebview.flowrl.com/) in the system's built-in web view, so no browser is bundled. `kit app --browser`, or running it without pywebview installed, uses the browser instead.

### A double-click app

`packaging/build_app.py` turns Kit into an app that needs no Terminal and no Python on the computer that runs it:

```
pip install -e ".[app]" pyinstaller
python packaging/build_app.py
```

The result is `dist/Kit.app` on macOS and `dist/Kit/Kit.exe` on Windows. PyInstaller builds for the system it runs on, so the Windows app has to be built on Windows. The app is not signed: the first time it is opened on another Mac, macOS will refuse until it is allowed under System Settings > Privacy & Security.

## Sharing a kit

A remote is anywhere a copy of the history can live. The simplest one is a folder: a USB drive, or a folder synced by Google Drive or Dropbox.

```
kit push ~/GoogleDrive/shared-kit            # send your versions; the remote is remembered
kit push                                     # later pushes send only what is new
```

Someone else gets their own copy, which is also how a pack is forked:

```
kit clone ~/GoogleDrive/shared-kit my-copy --author "Lucas"
cd my-copy
# add or change sounds, then:
kit commit -m "added an open hat"
kit push
```

And you bring their work into yours:

```
kit pull
```

If both of you saved new versions since you last synced, `kit pull` merges the two histories. Sounds changed on only one side are taken as they are. If you both changed the same sound, nothing is thrown away: your file stays, and theirs is saved beside it as `name (from Lucas).wav` so you can listen and keep the one you want. `kit credits` then shows who added what, with a split by number of sounds.

### Cloud storage

A remote can also be a bucket on Amazon S3 or an S3-compatible service such as Cloudflare R2:

```
pip install boto3
kit push s3://my-bucket/my-kit
```

Credentials are read from the usual AWS environment variables or `~/.aws/credentials`. For a non-Amazon service, set `KIT_S3_ENDPOINT` to its URL. The S3 remote is tested against an in-memory S3 imitation (moto); it has not yet been run against a real bucket.

## Commands

| Command | What it does |
|---|---|
| `kit init` | Start tracking the current folder |
| `kit status` | Show what changed since the last version |
| `kit commit -m "message"` | Save the folder as a new version |
| `kit log` | List saved versions, newest first |
| `kit diff v1 v2` | Show what changed between two versions |
| `kit restore v1` | Put the folder back to an earlier version |
| `kit credits` | Show who first added each sound, and the split |
| `kit ui` | Open Kit as an app in your browser |
| `kit app [folder]` | Open Kit in its own window, starting on the list of your kits |
| `kit show [v2]` | Write a version out as a single web page you can send to someone |
| `kit remote [where]` | Show or set the default remote |
| `kit push [where]` | Send new versions to a remote |
| `kit pull [where]` | Get new versions from a remote, merging if needed |
| `kit clone <where> [folder]` | Make a new kit folder from a remote |
| `kit verify` | Check that nothing stored is damaged or missing |

Versions can be named as `v1`, `v2`, ..., as `latest`, or by the start of their ID. `where` is a folder path or `s3://bucket/prefix`.

A `.kitignore` file in the kit folder lists things to leave out, one pattern per line: `*.asd` for a file type, `Drafts/` for a folder.

## How it works

**Every file is stored once, under the name of its own hash.**
Each file is run through SHA-256 and saved as `.kit/objects/<hash>`. The same kick in ten versions takes the space of one kick. Renaming or moving a sample stores nothing, because the audio, and so the hash, did not change.

**A version is a list of "path -> hash", called a manifest.**
It holds no audio, so it is tiny. Comparing two manifests gives the changes: a new path is an addition, a new hash at the same path is a modification, and a hash that moved to a new path is a rename.

**Versions form a chain.**
Each commit is a few lines of JSON: its manifest, author, message, time, and the ID of the version before it. `HEAD` holds the ID of the latest one. A commit's ID is the hash of its own JSON, so an edited old version no longer matches its ID and is caught. A merge is a commit with two parents.

```
MyKit/
  Kicks/kick_01.wav ...       your sounds, untouched
  .kit/
    objects/ab/cdef...        every file ever saved, named by hash
    commits/<id>.json         one small file per version
    HEAD                      ID of the latest version
    config.json               author name and default remote
    index.json                cache of file hashes
```

**Sync sends only what is missing.**
`push` walks the history back from `HEAD` until it reaches a version the remote already has, then uploads those versions oldest first. For each one it uploads only the objects the remote lacks, then the commit, and moves the remote's `HEAD` last. An interrupted push can leave extra objects on the remote but never a version with pieces missing. `pull` does the same in the other direction and re-hashes every download, so a corrupted file is rejected.

`push` refuses when the remote holds versions the local repo has never seen, because moving `HEAD` would cut them out of the history. Pull first, then push.

**Merging is three-way, per file.**
The merge compares both sides with the last version they share. A file changed on one side takes that change; a file changed the same way on both sides needs nothing; a file changed differently on each side is kept twice. Audio cannot be merged line by line the way code can, so the merge never picks a winner.

**Other details**

- Files are written to a temporary name and then renamed, so a crash never leaves half a file. `HEAD` always moves last, so a failed save leaves the previous version current.
- `kit restore` does not rewrite history. It puts the files back and leaves `HEAD` alone, so the rollback shows up as ordinary changes that can be committed as a new version.
- Credits follow the audio, not the file name: each sound is credited to the author of the first version that contained its hash, including versions that arrived through a merge.
- `kit status` does not re-hash files whose size and modified time are unchanged, which keeps it fast on large kits. Files modified in the last two seconds are always re-hashed.
- Paths in a manifest that point outside the kit folder are refused, since manifests can come from other people.
- The app's server listens on 127.0.0.1 only, requires a random token that only its own page knows, and rejects requests whose Host header is not this machine. Without those checks, any web page open in the same browser could send it commands.

## Project layout

| File | Job |
|---|---|
| `src/kit/hashing.py` | SHA-256 of files and bytes |
| `src/kit/objects.py` | The content-addressed object store |
| `src/kit/manifest.py` | Scan a folder, compare snapshots, three-way merge |
| `src/kit/commits.py` | The commit record, its ID, and history walking |
| `src/kit/repo.py` | Commit, log, status, restore, merge, credits, verify |
| `src/kit/remote.py` | Folder remote, push, pull, clone |
| `src/kit/s3remote.py` | S3 / R2 remote |
| `src/kit/audio.py` | WAV length, sample rate and waveform outline |
| `src/kit/report.py` | The `kit show` web page |
| `src/kit/app.py` | The server behind `kit ui` and `kit app`: JSON endpoints over the same Repo methods |
| `src/kit/ui.html` | The page: layout, styles and script in one file |
| `src/kit/desktop.py` | `kit app`: the page in a native window, with a folder chooser |
| `src/kit/cli.py` | The `kit` command |
| `packaging/build_app.py` | Builds the double-click app with PyInstaller |

## Roadmap

| What | State |
|---|---|
| Local versions: commit, history, compare, roll back, credits | done |
| Share through a folder: push, pull, clone | done |
| Merge two people's versions, credits across people | done |
| S3 / R2 remote | done, not yet run against a real bucket |
| Web page with waveforms and players | done |
| Browser app (`kit ui`) | done |
| Desktop window (`kit app`) and a double-click build | done, not yet tried on Windows |
| Hash cache, `.kitignore`, `kit verify` | done |
| Hosted service: accounts, private and paid packs, download tracking | planned |
| Waveforms for MP3, FLAC and float WAV files | planned |
| Storing large audio compressed (FLAC) | planned |

Known limits: a folder remote cannot lock, so two pushes in the same second can race; version numbers (`v1`, `v2`, ...) follow the local main line, and versions that arrived through a merge are shown by ID.

## Development

```
pip install -e ".[dev]"
python -m pytest
```

The S3 tests run only when `boto3` and `moto` are installed (`pip install boto3 moto`).

The package is named `kit-vcs` because the plain name `kit` is taken on PyPI. The command is `kit`.
