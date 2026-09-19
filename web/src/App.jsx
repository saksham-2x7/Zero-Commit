import React, { useState, useEffect, useRef } from 'react';

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

  const radius = 50;
  const circumference = 2 * Math.PI * radius;
  const strokeDashoffset = activeChange && activeChange.ttl_seconds && countdown !== null
    ? circumference - (countdown / activeChange.ttl_seconds) * circumference 
    : 0;

  return (
    <div style={{ maxWidth: '800px', margin: '0 auto', padding: '2rem', fontFamily: 'system-ui, sans-serif' }}>
      <h1>Deadman</h1>
      
      {globalError && (
        <div style={{ background: '#fee', color: '#c00', padding: '1rem', marginBottom: '1rem', borderRadius: '4px', fontWeight: 'bold' }}>
          {globalError}
        </div>
      )}

      <div style={{ marginBottom: '2rem' }}>
        <label style={{ display: 'block', fontWeight: 'bold', marginBottom: '0.5rem' }}>Bearer Token</label>
        <input 
          type="password" 
          value={token} 
          onChange={e => setToken(e.target.value)} 
          placeholder="Enter token..."
          style={{ width: '100%', padding: '0.5rem' }}
        />
      </div>

      {!activeChange ? (
        <form onSubmit={submitChange} style={{ background: '#f5f5f5', padding: '1.5rem', borderRadius: '8px' }}>
          <h2>New Change</h2>
          <div style={{ display: 'flex', gap: '1rem', marginBottom: '1rem' }}>
            <div style={{ flex: 1 }}>
              <label style={{ display: 'block', fontWeight: 'bold' }}>SG ID</label>
              <input type="text" value={form.sg_id} onChange={e => setForm({...form, sg_id: e.target.value})} required style={{ width: '100%', padding: '0.5rem' }} />
            </div>
            <div>
              <label style={{ display: 'block', fontWeight: 'bold' }}>TTL (seconds)</label>
              <input type="number" value={form.ttl_seconds} onChange={e => setForm({...form, ttl_seconds: parseInt(e.target.value, 10)})} required style={{ width: '150px', padding: '0.5rem' }} />
            </div>
          </div>
          
          <h3>Operations ({form.ops.length}/5)</h3>
          {form.ops.map((op, i) => (
            <div key={i} style={{ display: 'flex', gap: '0.5rem', marginBottom: '0.5rem', alignItems: 'center' }}>
              <select value={op.action} onChange={e => updateOp(i, 'action', e.target.value)} style={{ padding: '0.5rem' }}>
                <option value="REVOKE">REVOKE</option>
                <option value="AUTHORIZE">AUTHORIZE</option>
              </select>
              <select value={op.protocol} onChange={e => updateOp(i, 'protocol', e.target.value)} style={{ padding: '0.5rem' }}>
                <option value="tcp">tcp</option>
                <option value="udp">udp</option>
                <option value="icmp">icmp</option>
              </select>
              <input type="number" value={op.from_port} onChange={e => updateOp(i, 'from_port', e.target.value)} placeholder="From Port" style={{ width: '80px', padding: '0.5rem' }} required />
              <input type="number" value={op.to_port} onChange={e => updateOp(i, 'to_port', e.target.value)} placeholder="To Port" style={{ width: '80px', padding: '0.5rem' }} required />
              <input type="text" value={op.cidr} onChange={e => updateOp(i, 'cidr', e.target.value)} placeholder="IPv4 CIDR" style={{ width: '150px', padding: '0.5rem' }} required />
              {form.ops.length > 1 && (
                <button type="button" onClick={() => removeOp(i)} style={{ padding: '0.5rem' }}>❌</button>
              )}
            </div>
          ))}
          {form.ops.length < 5 && (
            <button type="button" onClick={addOp} style={{ padding: '0.5rem', marginBottom: '1rem' }}>+ Add Rule</button>
          )}

          <div style={{ marginTop: '1rem' }}>
            <button type="submit" disabled={loading} style={{ background: '#0066cc', color: 'white', padding: '0.75rem 1.5rem', fontSize: '1.1rem', border: 'none', borderRadius: '4px', cursor: 'pointer' }}>
              {loading ? 'Submitting...' : 'Apply Change'}
            </button>
          </div>
        </form>
      ) : (
        <div style={{ background: '#f5f5f5', padding: '1.5rem', borderRadius: '8px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
            <h2 style={{ margin: 0 }}>Change {activeChange.change_id}</h2>
            <button onClick={() => setActiveChange(null)} style={{ padding: '0.5rem' }}>New Change</button>
          </div>
          
          <div style={{ marginBottom: '1rem' }}>
            <strong>Status:</strong> <span style={{ padding: '0.25rem 0.5rem', background: '#e0e0e0', borderRadius: '4px', fontWeight: 'bold' }}>{activeChange.status}</span>
          </div>

          {(activeChange.status === 'PARTIAL_REVERT' || activeChange.status === 'FAILED') && (
            <div style={{ background: '#fff3cd', color: '#856404', padding: '1rem', marginBottom: '1rem', borderRadius: '4px', fontWeight: 'bold' }}>
              ⚠️ WARNING: {activeChange.status === 'FAILED' ? 'Change failed to revert fully or failed initially' : 'Change partially reverted'} 
              {activeChange.failure_reason && ` - ${activeChange.failure_reason}`}
            </div>
          )}

          <div style={{ display: 'flex', gap: '2rem', alignItems: 'center', marginBottom: '2rem' }}>
            {countdown !== null && (
              <div style={{ position: 'relative', width: '120px', height: '120px' }}>
                <svg width="120" height="120" style={{ transform: 'rotate(-90deg)' }}>
                  <circle cx="60" cy="60" r={radius} fill="transparent" stroke="#ddd" strokeWidth="10" />
                  <circle cx="60" cy="60" r={radius} fill="transparent" stroke={countdown < 15 ? '#dc3545' : '#0066cc'} strokeWidth="10" strokeDasharray={circumference} strokeDashoffset={strokeDashoffset} style={{ transition: 'stroke-dashoffset 1s linear' }} />
                </svg>
                <div style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '1.5rem', fontWeight: 'bold' }}>
                  {countdown}s
                </div>
              </div>
            )}
            
            <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
              <button 
                onClick={() => handleAction('confirm')} 
                disabled={activeChange.status !== 'PENDING' || loading}
                style={{ background: activeChange.status === 'PENDING' ? '#28a745' : '#ccc', color: 'white', padding: '1rem 2rem', fontSize: '1.2rem', border: 'none', borderRadius: '4px', cursor: activeChange.status === 'PENDING' ? 'pointer' : 'not-allowed', fontWeight: 'bold' }}
              >
                CONFIRM
              </button>
              <button 
                onClick={() => handleAction('revert')} 
                disabled={activeChange.status !== 'PENDING' || loading}
                style={{ background: activeChange.status === 'PENDING' ? '#dc3545' : '#ccc', color: 'white', padding: '0.75rem 1.5rem', fontSize: '1rem', border: 'none', borderRadius: '4px', cursor: activeChange.status === 'PENDING' ? 'pointer' : 'not-allowed', fontWeight: 'bold' }}
              >
                REVERT NOW
              </button>
            </div>
          </div>

          <div style={{ background: 'white', padding: '1rem', borderRadius: '4px' }}>
            <h3 style={{ marginTop: 0 }}>Timeline</h3>
            <ul style={{ listStyle: 'none', padding: 0, margin: 0 }}>
              <li>✅ {new Date(activeChange.created_at * 1000).toLocaleTimeString()} - PENDING</li>
              {activeChange.confirmed_at && <li>✅ {new Date(activeChange.confirmed_at * 1000).toLocaleTimeString()} - CONFIRMED</li>}
              {activeChange.reverted_at && (
                <li>
                  {activeChange.status === 'REVERTED' ? '✅' : '⚠️'} {new Date(activeChange.reverted_at * 1000).toLocaleTimeString()} - {activeChange.status}
                  <br/><small>Trigger: {activeChange.revert_trigger}</small>
                </li>
              )}
            </ul>
            
            {activeChange.revert_report && (
              <div style={{ marginTop: '1rem', borderTop: '1px solid #eee', paddingTop: '1rem' }}>
                <h4 style={{ marginTop: 0 }}>Revert Report</h4>
                <ul style={{ margin: 0, paddingLeft: '1rem' }}>
                  {activeChange.revert_report.map((r, i) => (
                    <li key={i}>Op {r.op_id}: <strong>{r.result}</strong> {r.detail && `(${r.detail})`}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
          
          <div style={{ background: 'white', padding: '1rem', borderRadius: '4px', marginTop: '1rem' }}>
            <h3 style={{ marginTop: 0 }}>Planned Delta</h3>
            <pre style={{ margin: 0, overflowX: 'auto', fontSize: '0.9rem' }}>
              {JSON.stringify(activeChange.delta, null, 2)}
            </pre>
          </div>
        </div>
      )}
    </div>
  );
}
