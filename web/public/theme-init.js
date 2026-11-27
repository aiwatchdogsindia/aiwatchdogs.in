// Sets the colour theme before first paint so the page never flashes the
// wrong one. Served as a file (not inline) to stay inside the site CSP.
(function () {
  var theme = 'light';
  try {
    var stored = window.localStorage.getItem('aw.theme');
    if (stored === 'light' || stored === 'dark') {
      theme = stored;
    } else if (window.matchMedia('(prefers-color-scheme: dark)').matches) {
      theme = 'dark';
    }
  } catch (e) {
    // storage or matchMedia unavailable: keep light
  }
  document.documentElement.setAttribute('data-theme', theme);
})();
