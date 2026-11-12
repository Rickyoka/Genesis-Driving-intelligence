/* Runs synchronously in <head> (external file, CSP-safe) so the page never flashes before the intro. */
(function () {
  "use strict";
  try {
    var force = /[?&]intro=1(?:&|$)/.test(window.location.search);
    if (force || !window.sessionStorage.getItem("di_intro_seen")) {
      document.documentElement.classList.add("intro-pending");
      // Safety net: never leave the page hidden if intro.js fails to load.
      window.setTimeout(function () { document.documentElement.classList.remove("intro-pending"); }, 15000);
    }
  } catch (e) { /* storage disabled: skip intro */ }
})();
