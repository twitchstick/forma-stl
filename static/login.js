'use strict';
const loginForm = document.querySelector('#login-form'),
  loginMessage = document.querySelector('#login-message');
// After signing in, return to the library or the model page that asked for sign-in.
function appPath(path) {
  return path === '/' || /^\/models\/[A-Za-z0-9-]+$/.test(path);
}
function loginDestination() {
  const next = new URLSearchParams(location.search).get('next');
  if (next && next.startsWith('/') && !next.startsWith('//') && !next.includes('\\')) {
    try {
      const target = new URL(next, location.origin);
      if (target.origin === location.origin && appPath(target.pathname)) return target.pathname + target.search;
    } catch {}
  }
  return appPath(location.pathname) ? location.pathname + location.search : '/';
}
loginForm.onsubmit = async (event) => {
  event.preventDefault();
  const button = loginForm.querySelector('button');
  button.disabled = true;
  loginMessage.textContent = 'Signing in…';
  try {
    const response = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Forma-Request': '1' },
      body: JSON.stringify({
        username: loginForm.elements.username.value,
        password: loginForm.elements.password.value,
      }),
    });
    const result = await response.json();
    if (!response.ok) throw Error(result.error || 'Sign-in failed.');
    loginForm.elements.password.value = '';
    location.replace(loginDestination());
  } catch (error) {
    loginMessage.textContent = error.message;
  } finally {
    button.disabled = false;
  }
};
fetch('/api/auth/status')
  .then((r) => r.json())
  .then((status) => {
    if (status.authenticated || !status.authentication_required) location.replace(loginDestination());
  })
  .catch(() => {});
