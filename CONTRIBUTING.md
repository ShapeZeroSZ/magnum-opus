# Contributing

Thanks for helping. Two documents decide what belongs here:

- [MANIFESTO.md](MANIFESTO.md): every change is judged by one question, *does this
  reduce what the user has to hold in their head?* Changes that conflict with it are
  declined with thanks.
- [docs/SPEC.md](docs/SPEC.md): the note format and vault layout. Anything that reads
  or writes that format is a first-class citizen; the reference implementation is not
  privileged.

## Getting started

```bash
pip install -e ".[dev]"
pytest
```

No API key or network is needed for the tests.

## Rules that keep the vault trustworthy

- **Provenance is never invented.** Locators are resolved in code from verbatim
  anchors, never emitted by a model. An item with no provenance is honest; an item
  with false provenance cannot be caught. Don't add fallbacks that "find something
  close".
- **Nothing is ever lost.** Notes on disk are the record; every index must be
  rebuildable from them (`magnum reindex`). A tool that rewrites a note must keep
  what it doesn't understand.
- **Cost is visible before it is incurred.** Nothing reaches a paid API before the
  user has seen an estimate and agreed.
- **Agents say they are agents.** Anything an agent writes carries
  `author: agent:<name>`.

## Good first contributions

- **A parser for a new source** (another AI tool, a notes app, documents). It must
  produce conversations whose messages have stable, provider-native ids (or a content
  hash, flagged as synthetic). Add a small synthetic fixture and tests.
- **An organization scheme**: a program that reads notes and writes generated views,
  without modifying the notes.
- Anything under **Known issues** in the README.

Please include tests with changes; a fix should come with a test that fails without it.
