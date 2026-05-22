"""Run each role prompt through the Session-5 PoP (Prompt-of-Prompts) qualifier.

The qualifier (pop/qualifier.md) is a prompt-evaluation prompt: feed it a student
prompt and it returns a structured JSON review scoring 9 reasoning criteria. We send
each of our three role prompts through it via the gateway and save the verdict JSON.

    cd agentic && GATEWAY_URL=http://localhost:8101 AGENT_PROVIDER=openai \\
        .venv/bin/python pop/run_pop.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from decision import DECISION_SYSTEM      # noqa: E402
from gateway_client import Gateway        # noqa: E402
from memory import CLASSIFY_SYSTEM        # noqa: E402
from perception import PERCEPTION_SYSTEM  # noqa: E402

HERE = Path(__file__).resolve().parent
QUALIFIER = (HERE / "qualifier.md").read_text()

PROMPTS = {
    "perception": PERCEPTION_SYSTEM,
    "decision": DECISION_SYSTEM,
    "memory_classify": CLASSIFY_SYSTEM,
}


def _extract_json(text: str) -> dict:
    """The qualifier wraps its verdict in a ```json fence; pull the object out."""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError(f"no JSON object in qualifier reply:\n{text[:400]}")
    return json.loads(m.group(0))


def main() -> int:
    gw = Gateway()
    for name, prompt in PROMPTS.items():
        user = (
            "Here is the student prompt to evaluate. Review it against your nine "
            "criteria and respond with ONLY the JSON verdict in the required format.\n\n"
            "----- BEGIN STUDENT PROMPT -----\n"
            f"{prompt}\n"
            "----- END STUDENT PROMPT -----"
        )
        reply = gw.chat(system=QUALIFIER, user=user, provider="openai", temperature=0)
        verdict = _extract_json(reply)
        out = HERE / f"{name}.json"
        out.write_text(json.dumps(verdict, indent=2) + "\n")
        flags = {k: v for k, v in verdict.items() if isinstance(v, bool)}
        passed = sum(1 for v in flags.values() if v)
        print(f"{name:16s} → {passed}/{len(flags)} criteria true  ({out.name})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
