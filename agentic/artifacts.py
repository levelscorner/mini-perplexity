"""Content-addressable byte store, parallel to Memory.

Anything a tool returns larger than ARTIFACT_THRESHOLD_BYTES is written here
and Memory keeps only the handle. Handles are short auto-increment integers
('art:1', 'art:2', ...) — NOT sha256 strings, which weak models hallucinate.

This is mechanical plumbing (no LLM, no graded IP), so it ships complete.
Persistence to state/artifacts/ is optional; default is in-RAM + JSON index,
which is enough for the four assignment queries and keeps `state/` clean.
"""
from __future__ import annotations

import json
from pathlib import Path

from schemas import Artifact

ARTIFACT_THRESHOLD_BYTES = 4 * 1024  # 4 KB — above this, a tool result becomes an artifact


class ArtifactStore:
    def __init__(self, state_dir: str = "state", persist: bool = True):
        self._meta: dict[str, Artifact] = {}
        self._bytes: dict[str, bytes] = {}
        self._counter = 0
        self.persist = persist
        self.dir = Path(state_dir) / "artifacts"
        if persist:
            self.dir.mkdir(parents=True, exist_ok=True)

    def _next_id(self) -> str:
        self._counter += 1
        return f"art:{self._counter}"

    def put(self, blob: bytes, *, content_type: str = "text/plain",
            source: str = "", descriptor: str = "") -> str:
        aid = self._next_id()
        self._bytes[aid] = blob
        self._meta[aid] = Artifact(
            id=aid, content_type=content_type, size_bytes=len(blob),
            source=source, descriptor=descriptor[:200],
        )
        if self.persist:
            n = aid.split(":")[1]
            (self.dir / f"{n}.bin").write_bytes(blob)
            (self.dir / f"{n}.json").write_text(self._meta[aid].model_dump_json(indent=2))
        return aid

    def exists(self, artifact_id: str) -> bool:
        return artifact_id in self._bytes or (
            self.persist and (self.dir / f"{artifact_id.split(':')[1]}.bin").exists()
        )

    def get_bytes(self, artifact_id: str) -> bytes:
        if artifact_id in self._bytes:
            return self._bytes[artifact_id]
        n = artifact_id.split(":")[1]
        return (self.dir / f"{n}.bin").read_bytes()

    def get_meta(self, artifact_id: str) -> Artifact:
        if artifact_id in self._meta:
            return self._meta[artifact_id]
        n = artifact_id.split(":")[1]
        return Artifact.model_validate_json((self.dir / f"{n}.json").read_text())
