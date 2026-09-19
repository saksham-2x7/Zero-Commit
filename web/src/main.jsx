import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import './index.css';

async function boot() {
  if (import.meta.env.VITE_MOCK === '1') {
    const { setupMock } = await import('../mock/api-mock.js');
    setupMock();
    console.log('[Deadman] Mock mode active');
  }

  ReactDOM.createRoot(document.getElementById('root')).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>
  );
}

boot();
