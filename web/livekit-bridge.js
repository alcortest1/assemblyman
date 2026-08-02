/* LiveKit transport for the portal.
 *
 * app.js owns screens and state and knows nothing about WebRTC; this module owns the room and
 * hands back a plain roster in the shape app.js already renders. The two talk through
 * `window.PortalLive` so app.js can stay a classic script while this stays a module.
 *
 * The one dependency in the project, loaded from a CDN so there is still no build step. If the
 * import fails the portal falls back to its seeded demo roster rather than breaking.
 */
const SDK_URL = 'https://cdn.jsdelivr.net/npm/livekit-client@2.7.5/dist/livekit-client.esm.mjs';

/** Report to the dev server's terminal as well as the console — a failure nobody sees is
 *  indistinguishable from the demo roster, which is exactly how this went unnoticed once. */
function report(level, message) {
  const text = String(message);
  // eslint-disable-next-line no-console
  console[level === 'error' ? 'error' : 'log'](`[portal] ${text}`);
  try {
    fetch('/api/log', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ level, message: text }),
      keepalive: true,
    }).catch(() => {});
  } catch (_) {
    /* Diagnostics must never break the page. */
  }
}

// A static import failure cannot be caught from inside the module it breaks, so the SDK is
// pulled in dynamically: a blocked CDN then leaves the portal on its demo roster with a
// reason recorded, rather than a silently dead page.
let Room;
let RoomEvent;
let Track;
let ParticipantKind;
try {
  ({ Room, RoomEvent, Track, ParticipantKind } = await import(SDK_URL));
  report('info', 'livekit-client loaded');
} catch (error) {
  report('error', `livekit-client failed to load from ${SDK_URL}: ${error && error.message}`);
  throw error;
}

const TOKEN_ENDPOINT = '/api/token';
const ROOM_ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789';
const ROOM_CODE_LENGTH = 6;

let room = null;
let onUpdate = () => {};
let audioSink = null;

/** Strip separators the way the iOS app does, so a typed code reaches the same room. */
function canonical(code) {
  return Array.from(String(code || '').toUpperCase())
    .filter((character) => ROOM_ALPHABET.includes(character))
    .join('');
}

function audioContainer() {
  if (!audioSink) {
    audioSink = document.createElement('div');
    audioSink.id = 'lk-audio';
    audioSink.style.display = 'none';
    document.body.appendChild(audioSink);
  }
  return audioSink;
}

/** The operator is the phone relay; everything else is an agent or a watching human. */
function classify(participant) {
  const identity = participant.identity || '';
  if (participant.kind === ParticipantKind.AGENT) return 'agent';
  if (/^phone-/.test(identity)) return 'op';
  return 'viewer';
}

function initials(name) {
  return String(name || '?')
    .split(/\s+/)
    .map((w) => w[0])
    .join('')
    .slice(0, 2)
    .toUpperCase();
}

function describe(participant, kind) {
  if (kind === 'op') return 'Ray-Ban Meta · POV';
  if (kind === 'agent') return 'Agent · speaking';
  return 'Viewer';
}

/** The roster app.js renders, built from whoever is actually in the room. */
function roster() {
  if (!room) return [];
  const everyone = [room.localParticipant, ...room.remoteParticipants.values()];
  return everyone.filter(Boolean).map((participant) => {
    const kind = classify(participant);
    const isLocal = participant === room.localParticipant;
    const name = participant.name || (isLocal ? 'You' : participant.identity);
    return {
      id: participant.identity,
      name: isLocal ? 'You' : name,
      role: isLocal ? 'Viewer · this browser' : describe(participant, kind),
      speaking: !!participant.isSpeaking,
      isOp: kind === 'op',
      isAgent: kind === 'agent',
      isViewer: kind === 'viewer',
      initials: initials(isLocal ? 'You' : name),
      live: true,
    };
  });
}

function operator() {
  if (!room) return null;
  for (const participant of room.remoteParticipants.values()) {
    if (classify(participant) === 'op') return participant;
  }
  return null;
}

/** Attach the operator's camera track to `element`. Returns true when something is playing. */
function attachOperatorVideo(element) {
  const participant = operator();
  if (!participant || !element) return false;
  const publication = participant.getTrackPublication(Track.Source.Camera);
  const track = publication && publication.track;
  if (!track) return false;
  track.attach(element);
  return true;
}

function wire() {
  const notify = () => onUpdate(snapshot());
  [
    RoomEvent.ParticipantConnected,
    RoomEvent.ParticipantDisconnected,
    RoomEvent.TrackSubscribed,
    RoomEvent.TrackUnsubscribed,
    RoomEvent.TrackMuted,
    RoomEvent.TrackUnmuted,
    RoomEvent.ActiveSpeakersChanged,
    RoomEvent.ConnectionStateChanged,
  ].forEach((event) => room.on(event, notify));

  // Remote audio is never rendered on the stage — it just needs somewhere to play.
  room.on(RoomEvent.TrackSubscribed, (track) => {
    if (track.kind === Track.Kind.Audio) {
      audioContainer().appendChild(track.attach());
    }
  });

  room.on(RoomEvent.Disconnected, () => {
    onUpdate({ ...snapshot(), connected: false });
  });
}

function snapshot() {
  return {
    connected: !!room && room.state === 'connected',
    roster: roster(),
    hasOperatorVideo: !!operator()?.getTrackPublication(Track.Source.Camera)?.track,
  };
}

async function connect(code, handlers = {}) {
  onUpdate = handlers.onUpdate || (() => {});
  const roomName = canonical(code);
  if (roomName.length !== ROOM_CODE_LENGTH) {
    return { ok: false, error: 'Enter the full six-character room code.' };
  }

  let details;
  try {
    const response = await fetch(TOKEN_ENDPOINT, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ room: roomName }),
    });
    if (!response.ok) {
      const body = await response.text();
      return { ok: false, error: `Token request failed (${response.status}). ${body.slice(0, 120)}` };
    }
    details = await response.json();
  } catch (error) {
    return {
      ok: false,
      error: 'No token service. Run scripts/portal_dev_server.py rather than a plain file server.',
    };
  }

  room = new Room({ adaptiveStream: true, dynacast: true });
  wire();

  try {
    report('info', `connecting to ${details.serverUrl} room ${roomName}`);
    await room.connect(details.serverUrl, details.token);
    report('info', `connected to ${roomName}`);
  } catch (error) {
    room = null;
    report('error', `connect to ${roomName} failed: ${error && (error.message || error)}`);
    return { ok: false, error: `Could not join ${roomName}: ${error.message || error}` };
  }

  // Browsers block autoplay until a gesture; join is one, so this is the moment to ask.
  try {
    await room.startAudio();
  } catch (_) {
    /* Non-fatal — video still plays, audio unblocks on the next click. */
  }

  onUpdate(snapshot());
  return { ok: true, room: roomName };
}

async function disconnect() {
  if (!room) return;
  const closing = room;
  room = null;
  if (audioSink) audioSink.innerHTML = '';
  await closing.disconnect();
}

window.PortalLive = {
  available: true,
  connect,
  disconnect,
  attachOperatorVideo,
  snapshot,
  canonical,
};

// app.js may have initialised already; tell it the transport arrived either way. app.js also
// checks for `window.PortalLive` directly, since this can fire before its listener exists.
report('info', 'transport ready');
window.dispatchEvent(new CustomEvent('portal-live-ready'));
