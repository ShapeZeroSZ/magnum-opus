# Magnum Opus Note Format & Vault Spec — v0.2 (draft)

This document is the public interop surface of Magnum Opus. Anything that reads or
writes this format — an organization scheme, an export adapter, an agent, another tool
entirely — is a first-class citizen. The reference implementation is not privileged.

Design constraints: **dual-audience** (pleasant for humans in any markdown editor,
trivially parseable by machines) and **portable** (plain UTF-8 markdown files; no
database required to read a vault).

## 1. The Note

A note is one markdown file with YAML frontmatter. It is the distilled residue of one
source (an AI conversation, a document, a session).

```markdown
---
title: "Grey v9 expert routing bug"
segment: "load balancing fix"          # the segment's label, may be ""
conversation_id: aaa-111               # stable ID of the source
start_message: 0b6f487d-...            # first message of the segment (full id)
end_message: 22a0060b-...              # last message of the segment (full id)
provider: claude                       # claude | chatgpt | obsidian | manual | <other>
updated: 2026-07-01T11:00:00Z          # ISO 8601, from the source
project: grey                          # slug; "unsorted" until a sort has run
topic: "expert routing collapse"       # free-form label from distillation
author: distiller                      # human | distiller | agent:<name>
spec: "0.2"
---

# Grey v9 expert routing bug

One-to-three sentence summary in plain prose.

## Decisions
- Choices that were actually made.

## Ideas
- Concepts worth keeping.

## Open loops
- [ ] Unfinished threads, phrased as next actions.

**Touches:** [[shape-zero]] [[eve-zero]]
```

Rules:

- **Frontmatter keys** above are required except `author` (defaults to `distiller`),
  `segment` and `topic`. Free-text values (`title`, `segment`, `topic`) are
  written as JSON strings, which are always valid YAML double-quoted scalars, so
  any title survives any YAML reader. Unknown keys MUST be preserved by any tool
  that rewrites a note.
- **`project_set_by: human`** (optional) records that a person chose the note's
  project. A tool MUST NOT change the project of such a note. Removing the key hands
  the choice back to the tools. A tool that finds a note's `project:` changed from
  what it last wrote MUST treat that as the person's choice and record it this way.
- **Sections** `Decisions`, `Ideas`, `Open loops` are optional; omit when empty. Items
  are single-line list entries. Open loops SHOULD use task syntax (`- [ ]`) so any
  markdown task plugin works on them. A ticked loop (`- [x]`) is closed: it stays in
  the note and leaves generated views.
- **Links** use `[[wikilink]]` syntax with slugs (lowercase, hyphenated). They are the
  raw material of the convergence layer and resolve against project slugs first, then
  note titles.
- **Timestamps everywhere.** Notes carry `updated` from the source. Items SHOULD carry
  their date when the source provides one — e.g. `- Decision text (2026-07-01)` — so
  temporal order survives distillation. Generated files state their generation time.
  A vault must always be able to answer *when*.
- **Provenance** (`author`) is mandatory in meaning even where defaulted in syntax: a
  human's stated decision and an agent's inference must never be indistinguishable.
  Agents writing notes MUST set `author: agent:<name>`.

## 1a. Locators

Every extracted item (decision, idea, open loop) carries a locator back to the
message it came from:

    (conversation_id, message_id, role, verified)

`message_id` MUST be provider-native and stable — Claude's per-message `uuid`,
ChatGPT's mapping node id. A positional index MUST NOT be the primary identity:
parsers filter empty messages, so derived positions shift whenever the filter
changes. Where no native id exists, use a content hash, which survives re-export.

Locators are **resolved deterministically**, never emitted by a model. The
distiller returns a short verbatim `anchor` per item; code matches that anchor
against the segment's messages.

An anchor that matches nothing yields **no locator at all** (`null`). A fallback
locator pointing at some nearby message would resolve cleanly downstream and pass
verification while citing a message the anchor never appeared in — an internally
consistent falsehood. An item without provenance is honest and can be filtered;
an item with false provenance cannot be caught. Anchors shorter than four words
are rejected as coincidence rather than evidence, and an anchor matching several
messages is recorded as `ambiguous: true` and never `verified`.

`role` is part of the locator because attribution depends on it: an item sourced
from an assistant turn is a *suggestion*, not the user's decision, and the two
must never be conflated.

## 1b. Segments

A conversation may span months and several unrelated projects, so a note maps to
a **segment**, not a whole conversation. Segments are contiguous, non-overlapping
message ranges identified by `(start_message, end_message)`; `segment_key` is
`conversation_id:start_id:end_id`. Incremental state is tracked per segment and
per last-seen message, so appending to a long thread re-distills only what
changed.

## 2. The Vault

```
vault/
  QUEUE.md                    # generated: the default view (≤ 3 items)
  PRIORITIES.md               # generated: the complete ordered priority list
  MANIFESTO.md                # optional copy of the principles
  config.json                 # projects list + settings (all optional)
  unsorted/*.md               # notes not yet placed by a sort
  projects/<slug>/
    STATUS.md                 # generated: open loops + recent decisions
    chats/*.md                # notes
  # raw transcripts are NOT copied: the export is the immutable corpus,
  # and locators point into it. Keep the original export archived.
  .magnum/state.json          # implementation detail; other tools may ignore it
```

