import React, { useState, useEffect, useCallback, useRef } from 'react';
import './App.css';

/* ── Spec §5 Error Code Translation ── */
const ERROR_MAP = {
  'INVALID_REQUEST': 'The request was invalid. Please check your inputs.',
  'TTL_OUT_OF_RANGE': 'The TTL (time-to-live) is out of the allowed range.',
  'UNSUPPORTED_RULE': 'Only IPv4 CIDR ingress rules are supported.',
  'UNAUTHORIZED': 'Missing or invalid bearer token.',
  'SG_NOT_MANAGED': 'The target Security Group is not managed by Deadman or belongs to another stage.',
  'SG_NOT_FOUND': 'Security Group not found.',
  'CHANGE_NOT_FOUND': 'Change ID not found.',
  'SG_BUSY': 'There is already an active change on this Security Group.',
  'RULE_ALREADY_EXISTS': 'The rule you are trying to authorize already exists.',
  'RULE_NOT_FOUND': 'The rule you are trying to revoke does not exist.',
  'NOT_APPLIED_YET': 'The change is still being applied. Please wait.',
  'WINDOW_EXPIRED': 'The time window to confirm this change has expired.',
  'CHANGE_NOT_PENDING': 'This change is no longer pending.',
  'REVERT_IN_PROGRESS': 'A revert is already in progress.',
  'SCHEDULE_FAILED': 'Failed to schedule the auto-revert. Nothing was applied.',
  'APPLY_FAILED_REVERTED': 'Failed to apply the change. Any partial changes were reverted.',
  'APPLY_FAILED_REVERT_INCOMPLETE': 'Failed to apply the change, and the revert was incomplete. Check your SG manually.',
  'INTERNAL': 'An internal server error occurred.'
};

function formatOpDetails(op) {
  const proto = op.protocol || 'tcp';
  const portStr = (op.from_port === op.to_port || op.to_port === undefined)
    ? `${op.from_port}`
    : `${op.from_port}–${op.to_port}`;
  const cidr = op.cidr || op.source || '0.0.0.0/0';
  return `${proto} ${portStr} from ${cidr}`;
}

function friendlyError(data, status) {
  if (status === 401) return 'Missing or invalid bearer token.';
  if (status === 403 && (!data?.error?.code || data?.error?.code === 'UNAUTHORIZED')) {
    return 'Unauthorized or forbidden: Invalid bearer token.';
  }
  if (!data) return 'Network error — check your connection.';
  if (data.error?.message) return data.error.message;
  if (data.message) return data.message;
  const code = data.error?.code || data.error;
  if (code && ERROR_MAP[code]) return ERROR_MAP[code];
  return 'Something went wrong.';
}

