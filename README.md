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
unfinished work tracked and finished, and eventually agents that act on the vault on
your behalf, with every action they take recorded as theirs, never as yours.

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

Next, roughly in order:
- [ ] **Agent layer.** An MCP server (`ingest`, `query`, `read_queue`, `write_note`,
      `read_status`) so assistants can read the vault and write provenance-tagged
      notes, and later act on open loops for the user, every action recorded as
      `author: agent:<name>`.
- [ ] **Zero-config.** `magnum` with no arguments does the right thing.
- [ ] **Browser app (PWA).** Upload an export and get your queue, all client-side,
      bring-your-own-key, local storage, one-tap vault export.

Open to anyone, any time (no milestone gates these):
- [ ] **More sources.** Any place ideas pile up: other AI tools, notes apps, documents,
      an existing Obsidian vault. Write a parser that produces conversations with stable
      message ids and it plugs in.
- [ ] **More organization schemes** over the same note format (see SPEC §3).

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
line. `QUEUE.md`, `PRIORITIES.md` and `STATUS.md` are generated, so edits made in
those files are overwritten.

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
