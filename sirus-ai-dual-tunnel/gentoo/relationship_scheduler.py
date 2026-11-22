"""Conservative relationship-index admission for the autonomous research loop."""
from __future__ import annotations
from manual_relationship_index import sync as index_relationships

def index_if_idle(*, enabled, interactive, source, index, run=index_relationships):
    """Opt-in and chat-first: only four documents per 30-second inventory pass.

    This never queues inference or trains a model. It only refreshes derived
    original-source citations and remains a no-op when the controller is off.
    """
    if type(enabled) is not bool or type(interactive) is not bool:
        raise ValueError("Controller and chat state must be explicit booleans")
    if not enabled or interactive:
        return {"skipped": True, "reason": "disabled" if not enabled else "interactive"}
    return run(source=source, index=index, batch=4)


from procedure_review_queue import tick as recheck_registered

def recheck_if_idle(*, enabled, interactive, source, index, queue, run=recheck_registered):
    """Recheck one registered procedural comparison after chat-first index work.

    Never invent a model/year scope or register a cross-document comparison.
    This is only a private source recheck, not an inference or repair operation.
    """
    if type(enabled) is not bool or type(interactive) is not bool:
        raise ValueError("Controller and chat state must be explicit booleans")
    if not enabled or interactive:
        return {"skipped": True, "reason": "disabled" if not enabled else "interactive"}
    return run(queue=queue, source=source, index=index)
