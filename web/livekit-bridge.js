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
    if (!/^(localhost|127\.0\.0\.1)$/.test(location.hostname)) return;
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
/** Identities this viewer has silenced. Local only — muting someone here does not mute them
 *  for anyone else in the room, which is what a call UI leads people to expect. */
const mutedIdentities = new Set();

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

/** Every participant in the room, local first, by identity. */
function everyone() {
  if (!room) return [];
  return [room.localParticipant, ...room.remoteParticipants.values()].filter(Boolean);
}

function find(identity) {
  return everyone().filter((p) => p.identity === identity)[0] || null;
}

/** The camera track for a participant, local or remote, once it is actually playable. */
function cameraTrack(participant) {
  if (!participant) return null;
  const publication = participant.getTrackPublication(Track.Source.Camera);
  if (!publication || publication.isMuted) return null;
  return publication.track || null;
}

function micOn(participant) {
  const publication = participant && participant.getTrackPublication(Track.Source.Microphone);
  return !!publication && !publication.isMuted;
}

/** Silence someone for this viewer only — the room still hears them. Volume rather than
 *  unsubscribing, so unmuting is instant and nobody else sees a subscription flap. */
function applyLocalMute(participant) {
  if (!participant || participant === room.localParticipant) return;
  const publication = participant.getTrackPublication(Track.Source.Microphone);
  const track = publication && publication.track;
  if (track && typeof track.setVolume === 'function') {
    track.setVolume(mutedIdentities.has(participant.identity) ? 0 : 1);
  }
}

function setParticipantMuted(identity, muted) {
  if (muted) mutedIdentities.add(identity);
  else mutedIdentities.delete(identity);
  applyLocalMute(find(identity));
  onUpdate(snapshot());
}

/** The roster app.js renders, built from whoever is actually in the room. */
function roster() {
  return everyone().map((participant) => {
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
      // Meet renders whatever each participant is actually sending; so does the portal.
      hasVideo: !!cameraTrack(participant),
      micOn: micOn(participant),
      // Whether there is anything to listen to at all, and whether this viewer has silenced
      // it. Muted-at-source (their choice) and muted-by-me (ours) read differently.
      hasAudio: !!participant.getTrackPublication(Track.Source.Microphone),
      mutedByMe: !isLocal && mutedIdentities.has(participant.identity),
      isLocal,
      live: true,
    };
  });
}

/* Attaching the same track to the same element on every render restarts playback and black-
   frames the tile, so each element remembers the track it is already showing. */
const attached = new WeakMap();

/** Attach `identity`'s camera to `element`. Returns true when something is playing. */
function attachVideo(identity, element) {
  if (!element) return false;
  const track = cameraTrack(find(identity));
  if (!track) {
    const previous = attached.get(element);
    if (previous) {
      previous.detach(element);
      attached.delete(element);
    }
    return false;
  }
  if (attached.get(element) === track) return true;
  const previous = attached.get(element);
  if (previous) previous.detach(element);
  track.attach(element);
  attached.set(element, track);
  // The local preview is our own camera coming back at us; unmirrored it reads as wrong.
  element.style.transform = find(identity) === room.localParticipant ? 'scaleX(-1)' : '';
  return true;
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
  return participant ? attachVideo(participant.identity, element) : false;
}

/* — local media, the way Meet's prejoin leaves it: both on, both toggleable — */

async function setCamera(on) {
  if (!room) return false;
  try {
    await room.localParticipant.setCameraEnabled(on);
  } catch (error) {
    report('error', `camera ${on ? 'on' : 'off'} failed: ${error && (error.message || error)}`);
    return false;
  }
  onUpdate(snapshot());
  return true;
}

async function setMicrophone(on) {
  if (!room) return false;
  try {
    await room.localParticipant.setMicrophoneEnabled(on);
  } catch (error) {
    report('error', `microphone ${on ? 'on' : 'off'} failed: ${error && (error.message || error)}`);
    return false;
  }
  onUpdate(snapshot());
  return true;
}

/** Publish camera and mic on join. A denied permission prompt must not fail the join —
 *  watching without sending is still a useful session, so each is attempted separately. */
async function publishLocalMedia() {
  const results = await Promise.allSettled([
    room.localParticipant.setMicrophoneEnabled(true),
    room.localParticipant.setCameraEnabled(true),
  ]);
  results.forEach((result, index) => {
    if (result.status === 'rejected') {
      const what = index === 0 ? 'microphone' : 'camera';
      report('error', `${what} not published: ${result.reason && (result.reason.message || result.reason)}`);
    }
  });
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
    // Our own camera and mic change the roster too — without these the local tile never
    // updates when you toggle them.
    RoomEvent.LocalTrackPublished,
    RoomEvent.LocalTrackUnpublished,
  ].forEach((event) => room.on(event, notify));

  // Remote audio is never rendered on the stage — it just needs somewhere to play.
  room.on(RoomEvent.TrackSubscribed, (track, publication, participant) => {
    if (track.kind === Track.Kind.Audio) {
      audioContainer().appendChild(track.attach());
      // A participant muted before their track arrived must stay muted when it does.
      applyLocalMute(participant);
    }
  });

  room.on(RoomEvent.Disconnected, () => {
    onUpdate({ ...snapshot(), connected: false });
  });
}

function snapshot() {
  const local = room && room.localParticipant;
  return {
    // `active` remains true while LiveKit reconnects. app.js uses it to ensure a temporary
    // network drop never replaces the real roster with the seeded demo participants.
    active: !!room,
    connected: !!room && room.state === 'connected',
    connectionState: room ? room.state : 'disconnected',
    roster: roster(),
    hasOperatorVideo: !!cameraTrack(operator()),
    camOn: !!cameraTrack(local),
    micOn: micOn(local),
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

  mutedIdentities.clear();
  room = new Room({ adaptiveStream: true, dynacast: true });
  wire();

  try {
    report('info', `connecting to ${details.serverUrl} room ${roomName}`);
    await room.connect(details.serverUrl, details.token);
    report('info', `connected to ${roomName}`);
  } catch (error) {
    const failedRoom = room;
    room = null;
    try {
      await failedRoom.disconnect();
    } catch (_) {
      /* The failed connection may already be fully closed. */
    }
    report('error', `connect to ${roomName} failed: ${error && (error.message || error)}`);
    return { ok: false, error: `Could not join ${roomName}: ${error.message || error}` };
  }

  // Browsers block autoplay until a gesture; join is one, so this is the moment to ask.
  try {
    await room.startAudio();
  } catch (_) {
    /* Non-fatal — video still plays, audio unblocks on the next click. */
  }

  // A typed-code submit is an explicit join gesture. A shared URL passes false so merely
  // opening a link never turns on this browser's camera or microphone.
  if (handlers.publishLocalMedia !== false) await publishLocalMedia();

  onUpdate(snapshot());
  return { ok: true, room: roomName };
}

async function disconnect() {
  if (!room) return;
  const closing = room;
  room = null;
  mutedIdentities.clear();
  if (audioSink) audioSink.innerHTML = '';
  await closing.disconnect();
}

window.PortalLive = {
  available: true,
  connect,
  disconnect,
  attachOperatorVideo,
  attachVideo,
  setCamera,
  setMicrophone,
  setParticipantMuted,
  snapshot,
  canonical,
};

// app.js may have initialised already; tell it the transport arrived either way. app.js also
// checks for `window.PortalLive` directly, since this can fire before its listener exists.
report('info', 'transport ready');
window.dispatchEvent(new CustomEvent('portal-live-ready'));
