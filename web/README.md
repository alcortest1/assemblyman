# AssemblyMan Portal

The companion web portal: join a live session by room code, watch any
participant's screen — people or agents — and build agents in the Studio.

Implemented from the Claude Design source `AssemblyMan Portal.dc.html`, on the
Industry design system. The browser loads the LiveKit client from a pinned CDN URL, so
there is no local build step.

## Run

For the seeded design demo, any static file server works. To join a real LiveKit room,
use the development server so the API secret stays outside the browser:

```bash
./scripts/portal_dev_server.py
# → http://localhost:8777
```

## Deployment

Vercel, via the GitHub integration on `alcortest1/assemblyman`.

`vercel.json` at the repo root drives it: no framework, no build step,
`outputDirectory: "web"`. Everything else in the repo — the iOS samples, the
vendored SDK, and the LiveKit agent — is excluded by `.vercelignore`, along with
the credential files.

| Branch | Environment | URL |
|---|---|---|
| `main` | Production | `assemblyman.vercel.app` |
| any other branch | Preview | per-branch URL |

`main` does not yet contain `web/` or `vercel.json`, so the production URL will
not serve the portal until this branch merges.

The Vercel project needs `LIVEKIT_URL`, `LIVEKIT_API_KEY`, and
`LIVEKIT_API_SECRET` environment variables. `api/token.js` reads them in the serverless
runtime and returns a room-scoped viewer token; the secret never reaches browser code.

Cache headers are set so `index.html`, `app.js` and `styles/` always revalidate —
none of them are content-hashed, so a redeploy would otherwise serve stale
assets. Only `assets/` is cached (24h).

## Files

| Path | What it is |
|---|---|
| `index.html` | Markup for all three screens, mounted once and toggled |
| `app.js` | State and DOM syncing, ported from the design's component |
| `livekit-bridge.js` | LiveKit room lifecycle, roster, remote audio, and operator video |
| `../api/token.js` | Vercel token endpoint; generates viewer identities server-side |
| `styles/industry.css` | The Industry design system — tokens and component classes, copied verbatim from the design project's `_ds` bundle |
| `styles/portal.css` | Portal screens, built only from Industry tokens |
| `assets/plant.png` | Stand-in POV frame (same asset the iOS app's mock device uses) |

`styles/industry.css` is a vendored copy of the design system. Retune the look
there and re-sync it from the design project rather than editing token values in
`portal.css`.

## Screens

- **Join** (`#/join`) — room code entry. An empty field joins the demo room
  `K7F-3QD`; a partial code is rejected as a typo.
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

`state` in `app.js` keeps the seeded demo roster for design-only use. Once
`livekit-bridge.js` connects, the real LiveKit participant roster replaces it and the
operator's subscribed camera track replaces `assets/plant.png`. Nothing is persisted; a
reload leaves the room and resets local UI state.

The `agent/` directory at the repo root has the realtime agent that joins the
same room and consumes the operator's stream; the Studio's "Assembly Assistant"
is the portal-side representation of it.