- Generated files (`QUEUE.md`, `STATUS.md`) are owned by whichever scheme produced them
  and may be regenerated at any time; hand edits there are not durable. Notes are
  durable and are never regenerated destructively.
- **The notes are the record; any index follows them.** A person may edit, rename, move
  or delete notes with any editor. Tools find notes by identity (their
  `conversation_id`, `start_message`, `end_message`), never by path. A tool may change
  only two things in an existing note: which folder it sits in, and its `project:` line.
  A note deleted by the person stays deleted: tools MUST NOT recreate it. Files without
  Magnum Opus frontmatter belong to the person and are never modified.
- A vault with only notes and no generated files is still a valid vault.

## 2a. Sorting

`project` is assigned by a **corpus-wide pass**, never per note at distillation
time. A note written in isolation cannot know whether two topics are two projects
or two weeks of one, and per-note guessing fragments a single body of work across
several slugs. Until sorted, notes carry `project: unsorted` and a free-form
`topic` label. Sorting is idempotent: it recomputes the taxonomy from all notes,
so a grown corpus yields a corrected structure rather than an accreted one.

The same pass also produces the **ranking**. Ordering is a judgment, not a
count: a project with many open loops may be the live one and a project with few
may be abandoned, so the sort weighs closeness to a finishable state, whether
finishing unblocks other work, recent activity, and how much is genuinely undone
versus already superseded. Each project carries `status`
(active/dormant/done), a `next_action`, and a one-line `rank_reason` shown to the
user. Projects marked `done` leave the queue. Before a sort has run, ordering
falls back to loop count and is labelled provisional.

Requiring the user to declare their projects up front is also out of scope by
design — that is the labour the system exists to remove, and a hand-written list
biases the convergence layer toward the user's current self-description.

## 3. Organization Schemes

A **scheme** is any program that reads notes and writes generated views. The reference
scheme is `projects-queue` (per-project STATUS + a ≤3-item QUEUE ranked by
closeness-to-done + a complete ordered PRIORITIES list one step deeper). Other schemes — flat/tag-based, chronological, PARA, one-big-pile —
are equally valid. Schemes MUST NOT modify notes; they only add generated files.

## 4. Convergence Layer

The convergence layer reads all notes (and optionally external corpora such as an
existing Obsidian vault), computes similarity across them, and writes generated files:
`CONVERGENCE.md` (proposed relationships, each citing its evidence notes) and a
versioned `EMERGENT_THESIS.md`. **Time is provenance, never evidence:** temporal
proximity of sources MUST NOT be treated or cited as a signal of relatedness —
relationships are proposed on content alone. Timestamps answer *when*, not *with*.
It is strictly additive and optional — removing its
output files leaves a fully functional vault. User feedback (accept/reject a proposed
connection) is recorded and constrains future passes.

Reference implementation of `CONVERGENCE.md` (`magnum converge`, v0.4.0):

- Relationships are between groups: a project, or a single note that has no project
  yet, or a single external file. Documents in the same group are never compared, and
  neither are two segments of the same source conversation.
- Each relationship in `CONVERGENCE.md` is a block headed by
  `<!--convergence:c-xxxxxx-->`, where the id is derived from the two groups (stable
  across runs). The block lists its evidence as note links and ends with two task
  lines, `- [ ] accept` and `- [ ] reject`.
- Before regenerating, a tool MUST read the existing ticks and record them:
  - `accept` ticked: accepted;
  - `reject` ticked: rejected, never proposed again;
  - neither ticked on a previously decided id: the decision is withdrawn;
  - both ticked: nothing changes.

  Decisions live in `.magnum/convergence.json`, marked `by: human`.

Reference implementation of `EMERGENT_THESIS.md` (`magnum thesis`, v0.5.0):

- The file holds the current version: a statement, then claims. Every earlier
  version is kept in `.magnum/thesis/v<N>.md`.
- Every claim, and the statement, MUST cite notes that exist in the vault. This
  is checked by the tool, not trusted from the model:
  - a claim without such a citation is dropped, and the number dropped is shown;
  - a statement without one means no version is written.
- Each claim is a block headed by `<!--thesis:t-xxxxxx-->`, where the id is
  derived from the claim's text. The block ends with `- [ ] accept` and
  `- [ ] reject`. Ticks are read back by the same rules as `CONVERGENCE.md`.
  - Accepted claims appear in every later version.
  - A rejected claim is never written again.
  - Decisions, with each claim's text, live in `.magnum/thesis.json`.
- Nothing about when a note was written is sent to the model.
- Relationships the person rejected are never sent, and neither is anything
  from external corpora.

## 5. Agents (informative, not yet implemented)

The planned MCP server exposes: `ingest`, `query`, `read_queue`, `write_note`,
`read_status`. Agents interact with the vault exclusively through the note format above,
provenance-tagged. An agent MAY maintain its own vault as an orchestration substrate;
the format is identical.

## Versioning

The `spec` frontmatter key declares the format version a note was written under.
Breaking changes bump the minor version pre-1.0 and require a migration note in this
file. Additive changes (new optional keys/sections) do not bump the version.
