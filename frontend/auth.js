(function () {
  const localPorts = new Set(["5500", "5501", "5502", "5503"]);
  const apiBase = localPorts.has(window.location.port)
    ? `${window.location.protocol}//${window.location.hostname}:8000`
    : window.location.origin;
  window.JALSETHU_API_BASE = apiBase;

  const nativeFetch = window.fetch.bind(window);
  function tokenKey(role) {
    return `jalsethu-session-${role}`;
  }
  function currentToken() {
    const role = document.body && document.body.dataset.role;
    return role ? sessionStorage.getItem(tokenKey(role)) : null;
  }
  window.fetch = function (input, options) {
    const settings = options || {};
    let target;
    try {
      target = new URL(typeof input === "string" ? input : input.url, window.location.href);
    } catch {
      return nativeFetch(input, options);
    }
    const backendHost = target.port === "8000" && [window.location.hostname, "localhost", "127.0.0.1"].includes(target.hostname);
    if (target.origin !== apiBase && !backendHost) return nativeFetch(input, options);
    const headers = new Headers(settings.headers || (input instanceof Request ? input.headers : undefined));
    const sessionToken = currentToken();
    if (sessionToken) headers.set("Authorization", `Bearer ${sessionToken}`);
    return nativeFetch(input, Object.assign({}, settings, { headers }));
  };

  window.JALSETHU_AUTH = {
    apiBase,
    token: currentToken,
    headers() {
      const sessionToken = currentToken();
      return sessionToken ? { Authorization: `Bearer ${sessionToken}` } : {};
    },
    user: null,
  };

  const style = document.createElement("style");
  style.textContent = `
    #jalsetu-auth-overlay{position:fixed;inset:0;z-index:99999;display:grid;place-items:center;padding:20px;background:rgba(5,18,24,.86);font:15px/1.5 system-ui,sans-serif;color:#12303b}
    #jalsetu-auth-overlay{overflow:auto}
    #jalsetu-auth-overlay[hidden]{display:none}
    .ja-card{width:min(100%,440px);padding:28px;border-radius:18px;background:#fff;box-shadow:0 24px 80px #0005}
    .ja-card h1{margin:0 0 6px;font-size:26px}.ja-card p{margin:0 0 18px;color:#506b73}
    .ja-card label{display:block;margin:12px 0 5px;font-weight:650}
    .ja-card input{box-sizing:border-box;width:100%;padding:11px 12px;border:1px solid #bdd0d0;border-radius:9px;font:inherit}
    .ja-card button,.ja-toolbar button,.ja-dialog button{padding:11px 14px;border:0;border-radius:9px;background:#16785b;color:#fff;font:inherit;font-weight:700;cursor:pointer}
    .ja-card button[type=submit]{width:100%;margin-top:18px}.ja-card button:disabled{opacity:.6;cursor:wait}
    .ja-link{border:0;background:transparent!important;color:#126b52!important;text-decoration:underline;cursor:pointer}
    .ja-error{min-height:22px;margin-top:10px!important;color:#b42318!important}
    .ja-toolbar{position:relative;z-index:50;display:flex;justify-content:flex-end;align-items:center;gap:10px;padding:8px 16px;background:#0e302d;color:#fff;font:14px system-ui,sans-serif}
    .ja-toolbar button{padding:7px 11px;background:#218365}.ja-toolbar .ja-name{margin-right:auto}
    .ja-dialog{width:min(92vw,720px);max-height:80vh;border:0;border-radius:16px;padding:22px;color:#12303b}
    .ja-dialog::backdrop{background:#0008}.ja-order{padding:12px 0;border-bottom:1px solid #d9e4e1}.ja-events{color:#526b72;font-size:13px}
    .ja-admin-card{width:min(100%,760px);max-height:90vh;overflow:auto}.ja-admin-form{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:10px;align-items:end}.ja-admin-form label{margin:0}.ja-admin-form button{grid-column:1/-1}.ja-staff-row{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px;padding:10px 0;border-bottom:1px solid #d9e4e1}.ja-staff-row p{margin:0!important;overflow-wrap:anywhere}.ja-reset-form{display:flex;flex:1;min-width:240px;align-items:center;gap:8px;flex-wrap:wrap}.ja-reset-form input{box-sizing:border-box;min-width:180px;flex:1;padding:10px;border:1px solid #bdd0d0;border-radius:8px;font:inherit}.ja-reset-status{flex-basis:100%;margin:0!important;color:#126b52}.ja-admin-note{padding:12px;background:#edf7f3;border-radius:8px;overflow-wrap:anywhere}.ja-admin-actions{display:flex;gap:8px;flex-wrap:wrap}.ja-admin-actions button{margin-top:10px}
  `;
  document.head.appendChild(style);

  function authRequest(path, options) {
    return nativeFetch(`${apiBase}${path}`, options);
  }

  document.addEventListener("DOMContentLoaded", function () {
    const role = document.body.dataset.role;
    if (!["citizen", "operator", "driver"].includes(role)) return;
    const storageKey = tokenKey(role);
    let registering = false;
    let overlay = null;
    let adminToken = "";

    function showGate(errorText) {
      if (!overlay) {
        overlay = document.createElement("div");
        overlay.id = "jalsetu-auth-overlay";
        document.body.appendChild(overlay);
      }
      const roleLabel = role.charAt(0).toUpperCase() + role.slice(1);
      overlay.innerHTML = `
        <section class="ja-card" aria-labelledby="ja-title">
          <h1 id="ja-title">JalSetu ${roleLabel} ${registering ? "registration" : "login"}</h1>
          <p>${role === "citizen" ? "Sign in to save requests and view your delivery history." : "Sign in with your provisioned staff account to access assigned orders."}</p>
          <form id="ja-form" autocomplete="on">
            ${registering ? '<label for="ja-name">Full name</label><input id="ja-name" name="name" autocomplete="name" minlength="2" maxlength="100" required><label for="ja-phone">Phone (optional)</label><input id="ja-phone" name="phone" autocomplete="tel" maxlength="24">' : ""}
            <label for="ja-email">Email</label><input id="ja-email" name="email" type="email" autocomplete="username" maxlength="254" required>
            <label for="ja-password">Password</label><input id="ja-password" name="password" type="password" autocomplete="current-password" minlength="${registering ? "10" : "1"}" maxlength="128" required>
            <button type="submit">${registering ? "Create citizen account" : "Sign in"}</button>
            <p class="ja-error" id="ja-error" role="alert">${errorText || ""}</p>
            ${role === "citizen" ? `<button class="ja-link" id="ja-toggle" type="button">${registering ? "Already have an account? Sign in" : "New citizen? Create an account"}</button>` : '<p>Staff accounts are created by the JalSetu administrator.</p>'}
            <button class="ja-link" id="ja-admin-open" type="button">Administrator: manage operator and driver accounts</button>
          </form>
        </section>`;
      const toggle = document.getElementById("ja-toggle");
      if (toggle) toggle.addEventListener("click", function () { registering = !registering; showGate(""); });
      document.getElementById("ja-admin-open").addEventListener("click", showAdminGate);
      document.getElementById("ja-form").addEventListener("submit", submitAuth);
    }

    function showAdminGate(errorText) {
      if (!overlay) {
        overlay = document.createElement("div");
        overlay.id = "jalsetu-auth-overlay";
        document.body.appendChild(overlay);
      }
      overlay.innerHTML = `
        <section class="ja-card" aria-labelledby="ja-admin-title">
          <h1 id="ja-admin-title">Staff account administration</h1>
          <p>Enter the administrator token to view staff accounts, create logins, or reset passwords.</p>
          <form id="ja-admin-token-form" autocomplete="off">
            <label for="ja-admin-token">Administrator token</label><input id="ja-admin-token" type="password" autocomplete="current-password" required>
            <button type="submit">Continue</button>
            <p class="ja-error" id="ja-admin-error" role="alert">${errorText || ""}</p>
            <button class="ja-link" id="ja-admin-back" type="button">Back to sign in</button>
          </form>
        </section>`;
      document.getElementById("ja-admin-back").addEventListener("click", function () { showGate(""); });
      document.getElementById("ja-admin-token-form").addEventListener("submit", async function (event) {
        event.preventDefault();
        adminToken = document.getElementById("ja-admin-token").value;
        await loadAdminPanel();
      });
    }

    async function loadAdminPanel(message) {
      try {
        const headers = { "X-Admin-Token": adminToken };
        const responses = await Promise.all([
          authRequest("/auth/admin/users", { headers }),
          authRequest("/operators"),
        ]);
        const staffData = await responses[0].json();
        const operatorData = await responses[1].json();
        if (!responses[0].ok) throw new Error(staffData.message || staffData.detail || "Administrator token was rejected");
        if (!responses[1].ok) throw new Error("Could not load the configured operator list");
        renderAdminPanel(staffData.users || [], operatorData.operators || [], message);
      } catch (error) {
        showAdminGate(error.message || "Could not connect to the JalSetu API");
      }
    }

    function renderAdminPanel(users, operators, message) {
      overlay.innerHTML = `
        <section class="ja-card ja-admin-card" aria-labelledby="ja-admin-title">
          <h1 id="ja-admin-title">Manage operator and driver accounts</h1>
          <p>Staff passwords are stored as secure hashes. They can only be set or reset, never retrieved.</p>
          ${message ? '<p class="ja-admin-note" id="ja-admin-message" role="status"></p>' : ""}
          <h2>Create staff account</h2>
          <form class="ja-admin-form" id="ja-create-staff" autocomplete="off">
            <label>Name<input name="name" minlength="2" maxlength="100" required></label>
            <label>Email<input name="email" type="email" maxlength="254" required></label>
            <label>Temporary password<input name="password" type="password" minlength="10" maxlength="128" required></label>
            <label>Account type<select name="role"><option value="operator">Operator</option><option value="driver">Driver</option></select></label>
            <label>Assigned operator<select name="operator_id" required></select></label>
            <button type="submit">Create account</button>
          </form>
          <p class="ja-error" id="ja-create-error" role="alert"></p>
          <h2>Existing staff accounts</h2>
          <div id="ja-staff-list"></div>
          <div class="ja-admin-actions"><button id="ja-admin-refresh" type="button">Refresh list</button><button id="ja-admin-back" type="button">Close admin panel</button></div>
        </section>`;
      const messageNode = document.getElementById("ja-admin-message");
      if (messageNode) messageNode.textContent = message;
      const operatorSelect = overlay.querySelector('[name="operator_id"]');
      operators.forEach(function (operator) {
        const option = document.createElement("option");
        option.value = operator.operator_id;
        option.textContent = `${operator.name} (${operator.operator_id})${operator.type === "public" ? " · Public" : ""}`;
        operatorSelect.appendChild(option);
      });
      const staffList = document.getElementById("ja-staff-list");
      if (!users.length) {
        const empty = document.createElement("p");
        empty.textContent = "No operator or driver accounts have been created.";
        staffList.appendChild(empty);
      }
      users.forEach(function (user) {
        const row = document.createElement("article");
        row.className = "ja-staff-row";
        const details = document.createElement("p");
        details.textContent = `${user.name || user.display_name || "Staff"} · ${user.role} · ${user.email} · Operator ${user.operator_id}`;
        const reset = document.createElement("button");
        reset.type = "button";
        reset.textContent = "Set password";
        reset.addEventListener("click", function () {
          if (row.querySelector(".ja-reset-form")) return;
          const form = document.createElement("form");
          form.className = "ja-reset-form";
          const password = document.createElement("input");
          password.type = "password";
          password.name = "password";
          password.minLength = 10;
          password.maxLength = 128;
          password.autocomplete = "new-password";
          password.placeholder = "New password (10+ characters)";
          password.required = true;
          const save = document.createElement("button");
          save.type = "submit";
          save.textContent = "Save password";
          const cancel = document.createElement("button");
          cancel.type = "button";
          cancel.textContent = "Cancel";
          const status = document.createElement("p");
          status.className = "ja-reset-status";
          status.setAttribute("role", "status");
          cancel.addEventListener("click", function () { form.remove(); });
          form.addEventListener("submit", async function (event) {
            event.preventDefault();
            save.disabled = true;
            status.textContent = "Updating password…";
            try {
              const response = await authRequest(`/auth/admin/users/${user.id}/password`, {
                method: "PUT",
                headers: { "Content-Type": "application/json", "X-Admin-Token": adminToken },
                body: JSON.stringify({ password: password.value }),
              });
              const data = await response.json();
              if (!response.ok) throw new Error(data.message || data.detail || "Could not reset password");
              status.textContent = `Password updated for ${user.email}. Share the new password you entered; other active sessions were signed out.`;
              password.value = "";
            } catch (error) {
              status.textContent = error.message || "Could not reset password";
            } finally {
              save.disabled = false;
            }
          });
          form.append(password, save, cancel, status);
          row.appendChild(form);
          password.focus();
        });
        row.append(details, reset);
        staffList.appendChild(row);
      });
      document.getElementById("ja-admin-refresh").addEventListener("click", function () { loadAdminPanel(); });
      document.getElementById("ja-admin-back").addEventListener("click", function () { adminToken = ""; showGate(""); });
      document.getElementById("ja-create-staff").addEventListener("submit", async function (event) {
        event.preventDefault();
        const form = event.currentTarget;
        const values = new FormData(form);
        const errorNode = document.getElementById("ja-create-error");
        errorNode.textContent = "";
        const payload = Object.fromEntries(values.entries());
        try {
          const response = await authRequest("/auth/admin/users", {
            method: "POST",
            headers: { "Content-Type": "application/json", "X-Admin-Token": adminToken },
            body: JSON.stringify(payload),
          });
          const data = await response.json();
          if (!response.ok) throw new Error(data.message || data.detail || "Could not create account");
          await loadAdminPanel(`Created ${data.user.role} login for ${data.user.email}. Temporary password: ${payload.password}. Copy it for the staff member now; it cannot be retrieved later.`);
        } catch (error) {
          errorNode.textContent = error.message || "Could not create account";
        }
      });
    }

    function showHistory(orders) {
      let dialog = document.getElementById("ja-history-dialog");
      if (!dialog) {
        dialog = document.createElement("dialog");
        dialog.id = "ja-history-dialog";
        dialog.className = "ja-dialog";
        document.body.appendChild(dialog);
      }
      const heading = document.createElement("h2");
      heading.textContent = "Account history";
      const close = document.createElement("button");
      close.type = "button";
      close.textContent = "Close";
      close.addEventListener("click", function () { dialog.close(); });
      dialog.replaceChildren(heading, close);
      if (!orders.length) {
        const empty = document.createElement("p");
        empty.textContent = "No orders are recorded for this account yet.";
        dialog.appendChild(empty);
      }
      orders.forEach(function (order) {
        const item = document.createElement("article");
        item.className = "ja-order";
        const summary = document.createElement("strong");
        summary.textContent = `${order.order_id} · ${order.status} · ₹${order.price}`;
        const detail = document.createElement("p");
        detail.textContent = `${order.capacity_l} L · ${order.operator_id} · ${new Date(order.created_at).toLocaleString()}`;
        const events = document.createElement("p");
        events.className = "ja-events";
        events.textContent = (order.events || []).map(function (event) {
          return `${event.from_status || "START"} → ${event.to_status} (${new Date(event.at).toLocaleString()})`;
        }).join(" · ");
        item.append(summary, detail, events);
        dialog.appendChild(item);
      });
      dialog.showModal();
    }

    async function showAccount(user) {
      window.JALSETHU_AUTH.user = user;
      if (overlay) overlay.remove();
      overlay = null;
      let toolbar = document.getElementById("jalsetu-account-toolbar");
      if (!toolbar) {
        toolbar = document.createElement("nav");
        toolbar.id = "jalsetu-account-toolbar";
        toolbar.className = "ja-toolbar";
        const name = document.createElement("span");
        name.className = "ja-name";
        name.id = "ja-account-name";
        const historyButton = document.createElement("button");
        historyButton.type = "button";
        historyButton.textContent = "History";
        historyButton.addEventListener("click", async function () {
          try {
            const response = await window.fetch(`${apiBase}/auth/history`);
            const data = await response.json();
            if (!response.ok) throw new Error(data.message || "Could not load history");
            showHistory(data.orders || []);
          } catch (error) {
            window.alert(error.message || "Could not load history");
          }
        });
        const logoutButton = document.createElement("button");
        logoutButton.type = "button";
        logoutButton.textContent = "Sign out";
        logoutButton.addEventListener("click", async function () {
          try { await window.fetch(`${apiBase}/auth/logout`, { method: "POST" }); } catch {}
          sessionStorage.removeItem(storageKey);
          window.JALSETHU_AUTH.user = null;
          toolbar.remove();
          registering = false;
          showGate("");
        });
        toolbar.append(name, historyButton, logoutButton);
        document.body.insertBefore(toolbar, document.body.firstChild);
      }
      document.getElementById("ja-account-name").textContent = `${user.name} · ${user.role}`;
      window.dispatchEvent(new CustomEvent("jalsetu:authenticated", { detail: user }));
    }

    async function submitAuth(event) {
      event.preventDefault();
      const form = event.currentTarget;
      const button = form.querySelector('button[type="submit"]');
      const error = document.getElementById("ja-error");
      const formData = new FormData(form);
      const payload = {
        email: String(formData.get("email") || "").trim(),
        password: String(formData.get("password") || ""),
        role,
      };
      let path = "/auth/login";
      if (registering) {
        path = "/auth/register";
        payload.name = String(formData.get("name") || "").trim();
        payload.phone = String(formData.get("phone") || "").trim() || null;
        delete payload.role;
      }
      button.disabled = true;
      error.textContent = "";
      try {
        const response = await authRequest(path, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.message || "Authentication failed");
        if (!data.user || data.user.role !== role) throw new Error("This account is not authorized for this dashboard");
        sessionStorage.setItem(storageKey, data.access_token);
        await showAccount(data.user);
      } catch (requestError) {
        error.textContent = requestError.message || "Could not connect to the JalSetu API";
      } finally {
        if (button.isConnected) button.disabled = false;
      }
    }

    async function boot() {
      showGate("Checking your session…");
      const sessionToken = sessionStorage.getItem(storageKey);
      if (sessionToken) {
        try {
          const response = await authRequest("/auth/me", { headers: { Authorization: `Bearer ${sessionToken}` } });
          const data = await response.json();
          if (response.ok && data.user && data.user.role === role) {
            await showAccount(data.user);
            return;
          }
        } catch {}
        sessionStorage.removeItem(storageKey);
      }
      showGate("");
    }

    boot();
  });
})();
