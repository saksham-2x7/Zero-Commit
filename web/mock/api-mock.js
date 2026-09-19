/**
 * Mock API layer for Deadman.
 * When VITE_MOCK=1, main.jsx calls setupMock() which monkey-patches
 * window.fetch so that /api/* requests hit this in-memory backend.
 * The real fetch is preserved for non-API calls (like /config.json).
 */

export function setupMock() {
  const _realFetch = window.fetch.bind(window);

  let state = { change: null };
  let revertTimer = null;

  const now = () => Math.floor(Date.now() / 1000);
  const uid = () => 'chg_' + Math.random().toString(36).substring(2, 10);

  const json = (body, status = 200) =>
    new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    });

  window.fetch = async (url, opts = {}) => {
    // Pass through non-API requests to real fetch
    if (typeof url === 'string' && !url.includes('/api/')) {
      return _realFetch(url, opts);
    }

    const path = typeof url === 'string' ? url : url.toString();
    const method = (opts.method || 'GET').toUpperCase();

    // Auth check
    const auth = opts.headers?.Authorization || opts.headers?.authorization || '';
    if (!auth.startsWith('Bearer ') || auth.length < 10) {
      return json({ error: { code: 'UNAUTHORIZED', message: 'Missing or invalid token' } }, 401);
    }

    // Artificial latency
    await new Promise(r => setTimeout(r, 300));

    // ── POST /api/changes ──
    if (path.endsWith('/api/changes') && method === 'POST') {
      const body = JSON.parse(opts.body);

      if (!body.sg_id) return json({ error: { code: 'INVALID_REQUEST', message: 'sg_id is required' } }, 400);
      if (!body.sg_id.startsWith('sg-')) return json({ error: { code: 'SG_NOT_FOUND', message: 'SG not found' } }, 404);
      if (body.ttl_seconds < 30) return json({ error: { code: 'TTL_OUT_OF_RANGE', message: 'TTL too low (min 30)' } }, 400);
      if (body.ttl_seconds > 600) return json({ error: { code: 'TTL_OUT_OF_RANGE', message: 'TTL too high (max 600)' } }, 400);
      if (!body.ops || body.ops.length < 1 || body.ops.length > 5) return json({ error: { code: 'INVALID_REQUEST', message: '1-5 ops required' } }, 400);

      if (state.change && state.change.status === 'PENDING') {
        return json({ error: { code: 'SG_BUSY', message: 'Another change is active' } }, 409);
      }

      const server_time = now();
      state.change = {
        change_id: uid(),
        status: 'PENDING',
        sg_id: body.sg_id,
        ttl_seconds: body.ttl_seconds,
        created_at: server_time,
        expires_at: server_time + body.ttl_seconds,
        server_time,
        schedule_name: 'dm-mock-' + Date.now(),
        delta: body.ops.map((o, i) => ({ op_id: i + 1, ...o, applied: true })),
      };

      // Auto-revert after TTL
      if (revertTimer) clearTimeout(revertTimer);
      revertTimer = setTimeout(() => {
        if (state.change && state.change.status === 'PENDING') {
          state.change.status = 'REVERTED';
          state.change.reverted_at = now();
          state.change.revert_trigger = 'SCHEDULE';
          state.change.revert_report = state.change.delta.map(op => ({
            op_id: op.op_id, result: 'REVERTED', detail: '',
          }));
        }
      }, body.ttl_seconds * 1000);

      return json(state.change, 201);
    }

    // ── GET /api/changes/{id} ──
    const getMatch = path.match(/\/api\/changes\/([^/]+)$/);
    if (getMatch && method === 'GET') {
      if (!state.change || state.change.change_id !== getMatch[1]) {
        return json({ error: { code: 'CHANGE_NOT_FOUND', message: 'Not found' } }, 404);
      }
      return json({ ...state.change, server_time: now() });
    }

    // ── POST /api/changes/{id}/confirm ──
    const confirmMatch = path.match(/\/api\/changes\/([^/]+)\/confirm$/);
    if (confirmMatch && method === 'POST') {
      const id = confirmMatch[1];
      if (!state.change || state.change.change_id !== id) {
        return json({ error: { code: 'CHANGE_NOT_FOUND', message: 'Not found' } }, 404);
      }
      if (state.change.status === 'CONFIRMED') {
        return json({ change_id: id, status: 'CONFIRMED', confirmed_at: state.change.confirmed_at, idempotent: true });
      }
      if (state.change.status !== 'PENDING') {
        return json({ error: { code: 'CHANGE_NOT_PENDING', message: 'Not pending' } }, 409);
      }
      if (now() >= state.change.expires_at) {
        return json({ error: { code: 'WINDOW_EXPIRED', message: 'TTL expired' } }, 409);
      }

      state.change.status = 'CONFIRMED';
      state.change.confirmed_at = now();
      if (revertTimer) { clearTimeout(revertTimer); revertTimer = null; }
      return json({ change_id: id, status: 'CONFIRMED', confirmed_at: state.change.confirmed_at });
    }

    // ── POST /api/changes/{id}/revert ──
    const revertMatch = path.match(/\/api\/changes\/([^/]+)\/revert$/);
    if (revertMatch && method === 'POST') {
      const id = revertMatch[1];
      if (!state.change || state.change.change_id !== id) {
        return json({ error: { code: 'CHANGE_NOT_FOUND', message: 'Not found' } }, 404);
      }
      if (state.change.status === 'REVERTED') {
        return json({ change_id: id, status: 'REVERTED', revert_trigger: 'MANUAL', reverted_at: state.change.reverted_at, revert_report: state.change.revert_report });
      }
      if (state.change.status !== 'PENDING') {
        return json({ error: { code: 'CHANGE_NOT_PENDING', message: 'Not pending' } }, 409);
      }

      state.change.status = 'REVERTED';
      state.change.reverted_at = now();
      state.change.revert_trigger = 'MANUAL';
      state.change.revert_report = state.change.delta.map(op => ({
        op_id: op.op_id, result: 'REVERTED', detail: '',
      }));
      if (revertTimer) { clearTimeout(revertTimer); revertTimer = null; }
      return json({ change_id: id, status: 'REVERTED', revert_trigger: 'MANUAL', reverted_at: state.change.reverted_at, revert_report: state.change.revert_report });
    }

    return json({ error: { code: 'INTERNAL', message: 'Unknown route' } }, 500);
  };
}
