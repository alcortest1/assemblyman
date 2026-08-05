"""AssemblyMan realtime assistant.

Joins every LiveKit room as the default agent, subscribes to the video the
AssemblyMan iOS app publishes from the operator's Ray-Ban Meta glasses, and
answers the operator's questions about what it can see.

The agent is *reactive*: it listens and watches continuously but speaks only
when addressed, so it never talks over someone doing hands-on work. Set
ASSEMBLYMAN_PROACTIVE=1 to let Gemini volunteer observations instead.

Run:
    python assembly_agent.py dev       # local dev, hot reload
    python assembly_agent.py start     # production worker
    lk agent console assembly_agent.py # talk to it from the terminal
"""

import logging
import os

from dotenv import load_dotenv
from google.genai import types as genai
from livekit import rtc
from livekit.agents import Agent, AgentServer, AgentSession, JobContext, cli, room_io
from livekit.agents.utils import images
from livekit.plugins import google

import dataset_recorder

load_dotenv(".env.local")

logger = logging.getLogger("assemblyman-agent")
logger.setLevel(logging.INFO)

server = AgentServer()

# Reactive by default — the operator has their hands full and asks when they
# need something. Proactivity lets Gemini speak up unprompted on what it sees.
PROACTIVE = os.getenv("ASSEMBLYMAN_PROACTIVE", "0") == "1"

# Pinned, not `-latest`. The rolling alias resolved to a build whose setup schema has no
# `proactivity` field, and Gemini rejects an unknown field by closing the socket outright —
# the agent then joins the room and can neither speak nor hear, which reads as never having
# joined at all. A dated pin is worth more than a newer model here.
REALTIME_MODEL = os.getenv(
    "ASSEMBLYMAN_MODEL", "gemini-2.5-flash-native-audio-preview-12-2025"
)


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


# How long the operator has to stop talking before Gemini decides the turn is over. This is
# the single largest contributor to how slow a reply *feels*, because it is dead time added
# to every exchange before any thinking starts. The default is around a second, which suits
# open conversation; this is short commands in a workshop, so it is cut. Too low and the
# model interrupts someone mid-thought, so it is left tunable.
SILENCE_MS = _int("ASSEMBLYMAN_SILENCE_MS", 450)

# Audio that arrives before speech is detected, kept so the first syllable is not clipped.
PREFIX_PADDING_MS = _int("ASSEMBLYMAN_PREFIX_MS", 200)

# 2.5 models reason before answering. Useful for hard questions, pure latency for "what is
# this part". Zero disables it; raise it if answers get shallow.
THINKING_BUDGET = _int("ASSEMBLYMAN_THINKING_BUDGET", 0)

# The operator's POV at full resolution is a lot of tokens per second for a model that mostly
# needs to recognise a part. Low resolution cuts both latency and cost.
MEDIA_RESOLUTION = os.getenv("ASSEMBLYMAN_MEDIA_RESOLUTION", "LOW").upper()
MEDIA_SIZE = {
    "LOW": 512,
    "MEDIUM": 768,
    "HIGH": 1024,
}.get(MEDIA_RESOLUTION, 512)

# Affective dialog makes replies warmer at some cost in responsiveness.
AFFECTIVE = os.getenv("ASSEMBLYMAN_AFFECTIVE", "0") == "1"

INSTRUCTIONS = """\
You are AssemblyMan, a hands-free assembly assistant. You are speaking with an \
operator who is wearing Ray-Ban Meta glasses, and the video you receive is their \
point of view — you see exactly what they see while they work.

How to behave:
- Answer only when the operator addresses you or asks a question. They are using \
their hands; do not narrate, do not fill silence, and do not interrupt.
- Keep replies to one or two short sentences. This is spoken through the frames' \
open-ear audio in a noisy shop, so lead with the answer.
- Ground every answer in what you can actually see in the current view. If the \
part, label or fastener is out of frame or too blurry to read, say so and ask \
them to move closer or steady their head rather than guessing.
- When identifying hardware, give the spec they need to act: size, torque and the \
tool. Use metric units unless they use imperial first.
- If you see something genuinely unsafe — a hand in a pinch point, a missing \
guard, an obviously cross-threaded or damaged fastener — say it immediately, \
even though you were not asked. Safety is the one exception to staying quiet.
- Never invent a part number, torque figure or procedure step. If you do not \
know, say you do not know.
"""


class AssemblyAssistant(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=INSTRUCTIONS)


