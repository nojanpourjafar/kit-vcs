from kit.commits import lineage, new_commit, parse_commit
from kit.manifest import merge_manifests


def entry(content: str) -> dict:
    return {"hash": content, "size": len(content)}


BASE = {"kick.wav": entry("kick"), "hat.wav": entry("hat"), "snare.wav": entry("snare")}


def test_changes_on_one_side_are_taken():
    ours = {**BASE, "clap.wav": entry("clap")}                       # we added a clap
    theirs = {"kick.wav": entry("kick v2"), "hat.wav": entry("hat")}  # they edited the kick, cut the snare

    merged, conflicts = merge_manifests(BASE, ours, theirs, "Lucas")

    assert conflicts == []
    assert merged == {"kick.wav": entry("kick v2"), "hat.wav": entry("hat"), "clap.wav": entry("clap")}


def test_the_same_change_on_both_sides_is_not_a_conflict():
    both = {**BASE, "kick.wav": entry("kick v2")}
    merged, conflicts = merge_manifests(BASE, both, dict(both), "Lucas")
    assert (merged, conflicts) == (both, [])


def test_different_edits_keep_both_files():
    ours = {**BASE, "hat.wav": entry("hat ours")}
    theirs = {**BASE, "hat.wav": entry("hat theirs")}

    merged, conflicts = merge_manifests(BASE, ours, theirs, "Lucas")

    assert conflicts == ["hat.wav"]
    assert merged["hat.wav"] == entry("hat ours")
    assert merged["hat (from Lucas).wav"] == entry("hat theirs")


def test_an_edit_beats_a_delete_in_either_direction():
    edited = {**BASE, "snare.wav": entry("snare v2")}
    deleted = {k: v for k, v in BASE.items() if k != "snare.wav"}

    kept_ours, _ = merge_manifests(BASE, edited, deleted, "Lucas")
    kept_theirs, _ = merge_manifests(BASE, deleted, edited, "Lucas")

    assert kept_ours["snare.wav"] == kept_theirs["snare.wav"] == entry("snare v2")


def test_side_by_side_names_never_overwrite_anything():
    ours = {"hat.wav": entry("a"), "hat (from Lucas).wav": entry("already here")}
    theirs = {"hat.wav": entry("b")}

    merged, _ = merge_manifests({}, ours, theirs, "Lucas")

    assert merged["hat (from Lucas).wav"] == entry("already here")
    assert merged["hat (from Lucas 2).wav"] == entry("b")


def test_ordinary_commits_keep_their_ids_and_merges_record_two_parents():
    plain = new_commit(None, "m" * 64, "Nojan", "first", time="2026-01-01T00:00:00+00:00")
    assert b"merged" not in plain.to_bytes()
    merge = new_commit(plain.id, "m" * 64, "Nojan", "merge", merged="f" * 64)
    assert parse_commit(merge.to_bytes()) == merge
    assert merge.parents == [plain.id, "f" * 64]


def test_lineage_lists_parents_before_children_and_can_stop_early():
    a = new_commit(None, "m" * 64, "x", "a")
    b = new_commit(a.id, "m" * 64, "x", "b")
    c = new_commit(a.id, "m" * 64, "y", "c")
    d = new_commit(b.id, "m" * 64, "x", "merge", merged=c.id)
    commits = {x.id: x for x in (a, b, c, d)}

    assert [x.message for x in lineage(d.id, commits.__getitem__)] == ["a", "b", "c", "merge"]
    assert [x.message for x in lineage(d.id, commits.__getitem__, have={a.id, b.id}.__contains__)] \
        == ["c", "merge"]
