"""Session 10: the Computer-Use skill — cascade over cua-driver.

Mirrors the S9 Browser skill shape (skill.py owns the cascade; bypasses
the LLM-chat dispatch in skills.py). Four layers:

    Layer 1  — extract        read AX text directly, no clicks (zero LLM)
    Layer 2a — deterministic  known hotkey sequences (zero LLM)
    Layer 2b — a11y + judge   scan → cheap text LLM → act → verify loop
    Layer 3  — vision         screenshot + set-of-marks + V9 /v1/vision

Precondition: cua-driver permissions (TCC on macOS). On empty AX tree we
raise PreconditionError and the orchestrator surfaces it — the user
must run `cua-driver permissions grant` to fix.

Returns a typed ComputerUseOutput with `path` set to the layer that
produced the answer (extract / deterministic / a11y / vision) so the
replay viewer can show the cascade decision the same way it shows
Browser's output.path.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import httpx

from schemas import AgentResult, NodeSpec

from . import driver

# ── known deterministic recipes (Layer 2a) ─────────────────────────────────
# A recipe matches when its `bundle_id` matches AND its `phrase` substring
# appears in the goal. Each recipe is a sequence of (tool, args) shell-outs
# to cua-driver. No LLM, no scanning between steps.

_DETERMINISTIC_RECIPES: list[dict[str, Any]] = [
    {
        "name": "calculator_arithmetic",
        "bundle_id": "com.apple.calculator",
        "phrase_keywords": ["calculator", "compute", "calculate", "arithmetic", "*", "+", "-", "/", "x", "×"],
        # Recipe is "open Calculator, parse the expression from the goal,
        # press each digit/op via press_key, then = and read the result".
        # The actual digit sequence is built dynamically in
        # _run_calculator() below — this entry exists so the cascade
        # KNOWS the recipe exists.
        "handler": "_run_calculator",
    },
]


# ── LLM client (V9 chat for Layer 2b; V9 vision for Layer 3) ──────────────

class _V9Client:
    def __init__(self, base_url: str = "http://localhost:8109"):
        self.base_url = base_url
        self.client = httpx.Client(timeout=60)

    def chat(self, system: str, user: str, *, agent: str, session: str,
             response_format: dict | None = None) -> dict:
        body: dict[str, Any] = {
            "system": system, "prompt": user,
            "provider": "g", "temperature": 1.0,
            "max_tokens": 800, "agent": agent, "session": session,
        }
        if response_format:
            body["response_format"] = response_format
        r = self.client.post(f"{self.base_url}/v1/chat", json=body)
        r.raise_for_status()
        return r.json()

    def vision(self, system: str, user: str, image_b64: str, *,
               agent: str, session: str) -> dict:
        body = {
            "system": system, "prompt": user,
            "image_b64": image_b64,
            "agent": agent, "session": session,
        }
        r = self.client.post(f"{self.base_url}/v1/vision", json=body)
        r.raise_for_status()
        return r.json()


# ── the skill ──────────────────────────────────────────────────────────────

class ComputerUseSkill:
    """Mirrors BrowserSkill: takes a NodeSpec, returns an AgentResult."""

    PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "computer_use.md"
    MAX_TURNS = 12

    def __init__(self, *, artifacts_root: str, session: str,
                 v9_base_url: str = "http://localhost:8109"):
        self.artifacts_root = Path(artifacts_root)
        self.artifacts_root.mkdir(parents=True, exist_ok=True)
        self.session = session
        self.llm = _V9Client(v9_base_url)
        self._prompt = self.PROMPT_PATH.read_text()
        self.turn_log: list[dict] = []

    # ─── main entry ────────────────────────────────────────────────────
    async def run(self, node: NodeSpec) -> AgentResult:
        meta = node.metadata or {}
        goal: str = meta.get("goal") or meta.get("question") or ""
        bundle_id: str = meta.get("bundle_id") or self._infer_bundle(goal)
        electron_port: int | None = meta.get("electron_port")
        force_path: str | None = meta.get("force_path")  # 'extract'|'deterministic'|'a11y'|'vision'
        record_dir: str | None = meta.get("record_dir")

        started = time.time()
        try:
            driver.ensure_daemon()
        except driver.CuaError as e:
            return AgentResult(
                skill="computer_use", status="failed",
                error_code="gateway_blocked",
                output={"error": f"cua-driver unavailable: {e}", "path": "blocked"},
            )

        if record_dir:
            try:
                driver.start_recording(record_dir)
            except driver.CuaError:
                pass  # recording is nice-to-have, not load-bearing

        try:
            pid, window_id = await asyncio.to_thread(
                driver.launch_and_activate, bundle_id, electron_port=electron_port
            )
        except driver.CuaError as e:
            return AgentResult(
                skill="computer_use", status="failed",
                error_code="precondition_failed",
                output={"error": str(e), "path": "blocked"},
            )

        # cascade
        try:
            for layer in self._layer_order(force_path):
                result = await asyncio.to_thread(
                    self._run_layer, layer, pid, window_id, goal, bundle_id, electron_port,
                )
                if result is not None:
                    elapsed = time.time() - started
                    return AgentResult(
                        skill="computer_use", status="complete",
                        output={
                            "path": layer,
                            "goal": goal,
                            "bundle_id": bundle_id,
                            "turns": len(self.turn_log),
                            "result": result,
                            "trajectory": self.turn_log,
                        },
                        elapsed_s=elapsed,
                    )
        except driver.PermissionsError as e:
            return AgentResult(
                skill="computer_use", status="failed",
                error_code="precondition_failed",
                output={"error": str(e), "path": "blocked"},
            )
        finally:
            if record_dir:
                try: driver.stop_recording()
                except Exception: pass

        return AgentResult(
            skill="computer_use", status="failed",
            error_code="cascade_exhausted",
            output={"path": "exhausted", "turns": len(self.turn_log)},
        )

    # ─── layer dispatcher ──────────────────────────────────────────────
    def _layer_order(self, force_path: str | None) -> list[str]:
        if force_path:
            return [force_path]
        return ["extract", "deterministic", "a11y", "vision"]

    def _run_layer(self, layer: str, pid: int, window_id: int, goal: str,
                   bundle_id: str, electron_port: int | None) -> Any:
        if layer == "extract":
            return self._run_extract(pid, window_id, goal)
        if layer == "deterministic":
            return self._run_deterministic(pid, window_id, goal, bundle_id)
        if layer == "a11y":
            return self._run_a11y(pid, window_id, goal, electron_port)
        if layer == "vision":
            return self._run_vision(pid, window_id, goal)
        return None

    # ─── layer 1: extract ──────────────────────────────────────────────
    def _run_extract(self, pid: int, window_id: int, goal: str) -> Any:
        """Read text directly from the AX tree. Useful for 'what's in the window?'
        goals that need no clicks. Returns the markdown if it looks substantive."""
        if not any(k in goal.lower() for k in ("read", "what does", "what is shown", "extract", "value of")):
            return None
        try:
            state = driver.scan(pid, window_id)
        except driver.PermissionsError:
            raise
        tree = state.get("tree_markdown", "")
        if len(tree) > 200:
            return {"extracted_tree": tree[:4000], "element_count": state.get("element_count")}
        return None

    # ─── layer 2a: deterministic ───────────────────────────────────────
    def _run_deterministic(self, pid: int, window_id: int, goal: str,
                           bundle_id: str) -> Any:
        for recipe in _DETERMINISTIC_RECIPES:
            if recipe["bundle_id"] != bundle_id:
                continue
            if not any(kw in goal.lower() for kw in recipe["phrase_keywords"]):
                continue
            handler = getattr(self, recipe["handler"], None)
            if handler:
                return handler(pid, window_id, goal)
        return None

    def _run_calculator(self, pid: int, window_id: int, goal: str) -> Any:
        """Parse the arithmetic expression from `goal` and key it in.

        Supports digits, + - × * / ÷ . ( ) and an explicit '=' / 'equals'.
        Reads the AXStaticText display for the result.
        """
        import re
        # Extract a numeric expression like "7 * 8", "12.5 + 3", "(4+5)*6"
        expr_match = re.search(r"[\d\s\.\+\-\×x\*/÷\(\)]+", goal.replace("times", "*").replace("plus","+").replace("minus","-"))
        if not expr_match:
            return None
        expr = expr_match.group(0).strip()
        # Normalize symbols Calculator understands
        expr_keys: list[str] = []
        for ch in expr:
            if ch.isspace():
                continue
            if ch in "0123456789":
                expr_keys.append(ch)
            elif ch == "+":
                expr_keys.append("+")
            elif ch == "-":
                expr_keys.append("-")
            elif ch in "×x*":
                expr_keys.append("*")
            elif ch in "/÷":
                expr_keys.append("/")
            elif ch == ".":
                expr_keys.append(".")
            elif ch == "(":
                expr_keys.append("(")
            elif ch == ")":
                expr_keys.append(")")
        # Type the expression as a single string (Calculator accepts text input)
        try:
            driver.type_text("".join(expr_keys))
            self.turn_log.append({"turn": 1, "layer": "deterministic",
                                  "action": "type_text", "args": {"text": "".join(expr_keys)}})
            driver.press_key("Return")
            self.turn_log.append({"turn": 2, "layer": "deterministic",
                                  "action": "press_key", "args": {"key": "Return"}})
            time.sleep(0.4)
        except driver.CuaError as e:
            return None
        # Verify — read the display
        state = driver.scan(pid, window_id, query="AXStaticText")
        tree = state.get("tree_markdown", "")
        return {
            "expression": "".join(expr_keys),
            "display_after": tree[:2000],
            "raw_evaluated": str(_safe_eval("".join(expr_keys))),
        }

    # ─── layer 2b: a11y + judge ────────────────────────────────────────
    def _run_a11y(self, pid: int, window_id: int, goal: str,
                  electron_port: int | None) -> Any:
        """SCAN → cheap text LLM → ACT → VERIFY, loop up to MAX_TURNS."""
        last_action: dict | None = None
        for turn in range(1, self.MAX_TURNS + 1):
            try:
                state = driver.scan(pid, window_id, query="button OR text OR field OR menu")
            except driver.PermissionsError:
                raise
            tree = state.get("tree_markdown", "")
            if not tree:
                # tree is empty even though element_count > 0 — likely Electron.
                # Caller should have set electron_port; if not, escalate.
                return None

            user_msg = (
                f"USER_QUERY: {goal}\n\nTURN: {turn}\n\n"
                f"WINDOW:\n{tree[:3500]}\n\n"
                f"LAST_ACTION: {json.dumps(last_action) if last_action else 'null'}"
            )
            reply = self.llm.chat(
                system=self._prompt, user=user_msg,
                agent="computer_use:a11y", session=self.session,
            )
            text = reply.get("text", "").strip()
            try:
                judgment = json.loads(_extract_json(text))
            except Exception:
                judgment = {"verdict": "escalate", "rationale": f"unparseable LLM output: {text[:120]}"}
            self.turn_log.append({
                "turn": turn, "layer": "a11y",
                "judgment": judgment, "last_action": last_action,
            })
            verdict = judgment.get("verdict")
            if verdict == "done":
                return {"verdict": "done", "expected": judgment.get("expected", ""),
                        "final_tree": tree[:2000]}
            if verdict == "escalate":
                return None
            # verdict == act
            action = judgment.get("action") or {}
            tool = action.get("tool")
            args = action.get("args") or {}
            try:
                if tool == "click":
                    driver.click_index(pid, window_id, int(args["element_index"]))
                elif tool == "type_text":
                    driver.type_text(args.get("text", ""))
                elif tool == "press_key":
                    driver.press_key(args["key"], modifiers=args.get("modifiers"))
                elif tool == "hotkey":
                    keys = args.get("keys", [])
                    driver.call("hotkey", {"keys": keys})
                else:
                    return None
            except driver.CuaError as e:
                self.turn_log.append({"turn": turn, "layer": "a11y",
                                      "error": str(e)[:200]})
                return None
            last_action = action
            time.sleep(0.4)
        return None  # ran out of turns → escalate to vision

    # ─── layer 3: vision ───────────────────────────────────────────────
    def _run_vision(self, pid: int, window_id: int, goal: str) -> Any:
        """Screenshot the window, send to V9 vision, click by coords.

        For MVP this is a single-turn implementation: take ONE screenshot,
        let VLM emit one click coordinate, dispatch, scan to verify.
        """
        import base64
        png_path = self.artifacts_root / f"vision-{int(time.time())}.png"
        try:
            driver.screenshot(pid, window_id, str(png_path))
            img_bytes = png_path.read_bytes()
            image_b64 = base64.b64encode(img_bytes).decode("ascii")
        except Exception as e:
            return None

        system = (
            "You are LAYER 3 of a Computer-Use cascade. The accessibility tree "
            "was empty or unhelpful. Look at this screenshot and pick ONE click "
            "coordinate that advances the goal. Reply with strict JSON:\n"
            '  {"x": <int>, "y": <int>, "rationale": "<one short sentence>"}\n'
            "x and y are window-local pixels. Use integers."
        )
        user = f"Goal: {goal}\nReply with JSON only — no prose, no fences."
        try:
            reply = self.llm.vision(system=system, user=user, image_b64=image_b64,
                                    agent="computer_use:vision", session=self.session)
            text = reply.get("text", "")
            decision = json.loads(_extract_json(text))
        except Exception as e:
            return None
        x, y = int(decision["x"]), int(decision["y"])
        try:
            driver.click_xy(pid, window_id, x, y)
            self.turn_log.append({"turn": len(self.turn_log) + 1, "layer": "vision",
                                  "click_xy": [x, y], "rationale": decision.get("rationale", "")})
            time.sleep(0.5)
            state = driver.scan(pid, window_id)
            return {"verdict": "act_via_vision", "click": [x, y],
                    "after_tree": state.get("tree_markdown", "")[:1500]}
        except driver.CuaError:
            return None

    # ─── helpers ───────────────────────────────────────────────────────
    def _infer_bundle(self, goal: str) -> str:
        g = goal.lower()
        if "calculator" in g: return "com.apple.calculator"
        if "notes" in g: return "com.apple.notes"
        if "textedit" in g: return "com.apple.TextEdit"
        if "vscode" in g or "visual studio code" in g: return "com.microsoft.VSCode"
        if "safari" in g: return "com.apple.Safari"
        if "chrome" in g: return "com.google.Chrome"
        if "slack" in g: return "com.tinyspeck.slackmacgap"
        return "com.apple.calculator"  # default for demo


def _extract_json(text: str) -> str:
    """Pull the first {...} block out of a possibly-prosey LLM response."""
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1]
    return text


def _safe_eval(expr: str) -> float | None:
    """Evaluate a pure-arithmetic expression. None on any failure."""
    import ast, operator
    OPS = {
        ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.USub: operator.neg,
    }
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return None
    def ev(n):
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in OPS:
            return OPS[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in OPS:
            return OPS[type(n.op)](ev(n.operand))
        raise ValueError("unsafe node")
    try:
        return ev(tree.body)
    except Exception:
        return None
