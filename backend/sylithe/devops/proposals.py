"""Apply or reject agent code-change proposals (the operator's y/n)."""

from pathlib import Path

from sylithe.memory.store import MemoryStore
from sylithe.skills.builtin import _safe_path


def apply_proposal(memory: MemoryStore, workspace: Path, proposal_id: int) -> tuple[bool, str]:
    proposal = memory.get_proposal(proposal_id)
    if proposal is None:
        return False, f"Proposal {proposal_id} not found"
    if proposal.status != "pending":
        return False, f"Proposal {proposal_id} already {proposal.status}"
    try:
        target = _safe_path(workspace, proposal.file_path)
        if target.exists() and target.read_text() != proposal.original_content:
            memory.resolve_proposal(proposal_id, "stale")
            return False, (f"File '{proposal.file_path}' changed since the proposal was "
                           f"made; marked stale. Re-run the agent for a fresh proposal.")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(proposal.proposed_content)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    memory.resolve_proposal(proposal_id, "applied")
    return True, f"Applied change to {proposal.file_path}"


def reject_proposal(memory: MemoryStore, proposal_id: int) -> tuple[bool, str]:
    proposal = memory.get_proposal(proposal_id)
    if proposal is None:
        return False, f"Proposal {proposal_id} not found"
    if proposal.status != "pending":
        return False, f"Proposal {proposal_id} already {proposal.status}"
    memory.resolve_proposal(proposal_id, "rejected")
    return True, f"Rejected change to {proposal.file_path}"
