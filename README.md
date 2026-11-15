# Kit

**Git-style version control for sample packs and loop kits.**

Keep your kit folder exactly as it is. Kit remembers every version, shows what changed between versions, rolls back changes, shares kits with other producers, merges their contributions, and tracks who added each sound.

## Why Kit?

Producers often share sample packs as ZIP files. Every update becomes another archive, making it difficult to track what changed, which sounds were added, or who contributed them.

Kit gives sample packs a proper version history. Instead of managing files like `MyKit_v3_FINAL.zip`, producers can:

- Track changes to their sample packs over time.
- Save versions without duplicating unchanged audio files.
- Share updates that transfer only new or modified sounds.
- Merge contributions from other producers.
- Track the original creator of every sound, even after renaming or moving files.

## Installation

Requires **Python 3.10 or newer**. No additional dependencies are required for the core functionality.

```bash
git clone https://github.com/nojanpourjafar/kit-vcs.git
cd kit-vcs
pip install -e .
```

This installs the `kit` command.

To run Kit without installing it, use `PYTHONPATH=src python -m kit ...` instead of `kit ...`.

## Quick Start

Try Kit with a sample pack containing four synthesized sounds:

```bash
python examples/make_demo_kit.py demo_kit
cd demo_kit
```

Initialize version control and create your first commit:

```bash
kit init --author "Nojan"
kit commit -m "first four sounds"
```

Rename a sample and check what changed:

```bash
mv Kicks/kick_punch.wav Kicks/kick_01.wav
kit status
```

Kit detects the rename:

```text
Kicks/kick_01.wav (was Kicks/kick_punch.wav)
```

Commit the change:

```bash
kit commit -m "renamed the kick"
```

Because the audio itself has not changed, Kit stores no additional audio data:

```text
Stored 0 new file(s), 0 bytes.
```

Explore your version history and compare changes:

```bash
kit log
kit diff v1 v2
```

Restore an earlier version:

```bash
kit restore v1
```

See who contributed each sound and open an interactive version of your kit:

```bash
kit credits
kit show
```

## Sharing a Kit

A remote is any location where a copy of your kit's version history can be stored. The simplest option is a folder on a USB drive or a folder synchronized through Google Drive or Dropbox.

### Pushing changes

Send your versions to a shared folder:

```bash
kit push ~/GoogleDrive/shared-kit
```

Kit remembers the remote, so future pushes only need:

```bash
kit push
```

### Cloning a kit

Another producer can create their own copy of the kit and its version history:

```bash
kit clone ~/GoogleDrive/shared-kit my-copy --author "Lucas"
cd my-copy
```

They can then add or modify sounds and commit their changes:

```bash
kit commit -m "added an open hat"
kit push
```

### Pulling changes

To bring another producer's contributions into your own kit:

```bash
kit pull
```

If both producers have created new versions since their last synchronization, Kit automatically merges their histories.

- **Changes on one side:** The change is accepted automatically.
- **Identical changes on both sides:** Only one copy is kept.
- **Conflicting changes:** Both versions are preserved. Your file stays in place, while the other producer's version is saved alongside it with their name appended.

For example:

```text
kick.wav
kick (from Lucas).wav
```

This allows you to listen to both versions and decide which one to keep.

After merging, `kit credits` shows who contributed each sound, including a breakdown of contributions by number of files.

### Cloud Storage (S3 / R2)

Kit also supports Amazon S3 and S3-compatible storage services such as Cloudflare R2.

Install the optional dependency:

```bash
pip install boto3
```

Push to an S3 bucket:

```bash
kit push s3://my-bucket/my-kit
```

Credentials are read from the standard AWS environment variables or `~/.aws/credentials`.

For non-Amazon services, configure the endpoint using:

```bash
export KIT_S3_ENDPOINT=https://your-endpoint
```

**Note:** S3 support has been tested against an in-memory S3 implementation using Moto. It has not yet been tested against a real S3 bucket.

## Commands

| Command | Description |
| --- | --- |
| `kit init` | Initialize version control in the current folder. |
| `kit status` | Show changes since the last version. |
| `kit commit -m "message"` | Save the current folder as a new version. |
| `kit log` | List saved versions, newest first. |
| `kit diff v1 v2` | Compare changes between two versions. |
| `kit restore v1` | Restore the folder to an earlier version. |
| `kit credits` | Show who first added each sound and the contribution breakdown. |
| `kit show [v2]` | Open a version as a web page with waveforms and audio players. |
| `kit remote [where]` | Show or set the default remote. |
| `kit push [where]` | Send new versions to a remote. |
| `kit pull [where]` | Download new versions and merge them if necessary. |
| `kit clone <where> [folder]` | Create a new kit folder from a remote. |
| `kit verify` | Check that stored files are not damaged or missing. |

Versions can be referenced using their sequential names (`v1`, `v2`, etc.), `latest`, or the beginning of their commit ID.

The `where` argument accepts either a local folder path or an S3 location (`s3://bucket/prefix`).

### Ignoring Files

A `.kitignore` file can be created in the kit folder to exclude files and folders from version control.

Add one pattern per line:

```text
*.asd
Drafts/
```

In this example, Kit ignores all `.asd` files and everything inside the `Drafts/` directory.

## How It Works

Kit uses content-addressed storage, lightweight manifests, and a commit history to track changes without duplicating audio files.

### 1. Content-Addressed Storage

Every file is identified using its SHA-256 hash and stored inside `.kit/objects/`.

```text
.kit/objects/<hash>
```

If the same kick appears in ten different versions, Kit stores the audio only once.

