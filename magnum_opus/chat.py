"""`magnum chat`: talk with the AI that works on your vault.

Ask what it sees, correct it, point it at an area, rearrange priorities, have
it close what is finished, and have it remember how you want things done. It
works on the vault through tools, under rules the code enforces:

- **It reads freely.** Queue, projects, status, open loops, search, notes and
  your guidance: the same read tools as `magnum serve`.
- **Every change asks you first.** Closing a loop, moving a note, reordering
  priorities and adding to GUIDANCE.md each show you exactly what will change
  and happen only if you say yes. A closed loop is ticked in its note as you
  would tick it; a moved note is marked as placed by you.
- **It never starts a full pass.** Re-sorting everything, recomputing
  convergence or writing a new thesis happen only when you run them. When one
  would help (a new model came out, the vault changed a lot), it says so and
  gives you the command.
- **Corrections stick.** What you confirm with `remember` goes into
  GUIDANCE.md, which every later step reads.
- **Cost is visible and capped.** The running cost is shown after every
  reply, and the chat stops before a message would pass `--max-cost`.

It speaks a plain text tool protocol (a JSON object in <tool> tags), so it
works the same with Claude and with any OpenAI-compatible model, local ones
included. Transcripts are kept in .magnum/chats/.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from . import brief as briefmod, guidance, proposals as props
from .agents import Server, ToolError
from .llm import response_text
from .vault import Vault, slugify

MAX_STEPS = 8
MAX_RESULT = 8000
CHARS_PER_TOKEN = 4.0
TOOL = re.compile(r"<tool>(.*?)</tool>", re.DOTALL)

READ_TOOLS = ("read_queue", "list_projects", "read_status", "list_open_loops", "search",
              "recall", "read_note", "read_guidance")

TOOL_DOCS = """\
Read (free, no confirmation):
- read_queue {}                      the person's top three priorities
- list_projects {}                   every project, in priority order
- read_status {"project": slug}      a project's open loops and decisions
- list_open_loops {"project"?: slug} open loops, each with an id (l-...)
- recall {"query": words, "project"?: slug}  the most relevant decisions, ideas and
                                     loops, each with an id, date and source link
- search {"query": words}            notes by content, with links to the original chats
- read_note {"path": path}           one note's full text
- read_guidance {}                   the person's standing instructions
Change (the person is asked to confirm each one):
- close_loop {"loop_id": id, "reason": why}       tick a finished loop in its note
- move_note {"path": path, "project": slug}       file a note under another project
- reorder {"order": [slug, ...]}                  set the priority order (listed first)
- remember {"text": one line}                     add a standing instruction to GUIDANCE.md
Other:
- suggest_pass {"kind": "sort"|"converge"|"thesis", "reason": why}
      show the person the command for a full pass; you never run one
- export_brief {}                    write BRIEF.md, a summary to hand to another assistant"""

SYSTEM = """You are the person's assistant inside Magnum Opus, their vault of notes \
distilled from their AI conversations and grouped into projects. Help them see, \
steer and finish their work.

How you work:
- Look before you answer: use the read tools. Never invent a note, project or loop.
- When you mention a note, include the link to its original conversation from the \
search results, so the person can find it.
- You change nothing on your own. To close a loop, move a note, reorder priorities \
or remember an instruction, call that tool: the person is asked to confirm.
- Never start a full pass (sort, converge, thesis). If one would help, use \
suggest_pass; the person decides whether to run it.
- When the person corrects you or states a preference that should last, offer to \
remember it.
- Follow the person's standing guidance below. Be brief and plain.

To use a tool, reply with only this and nothing after it:
<tool>{{"name": "<tool>", "args": {{...}}}}</tool>
You will get the result and can continue. Otherwise, reply to the person.

TOOLS:
{tools}

THE PERSON'S STANDING GUIDANCE:
{guidance}

