"""magnum -- CLI for Magnum Opus.

  magnum inspect --export <folder>
  magnum estimate --export <folder> [--vault ./vault]
  magnum ingest   --export <folder> --vault ./vault [--dry-run] [--limit N]
  magnum status   --vault ./vault
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import archive, redact, sort as sortmod, reindex as reindexmod
from .distill import get_distiller
from .estimate import estimate as run_estimate, DEFAULT_INPUT_RATE, DEFAULT_OUTPUT_RATE
from .parsers import load_export
from .segment import segment_conversation, Segment
from .vault import Vault

SEGMENT_MODEL = "claude-haiku-4-5-20251001"


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


def _segments(convs, client=None):
    out = {}
    for c in convs:
        out[c.id] = segment_conversation(c, client=client, model=SEGMENT_MODEL)
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
    convs = _load(args)

    backend = "heuristic" if args.dry_run else args.backend

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
                              model=args.model, strip_code=not args.no_strip_code)
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
        segs = segment_conversation(conv, client=client, model=SEGMENT_MODEL)
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
    notes = vault.state["notes"]
    if not notes:
        print("No notes yet -- run `magnum ingest` first.")
        return 1

    import anthropic
    import os
    headers = {}
    ws = os.environ.get("ANTHROPIC_WORKSPACE_ID", "").strip()
    if ws:
        headers["anthropic-workspace-id"] = ws
    client = anthropic.Anthropic(default_headers=headers or None)

    print(f"Sorting {len(notes)} notes into projects...")
    try:
        assignment, described = sortmod.propose_taxonomy(
            notes, client, args.model,
            assign_model=args.assign_model,
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
        desc = vault.config.get("project_descriptions", {}).get(slug, "")
        print(f"  {counts[slug]:4d}  {slug:28} {desc[:60]}")
    print(f"\nDone. Open {args.vault}/QUEUE.md")
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
        print(f"  {result['unreadable']} files could not be parsed.")
    print(f"Done. Re-running ingest will now skip work already completed.")
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
    ing.add_argument("--backend", default="anthropic",
                     choices=["anthropic", "heuristic"])
    ing.add_argument("--model", default=None,
                     help="Override distillation model (default: Haiku 4.5)")
    ing.add_argument("--limit", type=int, default=0)
    ing.add_argument("--dry-run", action="store_true")
    ing.add_argument("--yes", action="store_true", help="Skip the cost prompt")
    ing.add_argument("--input-rate", type=float, default=DEFAULT_INPUT_RATE)
    ing.add_argument("--output-rate", type=float, default=DEFAULT_OUTPUT_RATE)
    ing.set_defaults(func=cmd_ingest)

    srt = sub.add_parser("sort",
                         help="Derive project structure from all notes at once")
    srt.add_argument("--vault", default="./vault")
    srt.add_argument("--model", default="claude-opus-5",
                     help="Model for the taxonomy pass (one call; quality matters)")
    srt.add_argument("--assign-model", default="claude-haiku-4-5-20251001",
                     help="Model for assigning notes to projects (mechanical)")
    srt.set_defaults(func=cmd_sort)

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
