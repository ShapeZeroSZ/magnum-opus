# Magnum Opus

**Distill your AI chats into a completion-focused, convergence-aware knowledge vault.**

AI made idea generation nearly free. Integration and completion still cost full human
attention — so the ideas pile up faster than any working memory can hold them. Plenty of
tools *archive* your ChatGPT/Claude history. Magnum Opus is the layer above that: it reads
your exports, keeps only the durable residue of each conversation, and maintains a vault
whose default view is deliberately small.

**Local-first.** Your chat history is intimate. Everything lives in plain markdown on your
disk, Obsidian-compatible. The only network call is the LLM distillation pass (bring your
own API key), and there's a fully offline `--dry-run` mode.

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
4. **Converge** *(phase 3, in progress)* — embeddings + clustering across the whole corpus
   surface the latent relationships between your projects, and a synthesis pass writes a
   versioned `EMERGENT_THESIS.md`: what this body of work appears to be about, with cited
   evidence. The thesis is an *output* of the system, not an input.

## Install

```bash
pip install -e ".[llm]"          # core + Anthropic backend
```

Then set your key. macOS/Linux:

```bash
export ANTHROPIC_API_KEY=sk-...
```

Windows (Command Prompt) — `set` lasts for the current window, `setx` persists:

```
set ANTHROPIC_API_KEY=sk-...
```

`inspect` and `estimate` need no key and spend nothing.

## Use

Export your data (Claude: Settings → Privacy → Export data; ChatGPT: Settings → Data
controls → Export). Both produce a `conversations.json`.

```bash
magnum inspect  --export ~/Downloads/claude-export      # what's there, no spend
magnum estimate --export ~/Downloads/claude-export      # tokens and cost
magnum ingest   --export ~/Downloads/claude-export --vault ~/vault
magnum sort     --vault ~/vault                         # derive project structure
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
  inbox/*.md                    ← unclassified notes
  config.json                   ← known project slugs (seed this to improve routing)
```

Open the vault folder in [Obsidian](https://obsidian.md) for search, backlinks, and graph
view — the `[[links]]` in distilled notes are the raw material the convergence engine uses.

## Roadmap

- [x] v0.1 — Claude + ChatGPT parsers, API distillation, vault, ≤3-item queue
- [x] v0.2 — shard-aware loader, redaction, segmentation, message-level locators
- [ ] v0.2 — zero-config: `magnum` with no args does the right thing; convergence engine
      (local embeddings, clustering, versioned `EMERGENT_THESIS.md`)
- [ ] v0.3 — serverless PWA: upload export → queue, all client-side, bring-your-own-key,
      local storage + one-tap vault export
- [ ] v0.4 — agent layer: MCP server (`ingest`, `query`, `read_queue`, `write_note`),
      provenance-tagged shared vaults
- [ ] later — interop adapters (ai-vault archives, more providers), community schemes

Design principles live in [MANIFESTO.md](MANIFESTO.md). The note format and vault layout
are specified in [docs/SPEC.md](docs/SPEC.md) — schemes, adapters, and agents built
against the spec are first-class citizens.

## License

MIT
