# Magnum Opus

**Distill your AI chats into a completion-focused, convergence-aware knowledge vault.**

AI made idea generation nearly free. Integration and completion still cost full human
attention — so the ideas pile up faster than any working memory can hold them. Plenty of
tools *archive* your ChatGPT/Claude history. Magnum Opus is the layer above that: it reads
your exports, keeps only the durable residue of each conversation, and maintains a vault
whose default view is deliberately small.

**Local-first.** Your chat history is intimate. Everything lives in plain markdown on your
disk, Obsidian-compatible. The only network calls are the model passes, and you choose
where they go: Claude under your own key, or any OpenAI-compatible server, including a
model running on your own machine, in which case nothing leaves it. There's also a fully
offline `--dry-run` mode.

**Where this is going.** The goal is to close the gap between how fast AI lets ideas
arrive and how fast a person can carry them through: fewer things held in your head,
unfinished work tracked and finished, and AI assistants that help carry it forward.
Your notes stay yours: an assistant can read them and, if you allow it, add notes of
its own, each labelled as its suggestion. It never changes your notes, and nothing it
writes counts as your decision until you act on it.

## What it does

1. **Distill** — each conversation is reduced to a short note: *decisions made, ideas worth
   keeping, open loops*. Pleasantries, dead ends, and anything re-derivable are discarded.
2. **Organize** — you never write a project list. Distillation deliberately does not
   guess a project per segment (a segment seen alone cannot tell one project from
   two, and guessing fragments one body of work into several names). Instead
   `magnum sort` derives the taxonomy in a single pass over every note, then files
   them and maintains a rolling `STATUS.md` per project.
3. **Queue** — `QUEUE.md` shows at most **three** items, ranked by what's closest to done.
   Everything else is intentionally hidden. This is the anti-overwhelm view.
4. **Converge** — `magnum converge` proposes how your projects relate, on content alone,
   in `CONVERGENCE.md`: a handful of cross-project connections, each with the notes and
   words it rests on. You accept or reject them; your answers shape every later pass.
   Then `magnum thesis` writes a versioned `EMERGENT_THESIS.md`: what this body of
   work appears to be about, every claim citing notes that exist. The thesis is an
   output of the system that you react to, not an input.

## Install

```bash
pip install -e ".[llm]"          # core + Anthropic (Claude) provider
pip install -e .                 # core only: enough for local / OpenAI-compatible models
```

**Claude** (the default provider). Set your key. macOS/Linux:

```bash
export ANTHROPIC_API_KEY=sk-...
```

Windows (Command Prompt) — `set` lasts for the current window, `setx` persists:

```
set ANTHROPIC_API_KEY=sk-...
```

**Any OpenAI-compatible server** instead: OpenAI, or a local model under Ollama,
llama.cpp, vLLM or LM Studio. Pass `--llm openai`, the server's URL and a model name
(an API key, if the server needs one, goes in `MAGNUM_API_KEY`):

```bash
magnum ingest --export ~/Downloads/claude-export --vault ~/vault \
    --llm openai --base-url http://localhost:11434/v1 --model llama3.1
magnum sort --vault ~/vault --llm openai --base-url http://localhost:11434/v1 --model llama3.1
```

`MAGNUM_PROVIDER` and `MAGNUM_BASE_URL` set the same defaults from the environment.
Small local models make weaker notes than frontier ones. The locator rules below
still hold, so a weak model yields unverified items, never false citations.

`inspect` and `estimate` need no key and spend nothing.

## Use

Export your data (Claude: Settings → Privacy → Export data; ChatGPT: Settings → Data
controls → Export). Both produce a `conversations.json`.

