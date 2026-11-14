# Kit

Git-style version control for sample packs and loop kits.

Keep your kit folder exactly as it is. `kit` remembers every version of it, shows what changed between versions, rolls back, and records who added each sound.

## Why

Producers share kits as zip files, so every update is a new zip and nobody can tell what changed. Kit gives a kit folder a real history: small, readable versions in place of `MyKit_v3_FINAL.zip`.

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
```

## Commands

| Command | What it does |
|---|---|
| `kit init` | Start tracking the current folder |
| `kit status` | Show what changed since the last version |
| `kit commit -m "message"` | Save the folder as a new version |
| `kit log` | List every saved version, newest first |
| `kit diff v1 v2` | Show what changed between two versions |
| `kit restore v1` | Put the folder back to an earlier version |
| `kit credits` | Show who first added each sound |
| `kit push <folder>` / `kit pull <folder>` | Share through a folder (in progress) |

Versions can be named as `v1`, `v2`, ..., as `latest`, or by the start of their ID.

## How it works

**Every file is stored once, under the name of its own hash.**
Each file is run through SHA-256 and saved as `.kit/objects/<hash>`. The same kick in ten versions takes the space of one kick. Renaming or moving a sample stores nothing, because the audio, and so the hash, did not change.

**A version is a list of "path -> hash", called a manifest.**
It holds no audio, so it is tiny. Comparing two manifests gives the changes: a new path is an addition, a new hash at the same path is a modification, and a hash that moved to a new path is a rename.

**Versions form a chain.**
Each commit is a few lines of JSON: its manifest, author, message, time, and the ID of the version before it. `HEAD` holds the ID of the latest one. A commit's ID is the hash of its own JSON, so an edited old version no longer matches its ID and is caught.

```
MyKit/
  Kicks/kick_01.wav ...       your sounds, untouched
  .kit/
    objects/ab/cdef...        every file ever saved, named by hash
    commits/<id>.json         one small file per version
    HEAD                      ID of the latest version
    config.json               author name, for credits
```

Two safety rules run through the code. Files are written to a temporary name and then renamed, so a crash never leaves half a file. `HEAD` always moves last, so a failed save leaves the previous version current.

`kit restore` does not rewrite history. It puts the files back and leaves `HEAD` alone, so the rollback shows up as ordinary changes that can be committed as a new version.

Credits follow the audio, not the file name: each sound is credited to the author of the first version that contained its hash, so renaming a sample does not change who added it.

## Project layout

| File | Job |
|---|---|
| `src/kit/hashing.py` | SHA-256 of files and bytes |
| `src/kit/objects.py` | The content-addressed object store |
| `src/kit/manifest.py` | Scan a folder, compare two snapshots |
| `src/kit/commits.py` | The commit record and its ID |
| `src/kit/repo.py` | Commit, log, status, restore, credits |
| `src/kit/audio.py` | WAV length and sample rate for output |
| `src/kit/remote.py` | Remote storage and sync |
| `src/kit/cli.py` | The `kit` command |

## Roadmap

| Version | What it adds | State |
|---|---|---|
| 1 | Local versions: commit, history, compare, roll back, credits | done |
| 2 | Share through a folder (USB drive, Google Drive, Dropbox): `push` and `pull` that send only what is missing | remote storage done, sync in progress |
| 2b | A cloud bucket (S3 or Cloudflare R2) as a second kind of remote | planned |
| 3 | Fork a pack, add to it, send it back, with credits across people | planned |
| 4 | Web page that shows a version's changes with waveforms and play buttons | planned |

Also planned: caching hashes by file size and modified time so large kits scan quickly, a `.kitignore` file, and compressing stored objects.

## Development

```
pip install -e ".[dev]"
python -m pytest
```

The four sync tests in `tests/test_remote.py` are marked as expected failures until `push` and `pull` are finished.

The package is named `kit-vcs` because the plain name `kit` is taken on PyPI. The command is `kit`.
