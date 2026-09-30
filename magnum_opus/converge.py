"""Convergence: how a body of work relates to itself, proposed on content alone.

Rules this module is built to (SPEC §4, MANIFESTO 7):

- **A lens, never a blender.** It reads notes and writes exactly two things:
  CONVERGENCE.md (a generated view) and .magnum/convergence.json (your
  feedback). It never moves, merges or edits a note. Delete both files and
  the vault works exactly as before.
- **Time is provenance, never evidence.** Dates, times, weekday and month
  names and bare numbers are stripped before comparison, so two notes are
  never "related" because they were written the same week.
- **Between projects, not within them.** Projects stay independent; only
  relationships that cross a project boundary are proposed.
- **The default view is small.** A handful of proposals; the rest one flag
  deeper.
- **Your judgment constrains every later pass.** Tick accept or reject in
  CONVERGENCE.md (in Obsidian or anywhere). Rejected connections are never
  proposed again; accepted ones stay pinned; untick to change your mind.

Backends, all content-only:
    builtin   TF-IDF over note text. No dependencies, deterministic, and it
              explains itself: every proposal lists the words it rests on.
    local     sentence-transformers on your machine (pip install
              "magnum-opus[converge]"). Better at meaning than wording.
    openai    embeddings from any OpenAI-compatible server (/v1/embeddings),
              e.g. a local Ollama. Asks before sending anything.

Scores are similarity heuristics, not probabilities; `--min-score` is a floor,
not a calibration.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .reindex import FRONT, GENERATED, normalize

DEFAULT_TOP = 5
DEFAULT_MIN_SCORE = {"builtin": 0.08, "local": 0.35, "openai": 0.35}
EVIDENCE_PAIRS = 3

# --- time is provenance -------------------------------------------------------

DATE_TEXT = re.compile(
    r"\d{4}-\d{2}-\d{2}(?:[t ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:z|[+-]\d{2}:?\d{2})?)?"
    r"|\b\d{1,2}/\d{1,2}/\d{2,4}\b|\b\d{1,2}:\d{2}(?::\d{2})?\s*(?:am|pm)?\b")
TIME_WORDS = set("""january february march april may june july august september
october november december jan feb mar apr jun jul aug sep sept oct nov dec monday
tuesday wednesday thursday friday saturday sunday mon tue tues wed thu thur thurs
fri sat sun today yesterday tomorrow tonight morning afternoon evening night
week weeks weekly month months monthly year years yearly daily day days hour
hours minute minutes ago recently lately soon""".split())
STOP = set("""the and for with that this from have has had was were are been being
not but you your yours our ours they them their its it's into onto about over under
than then when what which who whom whose why how all any each few more most other
some such only own same too very can will just should could would might must shall
also there here where while because until again further once both between through
during before after above below off out upon also like want need make made get got
use used using one two three first second new old way thing things lot lots really
yes okay ok let lets i'm i've we're don't doesn't didn't can't won't it isn't""".split())
TOKEN = re.compile(r"[a-z][a-z0-9]*(?:['\-][a-z0-9]+)*")


def terms(text: str) -> list:
    """Content terms of a text, with every trace of time removed."""
    text = DATE_TEXT.sub(" ", text.lower())
    return [t for t in TOKEN.findall(text)
            if len(t) >= 3 and t not in STOP and t not in TIME_WORDS]


# --- documents ------------------------------------------------------------------

UNPLACED = {"unsorted", "inbox", "misc", ""}


@dataclass
class Doc:
    key: str          # stable identity (segment key or external path)
    title: str
    group: str        # project slug, or a per-document group when unplaced
    group_label: str
    link: str         # wikilink target inside the vault, or "" if outside it
    text: str
    origin: str       # "note" | "external"
    source: str = ""  # the conversation a note came from ("" for external files)


def _note_text(rec: dict) -> str:
    parts = [rec.get("title", ""), rec.get("topic", ""), rec.get("summary", "")]
    for k in ("decisions", "ideas", "open_loops"):
        parts += [i.get("text", "") for i in rec.get(k, []) if isinstance(i, dict)]
    parts += [str(l) for l in rec.get("links", [])]
    return "\n".join(p for p in parts if p)


