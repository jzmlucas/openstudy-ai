(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const registrationOpen = document.body.dataset.registrationOpen === "true";
  const legacyProof = document.body.dataset.legacyProof === "true";
  const fields = {
    login: { form: $("login-form"), title: "Bem-vindo <span>de volta.</span>", subtitle: "Entre na sua conta para continuar de onde parou." },
    register: { form: $("register-form"), title: "Comece sua <span>jornada.</span>", subtitle: "Crie seu acesso e organize seus estudos em um só lugar." },
  };
  let mode = registrationOpen && !legacyProof ? "register" : "login";
  $("legacy-field").hidden = !legacyProof;
  if (legacyProof) $("legacy-password").required = true;

  function show(modeName) {
    mode = modeName;
    const registering = mode === "register";
    fields.login.form.hidden = registering;
    fields.register.form.hidden = !registering;
    $("auth-title").innerHTML = fields[mode].title;
    $("auth-subtitle").textContent = fields[mode].subtitle;
    $("auth-eyebrow").textContent = registering ? "CRIE SEU ESPAÇO DE APRENDIZADO" : "SEU ESPAÇO DE APRENDIZADO";
    $("switch-prompt").textContent = registering ? "Já tem uma conta?" : "Ainda não tem uma conta?";
    $("switch-mode").textContent = registering ? "Fazer login" : "Criar conta";
    $("auth-error").hidden = true;
    const first = fields[mode].form.querySelector("input");
    first?.focus();
  }
  $("switch-mode").addEventListener("click", () => show(mode === "login" ? "register" : "login"));
  document.querySelectorAll("[data-reveal]").forEach((button) => {
    button.addEventListener("click", () => {
      const input = $(button.dataset.reveal);
      input.type = input.type === "password" ? "text" : "password";
      button.textContent = input.type === "password" ? "Mostrar" : "Ocultar";
      button.setAttribute("aria-label", `${button.textContent} senha`);
    });
  });
  async function submit(form, endpoint, payload) {
    const button = form.querySelector("[type=submit]");
    button.disabled = true;
    const old = button.querySelector(".button-label").textContent;
    button.querySelector(".button-label").textContent = "Verificando...";
    $("auth-error").hidden = true;
    try {
      const response = await fetch(endpoint, {
        method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) {
        let message = body.detail || "Não foi possível concluir. Tente novamente.";
        if (Array.isArray(message)) message = message.map((item) => item.msg || "Confira os campos.").join("; ");
        throw new Error(message);
      }
      window.location.replace("/");
    } catch (error) {
      $("auth-error").textContent = error.message || "Falha na conexão. Tente novamente.";
      $("auth-error").hidden = false;
      button.disabled = false;
      button.querySelector(".button-label").textContent = old;
    }
  }
  fields.login.form.addEventListener("submit", (event) => {
    event.preventDefault();
    const username = $("login-username").value.trim();
    const password = $("login-password").value;
    if (!username || !password) return;
    submit(fields.login.form, "/api/auth/login", {username, password});
  });
  fields.register.form.addEventListener("submit", (event) => {
    event.preventDefault();
    const username = $("register-username").value.trim();
    const password = $("register-password").value;
    const confirm = $("register-confirm").value;
    if (password !== confirm) {
      $("auth-error").textContent = "As senhas não coincidem. Confira e tente novamente.";
      $("auth-error").hidden = false;
      return;
    }
    submit(fields.register.form, "/api/auth/register", {
      username, password, legacy_password: legacyProof ? $("legacy-password").value : ""
    });
  });
  show(mode);
})();
