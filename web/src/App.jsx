import React, { useState, useEffect } from 'react';

const ERROR_MAP = {
  'INVALID_REQUEST': 'The request was invalid. Please check your inputs.',
  'TTL_OUT_OF_RANGE': 'The TTL (time-to-live) is out of the allowed range.',
  'UNSUPPORTED_RULE': 'Only IPv4 CIDR rules are supported.',
  'UNAUTHORIZED': 'Missing or invalid token.',
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

export default function App() {
  const [apiBase, setApiBase] = useState('');
  const [token, setToken] = useState('');
  const [globalError, setGlobalError] = useState('');
  const [loading, setLoading] = useState(false);
  
  const [form, setForm] = useState({
    sg_id: '',
    ttl_seconds: 90,
    ops: [{ action: 'REVOKE', protocol: 'tcp', from_port: 80, to_port: 80, cidr: '0.0.0.0/0' }]
  });

  const [activeChange, setActiveChange] = useState(null);
  const [countdown, setCountdown] = useState(null);

  const [darkMode, setDarkMode] = useState(false);
  useEffect(() => {
    if (darkMode) {
      document.documentElement.classList.add('dark');
    } else {
      document.documentElement.classList.remove('dark');
    }
  }, [darkMode]);

  
  useEffect(() => {
    fetch('/config.json')
      .then(res => res.json())
      .then(data => setApiBase(data.apiBase))
      .catch(() => setApiBase('/api'));
  }, []);

  useEffect(() => {
    let timer;
    if (activeChange && (activeChange.status === 'PENDING' || activeChange.status === 'REVERTING')) {
      timer = setInterval(() => {
        pollStatus();
      }, 1000);
    }
    return () => clearInterval(timer);
  }, [activeChange, apiBase, token]);

  const getAuthHeaders = () => ({
    'Authorization': `Bearer ${token}`,
    'Content-Type': 'application/json'
  });

  const handleApiError = async (res) => {
    try {
      const data = await res.json();
      if (data.error && data.error.code) {
        const msg = ERROR_MAP[data.error.code] || `Error: ${data.error.code} - ${data.error.message || 'Unknown error'}`;
        setGlobalError(msg);
      } else {
        setGlobalError(`HTTP Error: ${res.status}`);
      }
    } catch (e) {
      setGlobalError(`HTTP Error: ${res.status}`);
    }
  };

  const pollStatus = async () => {
    try {
      const res = await fetch(`${apiBase}/changes/${activeChange.change_id}`, {
        headers: getAuthHeaders()
      });
      if (res.ok) {
        const data = await res.json();
        setActiveChange(data);
        if (data.status === 'PENDING') {
          const remaining = data.expires_at - data.server_time;
          setCountdown(remaining > 0 ? remaining : 0);
        } else {
          setCountdown(null);
        }
      }
    } catch (e) {
      console.error('Poll failed', e);
    }
  };

  const submitChange = async (e) => {
    e.preventDefault();
    setGlobalError('');
    if (!token) {
      setGlobalError('Please enter a bearer token');
      return;
    }
    if (form.ops.length === 0 || form.ops.length > 5) {
      setGlobalError('1 to 5 ops allowed');
      return;
    }
    
    setLoading(true);
    try {
      const res = await fetch(`${apiBase}/changes`, {
        method: 'POST',
        headers: getAuthHeaders(),
        body: JSON.stringify(form)
      });
      if (res.ok) {
        const data = await res.json();
        setActiveChange(data);
        const remaining = data.expires_at - data.server_time;
        setCountdown(remaining > 0 ? remaining : 0);
      } else {
        await handleApiError(res);
      }
    } catch (e) {
      setGlobalError('Network error');
    } finally {
      setLoading(false);
    }
  };

  const handleAction = async (action) => {
    setGlobalError('');
    setLoading(true);
    try {
      const res = await fetch(`${apiBase}/changes/${activeChange.change_id}/${action}`, {
        method: 'POST',
        headers: getAuthHeaders()
      });
      if (res.ok) {
        const data = await res.json();
        setActiveChange(prev => ({ ...prev, ...data }));
        if (action === 'confirm' || action === 'revert') {
          setCountdown(null);
        }
      } else {
        await handleApiError(res);
      }
    } catch (e) {
      setGlobalError('Network error');
    } finally {
      setLoading(false);
    }
  };

  const addOp = () => {
    if (form.ops.length >= 5) return;
    setForm(prev => ({ ...prev, ops: [...prev.ops, { action: 'REVOKE', protocol: 'tcp', from_port: 80, to_port: 80, cidr: '0.0.0.0/0' }] }));
  };
  
  const updateOp = (index, field, value) => {
    const newOps = [...form.ops];
    newOps[index][field] = field.includes('port') ? parseInt(value, 10) : value;
    setForm(prev => ({ ...prev, ops: newOps }));
  };
  
  const removeOp = (index) => {
    setForm(prev => ({ ...prev, ops: prev.ops.filter((_, i) => i !== index) }));
  };

  const radius = 60;
  const circumference = 2 * Math.PI * radius;
  const strokeDashoffset = activeChange && activeChange.ttl_seconds && countdown !== null
    ? circumference - (countdown / activeChange.ttl_seconds) * circumference 
    : 0;

  return (
    <div style={{ maxWidth: '800px', margin: '2rem auto', padding: '0 1rem' }}>
      <header style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '2rem' }}>
        <h1 style={{ margin: 0, fontSize: '2.5rem', fontWeight: '800', letterSpacing: '-1px' }}>Deadman</h1>
        <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', width: '300px' }}>
          <button 
            type="button" 
            onClick={() => setDarkMode(!darkMode)}
            style={{ padding: '0.5rem', borderRadius: '50%', border: '1px solid #ccc', background: 'transparent', cursor: 'pointer', fontSize: '1.2rem' }}
            title="Toggle Dark Mode"
          >
            {darkMode ? '☀️' : '🌙'}
          </button>
          <input 
            type="password" 
            value={token} 
            onChange={e => setToken(e.target.value)} 
            placeholder="Bearer Token (Required)"
            style={{ width: '100%', padding: '0.75rem', borderRadius: '6px', border: '1px solid #ccc', boxSizing: 'border-box' }}
            aria-label="Bearer Token"
          />
        </div>
      </header>
      
      {globalError && (
        <div role="alert" style={{ background: '#fee2e2', color: '#991b1b', padding: '1rem', marginBottom: '2rem', borderRadius: '6px', fontWeight: 'bold', border: '1px solid #f87171' }}>
          {globalError}
        </div>
      )}

      {!activeChange ? (
        <form onSubmit={submitChange} style={{ background: '#fff', padding: '2rem', borderRadius: '12px', boxShadow: '0 4px 6px rgba(0,0,0,0.05)', border: '1px solid #e5e7eb' }}>
          <h2 style={{ marginTop: 0, borderBottom: '2px solid #f3f4f6', paddingBottom: '1rem' }}>Draft a Security Group Change</h2>
          
          <div style={{ display: 'flex', gap: '1.5rem', marginBottom: '2rem' }}>
            <div style={{ flex: 1 }}>
              <label style={{ display: 'block', fontWeight: '600', marginBottom: '0.5rem', color: '#4b5563' }}>Security Group ID</label>
              <input type="text" value={form.sg_id} onChange={e => setForm({...form, sg_id: e.target.value})} required placeholder="sg-0123456789abcdef" style={{ width: '100%', padding: '0.75rem', boxSizing: 'border-box' }} />
            </div>
            <div>
              <label style={{ display: 'block', fontWeight: '600', marginBottom: '0.5rem', color: '#4b5563' }}>Timeout / TTL (s)</label>
              <input type="number" value={form.ttl_seconds} onChange={e => setForm({...form, ttl_seconds: parseInt(e.target.value, 10)})} required min="60" max="600" style={{ width: '150px', padding: '0.75rem', boxSizing: 'border-box' }} />
            </div>
          </div>
          
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
            <h3 style={{ margin: 0, color: '#374151' }}>Rules ({form.ops.length}/5)</h3>
            {form.ops.length < 5 && (
              <button type="button" onClick={addOp} style={{ background: '#f3f4f6', color: '#374151', padding: '0.5rem 1rem', borderRadius: '6px', border: '1px solid #d1d5db', cursor: 'pointer', fontWeight: '600' }}>
                + Add Rule
              </button>
            )}
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem', marginBottom: '2rem' }}>
            {form.ops.map((op, i) => (
              <div key={i} style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', background: '#f9fafb', padding: '1rem', borderRadius: '8px', border: '1px solid #e5e7eb' }}>
                <select value={op.action} onChange={e => updateOp(i, 'action', e.target.value)} style={{ padding: '0.75rem', background: 'white' }}>
                  <option value="REVOKE">REVOKE</option>
                  <option value="AUTHORIZE">AUTHORIZE</option>
                </select>
                <select value={op.protocol} onChange={e => updateOp(i, 'protocol', e.target.value)} style={{ padding: '0.75rem', background: 'white' }}>
                  <option value="tcp">tcp</option>
                  <option value="udp">udp</option>
                  <option value="icmp">icmp</option>
                </select>
                <input type="number" value={op.from_port} onChange={e => updateOp(i, 'from_port', e.target.value)} placeholder="From Port" style={{ width: '90px', padding: '0.75rem' }} required />
                <span style={{ color: '#9ca3af' }}>-</span>
                <input type="number" value={op.to_port} onChange={e => updateOp(i, 'to_port', e.target.value)} placeholder="To Port" style={{ width: '90px', padding: '0.75rem' }} required />
                <input type="text" value={op.cidr} onChange={e => updateOp(i, 'cidr', e.target.value)} placeholder="0.0.0.0/0" style={{ flex: 1, padding: '0.75rem' }} required />
                {form.ops.length > 1 && (
                  <button type="button" onClick={() => removeOp(i)} style={{ padding: '0.75rem', background: 'transparent', border: 'none', color: '#ef4444', fontSize: '1.25rem', cursor: 'pointer' }} aria-label="Remove Rule" title="Remove rule">
                    &times;
                  </button>
                )}
              </div>
            ))}
          </div>

          <div>
            <button type="submit" disabled={loading} style={{ width: '100%', background: '#2563eb', color: 'white', padding: '1rem', fontSize: '1.25rem', fontWeight: 'bold', border: 'none', borderRadius: '8px', cursor: 'pointer', boxShadow: '0 4px 6px rgba(37, 99, 235, 0.2)' }}>
              {loading ? 'Submitting...' : 'Apply Change'}
            </button>
          </div>
        </form>
      ) : (
        <div style={{ background: '#fff', padding: '2rem', borderRadius: '12px', boxShadow: '0 4px 6px rgba(0,0,0,0.05)', border: '1px solid #e5e7eb' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '2rem', borderBottom: '2px solid #f3f4f6', paddingBottom: '1rem' }}>
            <h2 style={{ margin: 0, color: '#111827' }}>Change <span style={{ fontFamily: 'monospace', color: '#4b5563', fontSize: '1.5rem' }}>{activeChange.change_id}</span></h2>
            <button onClick={() => setActiveChange(null)} style={{ padding: '0.5rem 1rem', background: '#f3f4f6', color: '#374151', border: '1px solid #d1d5db', borderRadius: '6px', cursor: 'pointer', fontWeight: '600' }}>
              &larr; Start New
            </button>
          </div>
          
          <div style={{ marginBottom: '2rem', display: 'flex', alignItems: 'center', gap: '1rem' }}>
            <span style={{ fontSize: '1.1rem', fontWeight: '600', color: '#4b5563' }}>Status:</span> 
            <span style={{ 
              padding: '0.5rem 1rem', 
              background: activeChange.status === 'PENDING' ? '#dbeafe' : activeChange.status === 'CONFIRMED' ? '#dcfce7' : activeChange.status.includes('REVERT') ? '#fee2e2' : '#f3f4f6',
              color: activeChange.status === 'PENDING' ? '#1e40af' : activeChange.status === 'CONFIRMED' ? '#166534' : activeChange.status.includes('REVERT') ? '#991b1b' : '#374151',
              borderRadius: '9999px', 
              fontWeight: 'bold',
              fontSize: '0.9rem',
              letterSpacing: '0.05em'
            }}>
              {activeChange.status}
            </span>
          </div>

          {(activeChange.status === 'PARTIAL_REVERT' || activeChange.status === 'FAILED') && (
            <div style={{ background: '#fef3c7', color: '#92400e', padding: '1rem', marginBottom: '2rem', borderRadius: '8px', fontWeight: 'bold', border: '1px solid #fcd34d' }}>
              ⚠️ WARNING: {activeChange.status === 'FAILED' ? 'Change failed to apply or revert fully.' : 'Change partially reverted.'} 
              {activeChange.failure_reason && ` - ${activeChange.failure_reason}`}
            </div>
          )}

          <div style={{ display: 'flex', gap: '3rem', alignItems: 'center', marginBottom: '3rem', background: '#f8fafc', padding: '2rem', borderRadius: '12px', border: '1px solid #e2e8f0' }}>
            {countdown !== null ? (
              <div style={{ position: 'relative', width: '150px', height: '150px', flexShrink: 0 }}>
                <svg width="150" height="150" style={{ transform: 'rotate(-90deg)' }}>
                  <circle cx="75" cy="75" r={radius} fill="transparent" stroke="#e2e8f0" strokeWidth="12" />
                  <circle cx="75" cy="75" r={radius} fill="transparent" stroke={countdown < 15 ? '#ef4444' : '#3b82f6'} strokeWidth="12" strokeDasharray={circumference} strokeDashoffset={strokeDashoffset} style={{ transition: 'stroke-dashoffset 1s linear, stroke 0.3s' }} strokeLinecap="round" />
                </svg>
                <div style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center' }}>
                  <span style={{ fontSize: '2.5rem', fontWeight: '800', color: countdown < 15 ? '#ef4444' : '#1e293b', lineHeight: 1 }}>{countdown}</span>
                  <span style={{ fontSize: '0.8rem', color: '#64748b', fontWeight: '600', textTransform: 'uppercase' }}>seconds</span>
                </div>
              </div>
            ) : (
              <div style={{ width: '150px', height: '150px', flexShrink: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#e2e8f0', borderRadius: '50%', color: '#64748b', fontWeight: 'bold' }}>
                {activeChange.status}
              </div>
            )}
            
            <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem', flex: 1 }}>
              <button 
                onClick={() => handleAction('confirm')} 
                disabled={activeChange.status !== 'PENDING' || loading}
                style={{ 
                  background: activeChange.status === 'PENDING' ? '#10b981' : '#e5e7eb', 
                  color: activeChange.status === 'PENDING' ? 'white' : '#9ca3af', 
                  padding: '1.25rem', 
                  fontSize: '1.5rem', 
                  border: 'none', 
                  borderRadius: '8px', 
                  cursor: activeChange.status === 'PENDING' ? 'pointer' : 'not-allowed', 
                  fontWeight: '800',
                  boxShadow: activeChange.status === 'PENDING' ? '0 4px 6px rgba(16, 185, 129, 0.2)' : 'none',
                  letterSpacing: '1px'
                }}
              >
                CONFIRM
              </button>
              <button 
                onClick={() => handleAction('revert')} 
                disabled={activeChange.status !== 'PENDING' || loading}
                style={{ 
                  background: activeChange.status === 'PENDING' ? '#ef4444' : '#e5e7eb', 
                  color: activeChange.status === 'PENDING' ? 'white' : '#9ca3af', 
                  padding: '1rem', 
                  fontSize: '1.1rem', 
                  border: 'none', 
                  borderRadius: '8px', 
                  cursor: activeChange.status === 'PENDING' ? 'pointer' : 'not-allowed', 
                  fontWeight: '700',
                  letterSpacing: '0.5px'
                }}
              >
                REVERT NOW
              </button>
            </div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '2rem' }}>
            <div>
              <h3 style={{ marginTop: 0, color: '#374151', borderBottom: '2px solid #f3f4f6', paddingBottom: '0.5rem' }}>Timeline</h3>
              <ul style={{ listStyle: 'none', padding: 0, margin: 0, display: 'flex', flexDirection: 'column', gap: '1rem' }}>
                <li style={{ display: 'flex', gap: '0.75rem', alignItems: 'flex-start' }}>
                  <span style={{ fontSize: '1.2rem' }}>🕒</span> 
                  <div>
                    <div style={{ fontWeight: 'bold' }}>PENDING</div>
                    <div style={{ fontSize: '0.9rem', color: '#6b7280' }}>{new Date(activeChange.created_at * 1000).toLocaleString()}</div>
                  </div>
                </li>
                {activeChange.confirmed_at && (
                  <li style={{ display: 'flex', gap: '0.75rem', alignItems: 'flex-start' }}>
                    <span style={{ fontSize: '1.2rem' }}>✅</span> 
                    <div>
                      <div style={{ fontWeight: 'bold', color: '#10b981' }}>CONFIRMED</div>
                      <div style={{ fontSize: '0.9rem', color: '#6b7280' }}>{new Date(activeChange.confirmed_at * 1000).toLocaleString()}</div>
                    </div>
                  </li>
                )}
                {activeChange.reverted_at && (
                  <li style={{ display: 'flex', gap: '0.75rem', alignItems: 'flex-start' }}>
                    <span style={{ fontSize: '1.2rem' }}>{activeChange.status === 'REVERTED' ? '🔙' : '⚠️'}</span> 
                    <div>
                      <div style={{ fontWeight: 'bold', color: '#ef4444' }}>{activeChange.status} ({activeChange.revert_trigger})</div>
                      <div style={{ fontSize: '0.9rem', color: '#6b7280' }}>{new Date(activeChange.reverted_at * 1000).toLocaleString()}</div>
                      
                      {activeChange.revert_report && (
                        <div style={{ marginTop: '0.5rem', background: '#f9fafb', padding: '0.75rem', borderRadius: '6px', fontSize: '0.85rem', border: '1px solid #e5e7eb' }}>
                          <div style={{ fontWeight: '600', marginBottom: '0.25rem', color: '#4b5563' }}>Revert Report:</div>
                          <ul style={{ margin: 0, paddingLeft: '1.25rem', color: '#374151' }}>
                            {activeChange.revert_report.map((r, i) => (
                              <li key={i}>Op {r.op_id}: <strong style={{ color: r.result === 'REVERTED' ? '#10b981' : '#d97706' }}>{r.result}</strong> {r.detail && `(${r.detail})`}</li>
                            ))}
                          </ul>
                        </div>
                      )}
                    </div>
                  </li>
                )}
              </ul>
            </div>
            
            <div>
              <h3 style={{ marginTop: 0, color: '#374151', borderBottom: '2px solid #f3f4f6', paddingBottom: '0.5rem' }}>Planned Operations</h3>
              <div style={{ background: '#1e293b', color: '#f8fafc', padding: '1rem', borderRadius: '8px', overflowX: 'auto', fontFamily: 'monospace', fontSize: '0.85rem' }}>
                <pre style={{ margin: 0 }}>
                  {JSON.stringify(activeChange.delta, null, 2)}
                </pre>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
