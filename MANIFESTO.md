# MANIFESTO

Magnum Opus is an **ease-of-mind** productivity tool. Every feature, contribution, and
design decision is judged against one question: *does this reduce what the user has to
hold in their head?* If it doesn't, it doesn't ship.

## What this is

AI made generating ideas nearly free. Integrating and completing them still costs full
human attention. Magnum Opus distills the flood — AI chats, notes, threads — into the
smallest set of things worth acting on, and quietly maintains an understanding of how a
person's threads relate, for those who want to see it.

## Principles

1. **Nothing is ever lost.** Every distilled note links to its preserved source. Every
   classification is reversible. Deletion happens only by explicit user request. A
   thought must never be able to die silently here. The notes on disk are the
   record; every index over them must be rebuildable from them, and a lost or
   corrupt index must never cost the work it pointed at.

2. **Calm technology.** No notifications. No nags. No streaks, badges, or gamification —
   ever. The user opens the tool when they want it; it is silent otherwise. Pull, not
   push.

3. **The default view is small.** The queue shows at most three items. Hiding things is a
   feature, not a limitation. Any change that puts more in front of the user by default
   is a regression. A complete, ordered priority list (`PRIORITIES.md`) exists one step
   deeper for whoever wants the whole picture — depth is opt-in, never default.

4. **Local-first, serverless-first.** Data lives on the user's device in plain markdown.
   No account required. No server required. Intelligence runs through frontier APIs
   under the user's own key.

5. **Honest about the seams.** Distillation sends content to an API provider under the
   user's own key — the first-run screen says so plainly. Browser storage can be
   evicted — the tool encourages backups. Calm comes from clarity, not omission.

6. **Cost is visible before it is incurred.** The estimate is shown, and consent
   given, before *any* call reaches the API — including preparatory passes the
   user did not ask about by name. A step that spends money before the prompt is
   a bug regardless of how small the amount is. Cheap models by default,
   user-settable caps, no surprise bills.

7. **Convergence is a lens, never a blender.** Projects remain fully independent. The
   convergence layer describes relationships; it never restructures, merges, or forces a
   unified anything. It is optional and off the critical path. The emergent thesis is an
   output the user reacts to, never a constraint imposed on them.

8. **Organization is open-ended.** The note format is the standard; organization schemes
   are interchangeable views over it. Our projects/queue scheme is a default, not a
   doctrine. Interop (Obsidian and friends) is welcomed everywhere and required nowhere.

9. **Optimized for an AI, usable by a human.** AI-first structure, human-first surface.
   The vault *is* the API — there is never a second machine-only format alongside the
   human one. Structure is explicit, stable, and self-contained; the surface stays plain
   prose in plain markdown. Where the two genuinely conflict, structure wins in the
   files and readability wins in the views.

10. **Agent-friendly, provenance-honest.** The vault is dual-audience by design — humans
   and agents read and write the same files. Every note declares its author. An agent's
   inference must never masquerade as a human's decision.

11. **This tool must never become the problem.** No feature may turn Magnum Opus into a
    second system that itself demands maintenance, curation, or guilt. When in doubt,
    leave it out.

## What this will never do

- Notify, remind, or interrupt
- Require an account, a server, or a subscription to function
- Merge or restructure a user's projects without an explicit request
- Add social features, sharing feeds, or engagement mechanics
- Grow a settings page longer than one screen
- Hold data hostage in a proprietary format

Contributions that conflict with this document will be declined with thanks. The
manifesto can change, but changing it is a bigger deal than any feature.
