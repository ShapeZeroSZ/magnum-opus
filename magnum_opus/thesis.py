"""Emergent thesis: what this body of work appears to be about, with evidence.

One model call reads your notes, your projects and the relationships
`magnum converge` proposed (and you accepted), and says what the work as a
whole appears to be about. The model judges; the code enforces:

- **Every claim cites notes that exist.** The model sees notes under short
  ids and must cite them. Code maps each id back to a note in the vault; a
  claim left with no real citation is dropped, and the file says how many
  were. A thesis statement with no real citation writes nothing at all.
- **Time is provenance, never evidence.** Dates and times are stripped from
  what the model sees, and it is told not to reason from when things were
  written.
- **Your judgment constrains every later version.** Tick accept or reject
  under a claim. Accepted claims are kept in every later version; rejected
  ones are never repeated (checked in code, as well as told to the model).
  Untick to change your mind. Relationships you rejected in CONVERGENCE.md
  are never sent.
- **Versioned, and a lens, not a blender.** EMERGENT_THESIS.md is the
  current version; every version is kept in .magnum/thesis/. No note is
  moved, merged or edited.
- **Cost is visible before it is incurred.** The CLI shows what will be sent,
  where, and the worst-case cost, and asks first. `--dry-run` writes the
  exact prompt to .magnum/thesis/prompt.txt and sends nothing.

Relationships that involve files outside the vault (`converge --external`)
are not sent: those files were only ever read locally.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from . import converge as conv
from .sort import _call_json

DEFAULT_MAX_NOTES = 120
DEFAULT_MAX_CLAIMS = 7
DEFAULT_MAX_TOKENS = 4000
ITEMS_PER_KIND = 3
CHARS_PER_TOKEN = 4.0
# One call where quality matters, so the default model is a large one. These
# are list prices of that class of model; check yours and pass your own rates.
DEFAULT_INPUT_RATE = 5.0
DEFAULT_OUTPUT_RATE = 25.0

THESIS_BLOCK = re.compile(r"<!--thesis:(?P<id>t-[0-9a-f]{6})-->")
MONTHS = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|"
          r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
DATES = re.compile(
    conv.DATE_TEXT.pattern
    + rf"|\b{MONTHS}\.? \d{{1,2}}(?:st|nd|rd|th)?\b(?:,? \d{{4}})?"
    + rf"|\b\d{{1,2}}(?:st|nd|rd|th)? {MONTHS}\b(?:,? \d{{4}})?"
    + r"|\b(?:mon|tues|wednes|thurs|fri|satur|sun)day\b"
    + r"|\b(?:today|yesterday|tomorrow|tonight)\b", re.IGNORECASE)
# The words a removed date leaves stranded: "on <date>" -> "".
ORPHANS = re.compile(r"\b(?:on|at|by|from|until|since|of|in)\s*(?=\x00)", re.IGNORECASE)

PROMPT = """You are reading someone's body of work: short notes distilled from \
their AI conversations, grouped into projects, and the relationships between \
projects that a similarity pass found. Say what this body of work, taken as a \
whole, appears to be about: the through-line they may not have named yet, and \
the few claims that best support or complicate it.

Rules:
- Every claim cites the note ids (n1, n2, ...) it rests on. Cite only ids listed \
below. Leave out any claim the notes do not support.
- Judge by content only. When something was written is not evidence of anything.
- Describe; do not prescribe. Do not tell them what to do, and do not propose \
merging or restructuring their projects.
- Relationships marked CONFIRMED were confirmed by the person; PROPOSED ones \
have not been judged yet.
- Stay consistent with claims the person confirmed. Never assert a claim the \
person rejected, or one that means the same.
- At most {max_claims} claims, each one plain sentence.

Respond with ONLY a JSON object, no markdown fences:
{{"statement": "<two or three sentences: what this body of work appears to be about>",
 "statement_cites": ["n1", "..."],
 "claims": [{{"text": "<one sentence>", "cites": ["n3", "n7"]}}]}}

PROJECTS:
{projects}

NOTES ({n_notes}):
{notes}

RELATIONSHIPS BETWEEN PROJECTS:
{relationships}

CLAIMS THE PERSON CONFIRMED:
{confirmed}

