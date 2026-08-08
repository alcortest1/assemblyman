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

import asyncio
import json
import logging
import os

from dotenv import load_dotenv
from google.genai import types as genai
from livekit import rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    RunContext,
    cli,
    function_tool,
    room_io,
)
from livekit.agents.utils import images
from livekit.plugins import google

import criteria_prompt
import dataset_recorder
import grading
import identify
from frame_grabber import FrameGrabber
from photo_capture import PhotoCapture

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
#
# `gemini-3.1-flash-live-preview` is the only live model at the 3.1 generation —
# `gemini-3.1-pro-preview` exists but is not a realtime model and cannot drive a
# session. It is served by the AI Studio API only; Vertex carries nothing newer
# than `gemini-live-2.5-flash-native-audio`, so this reads GOOGLE_API_KEY and not
# the Vertex service account.
REALTIME_MODEL = os.getenv("ASSEMBLYMAN_MODEL", "gemini-3.1-flash-live-preview")


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

# The rubrics for every task in the pilot, loaded once at import. Absent on a
# fresh clone — criteria/ is generated, not source — in which case the agent runs
# exactly as before and simply has no grading tool.
CRITERIA = criteria_prompt.load()

GRADING_INSTRUCTIONS = """\

GRADING

You can grade the operator's work. When they ask you to — "grade this", "how did \
I do", "check my work" — call `grade_work` with the task and subtask codes for \
the work in front of them, taken from the rubrics below.

- Grade only when asked. Seeing finished work is not a request to mark it.
- Work out which subtask they mean from what they said and what you can see. If \
two are plausible, ask which one; do not guess between them. The codes are \
exact strings from the rubric headings, like AM.II.A.S6 and rivet_layout.
- The tool photographs what the operator is looking at right now, so tell them \
to hold their head still and frame the finished work before you call it.
- When it returns, say the overall result and the headline reason in one or two \
sentences. The full breakdown appears on their screen — do not read every \
criterion aloud.
- These rubrics are machine-drafted and have not been reviewed by an instructor. \
If asked whether the grade is final, say plainly that it is a first opinion for \
an instructor to confirm.
- A criterion can fail because the work is wrong or because the photo does not \
show it. Those are different things to the student and you must not conflate \
them: if it failed for want of a view, say so and offer to grade again.
"""


# Everything the portal listens for arrives on this topic. One topic rather than
# one per message kind, so a viewer joining mid-session subscribes to a single
# thing and switches on `type`.
DATA_TOPIC = "assemblyman.grade"

# A photograph the phone sends unasked, because the operator pressed the capture
# button and chose a subtask rather than speaking to the agent. Separate from the
# capture topic, which carries photos the agent itself requested — the two arrive
# by different routes and only one of them has a waiting future.
GRADE_REQUEST_TOPIC = "assemblyman.grade-request"
# A photograph sent to be named rather than graded.
IDENTIFY_REQUEST_TOPIC = "assemblyman.identify-request"


