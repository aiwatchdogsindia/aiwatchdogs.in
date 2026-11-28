// Sets the theme before first paint so a dark-mode visitor never sees a white
// flash. Loaded as a file because the site's CSP allows no inline scripts.
// Keep the storage key and colours in step with src/lib/theme.ts.
(function () {
  var theme = null;
  try {
    theme = window.localStorage.getItem('aw.theme');
  } catch (e) {}
  if (theme !== 'light' && theme !== 'dark') {
    theme =
      window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches
        ? 'dark'
        : 'light';
  }
  document.documentElement.setAttribute('data-theme', theme);
  if (theme === 'dark') {
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', '#131313');
  }
})();
