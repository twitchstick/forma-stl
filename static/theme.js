// Run before the page paints; this stores an appearance preference only.
(() => {
  let choice = 'system';
  try {
    choice = localStorage.getItem('forma-theme') || 'system';
  } catch {}
  if (!['dark', 'light', 'system'].includes(choice)) choice = 'system';
  const resolved =
    choice === 'system' ? (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light') : choice;
  document.documentElement.dataset.theme = resolved === 'light' ? 'light' : 'dark';
  document.documentElement.dataset.themeChoice = choice;
})();
