/* =========================================================
   RAGDEMO.WIN — navigation, reveal motion, small details
   Vanilla JavaScript. No dependencies.
   ========================================================= */

(function () {
  "use strict";

  /* ---------- Current year ---------- */
  document.querySelectorAll("[data-year]").forEach(function (node) {
    node.textContent = String(new Date().getFullYear());
  });

  /* ---------- Menu ---------- */
  var menu = document.getElementById("mobile-menu");
  var openButton = document.querySelector("[data-menu-open]");
  var closeButton = document.querySelector("[data-menu-close]");

  if (menu && openButton) {
    var openMenu = function () {
      document.documentElement.classList.add("menu-open");
      if (typeof menu.showModal === "function") {
        if (!menu.open) menu.showModal();
      } else {
        menu.setAttribute("open", "");
      }
    };

    var closeMenu = function () {
      document.documentElement.classList.remove("menu-open");
      if (typeof menu.close === "function" && menu.open) {
        menu.close();
      } else {
        menu.removeAttribute("open");
      }
    };

    openButton.addEventListener("click", openMenu);

    if (closeButton) {
      closeButton.addEventListener("click", closeMenu);
    }

    menu.addEventListener("close", function () {
      document.documentElement.classList.remove("menu-open");
    });

    menu.addEventListener("click", function (event) {
      if (event.target === menu) closeMenu();
    });

    menu.querySelectorAll("a").forEach(function (link) {
      link.addEventListener("click", function () {
        closeMenu();
      });
    });
  }

  /* ---------- Header hairline on scroll ---------- */
  var header = document.getElementById("site-header");
  var updateHeader = function () {
    if (!header) return;
    header.classList.toggle("is-scrolled", window.scrollY > 8);
  };

  updateHeader();
  window.addEventListener("scroll", updateHeader, { passive: true });

  /* ---------- Reveal on scroll ---------- */
  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  var revealItems = document.querySelectorAll("[data-reveal]");

  if (!reduceMotion.matches && "IntersectionObserver" in window && revealItems.length) {
    var observer = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (!entry.isIntersecting) return;
          entry.target.classList.add("is-visible");
          observer.unobserve(entry.target);
        });
      },
      {
        rootMargin: "0px 0px -8% 0px",
        threshold: 0.01
      }
    );

    revealItems.forEach(function (item) {
      item.classList.add("will-reveal");
      observer.observe(item);
    });

    // Safety net: never leave content hidden if the observer misses an entry.
    window.setTimeout(function () {
      revealItems.forEach(function (item) {
        if (item.getBoundingClientRect().top < window.innerHeight) {
          item.classList.add("is-visible");
        }
      });
    }, 1800);
  }
})();
