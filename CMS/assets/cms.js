/* Dependency-free asynchronous CMS navigation with history and safe GET fallbacks. */
(() => {
  "use strict";
  let pending = null;

  function internalLink(link) {
    if (!link || link.target || link.hasAttribute("download") ||
        link.hasAttribute("data-full-navigation")) return null;
    const url = new URL(link.href, window.location.href);
    if (url.origin !== window.location.origin || url.protocol !== "https:" &&
        url.protocol !== "http:") return null;
    const path = url.pathname;
    // Executable applications keep their own document lifecycle.
    const cmsRoute = path === "/" || path === "/projects" || path === "/about" ||
      /^\/pages\/[a-z][a-z0-9-]+$/.test(path) ||
      /^\/projects\/[a-z][a-z0-9-]+$/.test(path) ||
      path === "/admin" || path.startsWith("/admin/") ||
      link.hasAttribute("data-cms-nav");
    return cmsRoute ? url : null;
  }

  async function navigate(url, push) {
    if (pending) pending.abort();
    const controller = new AbortController();
    pending = controller;
    try {
      const response = await fetch(url.href, {
        method: "GET", credentials: "same-origin", redirect: "follow",
        signal: controller.signal, headers: {"X-Sirus-Navigation": "1"}
      });
      if (controller.signal.aborted) return;
      if (!response.ok || !(response.headers.get("content-type") || "").includes("text/html")) {
        window.location.assign(url.href);
        return;
      }
      const parsed = new DOMParser().parseFromString(await response.text(), "text/html");
      const incoming = parsed.querySelector("main.page-main");
      const current = document.querySelector("main.page-main");
      const finalUrl = new URL(response.url, window.location.href);
      if (!incoming || !current || finalUrl.origin !== window.location.origin) {
        window.location.assign(url.href);
        return;
      }
      current.replaceWith(document.importNode(incoming, true));
      document.title = parsed.title || document.title;
      if (parsed.documentElement.dataset.accent)
        document.documentElement.dataset.accent = parsed.documentElement.dataset.accent;
      const oldNav = document.querySelector("#primaryNav");
      const newNav = parsed.querySelector("#primaryNav");
      if (oldNav && newNav) oldNav.replaceWith(document.importNode(newNav, true));
      const oldFooter = document.querySelector(".site-footer");
      const newFooter = parsed.querySelector(".site-footer");
      if (oldFooter && newFooter) oldFooter.replaceWith(document.importNode(newFooter, true));
      if (push) window.history.pushState({sirusCms: true}, "", finalUrl.href);
      window.scrollTo(0, 0);
      const heading = document.querySelector("main h1");
      if (heading) { heading.setAttribute("tabindex", "-1"); heading.focus({preventScroll: true}); }
    } catch (error) {
      if (error.name !== "AbortError") window.location.assign(url.href);
    } finally {
      if (pending === controller) pending = null;
    }
  }

  document.addEventListener("click", event => {
    const toggle = event.target.closest("[data-nav-toggle]");
    if (toggle) {
      const nav = document.querySelector("#primaryNav");
      if (!nav) return;
      const expanded = toggle.getAttribute("aria-expanded") !== "true";
      toggle.setAttribute("aria-expanded", String(expanded));
      nav.classList.toggle("is-open", expanded);
      return;
    }
    if (event.defaultPrevented || event.button !== 0 ||
        event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const link = event.target.closest("a[href]");
    const url = internalLink(link);
    if (!url) return;
    event.preventDefault();
    navigate(url, true);
  });
  document.addEventListener("submit", event => {
    const form = event.target.closest("form[data-confirm]");
    if (form && !window.confirm(form.dataset.confirm || "Confirm this action?"))
      event.preventDefault();
  });
  window.addEventListener("popstate", () => navigate(new URL(window.location.href), false));
})();
