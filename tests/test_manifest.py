from kit.manifest import Change, diff, dumps, loads, scan


def test_scan_skips_clutter_and_the_kit_folder(kit):
    (kit / ".DS_Store").write_bytes(b"junk")
    (kit / "Thumbs.db").write_bytes(b"junk")
    (kit / ".kit" / "objects").mkdir(parents=True)
    (kit / ".kit" / "objects" / "x").write_bytes(b"internal")

    assert sorted(scan(kit)) == ["Hats/hat_01.wav", "Kicks/kick_01.wav", "Kicks/kick_02.wav"]


def test_diff_finds_each_kind_of_change(kit):
    before = scan(kit)
    (kit / "Hats" / "hat_02.wav").write_bytes(b"new hat")            # added
    (kit / "Kicks" / "kick_02.wav").unlink()                         # removed
    (kit / "Hats" / "hat_01.wav").write_bytes(b"brighter")           # modified
    (kit / "Kicks" / "kick_01.wav").rename(kit / "Kicks" / "kick_main.wav")  # renamed

    assert diff(before, scan(kit)) == [
        Change("modified", "Hats/hat_01.wav"),
        Change("added", "Hats/hat_02.wav"),
        Change("removed", "Kicks/kick_02.wav"),
        Change("renamed", "Kicks/kick_main.wav", "Kicks/kick_01.wav"),
    ]


def test_no_changes_means_empty_diff(kit):
    assert diff(scan(kit), scan(kit)) == []


def test_manifest_bytes_do_not_depend_on_order():
    a = {"b.wav": {"hash": "2", "size": 1}, "a.wav": {"hash": "1", "size": 1}}
    b = {"a.wav": {"hash": "1", "size": 1}, "b.wav": {"hash": "2", "size": 1}}
    assert dumps(a) == dumps(b)
    assert loads(dumps(a)) == a
