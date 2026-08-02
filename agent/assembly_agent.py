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
from livekit.agents import Agent, AgentServer, AgentSession, JobContext, cli, room_io
from livekit.plugins import google

load_dotenv(".env.local")

logger = logging.getLogger("assemblyman-agent")
logger.setLevel(logging.INFO)

server = AgentServer()

# Reactive by default — the operator has their hands full and asks when they
# need something. Proactivity lets Gemini speak up unprompted on what it sees.
PROACTIVE = os.getenv("ASSEMBLYMAN_PROACTIVE", "0") == "1"

REALTIME_MODEL = os.getenv(
    "ASSEMBLYMAN_MODEL", "gemini-2.5-flash-native-audio-latest"
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

    session = AgentSession(
        llm=google.beta.realtime.RealtimeModel(
            model=REALTIME_MODEL,
            proactivity=PROACTIVE,
            enable_affective_dialog=AFFECTIVE,
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
            media_resolution=getattr(
                genai.MediaResolution,
                f"MEDIA_RESOLUTION_{MEDIA_RESOLUTION}",
                genai.MediaResolution.MEDIA_RESOLUTION_LOW,
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