class Grader:
    """Holds the frame buffer and turns a spoken request into a published verdict."""

    def __init__(self, room: rtc.Room) -> None:
        self._room = room
        self.frames = FrameGrabber()
        self.photos = PhotoCapture(room)

    async def _image(self) -> tuple[bytes, dict] | None:
        """The best picture of the work available right now.

        A still from the glasses first: it is properly exposed and full
        resolution, where the relayed video is compressed for motion and
        downscaled. Only when the phone cannot supply one does this fall back to
        the sharpest buffered frame — a worse image, and the payload says which
        was used so a surprising verdict can be read in that light.
        """
        photo = await self.photos.request()
        if photo:
            return photo

        shot = self.frames.best_jpeg()
        if not shot:
            return None
        jpeg, meta = shot
        return jpeg, {**meta, "source": "video_frame"}

    async def _publish(self, payload: dict) -> None:
        """Send a message to every participant. Never raises.

        A failed publish must not fail the grade: the operator still hears the
        verdict, and losing the overlay is a degraded result rather than a
        broken one.
        """
        try:
            await self._room.local_participant.publish_data(
                json.dumps(payload).encode(), reliable=True, topic=DATA_TOPIC
            )
        except Exception as error:  # noqa: BLE001
            logger.warning("could not publish %s: %s", payload.get("type"), error)

    async def run(self, task_code: str, subtask_code: str) -> str:
        rubric = CRITERIA.find(task_code, subtask_code)
        if not rubric:
            logger.info("no rubric for %s / %s", task_code, subtask_code)
            return (
                f"I have no rubric for {task_code} / {subtask_code}. Ask the operator "
                "which task and subtask they are on, and use the exact codes from the "
                "rubric headings."
            )

        shot = await self._image()
        if not shot:
            return ("I could not get a picture of the work — the glasses did not return "
                    "a photo and no video frame has arrived. Ask the operator to check "
                    "their feed and try again.")
        jpeg, frame_meta = shot

        # Tell the portal a grade is running before the model is called, so the
        # overlay can open immediately. A grade takes seconds, and an interface
        # that shows nothing until it lands reads as a request that was ignored.
        await self._publish({
            "type": "grading",
            "task_code": rubric.task_code,
            "subtask_code": rubric.subtask_code,
            "subtask": rubric.subtask,
            "subject": rubric.subject,
            "criteria": [{"index": i, "text": t} for i, t in enumerate(rubric.criteria, 1)],
        })

        result = await grading.grade(jpeg, rubric, frame_meta)
        await self._publish(result)
        return grading.spoken_summary(result)

    # ---------------------------------------------------------------- phone path
    #
    # The operator can also start a grade from the phone: press capture, pick the
    # subtask, send. That arrives as a photograph on its own topic with the codes
    # in the stream attributes, and takes the same route from there — same rubric
    # lookup, same model, same published payload — so a grade started by hand and
    # one started by voice cannot disagree about anything but the picture.

    def listen_for_phone_grades(self) -> None:
        try:
            self._room.register_byte_stream_handler(
                GRADE_REQUEST_TOPIC,
                lambda reader, identity: asyncio.create_task(
                    self._on_phone_grade(reader, identity)
                ),
            )
        except Exception as error:  # noqa: BLE001
            logger.warning("could not listen for phone grades: %s", error)

    async def _on_phone_grade(self, reader, identity: str) -> None:
        attributes = dict(getattr(getattr(reader, "info", None), "attributes", None) or {})
        rubric = CRITERIA.find(attributes.get("task_code", ""),
                               attributes.get("subtask_code", ""))
        try:
            jpeg = b"".join([chunk async for chunk in reader])
        except Exception as error:  # noqa: BLE001
            logger.warning("phone grade photo from %s failed: %s", identity, error)
            return

        if not rubric:
            logger.info("phone asked for an unknown rubric: %s", attributes)
            await self._publish({
                "type": "grade", "error": "no_rubric",
                "message": "No rubric for that task and subtask.",
                "task_code": attributes.get("task_code", ""),
                "subtask_code": attributes.get("subtask_code", ""),
            })
            return

        logger.info("phone-initiated grade of %s (%d bytes)", rubric.key, len(jpeg))
        await self._publish({
            "type": "grading",
            "task_code": rubric.task_code, "subtask_code": rubric.subtask_code,
            "subtask": rubric.subtask, "subject": rubric.subject,
            "criteria": [{"index": i, "text": t} for i, t in enumerate(rubric.criteria, 1)],
        })
        result = await grading.grade(
            jpeg, rubric, {"source": "glasses_photo", "bytes": len(jpeg)}
        )
        await self._publish(result)

    def catalogue(self) -> list[dict]:
        """Everything this room can grade, as codes and names.

        Built from the rubrics rather than stored, so it cannot fall out of step
        with them. Used twice: sent to the phone to fill its picker, and given to
        the identifier as the closed set of answers it may choose from.
        """
        tasks: dict[str, dict] = {}
        for item in CRITERIA.items:
            task = tasks.setdefault(item.task_code, {
                "task_code": item.task_code, "task_title": item.task_title, "subtasks": [],
            })
            task["subtasks"].append({
                "subtask_code": item.subtask_code,
                "subtask": item.subtask,
                "subject": item.subject,
                "criteria_count": len(item.criteria),
            })
        return list(tasks.values())

    async def publish_catalogue(self) -> None:
        """Tell the phone what it can ask to be graded against.

        The rubrics live here, not on the phone, so the picker behind the capture
        button would otherwise have nothing to list. Codes and names only — a few
        kilobytes, not the 15k-token prompt.
        """
        await self._publish({"type": "catalogue", "tasks": self.catalogue()})

    # ------------------------------------------------------------- identification
    #
    # The operator knows what they just built. Making them find it in a picker
    # forty-one subtasks deep, holding a phone, is asking them to do the model's
    # job — so the photograph is identified first and the picker opens on the
    # answer.
    #
    # A suggestion only. It never grades on its own: acting on a wrong guess
    # silently would mark a student's work against the wrong rubric, which is
    # worse than any amount of scrolling.

    def listen_for_identification(self) -> None:
        try:
            self._room.register_byte_stream_handler(
                IDENTIFY_REQUEST_TOPIC,
                lambda reader, identity: asyncio.create_task(
                    self._on_identify(reader, identity)
                ),
            )
        except Exception as error:  # noqa: BLE001
            logger.warning("could not listen for identification requests: %s", error)

    async def _on_identify(self, reader, identity: str) -> None:
        attributes = dict(getattr(getattr(reader, "info", None), "attributes", None) or {})
        request_id = attributes.get("request_id", "")
        try:
            jpeg = b"".join([chunk async for chunk in reader])
        except Exception as error:  # noqa: BLE001
            logger.warning("identification photo from %s failed: %s", identity, error)
            return

        result = await identify.identify(jpeg, self.catalogue())
        # Always answered, matched or not. The phone opens its picker on this
        # message, so a silent failure would leave it waiting on a spinner with
        # nothing coming.
        await self._publish({"type": "identification", "request_id": request_id, **result})