PROJECTS NOW:
{projects}
"""

PASSES = {"sort": "magnum sort --vault {vault}",
          "converge": "magnum converge --vault {vault}",
          "thesis": "magnum thesis --vault {vault}"}


class BudgetReached(Exception):
    pass


class Chat:
    def __init__(self, vault_root, client, model: str, confirm, show=print,
                 max_cost: float = 1.0, input_rate: float = 5.0, output_rate: float = 25.0,
                 max_tokens: int = 2000):
        self.root = vault_root
        self.client, self.model = client, model
        self.confirm, self.show = confirm, show
        self.max_cost, self.max_tokens = max_cost, max_tokens
        self.input_rate, self.output_rate = input_rate, output_rate
        self.spent = 0.0
        self.history = []
        self.reader = Server(vault_root)
        self.log = (Vault(vault_root).root / ".magnum" / "chats"
                    / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".md"))

    # --- context ------------------------------------------------------------------

    def system(self) -> str:
        v = self._vault()
        try:
            projects = self.reader.list_projects({})
        except ToolError as e:
            projects = str(e)
        return SYSTEM.format(tools=TOOL_DOCS,
                             guidance=guidance.read(v) or "(none yet)",
                             projects=projects)

    def _vault(self) -> Vault:
        v = Vault(self.root)
        v.sync_from_disk()
        return v

    # --- cost ------------------------------------------------------------------------

    def _cost(self, in_chars: int, out_chars: int) -> float:
        return (in_chars / CHARS_PER_TOKEN / 1e6 * self.input_rate
                + out_chars / CHARS_PER_TOKEN / 1e6 * self.output_rate)

    def _worst_case(self, system: str) -> float:
        chars = len(system) + sum(len(m["content"]) for m in self.history)
        return self._cost(chars, 0) + self.max_tokens / 1e6 * self.output_rate

    # --- one exchange ----------------------------------------------------------------

    def turn(self, text: str) -> str:
        """The person says something; returns the assistant's reply to them."""
        self.history.append({"role": "user", "content": text})
        self._log("you", text)
        for _ in range(MAX_STEPS):
            system = self.system()
            if self.spent + self._worst_case(system) > self.max_cost:
                raise BudgetReached(
                    f"The next message could pass your limit of ${self.max_cost:.2f} "
                    f"(spent about ${self.spent:.2f}). Start again with a higher "
                    "--max-cost if you want to continue.")
            resp = self.client.messages.create(model=self.model, max_tokens=self.max_tokens,
                                               system=system, messages=list(self.history))
            reply = response_text(resp)
            self.spent += self._cost(len(system) + sum(len(m["content"]) for m in self.history),
                                     len(reply))
            self.history.append({"role": "assistant", "content": reply})
            m = TOOL.search(reply)
            if not m:
                self._log("assistant", reply)
                return reply.strip()
            before = reply[:m.start()].strip()
            if before:
                self.show(before)
            result = self.run_tool(m.group(1))
            self._log("tool", f"{m.group(1)}\n→ {result}")
            self.history.append({"role": "user",
                                 "content": f"<result>\n{result[:MAX_RESULT]}\n</result>"})
        self._log("assistant", "(stopped after too many steps)")
        return ("I took too many steps on that without finishing. "
                "Could you ask again, more narrowly?")

    # --- tools -------------------------------------------------------------------------

    def run_tool(self, raw: str) -> str:
        try:
            call = json.loads(raw)
            name, args = call.get("name"), call.get("args") or {}
            if not isinstance(args, dict):
                raise ValueError("args must be an object")
        except (ValueError, AttributeError) as e:
            return f"Error: the tool call was not valid JSON ({e})."
        try:
            if name in READ_TOOLS:
                return getattr(self.reader, name)(args)
            action = getattr(self, f"_do_{name}", None) if isinstance(name, str) else None
            if action is None:
                return f"Error: there is no tool {name!r}."
            return action(args)
        except ToolError as e:
            return f"Error: {e}"
        except (KeyError, TypeError, ValueError) as e:
            return f"Error: bad arguments ({e})."

    def _ask(self, question: str) -> bool:
        ok = bool(self.confirm(question))
        self._log("you", f"{question} → {'yes' if ok else 'no'}")
        return ok

    def _do_close_loop(self, args):
        v = self._vault()
        loop = next((l for l in props.open_loops(v) if l["id"] == str(args["loop_id"])), None)
        if not loop:
            return "Error: no open loop with that id. Use list_open_loops."
        reason = " ".join(str(args.get("reason", "")).split())[:500]
        if not self._ask(f"Close “{loop['text']}” [{loop['project']}]?"
                         + (f"\n  why: {reason}" if reason else "")):
            return "The person said no. The loop stays open."
        if not v.close_loop(loop["key"], loop["text"]):
            return "The loop is no longer open; nothing changed."
        state = props.load_state(v)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        pid = "p-" + loop["id"][2:8]
        state["proposals"][pid] = {
            "loop_id": loop["id"], "segment_key": loop["key"], "loop": loop["text"],
            "project": loop["project"], "action": "close", "reason": reason,
            "evidence": [], "by": "agent:chat", "at": now, "status": "applied",
            "decided_at": now, "decided_by": "human", "applied_at": now}
        v.rebuild_rollups()
        v.save()
        props.refresh(v, state)
        return f"Closed “{loop['text']}” (the person confirmed)."

    def _do_move_note(self, args):
        v = self._vault()
        rel = str(args["path"]).strip()
        rel = rel if rel.endswith(".md") else rel + ".md"
        key = next((k for k, s in v.state["segments"].items() if s.get("path") == rel), None)
        if not key:
            return "Error: no note at that path. Use search to find its path."
        slug = slugify(str(args["project"]))
        rec = next(r for r in v.state["notes"] if r["segment_key"] == key)
        if slugify(rec.get("project", "")) == slug:
            return f"That note is already in {slug}."
        if not self._ask(f"Move “{rec.get('title', rel)}” from {rec.get('project')} to {slug}?"):
            return "The person said no. The note stays where it is."
        v.move_note(key, slug, human=True)
        v.rebuild_rollups()
        v.save()
        return f"Moved to {slug}, marked as placed by the person."

    def _do_reorder(self, args):
        v = self._vault()
        meta = v.config.setdefault("project_meta", {})
        known = {slugify(r.get("project", "")) for r in v.state["notes"]}
        order = [slugify(x) for x in args["order"]]
        unknown = [x for x in order if x not in known]
        if unknown or not order:
            return f"Error: unknown projects {unknown}. Known: {sorted(known)}"
        rest = sorted((p for p in known if p not in order),
                      key=lambda p: (meta.get(p, {}).get("rank", 10 ** 6), p))
        final = order + rest
        if not self._ask("Set the priority order to:\n"
                         + "\n".join(f"  {i}. {p}" for i, p in enumerate(final, 1))):
            return "The person said no. The order is unchanged."
        for i, p in enumerate(final):
            m = meta.setdefault(p, {"description": "", "status": "active",
                                    "next_action": "", "rank_reason": ""})
            m["rank"] = i
            if p in order:
                m["rank_reason"] = "you set this order"
        v.rebuild_rollups()
        v.save()
        return "Priorities reordered; QUEUE.md and PRIORITIES.md are updated."

    def _do_remember(self, args):
        text = " ".join(str(args["text"]).split())[:300]
        if not text:
            return "Error: nothing to remember."
        if not self._ask(f"Add to GUIDANCE.md: “{text}”?"):
            return "The person said no. Nothing was added."
        guidance.append(self._vault(), text)
        return "Added to GUIDANCE.md; every later step will follow it."

    def _do_suggest_pass(self, args):
        kind = str(args.get("kind", ""))
        if kind not in PASSES:
            return f"Error: kind must be one of {sorted(PASSES)}."
        cmd = PASSES[kind].format(vault=self.root)
        why = " ".join(str(args.get("reason", "")).split())[:300]
        self.show(f"[suggested, not run] {cmd}" + (f"\n  why: {why}" if why else ""))
        return ("Shown to the person. It runs only if they run it themselves; "
                "it may cost money and replaces the current result.")

    def _do_export_brief(self, args):
        path = briefmod.write(self._vault())
        return f"Wrote {path.name}. The person can hand it to another assistant."

    # --- record -------------------------------------------------------------------------

    def _log(self, who: str, text: str):
        self.log.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log, "a", encoding="utf-8") as fh:
            fh.write(f"**{who}:** {text}\n\n")