def vault_documents(vault) -> list:
    vault.sync_from_disk()
    docs = []
    for rec in vault.state["notes"]:
        key = rec["segment_key"]
        path = vault.state["segments"].get(key, {}).get("path") or ""
        project = rec.get("project", "")
        placed = project not in UNPLACED
        title = rec.get("title", "") or key
        part = rec.get("label", "")
        name = f"{title}: {part}" if part and part != title else title
        docs.append(Doc(
            key=key, title=name,
            group=project if placed else f"note:{key}",
            group_label=project if placed else f"“{name}” (unsorted)",
            link=path[:-3] if path.endswith(".md") else path,
            text=_note_text(rec), origin="note",
            source=rec.get("conversation_id", "")))
    return docs


def external_documents(root, vault_root=None) -> list:
    """Markdown files from another folder (e.g. an existing Obsidian vault),
    read-only. Frontmatter is dropped; the body is the content."""
    root = Path(root).resolve()
    inside = None
    if vault_root is not None:
        try:
            root.relative_to(Path(vault_root).resolve())
            inside = Path(vault_root).resolve()
        except ValueError:
            inside = None
    docs = []
    for path in sorted(root.rglob("*.md")):
        if path.name in GENERATED or {".obsidian", ".magnum", ".trash"} & set(path.parts):
            continue
        try:
            text = normalize(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
        m = FRONT.match(text)
        if m and "conversation_id:" in m.group(1):
            continue                     # a Magnum Opus note: already a document
        body = FRONT.sub("", text, count=1)
        rel = path.relative_to(root).as_posix()
        link = path.relative_to(inside).as_posix()[:-3] if inside else ""
        docs.append(Doc(key=f"external:{path}", title=path.stem,
                        group=f"external:{path}", group_label=f"{rel} (your notes)",
                        link=link, text=f"{path.stem}\n{body}", origin="external"))
    return docs


# --- backends -------------------------------------------------------------------

class TfidfBackend:
    name = "builtin"

    def fit(self, docs):
        toks = [terms(d.text) for d in docs]
        df = {}
        for ts in toks:
            for t in set(ts):
                df[t] = df.get(t, 0) + 1
        n = len(docs)
        self.vecs = []
        for ts in toks:
            tf = {}
            for t in ts:
                tf[t] = tf.get(t, 0) + 1
            # Smoothed idf (as scikit-learn's default): a word every document
            # shares still counts, just least. Plain idf would zero it, and in a
            # small vault two notes about the same thing would look unrelated.
            v = {t: (1 + math.log(c)) * (math.log((1 + n) / (1 + df[t])) + 1)
                 for t, c in tf.items()}
            norm = math.sqrt(sum(w * w for w in v.values())) or 1.0
            self.vecs.append({t: w / norm for t, w in v.items()})
        return self

    def sim(self, i, j) -> float:
        a, b = self.vecs[i], self.vecs[j]
        if len(a) > len(b):
            a, b = b, a
        return sum(w * b[t] for t, w in a.items() if t in b)

    def shared(self, pairs, k=6) -> list:
        """The words a relationship rests on, strongest first."""
        weight = {}
        for _, i, j in pairs:
            a, b = self.vecs[i], self.vecs[j]
            for t in a.keys() & b.keys():
                weight[t] = weight.get(t, 0.0) + a[t] * b[t]
        return [t for t, _ in sorted(weight.items(), key=lambda x: (-x[1], x[0]))[:k]]


class EmbeddingBackend:
    """Any function mapping texts -> vectors. Cosine similarity."""

    def __init__(self, name, embed):
        self.name, self.embed = name, embed

    def fit(self, docs):
        vecs = self.embed([d.text for d in docs])
        self.vecs = []
        for v in vecs:
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            self.vecs.append([x / norm for x in v])
        return self

    def sim(self, i, j) -> float:
        return sum(x * y for x, y in zip(self.vecs[i], self.vecs[j]))

    def shared(self, pairs, k=6) -> list:
        return []


def local_backend(model: str | None = None):
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as e:
        raise RuntimeError('The local backend needs: pip install "magnum-opus[converge]"') from e
    st = SentenceTransformer(model or "all-MiniLM-L6-v2")
    return EmbeddingBackend("local", lambda texts: [list(map(float, v)) for v in
                                                    st.encode(texts)])


def openai_backend(model: str, base_url: str | None = None, api_key: str | None = None):
    from .llm import make_client
    client = make_client("openai", base_url, api_key)
    return EmbeddingBackend("openai", lambda texts: client.embed(model, texts))


# --- relationships ----------------------------------------------------------------

def pair_id(a: str, b: str) -> str:
    return "c-" + hashlib.sha1("|".join(sorted([a, b])).encode()).hexdigest()[:6]


def relationships(docs, backend) -> list:
    """Every cross-group relationship, strongest first. Pairs of documents in
    the same group (the same project) are never compared: projects stay
    independent, and only what crosses between them is proposed."""
    by_pair = {}
    for i in range(len(docs)):
        for j in range(i + 1, len(docs)):
            gi, gj = docs[i].group, docs[j].group
            if gi == gj or (docs[i].origin == "external" and docs[j].origin == "external"):
                continue
            # Two pieces of one conversation are related by where they came
            # from, not by what they say: provenance, like time, is not evidence.
            if docs[i].source and docs[i].source == docs[j].source:
                continue
            s = backend.sim(i, j)
            if s <= 0:
                continue
            key = tuple(sorted([gi, gj]))
            first = i if gi == key[0] else j
            by_pair.setdefault(key, []).append((s, first, j if first == i else i))
    out = []
    for (ga, gb), pairs in by_pair.items():
        pairs.sort(key=lambda p: (-p[0], docs[p[1]].key, docs[p[2]].key))
        top = pairs[:EVIDENCE_PAIRS]
        score = sum(p[0] for p in top) / len(top)
        a = next(d for d in docs if d.group == ga)
        b = next(d for d in docs if d.group == gb)
        out.append({"id": pair_id(ga, gb), "groups": [ga, gb],
                    "labels": [a.group_label, b.group_label],
                    "score": round(score, 4), "evidence": top,
                    "shared": backend.shared(top)})
    out.sort(key=lambda r: (-r["score"], r["id"]))
    return out


# --- feedback: your judgment, recorded where you gave it --------------------------

BLOCK = re.compile(r"<!--convergence:(?P<id>c-[0-9a-f]{6})-->")
BOX = re.compile(r"^- \[(?P<mark>[ xX])\] (?P<what>accept|reject)\b", re.IGNORECASE)


def read_ticks(path: Path, block=BLOCK) -> dict:
    """{id: "accepted" | "rejected" | None | "conflict"} from CONVERGENCE.md.
    None means the block is present with neither box ticked."""
    if not path.exists():
        return {}
    ticks, current = {}, None
    for line in normalize(path.read_text(encoding="utf-8")).splitlines():
        m = block.search(line)
        if m:
            current = m.group("id")
            ticks[current] = set()
            continue
        b = BOX.match(line.strip())
        if current and b and b.group("mark").lower() == "x":
            ticks[current].add(b.group("what").lower())
    out = {}
    for cid, marks in ticks.items():
        out[cid] = ("conflict" if len(marks) > 1 else
                    "accepted" if "accept" in marks else
                    "rejected" if "reject" in marks else None)
    return out


def load_state(vault) -> dict:
    p = vault.root / ".magnum" / "convergence.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"feedback": {}, "runs": []}


def apply_ticks(state: dict, ticks: dict) -> list:
    """Record what the person ticked. Returns human-readable changes."""
    fb, changes = state["feedback"], []
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for cid, decision in sorted(ticks.items()):
        prior = fb.get(cid, {}).get("decision")
        if decision == "conflict":
            changes.append(f"{cid}: both accept and reject ticked; left as it was")
        elif decision is None and prior:
            del fb[cid]
            changes.append(f"{cid}: un-{prior[:-2]}ed, back to proposals")
        elif decision and decision != prior:
            fb[cid] = {"decision": decision, "at": now, "by": "human"}
            changes.append(f"{cid}: {decision}")
    return changes


# --- the generated view -------------------------------------------------------------

def _link(doc) -> str:
    if doc.link:
        return f"[[{doc.link}|{doc.title}]]"
    return f"`{doc.key.removeprefix('external:')}`"


def _block(r, docs, decision=None) -> list:
    a, b = r["labels"]
    lines = [f"### {a} ↔ {b} <!--convergence:{r['id']}-->",
             f"strength {r['score']:.2f}"
             + (f" · shared: {', '.join(r['shared'])}" if r["shared"] else "")]
    for s, i, j in r["evidence"]:
        lines.append(f"- evidence: {_link(docs[i])} ↔ {_link(docs[j])} ({s:.2f})")
    lines.append(f"- [{'x' if decision == 'accepted' else ' '}] accept")
    lines.append(f"- [{'x' if decision == 'rejected' else ' '}] reject")
    return lines + [""]


def proposals(rels, state, min_score) -> list:
    """What is still open to the person: undecided and above the floor."""
    fb = state["feedback"]
    return [r for r in rels if r["id"] not in fb and r["score"] >= min_score]


def render(rels, docs, state, backend_name, top, min_score, n_external) -> str:
    fb = state["feedback"]
    decided = {r["id"]: r for r in rels if r["id"] in fb}
    open_ = proposals(rels, state, min_score)
    shown, hidden = open_[:top], max(0, len(open_) - top)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    n_notes = sum(1 for d in docs if d.origin == "note")
    src = f"{n_notes} notes" + (f" and {n_external} of your own files" if n_external else "")
    lines = ["# CONVERGENCE — how your work relates",
             "",
             f"_Generated {now} by `magnum converge` ({backend_name}) from {src}. "
             "A lens, not a blender: nothing was moved, merged or edited. Relationships "
             "are proposed on content alone; dates are never evidence. Tick **accept** "
             "or **reject**; the next run records it._",
             "", "## Proposed", ""]
    for r in shown:
        lines += _block(r, docs)
    if not shown:
        lines += ["_Nothing crosses between your projects strongly enough to propose._", ""]
    if hidden:
        lines += [f"_{hidden} weaker proposals not shown. Run with `--top {top + hidden}` "
                  "to see them all._", ""]
    acc = [r for r in decided.values() if fb[r["id"]]["decision"] == "accepted"]
    rej = [r for r in decided.values() if fb[r["id"]]["decision"] == "rejected"]
    if acc:
        lines += ["## Accepted by you", ""]
        for r in acc:
            lines += _block(r, docs, "accepted")
    if rej:
        lines += ["## Rejected by you (never proposed again; untick to reconsider)", ""]
        for r in rej:
            lines += _block(r, docs, "rejected")
    return "\n".join(lines).rstrip() + "\n"


def converge(vault, backend, externals=(), top: int = DEFAULT_TOP,
             min_score: float | None = None) -> dict:
    """One pass: read feedback, compare, write CONVERGENCE.md. Returns a summary."""
    out_path = vault.root / "CONVERGENCE.md"
    state = load_state(vault)
    changes = apply_ticks(state, read_ticks(out_path))
    docs = vault_documents(vault)
    ext = []
    for root in externals:
        ext += external_documents(root, vault.root)
    docs += ext
    if min_score is None:
        min_score = DEFAULT_MIN_SCORE.get(backend.name, 0.3)
    rels = relationships(docs, backend.fit(docs)) if len(docs) > 1 else []
    text = render(rels, docs, state, backend.name, top, min_score, len(ext))
    vault._atomic_write(out_path, text)
    # What the last pass showed, so later passes (the thesis) can build on
    # it without recomputing: the proposals on screen and what you accepted.
    fb = state["feedback"]
    state["latest"] = [
        {"id": r["id"], "labels": r["labels"], "score": r["score"],
         "shared": r["shared"],
         "decision": fb.get(r["id"], {}).get("decision", "proposed"),
         "evidence": [[docs[i].key, docs[j].key, round(sc, 4)]
                      for sc, i, j in r["evidence"]]}
        for r in (proposals(rels, state, min_score)[:top]
                  + [r for r in rels if fb.get(r["id"], {}).get("decision") == "accepted"])]
    state["runs"] = (state.get("runs", []) + [{
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "backend": backend.name, "documents": len(docs),
        "relationships": len(rels)}])[-20:]
    vault._atomic_write(vault.root / ".magnum" / "convergence.json",
                        json.dumps(state, indent=2))
    return {"documents": len(docs), "relationships": len(rels),
            "proposed": proposals(rels, state, min_score)[:top],
            "feedback_changes": changes}
