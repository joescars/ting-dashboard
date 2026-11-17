"use strict";
// Shared controller for /login and /setup. Both pages post JSON and then
// navigate, so a failed sign-in never loses what the user typed.
(() => {
  const form = document.getElementById("form");
  if (!form) return;
  const mode = form.dataset.mode === "setup" ? "setup" : "login";
  const btn = document.getElementById("submit");
  const err = document.getElementById("error");

  const fail = (msg) => {
    err.textContent = msg;
    err.hidden = false;
  };
  const busy = (on) => {
    btn.disabled = on;
    btn.classList.toggle("loading", on);
    btn.querySelector(".btn-label").textContent = on
      ? (mode === "setup" ? "Linking…" : "Signing in…")
      : (mode === "setup" ? "Link account" : "Sign in");
  };

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    err.hidden = true;
    const body = { password: document.getElementById("password").value };
    if (!body.password) return fail("Enter your password.");
    if (mode === "setup") {
      body.email = document.getElementById("email").value.trim();
      body.token = document.getElementById("token").value.trim();
      if (!body.email) return fail("Enter the email address on your Ting account.");
      if (!body.token) return fail("Enter the setup code from the container log.");
    }
    busy(true);
    try {
      const r = await fetch(`/api/${mode}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.error || `Request failed (HTTP ${r.status})`);
      location.replace("/");
    } catch (ex) {
      fail(ex.message || "Something went wrong");
      busy(false);
      const pw = document.getElementById("password");
      if (mode === "login") { pw.select(); } else { pw.value = ""; }
    }
  });
})();
