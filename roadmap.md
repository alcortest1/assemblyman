# Roadmap

Planned work on the shared session system (app + session service + web portal + agents),
newest thinking first. Companion docs: [`system.md`](docs/system.md) (architecture),
[`changelist.md`](docs/changelist.md) (what has landed). Design sources: Claude Design project
`443f7de7-8ca7-43ac-a961-081ad26fd0d3` — `AssemblyMan.dc.html` (app),
`AssemblyMan Portal.dc.html` (portal), `AssemblyMan System.dc.html` (system diagram).

Status legend: **designed** = prototyped in the design project · **proposed** = agreed
direction, not yet designed · **open** = needs a design decision first.

---

## 1. Shared interaction

| Feature | Status | Notes |
|---------|--------|-------|
| Viewer pointing | proposed | A viewer drops a temporary marker on the POV; it renders on every participant's stage and the host reads it aloud ("Dana is pointing at the left fastener"). Watch-only viewers stay capture-less — pointing is the one affordance they gain. |
| Session transcript rail | proposed | Durable record of who said what, agents included. Gives latecomers context and makes host room-changes auditable. Voice today is ephemeral. |
| Capture annotation | proposed | Viewers flag/comment on a still; the annotation syncs back to the operator's capture rail. Closes the feedback loop — captures are currently one-way. |
| Operator presence cues | proposed | Spoken roster cue in the frames on join/leave ("Dana joined"). The LED covers bystanders; this covers the operator, who can't see who's watching without opening the app. |

## 2. Agent model

| Feature | Status | Notes |
|---------|--------|-------|
| Default Gemini Realtime host | designed | Joins every room automatically, consumes the app's LiveKit video track (`video_input=True`), answers anything asked, pinned for the session lifetime. Host controls: add/remove agents, invite people by room code — by voice, from anyone in the room. |
| Agent Studio | designed | Portal screens to create/modify agents (name, instructions, voice, triggers) and deploy/recall them into the live room. |
| Per-agent boards | designed | Staging an agent shows its own board: stats row + timestamped log (host: questions answered, room changes; logger: stills filed, defects; spotter: parts identified). |
| Agent-to-agent handoffs | proposed | Agents cite each other's output ("per Inspection Logger's MASK 02 flag…") so the room reads as one system, not parallel bots. Convention on the overlay/log bus, not new infrastructure. |
| Permission tiers | open | Host (pinned, room control) and task agents (removable) exist. Add a viewer-invited tier that cannot speak through the operator's frames — only the operator controls what's in their ears. Needs a decision on who grants the tier. |

## 3. Session lifecycle

| Feature | Status | Notes |
|---------|--------|-------|
| Reconnect / degraded states | open | BT↔Wi-Fi handoff mid-session, operator drop, room paused. Undesigned, and where real usage breaks first. |
| Session summary artifact | proposed | On end, the host compiles captures + flags + transcript into a shareable report, giving the session a durable output. Pairs with the transcript rail. |

## 4. Already designed, pending implementation

Tracked in detail in [`changelist.md`](docs/changelist.md) (branch `industry-redesign`) and the
design project:

- Room codes minted per session, shown in-session with share, rotate on end.
- Wi-Fi/BT transport toggle gating stream quality (BT 480/720p, Wi-Fi 720/1080p), transport
  shown in the live chip and status plate.
- Segment Anything overlays as metadata, toggled independently per surface (app and portal).
- Portal: join by code, Meet-style participants rail (people + agents, click to stage),
  viewer-side overlay toggles, capture rail.

---

*Sections 1–3 originate from the shared-interaction design review of 2026-08-01.*
