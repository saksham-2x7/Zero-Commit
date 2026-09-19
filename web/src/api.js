/**
 * API client for Deadman.
 * Relative to apiBase loaded from web/config.json (defaults to '/api').
 */

let apiBase = '/api';

export async function initApi() {
  try {
    const res = await fetch('/config.json');
    if (res.ok) {
      const config = await res.json();
      apiBase = config.apiBase || '/api';
    }
  } catch {
    apiBase = '/api';
  }
}

async function doFetch(path, options = {}) {
  const isMock = import.meta.env.VITE_MOCK === '1';
  let response;
  if (isMock) {
    const { mockFetch } = await import('../mock/api-mock.js');
    if (typeof mockFetch === 'function') {
      response = await mockFetch(apiBase + path, options);
    } else {
      response = await fetch(apiBase + path, options);
    }
  } else {
    response = await fetch(apiBase + path, options);
  }
  
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(data.error?.message || data.message || 'API Error');
    error.code = data.error?.code || data.error || 'UNKNOWN_ERROR';
    error.status = response.status;
    error.data = data;
    throw error;
  }
  return data;
}

export function applyChange(token, payload) {
  return doFetch('/changes', {
    method: 'POST',
    headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });
}

export function getStatus(token, id) {
  return doFetch(`/changes/${id}`, {
    method: 'GET',
    headers: { 'Authorization': `Bearer ${token}` }
  });
}

export function confirmChange(token, id) {
  return doFetch(`/changes/${id}/confirm`, {
    method: 'POST',
    headers: { 'Authorization': `Bearer ${token}` }
  });
}

export function revertChange(token, id) {
  return doFetch(`/changes/${id}/revert`, {
    method: 'POST',
    headers: { 'Authorization': `Bearer ${token}` }
  });
}
