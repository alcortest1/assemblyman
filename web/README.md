# AssemblyMan Portal

The companion web portal: join a live session by room code, watch any
participant's screen — people or agents — and build agents in the Studio.

Implemented from the Claude Design source `AssemblyMan Portal.dc.html`, on the
Industry design system. No build step, no dependencies.

## Run

Any static file server works:

```bash
cd web
python3 -m http.server 8777
# → http://localhost:8777
```

## Files

| Path | What it is |
|---|---|
| `index.html` | Markup for all three screens, mounted once and toggled |
| `app.js` | State and DOM syncing, ported from the design's component |
| `styles/industry.css` | The Industry design system — tokens and component classes, copied verbatim from the design project's `_ds` bundle |
| `styles/portal.css` | Portal screens, built only from Industry tokens |
| `assets/plant.png` | Stand-in POV frame (same asset the iOS app's mock device uses) |

`styles/industry.css` is a vendored copy of the design system. Retune the look
there and re-sync it from the design project rather than editing token values in
`portal.css`.

## Screens

- **Join** (`#/join`) — room code entry. An empty field joins the demo room
  `K7F-3QD9`; a partial code is rejected as a typo.
- **Session** (`#/room/<code>`) — staged screen plus the participants rail.
  Clicking a tile stages that participant: the operator's POV, a viewer
  placeholder (viewers watch, they never broadcast), or an agent's board.
  Viewer-side overlays — viewfinder marks, thirds grid, Segment Anything masks —
  toggle locally and change nothing for anyone else.
- **Agent Studio** (`#/agents`) — create, edit, deploy and recall agents.
  A deployed agent shows up in the participants rail and can be staged like any
  person.

Each screen has its own URL, so a `#/room/<code>` link joins that room directly.

## State

`state` in `app.js` holds the design's seeded demo roster — an operator, two
viewers, and three agents. Nothing is persisted; a reload resets it.

To put the portal on live data, replace two things:

1. **The roster.** `state.people` and the `stage`/`participants` rendering read
   from plain objects. Swap them for LiveKit room participants and their tracks.
2. **The stage media.** `assets/plant.png` stands in for the operator's video —
   attach the subscribed video track to `.stage-media` instead.

The `agent/` directory at the repo root has the realtime agent that joins the
same room and consumes the operator's stream; the Studio's "Assembly Assistant"
is the portal-side representation of it.
