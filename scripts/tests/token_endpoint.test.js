const assert = require('node:assert/strict');
const test = require('node:test');

const handler = require('../../api/token.js');

function responseRecorder() {
  return {
    statusCode: 200,
    payload: null,
    status(code) {
      this.statusCode = code;
      return this;
    },
    json(payload) {
      this.payload = payload;
      return this;
    },
  };
}

function decodeClaims(token) {
  const claims = token.split('.')[1]
    .replace(/-/g, '+')
    .replace(/_/g, '/');
  return JSON.parse(Buffer.from(claims, 'base64').toString('utf8'));
}

test.beforeEach(() => {
  process.env.LIVEKIT_URL = 'wss://example.livekit.cloud';
  process.env.LIVEKIT_API_KEY = 'test-key';
  process.env.LIVEKIT_API_SECRET = 'test-secret';
});

test('mints a room-scoped token with a server-generated viewer identity', () => {
  const response = responseRecorder();

  handler(
    {
      method: 'POST',
      body: { room: 'ABC-DEF', identity: 'phone-ABCDEF' },
    },
    response,
  );

  assert.equal(response.statusCode, 200);
  const claims = decodeClaims(response.payload.token);
  assert.match(claims.sub, /^portal-[0-9a-f]{16}$/);
  assert.notEqual(claims.sub, 'phone-ABCDEF');
  assert.equal(claims.video.room, 'ABCDEF');
  assert.equal(claims.video.canPublish, true);
  assert.deepEqual(claims.video.canPublishSources, [
    'camera', 'microphone', 'screen_share', 'screen_share_audio',
  ]);
});

test('rejects partial, oversized, and excluded-glyph room codes', () => {
  for (const room of ['ABC', 'ABCDEFG', 'OOO-111']) {
    const response = responseRecorder();

    handler({ method: 'POST', body: { room } }, response);

    assert.equal(response.statusCode, 400, room);
  }
});
