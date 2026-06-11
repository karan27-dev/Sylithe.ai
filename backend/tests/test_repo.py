import subprocess
from pathlib import Path

from sylithe.devops.repo import detect


def _git(args, cwd):
    subprocess.run(["git"] + args, cwd=cwd, check=True,
                   capture_output=True)


def test_non_git_folder(tmp_path):
    ctx = detect(tmp_path)
    assert not ctx.is_git
    assert ctx.branch == ""
    assert ctx.repo_name == tmp_path.name


def test_git_repo_detected(tmp_path):
    _git(["init", "-b", "main"], tmp_path)
    _git(["config", "user.email", "test@test.com"], tmp_path)
    _git(["config", "user.name", "Test"], tmp_path)
    (tmp_path / "file.txt").write_text("hello")
    _git(["add", "."], tmp_path)
    _git(["commit", "-m", "init"], tmp_path)

    ctx = detect(tmp_path)
    assert ctx.is_git
    assert ctx.branch == "main"
    assert ctx.last_commit.endswith("init")
    assert not ctx.is_dirty


def test_dirty_state_detected(tmp_path):
    _git(["init", "-b", "main"], tmp_path)
    _git(["config", "user.email", "test@test.com"], tmp_path)
    _git(["config", "user.name", "Test"], tmp_path)
    (tmp_path / "file.txt").write_text("hello")
    _git(["add", "."], tmp_path)
    _git(["commit", "-m", "init"], tmp_path)
    (tmp_path / "file.txt").write_text("changed")  # unstaged change

    ctx = detect(tmp_path)
    assert ctx.is_dirty


def test_repo_name_with_dot_in_name():
    from sylithe.devops.repo import RepoContext
    ctx = RepoContext(is_git=True, remote_url="https://github.com/karan27-dev/Sylithe.ai")
    assert ctx.repo_name == "karan27-dev/Sylithe.ai"
    ctx2 = RepoContext(is_git=True, remote_url="git@github.com:acme/shop.git")
    assert ctx2.repo_name == "acme/shop"


def test_prompt_block_non_git(tmp_path):
    ctx = detect(tmp_path)
    block = ctx.as_prompt_block()
    assert "not a git repository" in block


def test_prompt_block_git(tmp_path):
    _git(["init", "-b", "feature"], tmp_path)
    _git(["config", "user.email", "test@test.com"], tmp_path)
    _git(["config", "user.name", "Test"], tmp_path)
    (tmp_path / "f.txt").write_text("x")
    _git(["add", "."], tmp_path)
    _git(["commit", "-m", "first"], tmp_path)

    block = detect(tmp_path).as_prompt_block()
    assert "Branch" in block
    assert "feature" in block
    assert "Commit" in block


def test_repo_context_injected_into_agent_message(make_state):
    from tests.conftest import FakeChat, FakeMessage
    chat = FakeChat([FakeMessage(content="done")])
    state = make_state(chat)
    state.runner().run("do something", repo_context="WORKSPACE CONTEXT\n  Repo: acme/shop\n  Branch: main")
    first_user_msg = chat.calls[0]["messages"][1]
    assert first_user_msg["role"] == "user"
    assert "acme/shop" in first_user_msg["content"]
    assert "TASK:" in first_user_msg["content"]


def test_no_repo_context_unchanged(make_state):
    from tests.conftest import FakeChat, FakeMessage
    chat = FakeChat([FakeMessage(content="done")])
    state = make_state(chat)
    state.runner().run("just a task")
    first_user_msg = chat.calls[0]["messages"][1]
    assert first_user_msg["content"] == "just a task"
