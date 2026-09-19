import React, { useState } from 'react'

export default function App() {
  const [status, setStatus] = useState('IDLE');

  const applyChange = () => setStatus('PENDING');
  const confirmChange = () => setStatus('CONFIRMED');
  const revertChange = () => setStatus('REVERTED');

  return (
    <div style={{ padding: '2rem', fontFamily: 'system-ui, sans-serif' }}>
      <h1>Deadman Dashboard</h1>
      <p>Status: <strong>{status}</strong></p>
      
      {status === 'IDLE' && <button onClick={applyChange}>Apply Change (TTL: 90s)</button>}
      
      {status === 'PENDING' && (
        <div style={{ marginTop: '1rem' }}>
          <div style={{ color: 'red', fontSize: '2rem', marginBottom: '1rem' }}>⏱ 90s remaining</div>
          <button onClick={confirmChange} style={{ background: 'green', color: 'white', marginRight: '1rem' }}>CONFIRM</button>
          <button onClick={revertChange} style={{ background: 'red', color: 'white' }}>REVERT NOW</button>
        </div>
      )}
    </div>
  )
}
