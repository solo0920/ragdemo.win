/* =========================================================
   RAGDEMO.WIN — apply saved visual style before first paint
   ========================================================= */

(function () {
  "use strict";

  var allowed = [
    "apple",
    "meta",
    "nvidia",
    "tesla",
    "ibm",
    "oracle",
    "stripe",
    "shopify",
    "spotify",
    "uber"
  ];

  try {
    var saved = window.localStorage.getItem("ragdemo-style");
    if (saved && allowed.indexOf(saved) !== -1) {
      document.documentElement.setAttribute("data-style", saved);
    }
  } catch (error) {
    /* Storage can be unavailable on file:// or in private modes. */
  }
})();
