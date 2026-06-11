"""Git watcher — detects new commits in the workspace and dispatches the agent.

When watching is enabled, any new commit triggers an autonomous review run:
inspect the change, run the tests, and if anything fails, find the root cause
and file a propose_change for the operator's y/n.
"""

import subprocess
import threading
from pathlib import Path
from typing import Callable


def _head(workspace: str) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=workspace,
            capture_output=True, text=True, timeout=8,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        return ""


def review_task(head: str, workspace: str) -> str:
    return (
        f"GIT WATCH: a new commit ({head[:10]}) just landed in the watched repo at "
        f"{workspace}. Review it: run_git 'log -1 --stat' and run_git 'show --stat HEAD' "
        f"to see what changed, then run the test suite. If tests fail or you spot a bug "
        f"in the changed lines, identify the exact root cause and use propose_change to "
        f"suggest the fix — the operator will approve or reject it. Finish with a short "
        f"summary: what changed, test status, and what (if anything) you proposed."
    )


class GitWatcher(threading.Thread):
    """Daemon thread polling the workspace HEAD; dispatches on change."""

    def __init__(self, get_workspace: Callable[[], str],
                 is_enabled: Callable[[], bool],
                 dispatch: Callable[[str], None],
                 interval_seconds: float = 10.0) -> None:
        super().__init__(daemon=True, name="sylithe-git-watcher")
        self._get_workspace = get_workspace
        self._is_enabled = is_enabled
        self._dispatch = dispatch
        self._interval = interval_seconds
        self._stop = threading.Event()
        self._last_head: dict[str, str] = {}

    def poll_once(self) -> str | None:
        """One poll cycle; returns the new HEAD if a fresh commit was detected."""
        workspace = self._get_workspace()
        if not workspace or not Path(workspace).exists():
            return None
        head = _head(workspace)
        if not head:
            return None
        previous = self._last_head.get(workspace)
        self._last_head[workspace] = head
        if previous and previous != head:
            self._dispatch(review_task(head, workspace))
            return head
        return None

    def run(self) -> None:
        while not self._stop.wait(self._interval):
            if self._is_enabled():
                try:
                    self.poll_once()
                except Exception:
                    # Watcher must never die from a transient git error.
                    pass

    def stop(self) -> None:
        self._stop.set()