CLAIMS THE PERSON REJECTED:
{rejected}
"""


def _norm(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(text).lower()))


def plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def claim_id(text: str) -> str:
    return "t-" + hashlib.sha1(_norm(text).encode()).hexdigest()[:6]


def _clean(text, limit: int = 300) -> str:
    """One line, dates and times removed, bounded."""
    text = re.sub(r"\s+", " ", str(text).replace("\x00", "")).strip()
    marked = DATES.sub("\x00", text)
    if marked != text:
        # Tidy only what removing a date left behind: "On , at ." -> "."
        text = ORPHANS.sub("", marked)
        text = re.sub(r"\s*\x00(?:[\s,]*\x00)*", " ", text)
        text = re.sub(r"\(\s*\)", "", text)
        text = re.sub(r"\s+([,.;:)])", r"\1", text)
        text = re.sub(r"([,;:])\s*(?=[,.;:)]|$)", "", text)
        text = re.sub(r"(^|[.!?]\s*)[,;:]\s*", r"\1", text)
        text = re.sub(r"\.(\s*\.)+", ".", text)
        text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


# --- state ------------------------------------------------------------------------

def state_path(vault):
    return vault.root / ".magnum" / "thesis.json"


def load_state(vault) -> dict:
    p = state_path(vault)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"versions": [], "feedback": {}}


def record_ticks(vault, state) -> list:
    """Read accept/reject ticks from EMERGENT_THESIS.md into state["feedback"],
    keeping each decided claim's text so it outlives the version it came from."""
    known = {}
    for v in state["versions"][-1:]:
        for c in v["claims"]:
            known[c["id"]] = {"text": c["text"], "cites": c["cites"]}
    for cid, entry in state["feedback"].items():
        known[cid] = {"text": entry.get("text", ""), "cites": entry.get("cites", [])}
    ticks = conv.read_ticks(vault.root / "EMERGENT_THESIS.md", THESIS_BLOCK)
    ticks = {cid: t for cid, t in ticks.items() if cid in known}
    changes = conv.apply_ticks(state, ticks)
    for cid, entry in state["feedback"].items():
        entry.setdefault("text", known.get(cid, {}).get("text", ""))
        entry.setdefault("cites", known.get(cid, {}).get("cites", []))
    return changes


def _decided(state, decision) -> list:
    return [dict(e, id=cid) for cid, e in sorted(state["feedback"].items())
            if e.get("decision") == decision]


# --- what is sent -----------------------------------------------------------------

@dataclass
class Prepared:
    prompt: str
    ids: dict                    # short id -> note key
    docs: dict                   # note key -> converge.Doc
    relationships: list
    confirmed: list
    rejected: list
    skipped_external: int = 0
    notes_total: int = 0

    @property
    def input_tokens(self) -> int:
        return int(len(self.prompt) / CHARS_PER_TOKEN)


def _relationships(vault) -> tuple:
    """The last converge pass's relationships, with ticks made since then
    honoured: a relationship you rejected is never sent."""
    cstate = conv.load_state(vault)
    ticks = conv.read_ticks(vault.root / "CONVERGENCE.md")
    out, skipped = [], 0
    for rel in cstate.get("latest", []):
        decision = ticks.get(rel["id"], rel.get("decision", "proposed"))
        if decision in ("rejected", "conflict"):
            continue
        if decision is None:
            decision = "proposed"
        if any(k.startswith("external:") for pair in rel["evidence"] for k in pair[:2]):
            skipped += 1
            continue
        out.append(dict(rel, decision=decision))
    out.sort(key=lambda r: (r["decision"] != "accepted", -r["score"], r["id"]))
    return out, skipped


def _weight(rec) -> int:
    return sum(len(rec.get(k, [])) for k in ("decisions", "ideas", "open_loops"))


def select_notes(vault, rels, max_notes: int) -> list:
    """Which notes to send, deterministically: the evidence of the
    relationships first, then every project in turn, fullest notes first."""
    recs = {r["segment_key"]: r for r in vault.state["notes"]}
    chosen, seen = [], set()

    def add(key):
        if key in recs and key not in seen:
            seen.add(key)
            chosen.append(key)

    for rel in rels:
        for a, b, _ in rel["evidence"]:
            add(a)
            add(b)
    meta = vault.config.get("project_meta", {})
    groups = {}
    for rec in vault.state["notes"]:
        groups.setdefault(rec.get("project", ""), []).append(rec)
    for g in groups.values():
        g.sort(key=lambda r: (-_weight(r), r["segment_key"]))
    order = sorted(groups, key=lambda p: (meta.get(p, {}).get("rank", 10 ** 6), p))
    depth = 0
    while any(depth < len(groups[p]) for p in order):
        for p in order:
            if depth < len(groups[p]):
                add(groups[p][depth]["segment_key"])
        depth += 1
    return chosen[:max_notes]


