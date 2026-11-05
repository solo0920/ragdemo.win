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

  /* ---------- Visual style chooser ---------- */
  var STYLE_KEY = "ragdemo-style";
  var DEFAULT_STYLE = "nvidia";
  var STYLES = [
    { id: "apple", name: "Apple", note: "純粹留白", themeColor: "#ffffff" },
    { id: "meta", name: "Meta", note: "明亮社群藍", themeColor: "#ffffff" },
    { id: "nvidia", name: "NVIDIA", note: "黑綠性能", themeColor: "#000000" },
    { id: "tesla", name: "Tesla", note: "極簡工程", themeColor: "#ffffff" },
    { id: "ibm", name: "IBM", note: "企業網格", themeColor: "#ffffff" },
    { id: "oracle", name: "Oracle", note: "溫潤襯線", themeColor: "#fbf9f8" },
    { id: "stripe", name: "Stripe", note: "紫藍漸層", themeColor: "#ffffff" },
    { id: "shopify", name: "Shopify", note: "黑底霓虹", themeColor: "#000000" },
    { id: "spotify", name: "Spotify", note: "深夜螢光", themeColor: "#121212" },
    { id: "uber", name: "Uber", note: "黑白街頭", themeColor: "#000000" }
  ];

  var root = document.documentElement;
  var themeMeta = document.querySelector('meta[name="theme-color"]');
  var styleStatus = document.querySelector("[data-style-status]");
  var styleMenu = document.getElementById("style-menu");
  var styleOptions = document.querySelector("[data-style-options]");

  var findStyle = function (id) {
    for (var i = 0; i < STYLES.length; i += 1) {
      if (STYLES[i].id === id) return STYLES[i];
    }
    return null;
  };

  var applyStyle = function (id, persist) {
    var style = findStyle(id) || findStyle(DEFAULT_STYLE);
    root.setAttribute("data-style", style.id);

    document.querySelectorAll("[data-style-label]").forEach(function (node) {
      node.textContent = style.name;
    });

    document.querySelectorAll("[data-style-trigger]").forEach(function (node) {
      node.setAttribute("aria-label", "選擇視覺風格，目前為 " + style.name);
    });

    document.querySelectorAll("[data-style-option]").forEach(function (option) {
      option.setAttribute("aria-pressed", option.getAttribute("data-style-option") === style.id ? "true" : "false");
    });

    if (themeMeta) themeMeta.setAttribute("content", style.themeColor);

    if (persist) {
      try {
        window.localStorage.setItem(STYLE_KEY, style.id);
      } catch (error) {
        /* Storage is optional; the style still applies for this page. */
      }
      if (styleStatus) styleStatus.textContent = "已套用 " + style.name + " 風格";
    }
  };

  if (styleOptions) {
    STYLES.forEach(function (style) {
      var option = document.createElement("button");
      option.type = "button";
      option.className = "style-option";
      option.setAttribute("data-style-option", style.id);
      option.setAttribute("aria-pressed", "false");
      option.innerHTML =
        '<span class="style-swatch swatch--' + style.id + '" aria-hidden="true"><i></i><i></i><i></i></span>' +
        '<span class="style-option-text"><strong>' + style.name + "</strong><small>" + style.note + "</small></span>" +
        '<span class="style-check" aria-hidden="true"></span>';
      option.addEventListener("click", function () {
        applyStyle(style.id, true);
      });
      styleOptions.appendChild(option);
    });
  }

  var initialStyle = root.getAttribute("data-style");
  if (!findStyle(initialStyle)) {
    try {
      initialStyle = window.localStorage.getItem(STYLE_KEY);
    } catch (error) {
      initialStyle = null;
    }
  }
  applyStyle(initialStyle, false);

  document.querySelectorAll("[data-style-reset]").forEach(function (button) {
    button.addEventListener("click", function () {
      applyStyle(DEFAULT_STYLE, true);
    });
  });

  if (styleMenu) {
    styleMenu.addEventListener("toggle", function (event) {
      if (event.newState !== "open") return;
      var selected = styleMenu.querySelector('[data-style-option][aria-pressed="true"]');
      if (selected) selected.focus();
    });
  }

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
