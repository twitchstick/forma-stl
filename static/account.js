'use strict';
let accountProfile = null;
const accountDialog = $('#account-dialog');
function accountInitials(profile) {
  return (profile.display_name || profile.username || 'F')
    .split(/\s+/)
    .map((s) => s[0])
    .join('')
    .slice(0, 2)
    .toLocaleUpperCase();
}
function paintAccount(profile) {
  accountProfile = profile;
  const initials = escapeHTML(accountInitials(profile)),
    picture = profile.avatar_url
      ? `<img src="${escapeHTML(profile.avatar_url)}" alt="Your profile picture">`
      : initials;
  $('#about-button').innerHTML = picture;
  $('#about-button').title = profile.display_name || profile.username;
  $('#account-photo-preview').innerHTML = picture;
  $('#account-remove-photo').disabled = !profile.avatar_url;
}
async function loadAccountSessions() {
  const result = await api('/api/account/sessions');
  $('#account-sessions').innerHTML =
    result.sessions
      .map(
        (s) =>
          `<div class="account-session"><div><strong>${escapeHTML(s.device)}${s.current ? ' · This browser' : ''}</strong><small>Last active ${escapeHTML(new Date(s.last_seen).toLocaleString())}</small></div></div>`
      )
      .join('') || '<p class="hint">No saved browser sessions. Sign in with a password to manage sessions.</p>';
}
async function openAccount() {
  $('#account-status').textContent = '';
  $('#account-profile-message').textContent = '';
  $('#account-password-message').textContent = '';
  if (!accountDialog.open) accountDialog.showModal();
  try {
    const profile = await api('/api/account');
    paintAccount(profile);
    const form = $('#account-profile-form');
    form.reset();
    form.elements.username.value = profile.username;
    form.elements.display_name.value = profile.display_name;
    $('#account-password-form').reset();
    $('#account-current-password-label').hidden = !profile.authentication_enabled;
    $('#account-password-form').elements.current_password.required = profile.authentication_enabled;
    $('#account-signout').hidden = !profile.authentication_enabled;
    $('#account-password-hint').textContent = profile.authentication_enabled
      ? 'Changing it signs out your other browsers and paired companions.'
      : 'Sign-in is currently disabled. Set a password here to protect your library.';
    await loadAccountSessions();
  } catch (error) {
    $('#account-status').textContent = error.message;
  }
}
$('#account-button').onclick = $('#about-button').onclick = openAccount;
$('#account-profile-form').onsubmit = async (event) => {
  event.preventDefault();
  const form = event.currentTarget,
    button = event.submitter;
  button.disabled = true;
  try {
    const result = await api('/api/account', {
      method: 'PATCH',
      body: JSON.stringify(Object.fromEntries(new FormData(form))),
    });
    paintAccount(result);
    form.elements.current_password.value = '';
    $('#account-profile-message').textContent = 'Profile saved.';
    await loadAccountSessions();
  } catch (error) {
    $('#account-profile-message').textContent = error.message;
  } finally {
    button.disabled = false;
  }
};
$('#account-password-form').onsubmit = async (event) => {
  event.preventDefault();
  const form = event.currentTarget,
    button = event.submitter;
  if (form.elements.new_password.value !== form.elements.confirm_password.value) {
    $('#account-password-message').textContent = 'The new passwords do not match.';
    return;
  }
  button.disabled = true;
  try {
    await api('/api/account/password', {
      method: 'POST',
      body: JSON.stringify(Object.fromEntries(new FormData(form))),
    });
    form.reset();
    paintAccount(await api('/api/account'));
    $('#account-current-password-label').hidden = false;
    form.elements.current_password.required = true;
    $('#account-signout').hidden = false;
    $('#account-password-hint').textContent = 'Changing it signs out your other browsers and paired companions.';
    $('#account-password-message').textContent =
      'Password updated. Other browsers are signed out. This browser stays signed in.';
    await loadAccountSessions();
  } catch (error) {
    $('#account-password-message').textContent = error.message;
  } finally {
    button.disabled = false;
  }
};
$('#account-avatar-file').onchange = async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  try {
    if (file.size > 5 * 1024 * 1024) throw Error('Choose a photo smaller than 5 MB.');
    $('#account-status').textContent = 'Saving profile picture…';
    const body = new FormData();
    body.append('avatar', file);
    paintAccount(await api('/api/account/avatar', { method: 'POST', body }));
    $('#account-status').textContent = 'Profile picture updated.';
  } catch (error) {
    $('#account-status').textContent = error.message;
  } finally {
    event.target.value = '';
  }
};
$('#account-remove-photo').onclick = async () => {
  try {
    paintAccount(await api('/api/account/avatar', { method: 'DELETE' }));
    $('#account-status').textContent = 'Profile picture removed.';
  } catch (error) {
    $('#account-status').textContent = error.message;
  }
};
$('#account-logout-others').onclick = async () => {
  try {
    await api('/api/account/logout-others', { method: 'POST', body: '{}' });
    await loadAccountSessions();
    $('#account-status').textContent = 'Other browsers signed out.';
  } catch (error) {
    $('#account-status').textContent = error.message;
  }
};
$('#account-signout').onclick = async () => {
  try {
    await api('/api/auth/logout', { method: 'POST', body: '{}' });
    location.assign('/login');
  } catch (error) {
    $('#account-status').textContent = error.message;
  }
};
api('/api/account')
  .then(paintAccount)
  .catch(() => {});
