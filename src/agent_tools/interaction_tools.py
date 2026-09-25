import json
import logging

from src.companion_capabilities import ALL_CAPABILITIES, capability_label, grant_for_tool

logger = logging.getLogger(__name__)

class AskUserTool:
    async def execute(self, content, ctx):
        """
        ask_user: the agent poses a multiple-choice question to the user to get a
        decision/clarification. This is a pure UI-control marker — no subprocess,
        no filesystem. It returns an `ask_user` payload that the agent loop turns
        into an `ask_user` SSE event and then ENDS the turn, so the chat waits for
        the user's selection (their choice arrives as the next message).
        """
        question, options, multi = "", [], False
        raw = (content or "").strip()
        try:
            parsed = json.loads(raw) if raw else {}
        except (ValueError, TypeError):
            parsed = {}

        if isinstance(parsed, dict):
            question = str(parsed.get("question", "")).strip()
            multi = bool(parsed.get("multi") or parsed.get("multiSelect"))
            for opt in (parsed.get("options") or []):
                if isinstance(opt, dict):
                    label = str(opt.get("label", "")).strip()
                    descr = str(opt.get("description", "")).strip()
                elif isinstance(opt, str):
                    label, descr = opt.strip(), ""
                else:
                    continue
                if label:
                    options.append({"label": label, "description": descr})
        else:
            question = raw

        if not question or len(options) < 2:
            return "ask_user: invalid", {
                "error": (
                    "ask_user needs a non-empty `question` and at least 2 `options` "
                    "(each an object with a `label`, optional `description`)."
                ),
                "exit_code": 1,
            }

        options = options[:6]  # keep the choice list sane
        desc = f"ask_user: {question[:80]}"
        labels = ", ".join(o["label"] for o in options)
        result = {
            "ask_user": {"question": question, "options": options, "multi": multi},
            "output": f"Asked the user: {question}\nOptions: {labels}\nAwaiting their selection.",
            "exit_code": 0,
        }
        logger.info("Tool executed: %s (%d options, multi=%s)", desc, len(options), multi)
        return desc, result


class RequestCapabilityTool:
    """Ask for a fixed chat capability without granting it from model text."""

    async def execute(self, content, ctx):
        raw = (content or "").strip()
        try:
            parsed = json.loads(raw) if raw else {}
        except (ValueError, TypeError):
            parsed = {}
        name = str(parsed.get("name") or "").strip() if isinstance(parsed, dict) else ""
        reason = str(parsed.get("reason") or "").strip() if isinstance(parsed, dict) else ""
        # Models often name the tool they want rather than the grant behind it.
        name = grant_for_tool(name) or name
        if name not in ALL_CAPABILITIES:
            return "request_capability: invalid", {
                "error": "Unknown chat capability. Use one of: " + ", ".join(sorted(ALL_CAPABILITIES)),
                "exit_code": 1,
            }
        label = capability_label(name)
        question = f"Enable {label} for this chat?"
        if reason:
            question += f" {reason[:500]}"
        return f"request_capability: {name}", {
            "ask_user": {
                "kind": "capability_request",
                "capability": name,
                "enable_label": f"Enable {label}",
                "question": question,
                "options": [
                    {"label": f"Enable {label}", "description": "Enable it for this chat and continue."},
                    {"label": "Not now", "description": "Keep this capability off."},
                ],
                "multi": False,
            },
            "output": f"Requested {label} for this chat.",
            "exit_code": 0,
        }

class UpdatePlanTool:
    async def execute(self, content, ctx):
        """
        update_plan: the agent writes back to the active plan — tick an item done
        or revise steps (e.g. when the user asks to change something). Pure UI
        marker: returns a `plan_update` payload the agent loop turns into a
        `plan_update` SSE event; the frontend replaces the stored plan and refreshes
        the docked plan window. Does NOT end the turn.
        """
        raw = (content or "").strip()
        plan = ""
        try:
            parsed = json.loads(raw) if raw else {}
        except (ValueError, TypeError):
            parsed = {}

        if isinstance(parsed, dict) and parsed.get("plan"):
            plan = str(parsed.get("plan", "")).strip()
        else:
            plan = raw

        if not plan:
            return "update_plan: invalid", {
                "error": "update_plan needs a non-empty `plan` (the full updated checklist as markdown).",
                "exit_code": 1,
            }

        plan = plan[:8192]
        done = plan.count("- [x]") + plan.count("- [X]")
        total = done + plan.count("- [ ]")
        desc = f"update_plan: {done}/{total} done" if total else "update_plan"
        result = {
            "plan_update": {"plan": plan},
            "output": f"Plan updated ({done}/{total} steps complete)." if total else "Plan updated.",
            "exit_code": 0,
        }
        logger.info("Tool executed: %s", desc)
        return desc, result