```bash
magnum inspect  --export ~/Downloads/claude-export      # what's there, no spend
magnum estimate --export ~/Downloads/claude-export      # tokens and cost
magnum ingest   --export ~/Downloads/claude-export --vault ~/vault
magnum sort     --vault ~/vault                         # derive project structure
magnum converge --vault ~/vault                         # how your projects relate
magnum thesis   --vault ~/vault                         # what it all appears to be about
magnum serve    --vault ~/vault                         # let an AI assistant read it (MCP)
magnum proposals --vault ~/vault                        # close the loops you agreed to close
magnum find orbit social --vault ~/vault                # where is it? (add --export to search everything)
magnum chat     --vault ~/vault                         # talk with the AI working on it
magnum brief    --vault ~/vault                         # one file to hand to any assistant
magnum reindex  --vault ~/vault                         # rebuild index after a crash
magnum status   --vault ~/vault                         # prints the queue
```

`--export` takes the **folder** of export zips; shards are merged automatically.
`light_metadata` is never read. Distillation defaults to Haiku 4.5; the cost
estimate is shown and confirmed before anything is spent.

Useful flags: `--dry-run` (offline, no API), `--limit 20` (test on a subset),
`--provider claude|chatgpt` (skips auto-detection), `--model` (override LLM).

Runs are incremental — re-ingest a fresh export any time; only new or updated
conversations are processed.

## Vault layout

```
vault/
  QUEUE.md                      ← the only file you need to open
  projects/<slug>/STATUS.md     ← rolling per-project state
  projects/<slug>/chats/*.md    ← distilled notes
  unsorted/*.md                 ← notes not yet placed by `magnum sort`
  config.json                   ← known project slugs (seed this to improve routing)
```