class AssemblyAssistant(Agent):
    def __init__(self, grader: "Grader | None" = None) -> None:
        instructions = INSTRUCTIONS
        if grader:
            instructions += GRADING_INSTRUCTIONS + "\n" + CRITERIA.text
        super().__init__(instructions=instructions)
        self._grader = grader

    @function_tool
    async def grade_work(
        self, context: RunContext, task_code: str, subtask_code: str
    ) -> str:
        """Photograph the operator's current view and grade it against a subtask rubric.

        Args:
            task_code: The task code from the rubric headings, e.g. AM.II.A.S6.
            subtask_code: The subtask code from the rubric headings, e.g. rivet_layout.
        """
        if not self._grader:
            return "Grading is unavailable in this session — no rubrics are loaded."
        return await self._grader.run(task_code, subtask_code)


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

    realtime = google.beta.realtime.RealtimeModel(
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
    )
    session = AgentSession(llm=realtime)

    # No rubrics means no grading tool and no 15k-token prompt — the assistant is
    # then exactly what it was before this existed.
    grader = Grader(ctx.room) if not CRITERIA.empty else None

    await session.start(
        room=ctx.room,
        agent=AssemblyAssistant(grader),
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

    # The grader reads from its own buffer of the operator's track, not from what
    # was last sent to the realtime model. Those are different pictures: the model
    # is fed downscaled frames on a schedule, and a grade needs the sharpest
    # full-resolution frame available at the moment it is asked for.
    if grader:
        def _attach_frames(track: rtc.Track, publication, participant) -> None:
            if (track.kind == rtc.TrackKind.KIND_VIDEO
                    and participant.identity.startswith("phone-")):
                grader.frames.attach(track)

        ctx.room.on("track_subscribed", _attach_frames)
        ctx.add_shutdown_callback(grader.frames.aclose)

        grader.photos.register()
        grader.listen_for_phone_grades()
        grader.listen_for_identification()

        # The phone builds its subtask picker from this, so it has to arrive after
        # the phone is listening. Published once now for whoever is already here,
        # and again per join — a few kilobytes, and a picker that is empty because
        # the catalogue landed first is a dead end the operator cannot get out of.
        async def _send_catalogue() -> None:
            await grader.publish_catalogue()

        ctx.room.on(
            "participant_connected",
            lambda participant: asyncio.create_task(_send_catalogue()),
        )
        asyncio.create_task(_send_catalogue())

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
        "thinking=%d, media=%s, affective=%s, grading=%s)",
        ctx.room.name,
        REALTIME_MODEL,
        PROACTIVE,
        SILENCE_MS,
        THINKING_BUDGET,
        MEDIA_RESOLUTION,
        AFFECTIVE,
        f"{CRITERIA.rubrics} rubrics/{CRITERIA.tasks} tasks via {grading.GRADER_MODEL}"
        if grader else "off",
    )

    # One short greeting so the operator knows the agent is watching, then quiet.
    #
    # Only where the model supports it. The 3.1 live models cannot take a mid-session
    # instruction at all, and the plugin gates `generate_reply` on exactly this flag —
    # calling it anyway resolves the future with an exception the framework logs as
    # "failed to generate a reply", which reads in the log like a broken session when
    # the session is in fact healthy and listening. Ask the model what it supports
    # rather than pattern-matching its name, so this stays right as models change.
    if realtime.capabilities.mutable_chat_context:
        await session.generate_reply(
            instructions="Greet the operator in one short sentence and tell them to "
            "just ask when they need something. Do not describe what you see."
        )
    else:
        logger.info(
            "%s takes no mid-session instruction, so there is no opening greeting — "
            "the agent is live and listening, it just will not speak first",
            REALTIME_MODEL,
        )


if __name__ == "__main__":
    cli.run_app(server)
