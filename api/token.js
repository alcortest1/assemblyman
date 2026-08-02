/* Vercel serverless function: mint a LiveKit token for a portal viewer.
 *
 * The deployed counterpart to scripts/portal_dev_server.py. The API secret stays here, in the
 * function's environment, and never reaches the browser — which is why the portal asks for a
 * token rather than signing one itself.
 *
 * Set in the Vercel project:
 *   LIVEKIT_URL         wss://<project>.livekit.cloud
 *   LIVEKIT_API_KEY
 *   LIVEKIT_API_SECRET
 */

const crypto = require('crypto');

const ROOM_PATTERN = /[^ABCDEFGHJKMNPQRSTUVWXYZ23456789]/g;
const ROOM_CODE_LENGTH = 6;
const TOKEN_TTL_SECONDS = 6 * 60 * 60;

function base64url(input) {
  return Buffer.from(input).toString('base64')
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=/g, '');
}

function mint(apiKey, apiSecret, room, identity) {
  const now = Math.floor(Date.now() / 1000);
  const header = { alg: 'HS256', typ: 'JWT' };
  const claims = {
    iss: apiKey,
    sub: identity,
    // Back-dated so a browser clock running fast is not rejected outright.
    nbf: now - 30,
    exp: now + TOKEN_TTL_SECONDS,
    name: 'Portal viewer',
    video: {
      roomJoin: true,
      room,
      // Portal participants publish their own camera and mic, the way LiveKit Meet does, so
      // the operator and everyone else in the room can see and hear them. The browser still
      // decides whether to actually turn either on — this only grants the permission.
      canPublish: true,
      canPublishSources: ['camera', 'microphone'],
      canSubscribe: true,
      canPublishData: true,
    },
  };

  const signingInput =
    `${base64url(JSON.stringify(header))}.${base64url(JSON.stringify(claims))}`;
  const signature = crypto.createHmac('sha256', apiSecret).update(signingInput).digest();
  return `${signingInput}.${base64url(signature)}`;
}

module.exports = (request, response) => {
  if (request.method !== 'POST') {
    response.status(405).json({ error: 'POST only' });
    return;
  }

  const { LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET } = process.env;
  if (!LIVEKIT_URL || !LIVEKIT_API_KEY || !LIVEKIT_API_SECRET) {
    response.status(500).json({ error: 'LiveKit environment variables are not configured.' });
    return;
  }

  let body = request.body;
  if (typeof body === 'string') {
    try {
      body = JSON.parse(body);
    } catch (_) {
      response.status(400).json({ error: 'malformed JSON' });
      return;
    }
  }
  body = body || {};

  // Normalise the way the iOS app does, so a code typed with its separator finds the room.
  const room = String(body.room || '').toUpperCase().replace(ROOM_PATTERN, '');
  if (room.length !== ROOM_CODE_LENGTH) {
    response.status(400).json({ error: 'room code must contain exactly 6 valid characters' });
    return;
  }

  // Never accept an identity from the caller. Reusing `phone-<room>` would evict the
  // operator because LiveKit identities are unique inside a room.
  const identity = `portal-${crypto.randomBytes(8).toString('hex')}`;

  response.status(200).json({
    serverUrl: LIVEKIT_URL,
    room,
    token: mint(LIVEKIT_API_KEY, LIVEKIT_API_SECRET, room, identity),
  });
};
