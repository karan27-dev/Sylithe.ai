import subprocess

from sylithe.devops.watcher import GitWatcher, review_task


def _git(args, cwd):
    subprocess.run(["git"] + args, cwd=cwd, check=True, capture_output=True)


def _make_repo(path):
    _git(["init", "-b", "main"], path)
    _git(["config", "user.email", "t@t.com"], path)
    _git(["config", "user.name", "T"], path)
    (path / "a.txt").write_text("one")
    _git(["add", "."], path)
    _git(["commit", "-m", "first"], path)


def test_watcher_detects_new_commit(tmp_path):
    _make_repo(tmp_path)
    dispatched = []
    watcher = GitWatcher(
        get_workspace=lambda: str(tmp_path),
        is_enabled=lambda: True,
        dispatch=dispatched.append,
    )
    assert watcher.poll_once() is None  # seeds HEAD, no dispatch
    assert dispatched == []

    (tmp_path / "a.txt").write_text("two")
    _git(["add", "."], tmp_path)
    _git(["commit", "-m", "second"], tmp_path)

    head = watcher.poll_once()
    assert head is not None
    assert len(dispatched) == 1
    assert "GIT WATCH" in dispatched[0]
    assert head[:10] in dispatched[0]

    assert watcher.poll_once() is None  # no further commits, no dispatch


def test_watcher_handles_non_git_folder(tmp_path):
    watcher = GitWatcher(
        get_workspace=lambda: str(tmp_path),
        is_enabled=lambda: True,
        dispatch=lambda t: (_ for _ in ()).throw(AssertionError("should not dispatch")),
    )
    assert watcher.poll_once() is None


def test_review_task_mentions_propose_change():
    task = review_task("abc123def456", "/some/repo")
    assert "propose_change" in task
    assert "abc123def4" in task
