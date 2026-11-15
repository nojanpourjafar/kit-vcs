from kit.cli import main


def test_a_whole_session(kit, monkeypatch, capsys):
    monkeypatch.chdir(kit)

    assert main(["init", "--author", "Nojan"]) == 0
    assert main(["status"]) == 0
    assert "Not saved yet" in capsys.readouterr().out

    assert main(["commit", "-m", "first version"]) == 0
    assert "Saved v1" in capsys.readouterr().out

    (kit / "Kicks" / "kick_01.wav").rename(kit / "Kicks" / "kick_main.wav")
    assert main(["status"]) == 0
    assert "(was Kicks/kick_01.wav)" in capsys.readouterr().out

    assert main(["commit", "-m", "renamed the kick"]) == 0
    assert "Stored 0 new file(s)" in capsys.readouterr().out

    assert main(["log"]) == 0
    out = capsys.readouterr().out
    assert out.index("v2") < out.index("v1")          # newest first

    assert main(["diff", "v1", "v2"]) == 0
    assert "1 renamed" in capsys.readouterr().out

    assert main(["restore", "v1"]) == 0
    assert (kit / "Kicks" / "kick_01.wav").exists()

    assert main(["credits"]) == 0
    assert "Nojan (v1)" in capsys.readouterr().out


def test_mistakes_get_a_plain_message_not_a_traceback(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["status"]) == 1
    assert "kit init" in capsys.readouterr().err


def test_sharing_between_two_folders(kit, tmp_path, monkeypatch, capsys):
    shared = str(tmp_path / "shared")
    monkeypatch.chdir(kit)
    main(["init", "--author", "Nojan"])
    main(["commit", "-m", "first"])
    assert main(["push"]) == 1                              # no remote known yet
    assert main(["push", shared]) == 0                      # the first remote used is remembered
    assert "Pushed 1 version(s), 4 object(s)." in capsys.readouterr().out
    assert main(["push"]) == 0
    assert "already has every version" in capsys.readouterr().out

    monkeypatch.chdir(tmp_path)
    assert main(["clone", shared, "fork", "--author", "Lucas"]) == 0
    monkeypatch.chdir(tmp_path / "fork")
    (tmp_path / "fork" / "Hats" / "hat_lucas.wav").write_bytes(b"lucas hat")
    main(["commit", "-m", "a hat from Lucas"])
    assert main(["push"]) == 0                              # clone saved the remote

    monkeypatch.chdir(kit)
    capsys.readouterr()
    assert main(["pull"]) == 0
    assert "+ Hats/hat_lucas.wav" in capsys.readouterr().out
    assert main(["credits"]) == 0
    out = capsys.readouterr().out
    assert "Lucas (v2)" in out and "Nojan 75% (3), Lucas 25% (1)" in out


def test_show_and_verify(kit, monkeypatch, capsys):
    monkeypatch.chdir(kit)
    main(["init"])
    main(["commit", "-m", "first"])
    assert main(["show", "--no-open"]) == 0
    assert (kit / ".kit" / "show.html").read_text().startswith("<!DOCTYPE html>")
    assert main(["status"]) == 0                            # the page is not seen as a change
    assert "Nothing changed" in capsys.readouterr().out.splitlines()[-1]
    assert main(["verify"]) == 0
