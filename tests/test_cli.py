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


def test_push_says_it_is_not_built_yet(kit, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(kit)
    main(["init"])
    main(["commit", "-m", "first"])
    code = main(["push", str(tmp_path / "remote")])
    if code == 2:                                      # still the starter stub
        assert "yours to write" in capsys.readouterr().err
    else:                                              # you have written push
        assert code == 0