Renaming or moving a sample does not require storing another copy because the file's contents, and therefore its hash, remain unchanged.

### 2. Version Manifests

Each version contains a manifest: a mapping of file paths to their corresponding hashes.

For example:

```text
Kicks/kick_01.wav -> a83f...
Snares/snare_01.wav -> b921...
Hats/hat_open.wav -> c102...
```

The manifest does not contain audio data, making it very small.

Comparing two manifests allows Kit to identify:

- **Additions:** A new file path appears.
- **Modifications:** A file exists at the same path but has a different hash.
- **Deletions:** A file path no longer exists.
- **Renames:** An existing hash appears under a different path.

### 3. Commit History

Each commit is stored as a small JSON file containing:

- The version's manifest.
- The author's name.
- The commit message.
- The timestamp.
- The ID of its parent version.

The `HEAD` file stores the ID of the latest commit.

Each commit ID is calculated by hashing its own JSON data. This means modifying an existing commit causes its ID to no longer match, allowing Kit to detect tampering.

Merge commits can contain two parents, allowing both histories to remain connected.

### Directory Structure

A typical Kit repository looks like this:

```text
MyKit/
├── Kicks/
│   └── kick_01.wav
├── Snares/
│   └── snare_01.wav
└── .kit/
    ├── objects/
    │   └── ab/
    │       └── cdef...
    ├── commits/
    │   └── <id>.json
    ├── HEAD
    ├── config.json
    └── index.json
```

Your original audio files remain in their folders. All version-control data is stored separately inside `.kit/`.

### 4. Efficient Synchronization

Kit transfers only the data that a remote does not already have.

When pushing, Kit:

1. Walks backward through the local commit history until it finds a version already present on the remote.
2. Uploads missing commits in chronological order.
3. Uploads only the audio objects the remote does not already contain.
4. Updates the remote's `HEAD` last.

This approach means interrupted pushes can leave extra objects on the remote, but cannot create a version that references missing data.

During a pull, Kit performs the same process in reverse and recalculates the hash of every downloaded object to reject corrupted files.

If the remote contains versions that the local repository has never seen, `push` refuses to proceed. Pulling first ensures that pushing will not overwrite or disconnect someone else's history.

### 5. Three-Way Merging

Kit uses three-way merging to combine contributions from different producers.

It compares both versions against their last shared ancestor.

- Changes made on only one side are accepted automatically.
- Identical changes made on both sides are treated as a single change.
- Conflicting changes are preserved as separate files.

Unlike source code, audio cannot be meaningfully merged line by line. Kit therefore avoids automatically choosing a winner when two producers modify the same sound.

### 6. Data Integrity and Credits

Kit includes several additional safeguards:

- **Atomic file writes:** Files are written to temporary locations and renamed only after writing completes, preventing partially written files.
- **Safe commits:** `HEAD` is updated last, so a failed save leaves the previous version current.
- **Non-destructive restoration:** `kit restore` changes the working folder without rewriting commit history. The rollback can then be committed as a new version.
- **Persistent credits:** Sounds are credited to the author of the first commit containing their hash. Credits survive renames, moves, and merges.
- **Efficient status checks:** Kit uses a hash cache and skips re-hashing files whose size and modification time have not changed. Files modified within the last two seconds are always re-hashed.
- **Path validation:** Manifest paths that point outside the kit folder are rejected, protecting against unsafe paths from other contributors.

## Project Structure

| File | Responsibility |
| --- | --- |
| `src/kit/hashing.py` | SHA-256 hashing of files and bytes. |
| `src/kit/objects.py` | Content-addressed object storage. |
| `src/kit/manifest.py` | Folder scanning, snapshot comparison, and three-way merging. |
| `src/kit/commits.py` | Commit records, commit IDs, and history traversal. |
| `src/kit/repo.py` | Commit, log, status, restore, merge, credits, and verification operations. |
| `src/kit/remote.py` | Folder remotes, push, pull, and clone operations. |
| `src/kit/s3remote.py` | Amazon S3 and Cloudflare R2 remote support. |
| `src/kit/audio.py` | WAV duration, sample rate, and waveform generation. |
| `src/kit/report.py` | Web page generation for `kit show`. |
| `src/kit/cli.py` | Command-line interface. |

## Roadmap

| Feature | Status |
| --- | --- |
| Local version control: commits, history, comparisons, and restoration | Complete |
| Folder-based sharing: push, pull, and clone | Complete |
| Merging contributions from multiple producers | Complete |
| Credits across collaborators | Complete |
| S3 / R2 remote support | Complete (not yet tested against a real bucket) |
| Interactive web page with waveforms and audio players | Complete |
| Hash caching, `.kitignore`, and `kit verify` | Complete |
| Hosted service with accounts, private and paid packs, and download tracking | Planned |
| Waveform support for MP3, FLAC, and float WAV files | Planned |
| Compression of large audio files using FLAC | Planned |

### Known Limitations

- Folder-based remotes do not support locking, so simultaneous pushes can create race conditions.
- Sequential version numbers (`v1`, `v2`, etc.) follow the local main history. Versions received through a merge are identified by their commit IDs.

## Development

Clone the repository and install the development dependencies:

```bash
pip install -e ".[dev]"
```

Run the test suite:

```bash
python -m pytest
```

S3 tests require `boto3` and `moto`:

```bash
pip install boto3 moto
```

## Package Information

The PyPI package is named `kit-vcs` because the package name `kit` is already taken.

After installation, the command-line interface is simply:

```bash
kit
```