function formatTime(epoch) {
  if (!epoch) return '—';
  return new Date(epoch * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function generatePlainSummary(ops, ttl) {
  if (!ops || ops.length === 0) return '';
  const describeOp = (op) => {
    const actionWord = op.action === 'REVOKE' ? 'closed to' : 'opened to';
    const portWord = (op.from_port === op.to_port || op.to_port === undefined)
      ? `Port ${op.from_port}`
      : `Ports ${op.from_port}–${op.to_port}`;
    const targetWord = (op.cidr || '').trim() === '0.0.0.0/0' ? 'everyone' : (op.cidr || '0.0.0.0/0');
    return `${portWord} will be ${actionWord} ${targetWord}`;
  };
  if (ops.length === 1) {
    return `${describeOp(ops[0])} for ${ttl} s unless you confirm.`;
  }
  if (ops.length === 2) {
    return `${describeOp(ops[0])} and ${describeOp(ops[1]).toLowerCase()} for ${ttl} s unless you confirm.`;
  }
  return `${ops.length} security group rules will be modified for ${ttl} s unless you confirm.`;
}

function checkDangerousRevoke(ops) {
  if (!ops || !Array.isArray(ops)) return false;
  return ops.some(op => {
    if (op.action !== 'REVOKE') return false;
    if ((op.protocol || '').toLowerCase() !== 'tcp') return false;
    const cidr = (op.cidr || '').trim();
    if (cidr !== '0.0.0.0/0') return false;
    const from = parseInt(op.from_port, 10);
    const to = parseInt(op.to_port, 10);
    return [22, 80, 443].some(p => p >= from && p <= to);
  });
}

export default function App() {
  const [apiBase, setApiBase] = useState('/api');
  const [token, setToken] = useState(() => sessionStorage.getItem('dm_token') || '');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const [form, setForm] = useState({
    sg_id: '',
    ttl_seconds: 90,
    ops: [{ action: 'REVOKE', protocol: 'tcp', from_port: 80, to_port: 80, cidr: '0.0.0.0/0' }]
  });

  const [activeChange, setActiveChange] = useState(null);
  const [countdown, setCountdown] = useState(null);
  const pollRef = useRef(null);

  // Load config
  useEffect(() => {
    fetch('/config.json')
      .then(res => res.json())
      .then(data => {
        if (data.apiBase) setApiBase(data.apiBase.trim().replace(/\/+$/, ''));
      })
      .catch(() => setApiBase('/api'));
  }, []);

  // Sync token to sessionStorage
  useEffect(() => {
    if (token) {
      sessionStorage.setItem('dm_token', token);
    }
  }, [token]);

  const getAuthHeaders = useCallback(() => ({
    'Authorization': `Bearer ${token}`,
    'Content-Type': 'application/json'
  }), [token]);

  // Polling mechanism
  useEffect(() => {
    if (pollRef.current) clearInterval(pollRef.current);
    if (!activeChange || !token) return;

    const st = activeChange.status;
    if (st !== 'PENDING' && st !== 'REVERTING') return;

    const poll = async () => {
      try {
        const res = await fetch(`${apiBase}/changes/${activeChange.change_id || activeChange.id}`, {
          headers: getAuthHeaders()
        });
        if (res.ok) {
          const data = await res.json();
          setActiveChange(data);
          if (data.status === 'PENDING' && data.expires_at && data.server_time) {
            const remaining = data.expires_at - data.server_time;
            setCountdown(remaining > 0 ? remaining : 0);
          } else {
            setCountdown(null);
          }
        }
      } catch {
        // silent on transient network poll failure
      }
    };

    poll();
    pollRef.current = setInterval(poll, 1000);
    return () => clearInterval(pollRef.current);
  }, [activeChange?.change_id, activeChange?.id, activeChange?.status, apiBase, getAuthHeaders, token]);

  const submitChange = async (e) => {
    e.preventDefault();
    setError('');
    if (!token.trim()) {
      setError('Please enter a bearer token');
      return;
    }
    if (form.ops.length === 0 || form.ops.length > 5) {
      setError('1 to 5 operations allowed');
      return;
    }

    setLoading(true);
    try {
      const res = await fetch(`${apiBase}/changes`, {
        method: 'POST',
        headers: getAuthHeaders(),
        body: JSON.stringify(form)
      });
      const data = await res.json().catch(() => null);
      if (res.ok && data) {
        setActiveChange(data);
        if (data.expires_at && data.server_time) {
          const remaining = data.expires_at - data.server_time;
          setCountdown(remaining > 0 ? remaining : 0);
        }
      } else {
        setError(friendlyError(data, res.status) || `HTTP Error: ${res.status}`);
      }
    } catch {
      setError('Network error — unable to reach API');
    } finally {
      setLoading(false);
    }
  };

  const handleAction = async (action) => {
    setError('');
    if (!token.trim()) {
      setError('Please enter a bearer token');
      return;
    }
    setLoading(true);
    const changeId = activeChange.change_id || activeChange.id;
    try {
      const res = await fetch(`${apiBase}/changes/${changeId}/${action}`, {
        method: 'POST',
        headers: getAuthHeaders()
      });
      const data = await res.json().catch(() => null);
      if (res.ok && data) {
        setActiveChange(prev => ({ ...prev, ...data }));
        setCountdown(null);
      } else {
        setError(friendlyError(data, res.status) || `HTTP Error: ${res.status}`);
      }
    } catch {
      setError('Network error — unable to reach API');
    } finally {
      setLoading(false);
    }
  };

  const addOp = () => {
    if (form.ops.length >= 5) return;
    setForm(prev => ({
      ...prev,
      ops: [...prev.ops, { action: 'REVOKE', protocol: 'tcp', from_port: 80, to_port: 80, cidr: '0.0.0.0/0' }]
    }));
  };

  const updateOp = (index, field, value) => {
    const newOps = [...form.ops];
    newOps[index][field] = field.includes('port') ? (parseInt(value, 10) || 0) : value;
    setForm(prev => ({ ...prev, ops: newOps }));
  };

  const removeOp = (index) => {
    setForm(prev => ({ ...prev, ops: prev.ops.filter((_, i) => i !== index) }));
  };

  const applyPreset = (presetKey) => {
    if (presetKey === 'close-80') {
      setForm(prev => ({
        ...prev,
        ops: [{ action: 'REVOKE', protocol: 'tcp', from_port: 80, to_port: 80, cidr: '0.0.0.0/0' }]
      }));
    } else if (presetKey === 'open-8081') {
      setForm(prev => ({
        ...prev,
        ops: [{ action: 'AUTHORIZE', protocol: 'tcp', from_port: 8081, to_port: 8081, cidr: '10.0.0.0/8' }]
      }));
    }
  };

  // Larger SVG ring calculations (radius 100, svg 250x250)
  const radius = 100;
  const circumference = 2 * Math.PI * radius;
  const ttl = activeChange?.ttl_seconds || form.ttl_seconds || 90;
  const strokeDashoffset = countdown !== null
    ? circumference - (Math.max(0, countdown) / ttl) * circumference
    : 0;

  const ringColor = countdown === null ? 'var(--border)'
    : countdown < 15 ? 'var(--danger)'
    : countdown < 30 ? 'var(--warning)'
    : 'var(--accent)';

  const currentStatus = activeChange?.status || '';
  const isPending = currentStatus === 'PENDING';
  const isTerminal = Boolean(
    currentStatus &&
    currentStatus !== 'PENDING' &&
    currentStatus !== 'REVERTING'
  );

  const hasDangerousRevoke = checkDangerousRevoke(form.ops);
  const plainSummary = generatePlainSummary(form.ops, form.ttl_seconds);

  return (
    <div className="app-container">
      <header className="app-header">
        <div className="logo-group">
          <span className="logo-icon" aria-hidden="true">🛡️</span>
          <div>
            <h1 className="logo-title">Deadman</h1>
            <span className="logo-subtitle">Commit-Confirmed Security Groups</span>
          </div>
        </div>
        <div className="token-wrapper">
          <span className="token-icon" aria-hidden="true">🔑</span>
          <input
            type="password"
            name="deadman_api_token"
            id="deadman_api_token"
            autoComplete="off"
            autoCorrect="off"
            autoCapitalize="off"
            spellCheck="false"
            data-lpignore="true"
            data-form-type="other"
            data-1p-ignore="true"
            value={token}
            onChange={e => setToken(e.target.value)}
            placeholder="Bearer Token (Required)"
            className="token-input"
            aria-label="Bearer Token"
          />
        </div>
      </header>

      {error && (
        <div role="alert" className="alert-banner alert-danger">
          <span className="alert-icon" aria-hidden="true">⛔</span>
          <div className="alert-text">{error}</div>
          <button className="alert-close" onClick={() => setError('')} aria-label="Dismiss error">×</button>
        </div>
      )}

      {!activeChange ? (
        <form onSubmit={submitChange} className="card form-card">
          <div className="form-header-row">
            <div>
              <h2 className="card-title">Draft Security Group Change</h2>
              <p className="card-subtitle">
                Changes will automatically revert after the timeout unless confirmed by an operator.
              </p>
            </div>
          </div>

          {/* Quick-action presets (prefill form only, never submit) */}
          <div className="presets-box">
            <div className="presets-label">Quick Presets (prefill only):</div>
            <div className="presets-btn-group">
              <button
                type="button"
                className="btn-preset"
                onClick={() => applyPreset('close-80')}
                title="Prefill with REVOKE tcp 80-80 0.0.0.0/0"
              >
                <span className="preset-pill preset-revoke">REVOKE</span>
                <span>Close port 80 (0.0.0.0/0)</span>
              </button>
              <button
                type="button"
                className="btn-preset"
                onClick={() => applyPreset('open-8081')}
                title="Prefill with AUTHORIZE tcp 8081-8081 10.0.0.0/8"
              >
                <span className="preset-pill preset-authorize">AUTHORIZE</span>
                <span>Open port 8081 (10.0.0.0/8)</span>
              </button>
            </div>
          </div>

          <div className="form-row-2col">
            <div className="form-field">
              <label htmlFor="sg_id">Security Group ID</label>
              <input
                id="sg_id"
                type="text"
                value={form.sg_id}
                onChange={e => setForm({ ...form, sg_id: e.target.value })}
                required
                placeholder="sg-0123456789abcdef0"
                className="form-input"
              />
            </div>
            <div className="form-field">
              <label htmlFor="ttl">Timeout / TTL (seconds)</label>
              <input
                id="ttl"
                type="number"
                value={form.ttl_seconds}
                onChange={e => setForm({ ...form, ttl_seconds: parseInt(e.target.value, 10) || 0 })}
                required
                min="1"
                className="form-input"
              />
            </div>
          </div>

          <div className="rules-section-header">
            <h3>Rules ({form.ops.length}/5)</h3>
            {form.ops.length < 5 && (
              <button type="button" onClick={addOp} className="btn-secondary btn-sm">
                + Add Rule
              </button>
            )}
          </div>

          <div className="rules-list">
            {form.ops.map((op, i) => (
              <div key={i} className="rule-row">
                <select
                  value={op.action}
                  onChange={e => updateOp(i, 'action', e.target.value)}
                  className="rule-select-action"
                  aria-label="Action"
                >
                  <option value="REVOKE">REVOKE</option>
                  <option value="AUTHORIZE">AUTHORIZE</option>
                </select>
                <select
                  value={op.protocol}
                  onChange={e => updateOp(i, 'protocol', e.target.value)}
                  className="rule-select-proto"
                  aria-label="Protocol"
                >
                  <option value="tcp">tcp</option>
                  <option value="udp">udp</option>
                  <option value="icmp">icmp</option>
                </select>
                <input
                  type="number"
                  value={op.from_port}
                  onChange={e => updateOp(i, 'from_port', e.target.value)}
                  placeholder="From"
                  className="rule-input-port"
                  required
                  aria-label="From port"
                />
                <span className="rule-separator" aria-hidden="true">–</span>
                <input
                  type="number"
                  value={op.to_port}
                  onChange={e => updateOp(i, 'to_port', e.target.value)}
                  placeholder="To"
                  className="rule-input-port"
                  required
                  aria-label="To port"
                />
                <input
                  type="text"
                  value={op.cidr}
                  onChange={e => updateOp(i, 'cidr', e.target.value)}
                  placeholder="0.0.0.0/0"
                  className="rule-input-cidr"
                  required
                  aria-label="IPv4 CIDR"
                />
                {form.ops.length > 1 && (
                  <button
                    type="button"
                    onClick={() => removeOp(i)}
                    className="btn-remove-rule"
                    aria-label="Remove Rule"
                    title="Remove rule"
                  >
                    ×
                  </button>
                )}
              </div>
            ))}
          </div>

          <div className="planned-delta-box">
            <h4>Planned Delta</h4>
            <div className="planned-delta-lines">
              {form.ops.map((op, i) => (
                <div key={i} className="delta-line">
                  <span className={`badge-op ${op.action === 'REVOKE' ? 'badge-revoke' : 'badge-authorize'}`}>
                    {op.action}
                  </span>
                  <span className="delta-text">{formatOpDetails(op)}</span>
                </div>
              ))}
            </div>
          </div>

          {/* Calm warning when critical port access may be severed */}
          {hasDangerousRevoke && (
            <div className="calm-warning-box" role="status">
              <span className="calm-warning-icon" aria-hidden="true">⚠️</span>
              <div className="calm-warning-content">
                <strong>Access warning:</strong> This may cut off access. Deadman will undo it after the timer unless you confirm.
              </div>
            </div>
          )}

          {/* Plain-language summary above Apply button */}
          {plainSummary && (
            <div className="plain-summary-box">
              <span className="summary-icon" aria-hidden="true">ℹ️</span>
              <span className="summary-text">{plainSummary}</span>
            </div>
          )}

          <button type="submit" disabled={loading} className="btn-primary btn-block">
            {loading ? 'Applying Change…' : 'Apply Change'}
          </button>
        </form>
      ) : (
        <div className="card status-card">
          <div className="status-top-bar">
            <div>
              <h2 className="change-heading">
                Change <span className="change-id-badge">{activeChange.change_id || activeChange.id}</span>
              </h2>
              {activeChange.sg_id && (
                <div className="change-sg-id">Target: <code>{activeChange.sg_id}</code></div>
              )}
            </div>
            <div className="status-top-actions">
              <span className={`status-pill status-${currentStatus.toLowerCase()}`}>
                {currentStatus}
              </span>
              <button
                onClick={() => { setActiveChange(null); setCountdown(null); setError(''); }}
                className="btn-secondary btn-sm"
              >
                ← Start New
              </button>
            </div>
          </div>

          {(currentStatus === 'PARTIAL_REVERT' || currentStatus === 'FAILED') && (
            <div role="alert" className="alert-banner alert-critical">
              <span className="alert-icon" aria-hidden="true">⚠️</span>
              <div>
                <strong>
                  {currentStatus === 'FAILED'
                    ? 'CRITICAL: Change failed to apply or revert fully.'
                    : 'WARNING: Change was only partially reverted.'}
                </strong>
                {activeChange.failure_reason && (
                  <div className="alert-detail">{activeChange.failure_reason}</div>
                )}
              </div>
            </div>
          )}

          {/* Result summary card when change reaches a terminal state */}
          {isTerminal && (
            <div className={`result-summary-card result-summary-${currentStatus.toLowerCase()}`}>
              <div className="result-summary-header">
                <span className="result-summary-badge-icon" aria-hidden="true">
                  {currentStatus === 'CONFIRMED' ? '✓' : currentStatus === 'REVERTED' ? '↩' : '!'}
                </span>
                <div className="result-summary-text">
                  <h3 className="result-summary-title">
                    {currentStatus === 'CONFIRMED'
                      ? 'Change Confirmed — Permanent'
                      : currentStatus === 'REVERTED'
                        ? activeChange.revert_trigger === 'MANUAL'
                          ? 'Manual Revert Complete'
                          : 'Auto-Revert Complete (Timer)'
                        : 'Change Ended with Warnings'}
                  </h3>
                  <p className="result-summary-detail">
                    {currentStatus === 'CONFIRMED'
                      ? 'The change was confirmed by an operator and is permanent. Rollback schedule deleted and lock released.'
                      : currentStatus === 'REVERTED'
                        ? activeChange.revert_trigger === 'MANUAL'
                          ? 'Operator triggered manual rollback. Security group rules restored to original state.'
                          : 'Safety timer elapsed without confirmation. Deadman reverted only its recorded changes.'
                        : 'Operation ended with partial revert or failure. Verify rules in AWS console.'}
                  </p>
                </div>
              </div>

              <div className="result-meta-grid">
                <div className="result-meta-item">
                  <span className="meta-label">Target SG</span>
                  <span className="meta-val"><code>{activeChange.sg_id || form.sg_id || '—'}</code></span>
                </div>
                <div className="result-meta-item">
                  <span className="meta-label">Result State</span>
                  <span className="meta-val font-semibold">
                    {currentStatus} {activeChange.revert_trigger ? `(${activeChange.revert_trigger})` : ''}
                  </span>
                </div>
                <div className="result-meta-item">
                  <span className="meta-label">Active Duration</span>
                  <span className="meta-val">
                    {activeChange.created_at && (activeChange.confirmed_at || activeChange.reverted_at)
                      ? `${Math.max(0, (activeChange.confirmed_at || activeChange.reverted_at) - activeChange.created_at)}s`
                      : `${activeChange.ttl_seconds || form.ttl_seconds}s`}
                  </span>
                </div>
                <div className="result-meta-item">
                  <span className="meta-label">Completed At</span>
                  <span className="meta-val">
                    {formatTime(activeChange.confirmed_at || activeChange.reverted_at)}
                  </span>
                </div>
              </div>
            </div>
          )}

          <div className="action-center">
            <div className="countdown-column">
              {countdown !== null ? (
                <div className="countdown-ring-wrap">
                  <svg width="250" height="250" className="countdown-svg">
                    <circle cx="125" cy="125" r={radius} className="ring-bg" strokeWidth="14" />
                    <circle
                      cx="125"
                      cy="125"
                      r={radius}
                      className="ring-bar"
                      stroke={ringColor}
                      strokeWidth="14"
                      strokeDasharray={circumference}
                      strokeDashoffset={strokeDashoffset}
                    />
                  </svg>
                  <div className="countdown-inner">
                    <span className="countdown-value" style={{ color: ringColor }}>{countdown}</span>
                    <span className="countdown-unit">SECONDS REMAINING</span>
                  </div>
                </div>
              ) : (
                <div className={`countdown-static status-${currentStatus.toLowerCase()}`}>
                  <div className="static-icon">
                    {currentStatus === 'CONFIRMED' ? '✓'
                      : currentStatus === 'REVERTED' ? '↩'
                      : currentStatus === 'REVERTING' ? '↻'
                      : '✕'}
                  </div>
                  <div className="static-label">{currentStatus}</div>
                </div>
              )}
              <div className="countdown-subtext">Auto-reverts unless confirmed</div>

              {/* Note while status is PENDING */}
              {isPending && (
                <div className="live-pending-notice" role="status">
                  <span className="live-indicator-dot" aria-hidden="true">●</span>
                  <span>The change is live. Services may be unreachable until you confirm or it reverts.</span>
                </div>
              )}
            </div>

            <div className="action-button-group">
              <button
                onClick={() => handleAction('confirm')}
                disabled={!isPending || loading}
                className="btn-confirm"
              >
                {loading ? 'Processing…' : 'CONFIRM'}
              </button>
              <button
                onClick={() => handleAction('revert')}
                disabled={!isPending || loading}
                className="btn-revert"
              >
                {loading ? 'Processing…' : 'REVERT NOW'}
              </button>
            </div>
          </div>

          <div className="status-grid">
            <div className="timeline-col">
              <h3 className="section-label">Timeline</h3>
              <ul className="timeline-list">
                <li className="timeline-node">
                  <span className={`timeline-marker ${currentStatus === 'PENDING' ? 'marker-pending' : 'marker-done'}`}>
                    ●
                  </span>
                  <div className="timeline-content">
                    <div className="timeline-title">PENDING</div>
                    <div className="timeline-time">{formatTime(activeChange.created_at)}</div>
                  </div>
                </li>

                {activeChange.confirmed_at && (
                  <li className="timeline-node">
                    <span className="timeline-marker marker-confirmed">✓</span>
                    <div className="timeline-content">
                      <div className="timeline-title title-confirmed">CONFIRMED</div>
                      <div className="timeline-time">{formatTime(activeChange.confirmed_at)}</div>
                    </div>
                  </li>
                )}

                {activeChange.reverted_at && (
                  <li className="timeline-node">
                    <span className={`timeline-marker ${currentStatus === 'FAILED' || currentStatus === 'PARTIAL_REVERT' ? 'marker-critical' : 'marker-reverted'}`}>
                      ↩
                    </span>
                    <div className="timeline-content">
                      <div className={`timeline-title ${currentStatus === 'FAILED' || currentStatus === 'PARTIAL_REVERT' ? 'title-critical' : 'title-reverted'}`}>
                        {currentStatus} {activeChange.revert_trigger && `(${activeChange.revert_trigger})`}
                      </div>
                      <div className="timeline-time">{formatTime(activeChange.reverted_at)}</div>
                    </div>
                  </li>
                )}
              </ul>
            </div>

            <div className="operations-col">
              <h3 className="section-label">
                {activeChange.revert_report ? 'Operations & Revert Results' : 'Planned Operations'}
              </h3>
              <div className="ops-card-list">
                {activeChange.revert_report ? (
                  activeChange.revert_report.map((r, i) => {
                    const matchingOp = activeChange.delta?.find(d => d.op_id === r.op_id) || activeChange.delta?.[i];
                    return (
                      <div key={i} className="op-card-item">
                        <div className="op-card-left">
                          <span className="op-num">Op {r.op_id}</span>
                          {matchingOp && (
                            <span className={`badge-op ${matchingOp.action === 'REVOKE' ? 'badge-revoke' : 'badge-authorize'}`}>
                              {matchingOp.action}
                            </span>
                          )}
                          <span className="op-readable-desc">
                            {matchingOp ? formatOpDetails(matchingOp) : `Rule operation ${r.op_id}`}
                          </span>
                          {r.detail && <span className="op-detail">({r.detail})</span>}
                        </div>
                        <span className={`op-badge-result result-${(r.result || 'reverted').toLowerCase()}`}>
                          {r.result}
                        </span>
                      </div>
                    );
                  })
                ) : activeChange.delta && activeChange.delta.length > 0 ? (
                  activeChange.delta.map((op, i) => (
                    <div key={i} className="op-card-item">
                      <div className="op-card-left">
                        <span className={`badge-op ${op.action === 'REVOKE' ? 'badge-revoke' : 'badge-authorize'}`}>
                          {op.action}
                        </span>
                        <span className="op-readable-desc">{formatOpDetails(op)}</span>
                      </div>
                      {op.applied !== undefined && op.applied !== null ? (
                        <span className={`op-badge-result ${op.applied ? 'result-reverted' : 'result-skipped'}`}>
                          {op.applied ? 'APPLIED' : 'PENDING'}
                        </span>
                      ) : null}
                    </div>
                  ))
                ) : (
                  <div className="op-empty">No operations recorded.</div>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      <footer className="app-footer">
        Deadman · Transactional Security Group Rollback · Hackathon 2026
      </footer>
    </div>
  );
}
