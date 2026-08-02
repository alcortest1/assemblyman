# AssemblyMan realtime agent

A Gemini Realtime agent that joins the LiveKit room as the **default agent**,
watches the video the AssemblyMan iOS app publishes from the operator's Ray-Ban
Meta glasses, and answers their questions about what it sees.

It is reactive by design: it listens and watches continuously but speaks only
when addressed — the operator has their hands full. The one exception is a
visible safety hazard, which it calls out unprompted.

## Setup

Python 3.9+ (3.10+ recommended).

```bash
cd agent
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env.local   # then fill in the values
```

`.env.local` needs your LiveKit project credentials and a
[Google AI Studio API key](https://aistudio.google.com/apikey). It is gitignored —
keep it that way.

## Run

```bash
python assembly_agent.py dev      # local development, reloads on change
python assembly_agent.py start    # production worker
lk agent console assembly_agent.py  # talk to it from the terminal, no app needed
```

With the worker running, any room created in the project gets the agent
automatically. Start a session in the iOS app and it joins.

## How it works

1. `AgentServer` + `@server.rtc_session()` registers a worker. **No `agent_name`
   is set** — an unnamed worker uses LiveKit's *automatic* dispatch and joins
   every new room. Naming it would switch rooms to explicit dispatch, which is
   the opposite of a default agent.
2. `RoomOptions(video_input=True)` subscribes the agent to the operator's camera
   track, so Gemini receives video frames alongside audio. Without it the model
   is audio-only and cannot see the work.
3. `google.beta.realtime.RealtimeModel` runs speech-to-speech, so replies come
   back as audio through the frames' open-ear speakers with no separate
   STT/TTS hop.
4. `proactivity` is off by default. Set `ASSEMBLYMAN_PROACTIVE=1` to let the
   model volunteer observations instead of waiting to be asked.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | — | LiveKit project, required |
| `GOOGLE_API_KEY` | — | Google AI Studio key, required |
| `ASSEMBLYMAN_PROACTIVE` | `0` | `1` lets the agent speak unprompted |
| `ASSEMBLYMAN_MODEL` | `gemini-2.5-flash-native-audio-preview-12-2025` | Realtime model override |

The agent's persona and answering rules live in `INSTRUCTIONS` in
`assembly_agent.py` — that string is the thing to edit to change its behaviour.

## Reference

Adapted from LiveKit's
[Gemini Realtime with live vision](https://docs.livekit.io/reference/recipes/gemini_live_vision.md)
recipe.
