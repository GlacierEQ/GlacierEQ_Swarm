"""Worker result receipt separating execution from useful outcome."""
from dataclasses import dataclass

@dataclass(frozen=True)
class WorkerReceipt:
    worker: str
    action: str
    executed: bool
    outcome: str | None = None
    evidence: tuple[str, ...] = ()

    @property
    def useful(self) -> bool:
        return self.executed and bool(self.outcome) and bool(self.evidence)