def _note_line(sid, rec, doc) -> str:
    parts = [f"{sid} [{doc.group_label}] {_clean(doc.title, 120)}"]
    if rec.get("summary"):
        parts.append(_clean(rec["summary"]))
    for kind, label in (("decisions", "decided"), ("ideas", "idea"), ("open_loops", "open")):
        items = [i for i in rec.get(kind, []) if isinstance(i, dict) and not i.get("done")]
        for item in items[:ITEMS_PER_KIND]:
            parts.append(f"{label}: {_clean(item.get('text', ''), 160)}")
    return " | ".join(p for p in parts if p)


def prepare(vault, state, max_notes=DEFAULT_MAX_NOTES,
            max_claims=DEFAULT_MAX_CLAIMS) -> Prepared:
    docs = {d.key: d for d in conv.vault_documents(vault)}
    rels, skipped = _relationships(vault)
    keys = select_notes(vault, rels, max_notes)
    ids = {f"n{i + 1}": k for i, k in enumerate(keys)}
    sid_of = {k: s for s, k in ids.items()}
    recs = {r["segment_key"]: r for r in vault.state["notes"]}

    meta = vault.config.get("project_meta", {})
    projects = sorted({docs[k].group_label for k in keys if not docs[k].group.startswith("note:")})
    project_lines = [f"- {p}: {_clean(meta.get(p, {}).get('description', ''), 160)}"
                     .rstrip(": ") for p in projects] or ["(none yet: notes are unsorted)"]

    sent_rels, rel_lines = [], []
    for rel in rels:
        pairs = [(sid_of[a], sid_of[b]) for a, b, _ in rel["evidence"]
                 if a in sid_of and b in sid_of]
        if not pairs:
            continue
        sent_rels.append(rel)
        tag = "CONFIRMED" if rel["decision"] == "accepted" else "PROPOSED"
        rel_lines.append(f"- {tag} {rel['labels'][0]} <-> {rel['labels'][1]}: rests on "
                         + ", ".join(f"{a}~{b}" for a, b in pairs))

    confirmed, rejected = _decided(state, "accepted"), _decided(state, "rejected")
    prompt = PROMPT.format(
        max_claims=max_claims,
        projects="\n".join(project_lines),
        n_notes=len(keys),
        notes="\n".join(_note_line(s, recs[k], docs[k]) for s, k in ids.items()),
        relationships="\n".join(rel_lines) or "(none: run `magnum converge` to add them)",
        confirmed="\n".join(f"- {c['text']}" for c in confirmed) or "(none)",
        rejected="\n".join(f"- {c['text']}" for c in rejected) or "(none)")
    return Prepared(prompt=prompt, ids=ids, docs=docs, relationships=sent_rels,
                    confirmed=confirmed, rejected=rejected, skipped_external=skipped,
                    notes_total=len(vault.state["notes"]))


def estimate(prep: Prepared, max_tokens: int, input_rate: float, output_rate: float) -> dict:
    """Worst case: every output token the call is allowed."""
    return {"input_tokens": prep.input_tokens, "max_output_tokens": max_tokens,
            "max_cost": prep.input_tokens / 1e6 * input_rate
            + max_tokens / 1e6 * output_rate}


# --- checking what comes back ---------------------------------------------------------

def _cites(raw, prep: Prepared) -> list:
    out = []
    for c in raw if isinstance(raw, list) else []:
        key = prep.ids.get(str(c).strip())
        if key and key not in out:
            out.append(key)
    return out


def verify(data, prep: Prepared, max_claims: int) -> dict:
    """Keep only what the notes support. Raises if the statement has no real
    citation, so a baseless thesis is never written."""
    if not isinstance(data, dict):
        raise RuntimeError("The model did not return a thesis object.")
    statement = _clean(data.get("statement", ""), 800)
    statement_cites = _cites(data.get("statement_cites"), prep)
    if not statement or not statement_cites:
        raise RuntimeError("The model's thesis cited no note that exists. "
                           "Nothing was written; the previous version is kept.")
    rejected = {_norm(c["text"]) for c in prep.rejected}
    confirmed = {_norm(c["text"]) for c in prep.confirmed}
    claims, dropped, seen = [], {"uncited": 0, "rejected": 0, "over_limit": 0}, set()
    for raw in data.get("claims") or []:
        if not isinstance(raw, dict):
            continue
        text = _clean(raw.get("text", ""), 400)
        cites = _cites(raw.get("cites"), prep)
        if not text or _norm(text) in seen:
            continue
        seen.add(_norm(text))
        if not cites:
            dropped["uncited"] += 1
        elif _norm(text) in rejected:
            dropped["rejected"] += 1      # never repeated, whatever the model was told
        elif _norm(text) in confirmed:
            continue                      # already kept, under "Confirmed by you"
        elif len(claims) >= max_claims:
            dropped["over_limit"] += 1
        else:
            claims.append({"id": claim_id(text), "text": text, "cites": cites})
    return {"statement": statement, "statement_cites": statement_cites,
            "claims": claims, "dropped": dropped}