Open the vault folder in [Obsidian](https://obsidian.md) for search, backlinks, and graph
view — the `[[links]]` in distilled notes are the raw material the convergence engine uses.

## Roadmap

Done:
- [x] Claude + ChatGPT parsers, model distillation, vault, ≤3-item queue
- [x] Shard-aware loader, redaction, segmentation, message-level locators
- [x] Corpus-wide sort (derived projects, judged ranking), crash-safe reindex
- [x] Any model provider: Claude, or any OpenAI-compatible server (v0.3.5)
- [x] Test suite and CI (v0.3.5)
- [x] Safe to live in from Obsidian: hand edits, properties, renames, deletions and
      your own project choices all survive (v0.3.6)
- [x] Convergence, first pass: cross-project relationships on content alone, with
      evidence, your accept/reject feedback, and optional external vaults (v0.4.0)
- [x] Emergent thesis: versioned, every claim citing real notes (checked in code),
      shaped by what you accept and reject, cost shown first (v0.5.0)
- [x] Agent layer: an MCP server so AI assistants can read the vault and, if you
      allow it, add labelled notes of their own; never change yours (v0.6.0)
- [x] Open-loop proposals: an assistant suggests a loop is done, you tick accept
      or reject, and only then is the loop closed (v0.7.0)
- [x] Everything can be found: every reference names its original chat, with a
      link to open it, and `magnum find` searches notes and the raw export (v0.8.0)
- [x] Chat, guidance and brief: talk with the AI working on your vault; your
      corrections stick in `GUIDANCE.md`; `magnum brief` hands the state of your
      work to any other assistant (v0.9.0)
- [x] Memory for agents: `recall` returns small cited items with no model calls,
      and Shape Zero chat logs are a source (v0.10.0); each item says who said it,
      so an assistant's suggestion is never recalled as your decision (v0.10.1)

Next, roughly in order:
- [ ] **Zero-config.** `magnum` with no arguments does the right thing.
- [ ] **Browser app (PWA).** Upload an export and get your queue, all client-side,
      bring-your-own-key, local storage, one-tap vault export.

Open to anyone, any time (no milestone gates these):
- [ ] **More sources.** Any place ideas pile up: other AI tools, notes apps, documents,
      an existing Obsidian vault. Write a parser that produces conversations with stable
      message ids and it plugs in.
- [ ] **More organization schemes** over the same note format (see SPEC §3).

## Chat: talk with the AI working on your vault

```bash
magnum chat --vault ~/vault                    # Claude by default; --max-cost 1.00
magnum chat --vault ~/vault --llm openai --base-url http://localhost:11434/v1 --model llama3.1
```

Ask what it sees ("what's stalled?", "where did I talk about orbit?"), correct it,
point it at an area, rearrange priorities, or tell it something is finished. It
answers from your vault and links every note it mentions to the original chat.

- **Every change asks you first.** Closing a loop, moving a note to another
  project, reordering priorities, and adding to your guidance each show you exactly
  what will change, and happen only if you say yes.
- **It never runs a full pass.** Re-sorting everything, recomputing convergence
  or writing a new thesis happen only when you run them, for example after a new
  model comes out and you want it to reassess. The chat can suggest one and show
  you the command; it can't start one.
- **Your corrections stick.** When you correct it or state a preference, it
  offers to remember it in `GUIDANCE.md`.
- **Cost is shown and capped.** The running cost is shown after every reply, and
  the chat stops before a message would pass `--max-cost` (default $1.00). With a
  model on your own machine, pass `--input-rate 0 --output-rate 0`.

Transcripts are kept in `.magnum/chats/`. `/brief` writes a brief; `/quit` leaves.

## Guidance: corrections that stick

`GUIDANCE.md` is yours: a plain file in the vault with your standing instructions,
such as:

- Grey and Shape Zero are one project.
- Finish the paper before starting anything new.
- The garden notes are personal; leave them out of the thesis.

Write in it directly in Obsidian, or confirm additions from the chat. Every step
that uses a model reads it first: `sort`, `thesis`, the chat, and any assistant
connected with `magnum serve`. Tools only ever add to it, and only lines you
confirmed; they never rewrite it.

## Brief: hand your work to any assistant

```bash
magnum brief --vault ~/vault                   # writes BRIEF.md; free, no model call
```

`BRIEF.md` is the state of your work in one file:
- your guidance and priorities;
- each project's next step, open loops and recent decisions;
- how your projects relate, and the current thesis;
- what's waiting for your decision.

Every item links to its original conversation, and those links work outside the
vault. Upload it to a new conversation, or give it to Claude Code, and it can pick
up the work. It's built by code from what your vault says, so it's exact and costs
nothing.

## Find: where is it?

Every place that points at a note (`STATUS.md`, `CONVERGENCE.md`,
`EMERGENT_THESIS.md`, `PROPOSALS.md`, and what an assistant tells you) also says
which conversation it came from and when, with a link that opens it in Claude or
ChatGPT:

> [[projects/orbit/chats/…|Orbit social app]], from the ChatGPT chat “Late night
> ideas” (2026-02-03) [open](https://chatgpt.com/c/…)

New notes carry the same line at the bottom (`**Source:**`).

To find something you remember but can't place:

```bash
magnum find orbit social --vault ~/vault
magnum find orbit social --vault ~/vault --export ~/Downloads/claude-export
```

The first searches your notes. With `--export`, it also searches the original
conversations directly, so it finds ideas that distillation left out, or that come
from chats you haven't ingested yet. It runs on your computer, sends nothing and
costs nothing. Dates are shown to help you find things; they are never used as
evidence of how your work relates.

## Converge: how your work relates

```bash
magnum converge --vault ~/vault                           # built in, no dependencies
magnum converge --vault ~/vault --external ~/Documents/MyObsidianVault
magnum converge --vault ~/vault --backend local           # pip install "magnum-opus[converge]"
magnum converge --vault ~/vault --backend openai --base-url http://localhost:11434/v1 \
    --model nomic-embed-text                                # any /v1/embeddings server
```

`CONVERGENCE.md` lists a few connections that cross between your projects, strongest
first. Each one shows the notes it rests on (as links) and, with the built-in backend,
the words the notes share. Under each connection, tick **accept** or **reject** in
Obsidian. The next run records your answer in `.magnum/convergence.json`:

- **rejected** connections are never proposed again;
- **accepted** ones stay pinned under "Accepted by you";
- **untick** either to change your mind.

The rules it keeps:

- **Content only.** Dates, times, weekday and month names and bare numbers are
  stripped before comparing. Two pieces of the same conversation are never paired:
  where something came from is provenance, not evidence.
- **A lens, not a blender.** Only relationships *between* projects are proposed,
  and no note is moved, merged or edited. `CONVERGENCE.md` and
  `.magnum/convergence.json` are the only files written; delete them and the vault
  works exactly as before.
- **Small by default.** Five proposals; `--top N` for more.
- **Your other notes, read-only.** `--external` reads another folder of markdown
  (e.g. an existing Obsidian vault) without writing to it. `.obsidian/` and
  `.trash/` are skipped.
- **Nothing sent without asking.** The `openai` backend shows what it will send, and
  where, before sending anything (`--yes` to skip).

Before `magnum sort`, notes are compared individually. After sorting, relationships
are between projects. Scores are similarity heuristics, not probabilities. The
built-in backend compares every pair of notes: about 10 seconds for 1,500 notes.

## Thesis: what your work appears to be about

```bash
magnum thesis --vault ~/vault --dry-run     # see exactly what would be sent; sends nothing
magnum thesis --vault ~/vault               # one call; shows the worst-case cost and asks first
```

One model call reads your notes, your projects and the relationships from the last
`magnum converge`, and writes `EMERGENT_THESIS.md`: a short statement of what the
work as a whole appears to be about, and up to seven claims. Run `converge` first
if you want the relationships included.

- **Every claim cites real notes.** The model cites notes by id; the code maps
  each id back to a note in your vault. A claim that cites nothing real is
  dropped, and the file says how many were. If the statement itself cites
  nothing real, nothing is written and the previous version stays.
- **You shape the next version.** Tick **accept** or **reject** under a claim.
  Accepted claims are kept in every later version. Rejected ones are never
  repeated: the model is told, and the code drops any that come back anyway.
  Untick to change your mind.
- **What you rejected in `CONVERGENCE.md` is never sent**, even if you ticked it
  after the last `converge`.
- **No dates.** Dates and times are removed from everything the model sees.
- **Your external files stay local.** Relationships found with `converge
  --external` are not sent.
- **Versioned.** Each run is a new version. Earlier ones are kept in
  `.magnum/thesis/`.
- **Cost first.** It sends up to 120 notes (`--max-notes`), evidence notes
  first. Before sending, it shows the worst-case cost. The default rates are
  those of a large model; pass your model's with `--input-rate` and
  `--output-rate`. `--dry-run` writes the exact prompt to
  `.magnum/thesis/prompt.txt`.

It uses the same providers as `sort`: Claude by default, or `--llm openai
--base-url ... --model ...` for any OpenAI-compatible server, including one on
your own machine.

## Agents: let an AI assistant read your vault

`magnum serve` is an [MCP](https://modelcontextprotocol.io) server, so any assistant
that supports MCP (Claude Desktop, Claude Code, and others) can work with your vault.
For Claude Desktop, add this to its `claude_desktop_config.json`:

```json
{"mcpServers": {"magnum-opus": {"command": "magnum",
                                "args": ["serve", "--vault", "/path/to/vault"]}}}
```

For Claude Code: `claude mcp add magnum-opus -- magnum serve --vault ~/vault`.

The assistant can read your queue, your projects and their status, your open loops,
and any note, and it can search your notes. That's all, unless you allow more:

- **Read-only by default.** The assistant cannot change a single file. Reading
  doesn't even update the index.
- **`--allow-write` lets it add notes, never change yours.** Its notes go in
  `agents/<its name>/`. It cannot edit, move or delete any existing note, and it
  never overwrites a file.
- **You can always tell whose words are whose.** An assistant's note is marked as
  its suggestion, in its properties (`author: agent:<name>`) and in its first line.
  It stays a suggestion until you act on it. To make it yours, edit it, move it, or
  change its `author`; delete it if it's wrong.
- **Its notes are never evidence.** `sort`, `converge` and `thesis` don't read
  them, so an assistant's guess can't shape your projects or claims about your work.
- **It can't spend your money.** Nothing it can do calls a paid API; `ingest`,
  `sort` and `thesis` stay with you, where the cost is shown first.
- **It stays inside the vault.** It can't read files outside it, or the
  `.magnum` and `.obsidian` folders.

### When an assistant thinks something is done

With `--allow-write`, an assistant can also suggest that one of your open loops is
finished or no longer needed, with its reason and the notes that show it. Its
suggestions collect in `PROPOSALS.md`, and nothing changes until you decide:

- **Accept:** tick **accept**, then run `magnum proposals`. The loop is ticked
  in its note, just as if you had ticked it yourself, and nothing else in the note
  changes. It leaves `STATUS.md` and `QUEUE.md`.
- **Reject:** tick **reject**. The loop stays open, and no assistant can suggest
  closing it again unless you untick.
- **Or do nothing.** If you tick the loop in the note yourself, the suggestion
  simply goes away.

The assistant can never close anything itself. If you've changed the loop's
wording since accepting, `magnum proposals` leaves it alone. An assistant can have
at most 20 suggestions waiting, so it can't flood you.

## Recall: the vault as an agent's memory

Connected assistants, the chat, and code (`from magnum_opus.recall import recall`)
can ask the vault what it remembers about something. What comes back is built on
what 2026 memory research found works:

- **Small items, not whole notes.** Each result is one decision, idea, open loop
  or summary, with its project, date and a link to the original conversation.
- **No model calls.** Retrieval is keyword ranking (BM25), optionally combined
  with meaning-based search if you supply an embedding function. It's fast, free,
  and the same for any model reading the results.
- **Checkable citations.** Every item has a stable id. A system using recall can
  require that an answer cites only ids recall actually returned
  (`check_citations`), so it can't cite something that isn't there.
- **Dates shown, never scored.** When something was said never makes it relevant.
  When two decisions from one project come back, the newer is listed first.
- **Only your notes.** Notes written by assistants are never recalled as
  evidence.

### Shape Zero chat logs as a source

Point `ingest` at one user's log folder (`data/logs/<owner>/`) and each day's
chat becomes a conversation, titled by its opening message:

```bash
magnum ingest --export /srv/shapezero/data/logs/user-42 --vault /srv/vaults/user-42
```

## Living in the vault from Obsidian

The notes are yours. Open the vault in Obsidian and work in it; every `magnum`
command reads your changes back and never overwrites them:

- **Write anything** in a note: paragraphs, headings, properties (`tags`,
  `aliases`, ...). It survives every `ingest`, `sort` and `reindex`.
- **Tick an open loop** (`- [x]`) and it leaves `STATUS.md` and `QUEUE.md`, while
  staying in the note.
- **Change a note's `project:`** and that choice is yours: `sort` files the note
  there, marks it `project_set_by: human`, and never moves it again. Delete that
  property to hand the choice back.
- **Rename or move a note** anywhere in the vault: it is found by identity, not path.
- **Delete a note** and it stays deleted. It is not re-distilled or re-created.
- **Your own files** (anything without Magnum Opus frontmatter) are never touched.

`magnum sort` changes exactly two things in a note: its folder and its `project:`
line. `magnum proposals` ticks only the open loops you accepted closing. `QUEUE.md`,
`PRIORITIES.md`, `STATUS.md`, `CONVERGENCE.md`, `EMERGENT_THESIS.md` and
`PROPOSALS.md` are generated, so text you write in them is overwritten. The
accept and reject ticks you make in the last three are read back first.

## Known issues

- Items do not yet carry their own dates (SPEC §1 says they SHOULD).
- The cost estimate prices tokens at Claude Haiku rates by default. With another
  provider, pass that provider's rates with `--input-rate` / `--output-rate` (a model
  on your own machine costs nothing per token).

## Development

```bash
pip install -e ".[dev]"
pytest
```

The tests need no API key and no network: models are faked, and the
OpenAI-compatible client is exercised against a local stub server. See
[CONTRIBUTING.md](CONTRIBUTING.md).

Design principles live in [MANIFESTO.md](MANIFESTO.md). The note format and vault layout
are specified in [docs/SPEC.md](docs/SPEC.md) — schemes, adapters, and agents built
against the spec are first-class citizens.

## License

MIT