# No agent_name: an unnamed worker uses LiveKit's automatic dispatch and joins
# every new room in the project. Naming it would switch the room over to
# explicit dispatch, which is the opposite of a default agent.
@server.rtc_session()
async def entrypoint(ctx: JobContext) -> None:
    ctx.log_context_fields = {"room": ctx.room.name}

    # Only send the optional behaviour flags when they are actually on. Gemini treats an
    # unknown setup field as fatal rather than ignoring it, so sending `proactivity=False`
    # buys nothing and breaks the session on any build that has dropped the field.
    options: dict[str, object] = {}
    if PROACTIVE:
        options["proactivity"] = True
    if AFFECTIVE:
        options["enable_affective_dialog"] = True

    session = AgentSession(
        llm=google.beta.realtime.RealtimeModel(
            model=REALTIME_MODEL,
            **options,
            # Endpointing. HIGH end-sensitivity means Gemini commits to "they have stopped"
            # sooner, which together with the shortened silence window is what removes the
            # pause between the operator finishing and the reply starting.
            realtime_input_config=genai.RealtimeInputConfig(
                automatic_activity_detection=genai.AutomaticActivityDetection(
                    end_of_speech_sensitivity=genai.EndSensitivity.END_SENSITIVITY_HIGH,
                    silence_duration_ms=SILENCE_MS,
                    prefix_padding_ms=PREFIX_PADDING_MS,
                )
            ),
            thinking_config=genai.ThinkingConfig(thinking_budget=THINKING_BUDGET),
            # Transcribe both directions. Without input transcription a session where the
            # microphone is silent and one where Gemini hears but declines to answer look
            # identical in the log — both are simply an absence. This makes the difference
            # visible, and gives the dataset recorder the operator's words to label with.
            input_audio_transcription=genai.AudioTranscriptionConfig(),
            output_audio_transcription=genai.AudioTranscriptionConfig(),
            # The LiveKit Google wrapper does not expose Gemini's `media_resolution`
            # setup field. Resize frames before they are sent instead, using the
            # wrapper's supported image encoding option.
            image_encode_options=images.EncodeOptions(
                format="JPEG",
                quality=75,
                resize_options=images.ResizeOptions(
                    width=MEDIA_SIZE,
                    height=MEDIA_SIZE,
                    strategy="scale_aspect_fit",
                ),
            ),
        ),
    )

    await session.start(
        room=ctx.room,
        agent=AssemblyAssistant(),
        # video_input subscribes the agent to the operator's published camera
        # track — without it Gemini gets audio only and cannot see the work.
        room_options=room_io.RoomOptions(video_input=True),
    )

    # The app publishes simulcast, and by default this subscriber is handed a low layer —
    # frames arrived at 180x320. That is enough to say "a view of a city" and nowhere near
    # enough to read a port label, which is the thing the assistant most needs to do. Ask
    # for the top layer explicitly on the operator's camera.
    def _request_full_resolution(publication, participant) -> None:
        if (
            publication.kind == rtc.TrackKind.KIND_VIDEO
            and participant.identity.startswith("phone-")
        ):
            try:
                publication.set_video_quality(rtc.VideoQuality.VIDEO_QUALITY_HIGH)
                logger.info("requested high-quality video from %s", participant.identity)
            except Exception as error:  # noqa: BLE001 - never break the session over this
                logger.warning("could not raise video quality: %s", error)

    ctx.room.on(
        "track_subscribed",
        lambda track, publication, participant: _request_full_resolution(
            publication, participant
        ),
    )
    for participant in ctx.room.remote_participants.values():
        for publication in participant.track_publications.values():
            if publication.subscribed:
                _request_full_resolution(publication, participant)
    # Dataset capture, when asked for. Rides on the session rather than a second worker: two
    # unnamed workers would be load-balanced across rooms by LiveKit, so only one of them
    # would see any given session — recording has to live where the video already is.
    if dataset_recorder.ENABLED:
        recorder = dataset_recorder.SessionRecorder(ctx.room.name)

        def _attach(track: rtc.Track, publication, participant) -> None:
            if track.kind == rtc.TrackKind.KIND_VIDEO and participant.identity.startswith("phone-"):
                recorder.attach(track)

        ctx.room.on("track_subscribed", _attach)

        @session.on("user_input_transcribed")
        def _on_speech(event) -> None:
            # Interim results fire continuously while someone talks; one still per finished
            # utterance is the sample worth labelling.
            if getattr(event, "is_final", True):
                recorder.capture(trigger="speech", transcript=getattr(event, "transcript", None))

        ctx.add_shutdown_callback(recorder.aclose)

    await ctx.connect()

    logger.info(
        "assemblyman agent live in %s (model=%s, proactive=%s, silence=%dms, "
        "thinking=%d, media=%s, affective=%s)",
        ctx.room.name,
        REALTIME_MODEL,
        PROACTIVE,
        SILENCE_MS,
        THINKING_BUDGET,
        MEDIA_RESOLUTION,
        AFFECTIVE,
    )

    # One short greeting so the operator knows the agent is watching, then quiet.
    await session.generate_reply(
        instructions="Greet the operator in one short sentence and tell them to "
        "just ask when they need something. Do not describe what you see."
    )


if __name__ == "__main__":
    cli.run_app(server)
