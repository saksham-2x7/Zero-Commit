export function setupMock() {
  let state = {
    status: null, // PENDING, CONFIRMED, REVERTING, REVERTED, PARTIAL_REVERT, FAILED
    change: null
  };

  const generateChangeId = () => '01J' + Math.random().toString(36).substr(2, 9);
  
  const getServerTime = () => Math.floor(Date.now() / 1000);

  window.fetch = async (url, options) => {
    const isApi = url.startsWith('/api/changes');
    if (!isApi) {
      console.warn('Unhandled mock request', url);
      return new Response('Not Found', { status: 404 });
    }

    const auth = options.headers?.Authorization;
    if (!auth || !auth.startsWith('Bearer ')) {
      return new Response(JSON.stringify({
        error: { code: 'UNAUTHORIZED', message: 'Missing or bad token' }
      }), { status: 401 });
    }
    
    // Artificial delay
    await new Promise(r => setTimeout(r, 500));

    // POST /api/changes
    if (url === '/api/changes' && options.method === 'POST') {
      const body = JSON.parse(options.body);
      const server_time = getServerTime();
      state.change = {
        change_id: generateChangeId(),
        status: 'PENDING',
        sg_id: body.sg_id,
        ttl_seconds: body.ttl_seconds,
        created_at: server_time,
        expires_at: server_time + body.ttl_seconds,
        server_time: server_time,
        schedule_name: 'dm-mock',
        delta: body.ops.map((o, i) => ({ op_id: i+1, ...o, applied: null }))
      };
      state.status = 'PENDING';
      
      // Auto-revert mock (Scheduler)
      setTimeout(() => {
        if (state.status === 'PENDING') {
          state.status = 'REVERTED';
          state.change.status = 'REVERTED';
          state.change.reverted_at = getServerTime();
          state.change.revert_trigger = 'SCHEDULE';
          state.change.revert_report = state.change.delta.map(op => ({ op_id: op.op_id, result: 'REVERTED', detail: '' }));
        }
      }, body.ttl_seconds * 1000);

      return new Response(JSON.stringify(state.change), { status: 201 });
    }

    // POST /api/changes/{id}/confirm
    const confirmMatch = url.match(/^\/api\/changes\/([^/]+)\/confirm$/);
    if (confirmMatch && options.method === 'POST') {
      const id = confirmMatch[1];
      if (!state.change || state.change.change_id !== id) {
        return new Response(JSON.stringify({ error: { code: 'CHANGE_NOT_FOUND', message: 'Not found' } }), { status: 404 });
      }
      if (state.status !== 'PENDING') {
        if (state.status === 'CONFIRMED') {
          return new Response(JSON.stringify({ change_id: id, status: 'CONFIRMED', confirmed_at: state.change.confirmed_at, idempotent: true }), { status: 200 });
        }
        return new Response(JSON.stringify({ error: { code: 'CHANGE_NOT_PENDING', message: 'Not pending', status: state.status } }), { status: 409 });
      }
      
      if (getServerTime() >= state.change.expires_at) {
        return new Response(JSON.stringify({ error: { code: 'WINDOW_EXPIRED', message: 'Late confirm' } }), { status: 409 });
      }

      state.status = 'CONFIRMED';
      state.change.status = 'CONFIRMED';
      state.change.confirmed_at = getServerTime();
      return new Response(JSON.stringify({
        change_id: id,
        status: 'CONFIRMED',
        confirmed_at: state.change.confirmed_at,
        idempotent: false
      }), { status: 200 });
    }

    // POST /api/changes/{id}/revert
    const revertMatch = url.match(/^\/api\/changes\/([^/]+)\/revert$/);
    if (revertMatch && options.method === 'POST') {
      const id = revertMatch[1];
      if (!state.change || state.change.change_id !== id) {
        return new Response(JSON.stringify({ error: { code: 'CHANGE_NOT_FOUND', message: 'Not found' } }), { status: 404 });
      }
      if (state.status !== 'PENDING') {
         if (state.status === 'REVERTED') {
           return new Response(JSON.stringify({ change_id: id, status: 'REVERTED', revert_trigger: 'MANUAL', reverted_at: state.change.reverted_at, idempotent: true, revert_report: state.change.revert_report }), { status: 200 });
         }
         return new Response(JSON.stringify({ error: { code: 'CHANGE_NOT_PENDING', message: 'Not pending' } }), { status: 409 });
      }
      
      state.status = 'REVERTED';
      state.change.status = 'REVERTED';
      state.change.reverted_at = getServerTime();
      state.change.revert_trigger = 'MANUAL';
      state.change.revert_report = state.change.delta.map(op => ({ op_id: op.op_id, result: 'REVERTED', detail: '' }));

      return new Response(JSON.stringify({
        change_id: id,
        status: 'REVERTED',
        revert_trigger: 'MANUAL',
        reverted_at: state.change.reverted_at,
        idempotent: false,
        revert_report: state.change.revert_report
      }), { status: 200 });
    }

    // GET /api/changes/{id}
    const getMatch = url.match(/^\/api\/changes\/([^/]+)$/);
    if (getMatch && options.method === 'GET') {
      const id = getMatch[1];
      if (!state.change || state.change.change_id !== id) {
        return new Response(JSON.stringify({ error: { code: 'CHANGE_NOT_FOUND', message: 'Not found' } }), { status: 404 });
      }
      
      // Update server_time dynamically
      const resp = { ...state.change, server_time: getServerTime() };
      return new Response(JSON.stringify(resp), { status: 200 });
    }

    return new Response('Not Found', { status: 404 });
  };
}
