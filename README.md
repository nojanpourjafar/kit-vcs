# Kit

Git-style version control for sample packs and loop kits. You keep your kit folder exactly as it is; `kit` remembers every version of it, shows what changed, rolls back, and records who added each sound.

Kit is Git for your kits. You run it as `kit`, the way you run Git as `git`.

The package itself is named `kit-vcs`, because the plain name `kit` is already taken on PyPI (it is not published there yet). The command you type is still `kit`.

## Try it in two minutes

Needs Python 3.10 or newer. No other dependencies.

```
pip install -e .                             # gives you the `kit` command
python examples/make_demo_kit.py demo_kit    # 4 synthesized sounds to play with
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

Without installing: run `PYTHONPATH=src python -m kit ...` in place of `kit ...`.

## How it works: three ideas

**1. Every file is stored once, under the name of its own hash.**
`kit` runs each file through SHA-256 and saves it as `.kit/objects/<hash>`. The same kick in ten versions takes the space of one kick. Renaming or moving a sample stores nothing, because the audio (and so the hash) did not change. This is called content-addressed storage.

**2. A version is a list of "path -> hash", called a manifest.**
It holds no audio, so it is tiny. Comparing two manifests gives the changes: a new path is an addition, a new hash at the same path is a modification, and a hash that moved to a new path is a rename.

**3. Versions form a chain.**
Each commit is a few lines of JSON: its manifest, author, message, time, and the ID of the version before it. `HEAD` holds the ID of the latest one. A commit's ID is the hash of its own JSON, so an edited old version no longer matches its ID and is caught.

```
MyKit/
  Kicks/kick_01.wav ...       your sounds, untouched
  .kit/
    objects/ab/cdef...        every file ever saved, named by hash
    commits/<id>.json         one small file per version
    HEAD                      ID of the latest version
    config.json               your name, for credits
```

Two safety rules run through the code. Files are written to a temporary name and then renamed, so a crash never leaves half a file. And `HEAD` always moves last, so a failed save leaves the previous version current.

## Code map

| File | Job | State |
|---|---|---|
| `src/kit/hashing.py` | SHA-256 of files and bytes | built |
| `src/kit/objects.py` | The object store (idea 1) | built |
| `src/kit/manifest.py` | Scan a folder, compare two snapshots (idea 2) | built |
| `src/kit/commits.py` | The commit record and its ID (idea 3) | built |
| `src/kit/repo.py` | Ties it together: commit, log, status, restore, credits | built |
| `src/kit/audio.py` | WAV length and sample rate for nicer output | built |
| `src/kit/cli.py` | The `kit` command | built |
| `src/kit/remote.py` | `FolderRemote` (a shared folder as a remote) | built |
| `src/kit/remote.py` | `push` and `pull` | **yours to write** |
| `tests/` | 22 passing tests, plus 4 waiting on push and pull | built |

Read the files in the order of the table. Each one is short and starts with a comment explaining why it exists.

## Roadmap

| Version | What it adds | State |
|---|---|---|
| 1 | Local versions: commit, history, compare, roll back, credits | done |
| 2 | Share through a folder: `push` and `pull` that send only what is missing | storage done; the two functions are yours |
| 2b | A cloud bucket (S3 or Cloudflare R2) as a second remote class | not started |
| 3 | Fork a pack, add to it, send it back (merge two histories), credits across people | not started |
| 4 | Web page that shows a version's changes with waveforms and play buttons | not started |

Later ideas: cache hashes by file size and modified time so big kits scan fast, a `.kitignore` file, and compressing objects.

## Your part: push and pull

Open `src/kit/remote.py`. The two functions at the bottom raise `NotImplementedError`, and each has a list of questions that walks you to the design. They need about 25 lines each, and everything they call already exists.

```
python -m pytest              # 22 passed, 4 xfailed  <- today
python -m pytest              # 22 passed, 4 xpassed  <- once both work
```

The four waiting tests are in `tests/test_remote.py`. They were checked against a working solution, so they are passable as written. When they pass, delete the `@yours_to_write` lines.

To try it for real, point two kit folders at one shared folder (a Google Drive or Dropbox folder works):

```
kit push ~/GoogleDrive/shared-kit         # from your kit
kit pull ~/GoogleDrive/shared-kit         # from a friend's empty repo
```

## Questions you should be able to answer

These are what an interviewer will ask. The code and its comments hold the answers.

1. Why store files by hash, and what does that make cheap?
2. How does `kit` tell a rename from a delete plus an add?
3. Why is a commit's ID the hash of its contents?
4. Why does `HEAD` move last when saving a version?
5. Why write to a temporary file and then rename it?
6. Why does `restore` leave history alone, where Git's `checkout` moves `HEAD`?
7. What happens if two people push at the same time? (You will decide this in `push`.)
8. What stops a manifest from someone else writing files outside the kit folder?
9. Hashing a 2 GB kit on every `status` is slow. How would you avoid re-hashing unchanged files?