# --- the generated view -------------------------------------------------------------

def _link(key, docs) -> str:
    d = docs.get(key)
    return conv.found_at(d) if d and d.link else "(a note no longer in the vault)"


def _evidence(keys, docs) -> list:
    return ["- evidence:"] + [f"  - {_link(k, docs)}" for k in keys]


def _across(cites, docs) -> list:
    return sorted({docs[k].group_label for k in cites
                   if k in docs and not docs[k].group.startswith("note:")})


def _claim_block(c, docs, decision=None) -> list:
    lines = [f"### {c['text']} <!--thesis:{c['id']}-->"]
    across = _across(c.get("cites", []), docs)
    if len(across) > 1:
        lines.append(f"across {', '.join(across)}")
    live = [k for k in c.get("cites", []) if k in docs]
    if live:
        lines += _evidence(live, docs)
    else:
        lines.append("- evidence: its notes are no longer in the vault")
    lines.append(f"- [{'x' if decision == 'accepted' else ' '}] accept")
    lines.append(f"- [{'x' if decision == 'rejected' else ' '}] reject")
    return lines + [""]


def render(version: dict, state: dict, docs: dict) -> str:
    n = version["version"]
    d = version["dropped"]
    lines = ["# EMERGENT THESIS — what your work appears to be about", "",
             f"_Version {n}, generated {version['at']} by `magnum thesis` "
             f"({version['model']}) from {version['notes_sent']} of "
             f"{version['notes_total']} notes and "
             f"{plural(version['relationships_sent'], 'relationship')}. An output to react to, not a plan: nothing was moved, "
             "merged or edited, and every claim cites notes that exist. Tick "
             "**accept** or **reject** under a claim; later versions keep what you "
             "accept and never repeat what you reject."
             + (" Earlier versions are in `.magnum/thesis/`." if n > 1 else "") + "_",
             "", "> " + version["statement"], ""]
    lines += _evidence(version["statement_cites"], docs)
    lines += ["", "## Claims", ""]
    for c in version["claims"]:
        lines += _claim_block(c, docs)
    if not version["claims"]:
        lines += ["_No new claims this version._", ""]
    notes = []
    if d.get("uncited"):
        notes.append(f"{plural(d['uncited'], 'claim')} dropped because "
                     f"{'it' if d['uncited'] == 1 else 'they'} cited no note that exists")
    if d.get("rejected"):
        notes.append(f"{d['rejected']} dropped because you had rejected them")
    if d.get("over_limit"):
        notes.append(f"{d['over_limit']} over the limit not shown")
    if notes:
        lines += ["_" + "; ".join(notes) + "._", ""]
    acc, rej = _decided(state, "accepted"), _decided(state, "rejected")
    if acc:
        lines += ["## Confirmed by you (kept in every version)", ""]
        for c in acc:
            lines += _claim_block(c, docs, "accepted")
    if rej:
        lines += ["## Rejected by you (never repeated; untick to reconsider)", ""]
        for c in rej:
            lines += _claim_block(c, docs, "rejected")
    return "\n".join(lines).rstrip() + "\n"


# --- one pass ---------------------------------------------------------------------------

def write_dry_run(vault, prep: Prepared):
    path = vault.root / ".magnum" / "thesis" / "prompt.txt"
    vault._atomic_write(path, prep.prompt)
    return path


def thesis(vault, client, model: str, prep: Prepared, state: dict,
           max_claims: int = DEFAULT_MAX_CLAIMS,
           max_tokens: int = DEFAULT_MAX_TOKENS) -> dict:
    """Send the prepared prompt, keep what the notes support, write the new
    version. Nothing is written if the call fails or the statement is baseless."""
    data = _call_json(client, model, prep.prompt, max_tokens,
                      vault.root / ".magnum" / "thesis_raw.txt")
    checked = verify(data, prep, max_claims)
    version = {"version": len(state["versions"]) + 1,
               "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "model": model, "notes_sent": len(prep.ids),
               "notes_total": prep.notes_total,
               "relationships_sent": len(prep.relationships), **checked}
    text = render(version, state, prep.docs)
    state["versions"].append(version)
    vault._atomic_write(vault.root / ".magnum" / "thesis" / f"v{version['version']}.md", text)
    vault._atomic_write(vault.root / "EMERGENT_THESIS.md", text)
    vault._atomic_write(state_path(vault), json.dumps(state, indent=2))
    return version
