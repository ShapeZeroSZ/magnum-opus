"""magnum -- CLI for Magnum Opus.

  magnum inspect  --export <folder>
  magnum estimate --export <folder>
  magnum ingest   --export <folder> --vault ./vault [--dry-run] [--limit N]
                  [--llm anthropic|openai --base-url URL --model NAME]
  magnum sort     --vault ./vault [--llm ... --model NAME]
  magnum converge --vault ./vault [--external ~/obsidian] [--backend builtin|local|openai]
  magnum thesis   --vault ./vault [--dry-run] [--llm ... --model NAME]
  magnum serve    --vault ./vault [--allow-write]    (MCP server for AI assistants)
  magnum proposals --vault ./vault                   (carry out what you accepted)
  magnum reindex  --vault ./vault
  magnum status   --vault ./vault
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import archive, redact, sort as sortmod, reindex as reindexmod, thesis as th
from .distill import get_distiller
from .llm import PROVIDERS, make_client
from .estimate import estimate as run_estimate, DEFAULT_INPUT_RATE, DEFAULT_OUTPUT_RATE
from .parsers import load_export
from .segment import segment_conversation, Segment
from .vault import Vault

SEGMENT_MODEL = "claude-haiku-4-5-20251001"   # anthropic default for segmentation
TAXONOMY_MODEL = "claude-opus-5"                # anthropic default for the sort pass
ASSIGN_MODEL = "claude-haiku-4-5-20251001"      # anthropic default for assignment


def _require_model(args, what: str) -> str | None:
    """Anthropic has sensible defaults; other providers must name a model."""
    if args.llm != "anthropic" and not args.model:
        print(f"--model is required with --llm {args.llm} ({what}).", file=sys.stderr)
        return None
    return args.model or ""


def _load(args):
    """Load conversations from an export folder or a bare conversations.json."""
    p = Path(args.export)
    if p.is_dir():
        convs = archive.load_conversations(p, provider=args.provider)
    else:
        convs = load_export(p, provider=args.provider)
    convs, rep = redact.redact_all(convs, strict=getattr(args, 'strict_pii', False))
    print(rep)
    return convs


def _segments(convs, client=None, model=SEGMENT_MODEL):
    out = {}
    for c in convs:
        out[c.id] = segment_conversation(c, client=client, model=model)
    return out


def cmd_inspect(args) -> int:
    p = Path(args.export)
    if p.is_dir():
        print(f"Export folder: {p}")
        print(archive.report(p))
    convs = _load(args)
    print(f"\n{len(convs)} conversations, "
          f"{sum(len(c.messages) for c in convs)} messages")
    synth = sum(1 for c in convs for m in c.messages if m.id_is_synthetic)
    print(f"stable message ids: {'all' if not synth else f'{synth} synthetic'}")
    sizes = sorted((c.char_count for c in convs), reverse=True)
    print("largest conversations (MB):", [round(s / 1e6, 1) for s in sizes[:5]])
    return 0


def cmd_estimate(args) -> int:
    convs = _load(args)
    est = run_estimate(convs, _segments(convs), strip_code=not args.no_strip_code)
    print("\nEstimate (heuristic segmentation; LLM segmentation may differ slightly):")
    print(est.render(args.input_rate, args.output_rate))
    return 0


def cmd_ingest(args) -> int:
    vault = Vault(args.vault)
    # The notes on disk are the record: pick up hand edits, renames and
    # deletions, and adopt notes the index lost, so nothing already on disk is
    # distilled (or paid for) twice or overwritten, and nothing deleted returns.
    vault.sync_from_disk()
    convs = _load(args)

    backend = "heuristic" if args.dry_run else args.backend
    if backend == "anthropic":          # the pre-0.3.5 spelling means Claude
        args.llm = "anthropic"
    llm_client, segment_model = None, SEGMENT_MODEL
    if backend != "heuristic":
        if _require_model(args, "distillation") is None:
            return 2
        if args.llm != "anthropic":
            segment_model = args.model
        # Building a client sends nothing; doing it before the estimate means a
        # missing key or URL fails now, not after the user has said yes.
        try:
            llm_client = make_client(args.llm, args.base_url)
        except RuntimeError as e:
            print(e, file=sys.stderr)
            return 2

    # Estimate FIRST, from free heuristic segmentation, and include the cost of
    # the segmentation calls themselves. Nothing reaches the API before the user
    # has seen a number and said yes.
    if not args.dry_run and not args.yes:
        est = run_estimate(convs, _segments(convs), strip_code=not args.no_strip_code)
        print("\n" + est.render(args.input_rate, args.output_rate))
        if input("\nProceed? [y/N] ").strip().lower() not in ("y", "yes"):
            print("Aborted. Nothing spent.")
            return 1

    distiller = get_distiller(backend, vault.known_projects(),
                              model=args.model, strip_code=not args.no_strip_code,
                              client=llm_client, provider=args.llm)
    client = getattr(distiller, "client", None)
    if backend == "heuristic":
        print("(heuristic backend: offline, no API calls)")

    # Segmentation is cached per conversation against its last message id, so a
    # re-run after adding messages re-segments only what changed.
    cache = vault.state.setdefault("segments_cache", {})
    segs_by_conv = {}
    n_long = sum(1 for c in convs if len(c.messages) > 40)
    done = 0
    for conv in convs:
        stamp = conv.messages[-1].id
        hit = cache.get(conv.id)
        if hit and hit.get("stamp") == stamp:
            segs_by_conv[conv.id] = [Segment(conv.id, s["start"], s["end"], s["label"])
                                     for s in hit["segments"]]
            continue
        if len(conv.messages) > 40:
            done += 1
            print(f"  segmenting [{done}/{n_long}] {conv.title[:50]!r} "
                  f"({len(conv.messages)} messages)...", flush=True)
        segs = segment_conversation(conv, client=client, model=segment_model)
        segs_by_conv[conv.id] = segs
        cache[conv.id] = {"stamp": stamp,
                          "segments": [{"start": s.start_id, "end": s.end_id,
                                        "label": s.label} for s in segs]}
        vault.save()

    todo = [(c, s) for c in convs for s in segs_by_conv[c.id]
            if not vault.is_processed(s.key)]
    if args.limit:
        todo = todo[:args.limit]

    print(f"\n{len(todo)} segments to distill.")
    fails = 0
    for i, (conv, seg) in enumerate(todo, 1):
        msgs = conv.span(seg.start_id, seg.end_id)
        try:
            note = distiller.distill(conv, seg, msgs)
        except Exception as e:                       # keep the batch alive
            fails += 1
            print(f"  [{i}/{len(todo)}] FAILED {conv.title!r}: {e}", file=sys.stderr)
            if fails == 2 and i == 2:
                print("\nTwo failures in a row -- this looks like a setup problem, "
                      "not bad data. Stopping so you can fix it once.\n"
                      "  'workspace-id is required' -> set ANTHROPIC_WORKSPACE_ID\n"
                      "  'credit balance' / 401     -> add credit, or re-set the key",
                      file=sys.stderr)
                break
            continue
        vault.write_note(note)
        vault.note_last_message(conv.id, conv.messages[-1].id)
        allitems = [x for grp in (note.decisions, note.ideas, note.open_loops)
                    for x in grp]
        n_ok = sum(1 for x in allitems if x.locator and x.locator.verified)
        n_none = sum(1 for x in allitems if x.locator is None)
        print(f"  [{i}/{len(todo)}] {conv.title[:40]!r} :: {seg.label[:30]} "
              f"({n_ok}/{len(allitems)} verified, {n_none} unlocated)")
        vault.save()

    vault.rebuild_rollups()
    vault.save()
    for slug, names in vault.collisions().items():
        print(f"  ! slug collision: {slug} <- {names}", file=sys.stderr)
    print(f"\nDone. Next: magnum sort --vault {args.vault}")
    return 0


def cmd_sort(args) -> int:
    """Derive the project taxonomy from the whole corpus at once."""
    vault = Vault(args.vault)
    vault.sync_from_disk()
    notes = vault.state["notes"]
    if not notes:
        print("No notes yet -- run `magnum ingest` first.")
        return 1

    if _require_model(args, "the taxonomy pass") is None:
        return 2
    model = args.model or TAXONOMY_MODEL
    assign_model = args.assign_model or (ASSIGN_MODEL if args.llm == "anthropic"
                                         else model)
    try:
        client = make_client(args.llm, args.base_url)
    except RuntimeError as e:
        print(e, file=sys.stderr)
        return 2

    print(f"Sorting {len(notes)} notes into projects...")
    try:
        assignment, described = sortmod.propose_taxonomy(
            notes, client, model,
            assign_model=assign_model,
            debug_dir=vault.root / ".magnum",
        )
    except Exception as e:
        print(f"Sort failed: {e}", file=sys.stderr)
        return 1
    if not assignment:
        print("Sort failed -- no assignments produced.", file=sys.stderr)
        return 1

    counts = sortmod.apply_taxonomy(vault, assignment, described)
    sortmod.cleanup_unsorted(vault)
    print("\nDerived projects:")
    for slug in sorted(counts, key=lambda s: -counts[s]):
        desc = vault.config.get("project_meta", {}).get(slug, {}).get("description", "")
        print(f"  {counts[slug]:4d}  {slug:28} {desc[:60]}")
    print(f"\nDone. Open {args.vault}/QUEUE.md")
    return 0


def cmd_converge(args) -> int:
    """Propose how the body of work relates across projects (CONVERGENCE.md)."""
    from . import converge as conv
    vault = Vault(args.vault)
    try:
        if args.backend == "builtin":
            backend = conv.TfidfBackend()
        elif args.backend == "local":
            backend = conv.local_backend(args.model)
        else:
            if not args.model:
                print("--model is required with --backend openai.", file=sys.stderr)
                return 2
            backend = conv.openai_backend(args.model, args.base_url)
    except RuntimeError as e:
        print(e, file=sys.stderr)
        return 2
    if args.backend == "openai" and not args.yes:
        # Nothing leaves the machine before the person has seen what and where.
        docs = conv.vault_documents(vault)
        for root in args.external:
            docs += conv.external_documents(root, vault.root)
        chars = sum(len(d.text) for d in docs)
        print(f"About to send {len(docs)} texts (~{chars // 4:,} tokens) to "
              f"{args.base_url or os.environ.get('MAGNUM_BASE_URL', '(MAGNUM_BASE_URL)')} "
              f"for embeddings with model {args.model}.")
        if input("Proceed? [y/N] ").strip().lower() not in ("y", "yes"):
            print("Aborted. Nothing sent.")
            return 1
    try:
        result = conv.converge(vault, backend, externals=args.external, top=args.top,
                               min_score=args.min_score)
    except RuntimeError as e:
        print(e, file=sys.stderr)
        return 1
    for change in result["feedback_changes"]:
        print(f"  recorded: {change}")
    print(f"Compared {result['documents']} documents: "
          f"{len(result['proposed'])} proposals shown.")
    print(f"Open {args.vault}/CONVERGENCE.md")
    return 0


def cmd_thesis(args) -> int:
    """Say what the whole body of work appears to be about (EMERGENT_THESIS.md)."""
    vault = Vault(args.vault)
    vault.sync_from_disk()
    if not vault.state["notes"]:
        print("No notes yet -- run `magnum ingest` first.")
        return 1
    if _require_model(args, "the thesis") is None:
        return 2
    model = args.model or TAXONOMY_MODEL
    state = th.load_state(vault)
    for change in th.record_ticks(vault, state):
        print(f"  recorded: {change}")
    prep = th.prepare(vault, state, max_notes=args.max_notes, max_claims=args.max_claims)
    est = th.estimate(prep, args.max_tokens, args.input_rate, args.output_rate)
    where = ("Anthropic" if args.llm == "anthropic" else
             args.base_url or os.environ.get("MAGNUM_BASE_URL", "(MAGNUM_BASE_URL)"))
    print(f"One call to {where} with model {model}:")
    print(f"  sends     {len(prep.ids)} of {prep.notes_total} notes, "
          f"{th.plural(len(prep.relationships), 'relationship')} "
          f"(~{est['input_tokens']:,} tokens)")
    if len(prep.ids) < prep.notes_total:
        print(f"            (the rest are left out; --max-notes {prep.notes_total} "
              "sends them all)")
    if prep.skipped_external:
        print(f"  not sent  {th.plural(prep.skipped_external, 'relationship')} "
              "involving your external files")
    if not prep.relationships:
        print("  (no relationships: run `magnum converge` first to include them)")
    print(f"  WORST-CASE COST  ${est['max_cost']:.2f}  (up to {args.max_tokens:,} output "
          f"tokens at ${args.input_rate}/${args.output_rate} per M; check your "
          "model's prices and pass --input-rate/--output-rate)")
    if args.dry_run:
        path = th.write_dry_run(vault, prep)
        print(f"Dry run: nothing sent. The exact prompt is in {path}")
        return 0
    try:
        client = make_client(args.llm, args.base_url)
    except RuntimeError as e:
        print(e, file=sys.stderr)
        return 2
    if not args.yes and input("Proceed? [y/N] ").strip().lower() not in ("y", "yes"):
        print("Aborted. Nothing spent.")
        return 1
    try:
        version = th.thesis(vault, client, model, prep, state,
                            max_claims=args.max_claims, max_tokens=args.max_tokens)
    except Exception as e:
        print(f"Thesis failed: {e}", file=sys.stderr)
        return 1
    d = version["dropped"]
    print(f"Version {version['version']}: {th.plural(len(version['claims']), 'claim')}"
          + (f", {d['uncited']} dropped for citing no real note" if d["uncited"] else "")
          + (f", {d['rejected']} dropped because you rejected them" if d["rejected"] else "")
          + ".")
    print(f"Open {args.vault}/EMERGENT_THESIS.md")
    return 0


def cmd_serve(args) -> int:
    """Let an AI assistant read the vault over MCP (stdio)."""
    from .agents import Server
    root = Path(args.vault)
    if not (root / ".magnum").is_dir():
        print(f"No Magnum Opus vault at {root}.", file=sys.stderr)
        return 2
    # stdout carries the protocol; anything for the person goes to stderr.
    print(f"magnum serve: {root.resolve()} "
          + ("(agents may add labelled notes in agents/ and propose closing loops "
             "in PROPOSALS.md)" if args.allow_write
             else "(read-only)"), file=sys.stderr)
    Server(root, allow_write=args.allow_write, agent_name=args.agent_name).serve()
    return 0


def cmd_proposals(args) -> int:
    """Carry out the proposals you accepted in PROPOSALS.md."""
    from . import proposals as props
    vault = Vault(args.vault)
    vault.sync_from_disk()
    state = props.load_state(vault)
    if not state["proposals"]:
        print("No proposals yet. Agents connected with `magnum serve --allow-write` "
              "can propose closing open loops.")
        return 0
    changes = props.record_ticks(vault, state) + props.apply(vault, state)
    vault.rebuild_rollups()                     # closed loops leave STATUS and QUEUE
    vault.save()
    changes += props.refresh(vault, state)
    for change in changes:
        print(f"  {change}")
    waiting = sum(1 for p in state["proposals"].values() if p["status"] == "proposed")
    print(f"{waiting} waiting for you. Open {args.vault}/PROPOSALS.md")
    return 0


def cmd_reindex(args) -> int:
    """Rebuild the index from the notes on disk."""
    vault = Vault(args.vault)
    before = len(vault.state.get("notes", []))
    result = reindexmod.reindex(vault)
    vault.rebuild_rollups()
    vault.save()
    print(f"Reindexed: {result['notes']} notes recovered "
          f"(index had {before} before).")
    if result["partial_locators"]:
        print(f"  {result['partial_locators']} notes carry truncated locator ids "
              "from an older version; those items are marked unverified.")
    if result["unreadable"]:
        print(f"  {result['unreadable']} note files could not be parsed.")
    if result["other_files"]:
        print(f"  {result['other_files']} other files in the vault are yours; "
              "left untouched.")
    print("Done. Re-running ingest will now skip work already completed.")
    return 0


def cmd_status(args) -> int:
    q = Path(args.vault) / "QUEUE.md"
    print(q.read_text(encoding="utf-8") if q.exists()
          else "No QUEUE.md yet -- run `magnum ingest` first.")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="magnum",
        description="Distill AI chat exports into a completion-focused vault.")
    sub = p.add_subparsers(dest="command", required=True)

    def llm_flags(sp):
        sp.add_argument("--llm", default=os.environ.get("MAGNUM_PROVIDER", "anthropic"),
                        choices=list(PROVIDERS),
                        help="Model provider: anthropic (Claude) or openai (any "
                             "OpenAI-compatible server: OpenAI, Ollama, vLLM, ...)")
        sp.add_argument("--base-url", default=None,
                        help="Server URL for --llm openai (or MAGNUM_BASE_URL); "
                             "API key from MAGNUM_API_KEY")

    def common(sp, vault=True):
        sp.add_argument("--export", required=True,
                        help="Folder of export zips, or a conversations.json")
        sp.add_argument("--provider", default="auto",
                        choices=["auto", "claude", "chatgpt"])
        sp.add_argument("--no-strip-code", action="store_true",
                        help="Keep assistant code blocks (costs far more)")
        sp.add_argument("--strict-pii", action="store_true",
                        help="Also exclude messages containing emails or phone numbers")
        if vault:
            sp.add_argument("--vault", default="./vault")

    ins = sub.add_parser("inspect", help="Report on an export without spending")
    common(ins, vault=False)
    ins.set_defaults(func=cmd_inspect)

    est = sub.add_parser("estimate", help="Token and cost estimate")
    common(est, vault=False)
    est.add_argument("--input-rate", type=float, default=DEFAULT_INPUT_RATE)
    est.add_argument("--output-rate", type=float, default=DEFAULT_OUTPUT_RATE)
    est.set_defaults(func=cmd_estimate)

    ing = sub.add_parser("ingest", help="Distill an export into the vault")
    common(ing)
    ing.add_argument("--backend", default="llm",
                     choices=["llm", "anthropic", "heuristic"],
                     help="llm (default) or heuristic (offline); "
                          "'anthropic' is kept as an alias of llm")
    llm_flags(ing)
    ing.add_argument("--model", default=None,
                     help="Distillation model (anthropic default: Haiku 4.5; "
                          "required for --llm openai)")
    ing.add_argument("--limit", type=int, default=0)
    ing.add_argument("--dry-run", action="store_true")
    ing.add_argument("--yes", action="store_true", help="Skip the cost prompt")
    ing.add_argument("--input-rate", type=float, default=DEFAULT_INPUT_RATE)
    ing.add_argument("--output-rate", type=float, default=DEFAULT_OUTPUT_RATE)
    ing.set_defaults(func=cmd_ingest)

    srt = sub.add_parser("sort",
                         help="Derive project structure from all notes at once")
    srt.add_argument("--vault", default="./vault")
    llm_flags(srt)
    srt.add_argument("--model", default=None,
                     help="Model for the taxonomy pass (one call; quality matters). "
                          f"anthropic default: {TAXONOMY_MODEL}; required for --llm openai")
    srt.add_argument("--assign-model", default=None,
                     help="Model for assigning notes to projects (mechanical). "
                          f"anthropic default: {ASSIGN_MODEL}; otherwise --model")
    srt.set_defaults(func=cmd_sort)

    cv = sub.add_parser("converge",
                        help="Propose how your projects relate (writes CONVERGENCE.md)")
    cv.add_argument("--vault", default="./vault")
    cv.add_argument("--backend", default="builtin", choices=["builtin", "local", "openai"],
                    help="builtin: no dependencies, explains itself (default); "
                         "local: sentence-transformers on this machine; "
                         "openai: an OpenAI-compatible /embeddings server")
    cv.add_argument("--model", default=None,
                    help="Embedding model (local default: all-MiniLM-L6-v2; "
                         "required for openai)")
    cv.add_argument("--base-url", default=None,
                    help="Server URL for --backend openai (or MAGNUM_BASE_URL)")
    cv.add_argument("--external", action="append", default=[],
                    help="Also read this folder of markdown (e.g. an existing Obsidian "
                         "vault), read-only. Repeatable.")
    cv.add_argument("--top", type=int, default=5,
                    help="How many proposals to show (default 5)")
    cv.add_argument("--min-score", type=float, default=None,
                    help="Similarity floor for proposals (heuristic, not a probability)")
    cv.add_argument("--yes", action="store_true",
                    help="Skip the confirmation before sending text to a server")
    cv.set_defaults(func=cmd_converge)

    ths = sub.add_parser("thesis",
                         help="Say what your whole body of work appears to be about "
                              "(writes EMERGENT_THESIS.md)")
    ths.add_argument("--vault", default="./vault")
    llm_flags(ths)
    ths.add_argument("--model", default=None,
                     help=f"anthropic default: {TAXONOMY_MODEL}; required for --llm openai")
    ths.add_argument("--max-notes", type=int, default=th.DEFAULT_MAX_NOTES,
                     help=f"Most notes to send (default {th.DEFAULT_MAX_NOTES})")
    ths.add_argument("--max-claims", type=int, default=th.DEFAULT_MAX_CLAIMS,
                     help=f"Most claims to keep (default {th.DEFAULT_MAX_CLAIMS})")
    ths.add_argument("--max-tokens", type=int, default=th.DEFAULT_MAX_TOKENS,
                     help=f"Output cap for the call (default {th.DEFAULT_MAX_TOKENS})")
    ths.add_argument("--input-rate", type=float, default=th.DEFAULT_INPUT_RATE,
                     help="$ per M input tokens, for the estimate")
    ths.add_argument("--output-rate", type=float, default=th.DEFAULT_OUTPUT_RATE,
                     help="$ per M output tokens, for the estimate")
    ths.add_argument("--dry-run", action="store_true",
                     help="Write the exact prompt to .magnum/thesis/prompt.txt; send nothing")
    ths.add_argument("--yes", action="store_true", help="Skip the cost prompt")
    ths.set_defaults(func=cmd_thesis)

    srv = sub.add_parser("serve",
                         help="Let AI assistants read the vault (MCP server over stdio)")
    srv.add_argument("--vault", default="./vault")
    srv.add_argument("--allow-write", action="store_true",
                     help="Let agents add notes of their own in agents/<name>/, labelled "
                          "as theirs, and propose closing open loops for you to decide. "
                          "They can never change, move or delete your notes.")
    srv.add_argument("--agent-name", default=None,
                     help="Name to label agent notes with (default: the client's name)")
    srv.set_defaults(func=cmd_serve)

    prp = sub.add_parser("proposals",
                         help="Close the open loops whose proposals you accepted")
    prp.add_argument("--vault", default="./vault")
    prp.set_defaults(func=cmd_proposals)

    rex = sub.add_parser("reindex",
                         help="Rebuild the index from notes on disk (after a crash)")
    rex.add_argument("--vault", default="./vault")
    rex.set_defaults(func=cmd_reindex)

    st = sub.add_parser("status", help="Print the completion queue")
    st.add_argument("--vault", default="./vault")
    st.set_defaults(func=cmd_status)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
