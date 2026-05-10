// Locally: talk directly to uvicorn. In production: Railway backend.
const BACKEND_URL = (window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1")
  ? "http://127.0.0.1:8000"
  : "https://cvora-production.up.railway.app";

console.log("app.js loaded");

// ── Auth ──────────────────────────────────────────────────────────────────────

const authScreen     = document.getElementById("authScreen");
const appScreen      = document.getElementById("appScreen");
const authForm       = document.getElementById("authForm");
const authEmail      = document.getElementById("authEmail");
const authPassword   = document.getElementById("authPassword");
const authError      = document.getElementById("authError");
const authTitle      = document.getElementById("authTitle");
const authSubmitBtn  = document.getElementById("authSubmitBtn");
const authToggleBtn  = document.getElementById("authToggleBtn");
const authToggleText = document.getElementById("authToggleText");
const loggedInEmail  = document.getElementById("loggedInEmail");
const passwordRules  = document.getElementById("passwordRules");

let isLoginMode = true;

function getToken() { return localStorage.getItem("token"); }
function getEmail() { return localStorage.getItem("userEmail"); }

function setSession(token, email) {
  localStorage.setItem("token", token);
  localStorage.setItem("userEmail", email);
}

function clearSession() {
  localStorage.removeItem("token");
  localStorage.removeItem("userEmail");
}

function authHeaders() {
  return {
    "Content-Type":  "application/json",
    "Authorization": `Bearer ${getToken()}`,
  };
}

// Central fetch wrapper — auto-logout on 401 (expired/invalid token)
async function apiFetch(url, options = {}) {
  const res = await fetch(url, options);
  if (res.status === 401) {
    clearSession();
    resetBuilder();
    showAuthScreen();
    // Show a friendly message instead of a blank screen
    authError.textContent = "Your session expired — please log in again.";
    throw new Error("Session expired");
  }
  return res;
}

function showApp() {
  authScreen.classList.add("hidden");
  appScreen.classList.remove("hidden");
  const displayName = getEmail() || "";
  loggedInEmail.textContent = displayName;
  // Set avatar initials from email
  const avatarBtn = document.getElementById("profileBtn");
  if (avatarBtn) {
    const initials = (displayName.split("@")[0] || "?").slice(0, 2).toUpperCase();
    avatarBtn.textContent = initials;
  }
  // Always land on upload mode
  document.getElementById("uploadSection")?.classList.remove("hidden");
  document.getElementById("builderSection")?.classList.add("hidden");
  document.querySelector(".btn-upload")?.classList.add("mode-active");
  document.querySelector(".btn-builder")?.classList.remove("mode-active");
  // Preview panel only visible in builder mode
  document.getElementById("previewCard")?.classList.add("hidden");
  // Ensure main view shown, profile hidden
  document.getElementById("mainView")?.removeAttribute("style");
  document.getElementById("profileScreen")?.classList.remove("visible");
}

function showAuthScreen() {
  appScreen.classList.add("hidden");
  authScreen.classList.remove("hidden");
  authError.textContent = "";
  authEmail.value = "";
  authPassword.value = "";
  // Clear confirm password and rule states
  const confirmField = document.getElementById("authConfirmPassword");
  if (confirmField) confirmField.value = "";
  // Reset password rules to unchecked state
  ["rule-length","rule-upper","rule-lower","rule-digit","rule-special"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.className = "rule-fail";
  });
  if (passwordRules) passwordRules.style.display = "none";
  // Reset all overlay forms back to login state
  document.getElementById("forgotForm")?.classList.remove("visible");
  document.getElementById("resetPassForm")?.classList.remove("visible");
  document.getElementById("verifyPendingForm")?.classList.remove("visible");
  if (authTitle)  { authTitle.style.display = ""; }
  const sub = document.querySelector(".auth-subtitle");
  if (sub) { sub.style.display = ""; sub.textContent = "Sign in to your account to continue."; }
  const authFormEl = document.getElementById("authForm");
  if (authFormEl) authFormEl.style.display = "";
  const atf = document.getElementById("authToggleFooter");
  if (atf) atf.style.display = "";
  const fpl = document.getElementById("forgotPasswordLink");
  if (fpl) fpl.style.display = "";
  // Reset to login mode
  if (!isLoginMode) {
    isLoginMode = true;
    authTitle.textContent = "Welcome back";
    authSubmitBtn.textContent = "Login";
    authToggleBtn.textContent = "Register";
    authToggleText.textContent = "Don't have an account?";
    passwordRules.style.display = "none";
    document.getElementById("confirmPasswordField").style.display = "none";
    document.getElementById("confirmPasswordError").textContent = "";
    document.getElementById("authConfirmPassword").value = "";
  }
}

window.logout = function () {
  resetBuilder();
  clearSession();
  showAuthScreen();
};

// Toggle between Login and Register
authToggleBtn.addEventListener("click", () => {
  isLoginMode = !isLoginMode;
  authTitle.textContent      = isLoginMode ? "Welcome back"       : "Create your account";
  authSubmitBtn.textContent  = isLoginMode ? "Login"              : "Register";
  authToggleBtn.textContent  = isLoginMode ? "Register"           : "Login";
  authToggleText.textContent = isLoginMode ? "Don't have an account?" : "Already have an account?";
  const sub = document.querySelector(".auth-subtitle");
  if (sub) sub.textContent = isLoginMode ? "Sign in to your account to continue." : "Fill in your details to get started.";
  authError.textContent = "";
  passwordRules.style.display  = isLoginMode ? "none" : "block";
  document.getElementById("confirmPasswordField").style.display = isLoginMode ? "none" : "block";
  document.getElementById("confirmPasswordError").textContent = "";
  document.getElementById("authConfirmPassword").value = "";
  if (!isLoginMode) {
    const p = authPassword.value;
    checkRule("rule-length",  p.length >= 8);
    checkRule("rule-upper",   /[A-Z]/.test(p));
    checkRule("rule-lower",   /[a-z]/.test(p));
    checkRule("rule-digit",   /\d/.test(p));
    checkRule("rule-special", /[!@#$%^&*()_+\-=\[\]{};':"\\|,.<>\/?]/.test(p));
  }
});

// Live password strength checker (register mode only)
function checkRule(ruleId, passes) {
  const el = document.getElementById(ruleId);
  if (!el) return;
  el.className = passes ? "rule-pass" : "rule-fail";
}

authPassword.addEventListener("input", () => {
  if (isLoginMode) return;
  const p = authPassword.value;
  checkRule("rule-length",  p.length >= 8);
  checkRule("rule-upper",   /[A-Z]/.test(p));
  checkRule("rule-lower",   /[a-z]/.test(p));
  checkRule("rule-digit",   /\d/.test(p));
  checkRule("rule-special", /[!@#$%^&*()_+\-=\[\]{};':"\\|,.<>\/?]/.test(p));
});

authForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  authError.textContent = "";

  const email    = authEmail.value.trim();
  const password = authPassword.value.trim();
  const endpoint = isLoginMode ? "/login" : "/register";

  // Stricter email validation — must have a real TLD (user@gmail not valid)
  const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;
  if (!emailRegex.test(email)) {
    authError.textContent = "Please enter a valid email address (e.g. you@example.com).";
    return;
  }

  if (!isLoginMode) {
    const confirm = document.getElementById("authConfirmPassword").value;
    const confirmErr = document.getElementById("confirmPasswordError");
    if (password !== confirm) {
      confirmErr.textContent = "Passwords do not match.";
      return;
    }
    confirmErr.textContent = "";
  }

  try {
    const res = await fetch(`${BACKEND_URL}${endpoint}`, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ email, password }),
    });

    const data = await res.json();

    if (!res.ok) {
      if (data.detail === "EMAIL_NOT_VERIFIED") {
        showVerifyPending(email);
      } else if ((data.detail || "").includes("Email already registered")) {
        authError.innerHTML = 'Email already registered. <button type="button" style="background:none;border:none;color:var(--primary);font-weight:600;cursor:pointer;padding:0;font-size:inherit;" onclick="switchToLogin()">Try logging in instead.</button>';
      } else {
        authError.textContent = data.detail || "Something went wrong.";
      }
      return;
    }

    // Registration returns requires_verification when email verification is enabled
    if (data.requires_verification) {
      showVerifyPending(data.email);
      return;
    }

    setSession(data.token, data.email);
    showApp();
  } catch (err) {
    authError.textContent = "Cannot reach the server. Is the backend running?";
  }
});

function switchToLogin() {
  if (!isLoginMode) authToggleBtn.click();
}

function showVerifyPending(email) {
  const authFormEl = document.getElementById("authForm");
  const authToggleFooter = document.getElementById("authToggleFooter");
  const forgotLink = document.getElementById("forgotPasswordLink");
  if (authFormEl) authFormEl.style.display = "none";
  if (authToggleFooter) authToggleFooter.style.display = "none";
  if (forgotLink) forgotLink.style.display = "none";
  if (authTitle) authTitle.style.display = "none";
  const sub = document.querySelector(".auth-subtitle");
  if (sub) sub.style.display = "none";
  const el = document.getElementById("verifyPendingEmail");
  if (el) el.textContent = email;
  document.getElementById("verifyPendingForm")?.classList.add("visible");
  // Store email for resend
  document.getElementById("verifyPendingForm")._pendingEmail = email;
}

document.getElementById("backFromVerifyBtn")?.addEventListener("click", () => {
  showAuthScreen();
});

document.getElementById("resendVerifyBtn")?.addEventListener("click", async () => {
  const form = document.getElementById("verifyPendingForm");
  const email = form?._pendingEmail;
  const msg = document.getElementById("resendMsg");
  if (!email) return;
  try {
    await fetch(`${BACKEND_URL}/resend-verification`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email }),
    });
    if (msg) msg.textContent = "Verification email resent. Check your inbox.";
  } catch (_) {
    if (msg) msg.textContent = "Failed to resend. Please try again.";
  }
});

// On page load: check for verify_token in URL, then check if already logged in
(async () => {
  const params = new URLSearchParams(window.location.search);
  const verifyToken = params.get("verify_token");
  if (verifyToken) {
    window.history.replaceState({}, "", window.location.pathname);
    try {
      const res = await fetch(`${BACKEND_URL}/verify-email`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: verifyToken }),
      });
      const data = await res.json();
      if (res.ok && data.token) {
        setSession(data.token, data.email);
        showApp();
        return;
      } else {
        showAuthScreen();
        authError.textContent = data.detail || "Verification link is invalid or expired.";
        return;
      }
    } catch (_) {
      showAuthScreen();
      return;
    }
  }
  if (getToken()) {
    showApp();
  } else {
    showAuthScreen();
  }
})();

const uploadForm = document.getElementById("uploadForm");
const builderForm = document.getElementById("builderForm");

const statusCard = document.getElementById("statusCard");
const statusText = document.getElementById("statusText");
const resultsCard = document.getElementById("resultsCard");
const candidateInfo = document.getElementById("candidateInfo");
const matchedJobs = document.getElementById("matchedJobs");

const uploadSection = document.getElementById("uploadSection");
const builderSection = document.getElementById("builderSection");

const technicalSkills = [];
const toolsSkills = [];
const softSkills = [];

window.showUpload = function () {
  uploadSection.classList.remove("hidden");
  builderSection.classList.add("hidden");
  document.querySelector(".btn-upload")?.classList.add("mode-active");
  document.querySelector(".btn-builder")?.classList.remove("mode-active");
  document.getElementById("previewCard")?.classList.add("hidden");
};

window.showBuilder = function () {
  builderSection.classList.remove("hidden");
  uploadSection.classList.add("hidden");
  document.querySelector(".btn-builder")?.classList.add("mode-active");
  document.querySelector(".btn-upload")?.classList.remove("mode-active");
  document.getElementById("previewCard")?.classList.remove("hidden");
};

// ── Inline error banner ───────────────────────────────────────────────────────
// Shows a red dismissable error inside whichever card is currently visible
// instead of disruptive browser alert() popups.
function showFormError(message, anchorId) {
  // Remove any existing error banner
  document.querySelectorAll(".form-error-banner").forEach(el => el.remove());

  const anchor = document.getElementById(anchorId);
  if (!anchor) { console.error(message); return; }

  const div = document.createElement("div");
  div.className = "form-error-banner";
  div.style.cssText = [
    "background:#FEF2F2", "border:1.5px solid #FECACA", "color:#991B1B",
    "border-radius:10px", "padding:14px 18px", "margin-bottom:16px",
    "font-size:13px", "line-height:1.7", "white-space:pre-wrap", "font-weight:500",
  ].join(";");
  div.innerHTML = `<strong style="display:block;margin-bottom:4px;font-size:13px;font-weight:700;">Please fix the following:</strong>${escapeHtml(message)}
    <button onclick="this.parentElement.remove()"
      style="float:right;background:none;color:#991B1B;padding:0;font-size:18px;line-height:1;border:none;cursor:pointer;margin-top:-22px;box-shadow:none;">&#215;</button>`;
  anchor.insertAdjacentElement("beforebegin", div);
  div.scrollIntoView({ behavior: "smooth", block: "center" });
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function sanitizeInput(val, maxLen = 500) {
  return String(val ?? "").replace(/[<>'"`;]/g, "").trim().slice(0, maxLen);
}

function createEntryCard(title, innerHtml) {
  return `
    <div class="entry-card">
      <div class="inline-actions">
        <button type="button" class="remove-entry-btn">Remove</button>
      </div>
      <h4>${title}</h4>
      ${innerHtml}
    </div>
  `;
}

function attachRemoveHandlers(containerId) {
  const container = document.getElementById(containerId);
  container.querySelectorAll(".remove-entry-btn").forEach((btn) => {
    btn.onclick = () => btn.closest(".entry-card").remove();
  });
}

function addLinkEntry() {
  const container = document.getElementById("linksEntries");
  container.insertAdjacentHTML("beforeend", createEntryCard("Link", `
    <label>Link Type</label>
    <select class="link-type-select">
      <option value="linkedin">LinkedIn</option>
      <option value="github">GitHub</option>
      <option value="portfolio">Portfolio</option>
      <option value="website">Website</option>
      <option value="twitter">Twitter / X</option>
      <option value="behance">Behance</option>
      <option value="dribbble">Dribbble</option>
      <option value="leetcode">LeetCode</option>
      <option value="other">Other</option>
    </select>
    <input class="link-type-custom" placeholder="Specify type" maxlength="50" style="display:none;" />
    <input class="link-url" type="url" placeholder="https://..." maxlength="300" />
    <input class="link-display" placeholder="Display text (max 100 chars)" maxlength="100" />
  `));
  const card = container.lastElementChild;
  attachRemoveHandlers("linksEntries");
  // show custom input when "Other" selected
  const sel = card.querySelector(".link-type-select");
  const custom = card.querySelector(".link-type-custom");
  sel.addEventListener("change", () => {
    custom.style.display = sel.value === "other" ? "" : "none";
    custom.required = sel.value === "other";
  });
}

function addEducationEntry() {
  const container = document.getElementById("educationEntries");
  container.insertAdjacentHTML("beforeend", createEntryCard("Education", `
    <label class="required-mark">Institution</label>
    <input class="education-institution" placeholder="e.g. University of Oxford" maxlength="200" required />

    <label>Degree / Type of Study *</label>
    <select class="education-degree-select" required>
      <option value="">Select degree…</option>
      <option>High School Diploma</option>
      <option>Associate's Degree</option>
      <option>Bachelor of Science (BS)</option>
      <option>Bachelor of Arts (BA)</option>
      <option>Bachelor of Engineering (BEng)</option>
      <option>Bachelor of Business Administration (BBA)</option>
      <option>Bachelor of Commerce (BCom)</option>
      <option>Master of Science (MS/MSc)</option>
      <option>Master of Arts (MA)</option>
      <option>Master of Business Administration (MBA)</option>
      <option>Master of Engineering (MEng)</option>
      <option>PhD / Doctorate</option>
      <option>Medical Degree (MD)</option>
      <option>Law Degree (JD/LLB)</option>
      <option>Diploma</option>
      <option>Certificate</option>
      <option>Other</option>
    </select>
    <input class="education-degree-other" placeholder="Specify degree *" maxlength="150" style="display:none;" />

    <input class="education-field" placeholder="Field of study (optional)" maxlength="150" />

    <label>Start Date *</label>
    <input class="education-start" type="month" required />
    <label>End Date (or expected)</label>
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;">
      <input class="education-currently-enrolled" type="checkbox" />
      <span style="font-size:13px;color:var(--text-secondary);">Currently enrolled / ongoing</span>
    </div>
    <input class="education-end" type="month" />

    <label>Grade (optional)</label>
    <select class="education-grade-type">
      <option value="">No grade</option>
      <option value="gpa">GPA (0.0 – 5.0)</option>
      <option value="percentage">Percentage score</option>
      <option value="letter">Letter grade</option>
    </select>
    <div class="grade-gpa-row" style="display:none;">
      <input class="education-gpa-number" type="number" min="0" max="5" step="0.01"
             placeholder="e.g. 3.85" />
    </div>
    <div class="grade-pct-row" style="display:none;">
      <input class="education-pct-number" type="number" min="0" max="100" step="0.01"
             placeholder="e.g. 88.50" />
    </div>
    <div class="grade-letter-row" style="display:none;">
      <select class="education-letter-select">
        <option>A+</option><option>A</option><option>A-</option>
        <option>B+</option><option>B</option><option>B-</option>
        <option>C+</option><option>C</option><option>C-</option>
        <option>D+</option><option>D</option><option>D-</option>
        <option>F</option>
      </select>
    </div>
  `));
  const card = container.lastElementChild;
  attachRemoveHandlers("educationEntries");

  // Degree "Other" toggle
  const degSel = card.querySelector(".education-degree-select");
  const degOther = card.querySelector(".education-degree-other");
  degSel.addEventListener("change", () => {
    const isOther = degSel.value === "Other";
    degOther.style.display = isOther ? "" : "none";
    degOther.required = isOther;
  });

  // "Currently enrolled" disables end date
  const enrolledCb  = card.querySelector(".education-currently-enrolled");
  const eduEndInput = card.querySelector(".education-end");
  enrolledCb.addEventListener("change", () => {
    eduEndInput.disabled = enrolledCb.checked;
    if (enrolledCb.checked) eduEndInput.value = "";
  });

  // Grade type toggle
  const gradeType = card.querySelector(".education-grade-type");
  const gpaRow    = card.querySelector(".grade-gpa-row");
  const pctRow    = card.querySelector(".grade-pct-row");
  const letRow    = card.querySelector(".grade-letter-row");
  const gpaInput  = card.querySelector(".education-gpa-number");
  const pctInput  = card.querySelector(".education-pct-number");
  gradeType.addEventListener("change", () => {
    const v = gradeType.value;
    gpaRow.style.display = v === "gpa"        ? "" : "none";
    pctRow.style.display = v === "percentage" ? "" : "none";
    letRow.style.display = v === "letter"     ? "" : "none";
    gpaInput.required = v === "gpa";
    pctInput.required = v === "percentage";
  });
}

function addExperienceEntry() {
  const container = document.getElementById("experienceEntries");
  container.insertAdjacentHTML("beforeend", createEntryCard("Work Experience", `
    <input class="experience-organization" placeholder="Organization *" maxlength="200" required />
    <input class="experience-position" placeholder="Position *" maxlength="150" required />
    <label>Start Date *</label>
    <input class="experience-start" type="month" required />
    <label>End Date</label>
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;">
      <input class="experience-currently-working" type="checkbox" />
      <span style="font-size:13px;color:var(--text-secondary);">Currently working here</span>
    </div>
    <input class="experience-end" type="month" />
    <input class="experience-location" placeholder="Location (optional)" maxlength="100" />
    <textarea class="experience-description" rows="4" maxlength="2000"
      placeholder="Responsibilities / achievements — one bullet per line *" required></textarea>
  `));
  const card = container.lastElementChild;
  attachRemoveHandlers("experienceEntries");

  // "Currently working here" disables end date
  const cwCb   = card.querySelector(".experience-currently-working");
  const endInp = card.querySelector(".experience-end");
  cwCb.addEventListener("change", () => {
    endInp.disabled = cwCb.checked;
    if (cwCb.checked) endInp.value = "";
  });
}

function addProjectEntry() {
  const container = document.getElementById("projectEntries");
  container.insertAdjacentHTML("beforeend", createEntryCard("Project", `
    <input class="project-title" placeholder="Title *" maxlength="150" required />
    <input class="project-role" placeholder="Role (optional, max 100 chars)" maxlength="100" />
    <input class="project-technologies"
           placeholder="Technologies, comma-separated (optional)" maxlength="300" />
    <button type="button" class="add-url-btn">+ Add URL</button>
    <div class="url-row" style="display:none;">
      <input class="project-link" type="url" placeholder="https://…" maxlength="300"
             style="margin-bottom:12px;" />
    </div>
    <textarea class="project-description" rows="4" maxlength="2000"
      placeholder="Description bullets — one per line *" required></textarea>
  `));
  const card = container.lastElementChild;
  attachRemoveHandlers("projectEntries");

  // URL toggle button
  const addBtn = card.querySelector(".add-url-btn");
  const urlRow = card.querySelector(".url-row");
  addBtn.addEventListener("click", () => {
    urlRow.style.display = "";
    addBtn.style.display = "none";
  });
}

function addExtracurricularEntry() {
  const container = document.getElementById("extracurricularEntries");
  container.insertAdjacentHTML(
    "beforeend",
    createEntryCard("Extracurricular", `
      <input class="extracurricular-title" placeholder="Title *" maxlength="150" required />
      <input class="extracurricular-role" placeholder="Role *" maxlength="100" required />
      <input class="extracurricular-organization" placeholder="Organization *" maxlength="200" required />
      <label>Date (optional)</label>
      <input class="extracurricular-date" type="month" />
      <textarea class="extracurricular-description" rows="4" maxlength="1000" placeholder="Description (optional). Separate bullets with a new line."></textarea>
    `)
  );
  attachRemoveHandlers("extracurricularEntries");
}

function addCertificationEntry() {
  const container = document.getElementById("certificationEntries");
  container.insertAdjacentHTML(
    "beforeend",
    createEntryCard("Certification", `
      <input class="certification-title" placeholder="Title *" maxlength="200" required />
      <label>Date (optional)</label>
      <input class="certification-date" type="month" />
      <input class="certification-organization" placeholder="Organization (optional)" maxlength="200" />
    `)
  );
  attachRemoveHandlers("certificationEntries");
}

function addAwardEntry() {
  const container = document.getElementById("awardEntries");
  container.insertAdjacentHTML(
    "beforeend",
    createEntryCard("Award", `
      <input class="award-title" placeholder="Title *" maxlength="200" required />
      <label>Date (optional)</label>
      <input class="award-date" type="month" />
      <input class="award-institution" placeholder="Institution (optional)" maxlength="200" />
    `)
  );
  attachRemoveHandlers("awardEntries");
}

function addLanguageEntry() {
  const container = document.getElementById("languageEntries");
  container.insertAdjacentHTML(
    "beforeend",
    createEntryCard("Language", `
      <input class="language-name" placeholder="Language" />
      <select class="language-level">
        <option value="Native">Native</option>
        <option value="Fluent">Fluent</option>
        <option value="Professional">Professional</option>
        <option value="Intermediate">Intermediate</option>
        <option value="Basic">Basic</option>
      </select>
    `)
  );
  attachRemoveHandlers("languageEntries");
}

const SKILL_PATTERN = /^[a-zA-Z0-9+#&.\-\s']+$/;
const SKILL_MAX_LENGTH = 60;

function setupSkillInput(inputId, containerId, stateArray) {
  const input = document.getElementById(inputId);
  if (!input) return;
  input.setAttribute("maxlength", SKILL_MAX_LENGTH);
  input.addEventListener("keydown", function (e) {
    if (e.key === "Enter") {
      e.preventDefault();
      const value = this.value.trim();
      if (!value) return;
      if (value.length > SKILL_MAX_LENGTH) {
        showFormError(`Skill name must be ${SKILL_MAX_LENGTH} characters or fewer.`, "builderForm");
        return;
      }
      if (!SKILL_PATTERN.test(value)) {
        showFormError("Skill contains invalid characters. Use letters, numbers, and common symbols only.", "builderForm");
        return;
      }
      if (!stateArray.includes(value)) stateArray.push(value);
      this.value = "";
      renderSkillChips(containerId, stateArray);
    }
  });
}

function renderSkillChips(containerId, stateArray) {
  const container = document.getElementById(containerId);
  const modClass = containerId === "toolsSkillsContainer" ? " badge--tools"
                 : containerId === "softSkillsContainer"  ? " badge--soft"
                 : "";
  container.innerHTML = stateArray
    .map((skill, index) => `
      <span class="badge${modClass}">
        ${escapeHtml(skill)}
        <button type="button" onclick="removeSkillChip('${containerId}', ${index})" title="Remove">&#215;</button>
      </span>
    `)
    .join("");
}

window.removeSkillChip = function (containerId, index) {
  const map = {
    technicalSkillsContainer: technicalSkills,
    toolsSkillsContainer: toolsSkills,
    softSkillsContainer: softSkills,
  };
  map[containerId].splice(index, 1);
  renderSkillChips(containerId, map[containerId]);
};

function getTextValue(selector, root) {
  return root.querySelector(selector)?.value?.trim() || "";
}

function splitBullets(text) {
  return (text || "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

function collectEntries(containerId, mapper) {
  return Array.from(document.querySelectorAll(`#${containerId} .entry-card`))
    .map(mapper)
    .filter(Boolean);
}

function buildCandidateProfile() {
  const allSkills = [
    ...technicalSkills,
    ...toolsSkills,
    ...softSkills,
  ];

  return {
    full_name: document.getElementById("fullName")?.value?.trim() || "",
    email: document.getElementById("email")?.value?.trim() || "",
    phone: document.getElementById("phone")?.value?.trim() || "",
    location: document.getElementById("location")?.value?.trim() || null,
    links: collectEntries("linksEntries", (card) => {
      const typeSel  = card.querySelector(".link-type-select");
      const typeCustom = getTextValue(".link-type-custom", card);
      const type = typeSel?.value === "other" ? typeCustom : (typeSel?.value || "");
      const url  = getTextValue(".link-url", card);
      const display = getTextValue(".link-display", card);
      if (!url) return null;
      return { type, url, display };
    }),
    summary: document.getElementById("summary")?.value?.trim() || null,
    skills: allSkills,
    skill_groups: {
      technical: [...technicalSkills],
      tools: [...toolsSkills],
      soft: [...softSkills],
    },
    setup: {
      user_type: document.getElementById("userType")?.value || "",
      target_fields: (document.getElementById("fields")?.value || "")
        .split(",")
        .map((v) => v.trim())
        .filter(Boolean),
      application_level: document.getElementById("level")?.value || "",
    },
    languages: collectEntries("languageEntries", (card) => {
      const language = getTextValue(".language-name", card);
      const proficiency = card.querySelector(".language-level")?.value || "";
      if (!language) return null;
      return { language, proficiency };
    }),
    certifications: collectEntries("certificationEntries", (card) => {
      const title = getTextValue(".certification-title", card);
      const date = getTextValue(".certification-date", card);
      const organization = getTextValue(".certification-organization", card);
      if (!title) return null;
      return { title, date: date || null, organization: organization || null };
    }),
    work_experience: collectEntries("experienceEntries", (card) => {
      const organization = getTextValue(".experience-organization", card);
      const position     = getTextValue(".experience-position", card);
      const start_date   = getTextValue(".experience-start", card);
      const isCurrent    = card.querySelector(".experience-currently-working")?.checked;
      const end_date     = isCurrent ? "" : getTextValue(".experience-end", card);
      const location     = getTextValue(".experience-location", card);
      const description  = splitBullets(getTextValue(".experience-description", card));
      if (!organization && !position) return null;
      return { organization, position, start_date, end_date, location: location || null, description };
    }),
    education: collectEntries("educationEntries", (card) => {
      const institution = getTextValue(".education-institution", card);

      // Degree: dropdown or custom "Other"
      const degSel   = card.querySelector(".education-degree-select");
      const degOther = getTextValue(".education-degree-other", card);
      const degree   = degSel?.value === "Other" ? degOther : (degSel?.value || "");

      const start_date   = getTextValue(".education-start", card);
      const isEnrolled   = card.querySelector(".education-currently-enrolled")?.checked;
      const end_date     = isEnrolled ? "" : getTextValue(".education-end", card);
      const field_of_study = getTextValue(".education-field", card);

      // Grade: depends on selected grade type
      const gradeType = card.querySelector(".education-grade-type")?.value || "";
      let gpa = null;
      if (gradeType === "gpa") {
        const val = getTextValue(".education-gpa-number", card);
        if (val) gpa = `GPA: ${val}`;
      } else if (gradeType === "percentage") {
        const val = getTextValue(".education-pct-number", card);
        if (val) gpa = `${val}%`;
      } else if (gradeType === "letter") {
        gpa = card.querySelector(".education-letter-select")?.value || null;
      }

      if (!institution && !degree && !start_date && !end_date) return null;
      return { institution, degree, start_date, end_date, field_of_study: field_of_study || null, gpa };
    }),
    projects: collectEntries("projectEntries", (card) => {
      const title = getTextValue(".project-title", card);
      const role = getTextValue(".project-role", card);
      const technologies = getTextValue(".project-technologies", card)
        .split(",")
        .map((v) => v.trim())
        .filter(Boolean);
      const link = getTextValue(".project-link", card);
      const description = splitBullets(getTextValue(".project-description", card));
      if (!title && description.length === 0) return null;
      return {
        title,
        role: role || null,
        technologies,
        link: link || null,
        description,
      };
    }),
    extracurriculars: collectEntries("extracurricularEntries", (card) => {
      const title = getTextValue(".extracurricular-title", card);
      const role = getTextValue(".extracurricular-role", card);
      const organization = getTextValue(".extracurricular-organization", card);
      const date = getTextValue(".extracurricular-date", card);
      const description = splitBullets(getTextValue(".extracurricular-description", card));
      if (!title && !role && !organization) return null;
      return {
        title,
        role,
        organization,
        date: date || null,
        description,
      };
    }),
    awards: collectEntries("awardEntries", (card) => {
      const title = getTextValue(".award-title", card);
      const date = getTextValue(".award-date", card);
      const institution = getTextValue(".award-institution", card);
      if (!title) return null;
      return { title, date: date || null, institution: institution || null };
    }),
  };
}

// Show/hide relocation countries based on checkbox
document.querySelectorAll('#uploadSection input[type="checkbox"]').forEach(cb => {
  cb.addEventListener("change", () => {
    const willRelocate = document.querySelector('#uploadSection input[value="willing_to_relocate"]')?.checked;
    const group = document.getElementById("relocationCountriesGroup");
    if (group) group.style.display = willRelocate ? "" : "none";
  });
});
// Also for builder section if it has similar checkboxes
document.querySelectorAll('#builderSection input[type="checkbox"]').forEach(cb => {
  cb.addEventListener("change", () => {
    const willRelocate = document.querySelector('#builderSection input[value="willing_to_relocate"]')?.checked;
    const group = document.getElementById("relocationCountriesGroupBuilder");
    if (group) group.style.display = willRelocate ? "" : "none";
  });
});

if (uploadForm) {
  uploadForm.addEventListener("submit", async (e) => {
    e.preventDefault();

    const fileInput = document.getElementById("cv");
    if (fileInput.files[0] && fileInput.files[0].size > 10 * 1024 * 1024) {
      showFormError("File is too large. Please upload a PDF under 10 MB.", "uploadSection");
      return;
    }

    // Check if there's already an active analysis
    if (_activeJobId && _activeInterval) {
      const ok = await showConfirmModal(
        "Analysis in progress",
        "A CV is currently being analyzed. Starting a new one will stop the current analysis. Continue?"
      );
      if (!ok) return;
      window.stopAnalysis();
    }
    const checkedModes = Array.from(
      document.querySelectorAll('#uploadSection input[type="checkbox"]:checked')
    ).map((cb) => cb.value);

    // Collect selected country codes from the multi-select dropdown
    const relocationSelect = document.getElementById("relocationCountries");
    const selectedCountryCodes = Array.from(relocationSelect.selectedOptions).map(o => o.value);

    const formData = new FormData();
    formData.append("file", fileInput.files[0]);
    formData.append("modes", checkedModes.join(","));
    formData.append("relocation_locations", selectedCountryCodes.join(","));

    statusCard.classList.remove("hidden");
    resultsCard.classList.add("hidden");
    setStatusLoading("Uploading CV…");

    try {
      const uploadRes = await apiFetch(`${BACKEND_URL}/upload-cv`, {
        method: "POST",
        headers: { "Authorization": `Bearer ${getToken()}` },
        body: formData,
      });

      const uploadData = await uploadRes.json();

      if (!uploadRes.ok) {
        const detail = uploadData.detail;
        setStatusFailed(typeof detail === "string" ? detail : "Upload failed.");
        return;
      }

      pollResults(uploadData.job_id);
    } catch (err) {
      console.error("Upload error:", err);
      setStatusFailed("Upload failed. Is the backend running?");
    }
  });
}

// ── Shared builder validation helper ─────────────────────────────────────────
function validateBuilderForm() {
  if (!builderForm.reportValidity()) return false;

  const candidate_profile = buildCandidateProfile();

  if (!candidate_profile.full_name || !candidate_profile.email || !candidate_profile.phone) {
    showFormError("Full name, email, and phone are all required.", "builderForm");
    return false;
  }
  if (candidate_profile.education.length === 0) {
    showFormError("At least one education entry is required.", "builderForm");
    return false;
  }
  if (candidate_profile.skills.length === 0) {
    showFormError("At least one skill is required — use the Skills section below.", "builderForm");
    return false;
  }
  if (
    candidate_profile.work_experience.length === 0 &&
    candidate_profile.projects.length === 0 &&
    candidate_profile.extracurriculars.length === 0
  ) {
    showFormError(
      "At least one of the following sections is required:\n• Work Experience\n• Projects\n• Extracurricular Activities",
      "builderForm"
    );
    return false;
  }

  function validateDates(containerId, sectionLabel) {
    for (const card of document.querySelectorAll(`#${containerId} .entry-card`)) {
      const startEl = card.querySelector('[class*="-start"]');
      const endEl   = card.querySelector('[class*="-end"]');
      const isCurrent = card.querySelector(".experience-currently-working")?.checked
                     || card.querySelector(".education-currently-enrolled")?.checked;
      if (!startEl || !endEl || isCurrent) continue;
      const start = startEl.value;
      const end   = endEl.value;
      if (start && end && start > end) {
        return `${sectionLabel}: start date must be on or before end date.`;
      }
    }
    return null;
  }
  const dateError = (
    validateDates("educationEntries",  "Education") ||
    validateDates("experienceEntries", "Work Experience")
  );
  if (dateError) { showFormError(dateError, "builderForm"); return false; }

  for (const [i, el] of [...document.querySelectorAll(".project-technologies")].entries()) {
    if (!el.value.trim()) continue;
    const invalid = el.value.split(",").find(t => !/^[\w\s.+#\-/'&()]+$/.test(t.trim()));
    if (invalid) {
      showFormError(
        `Project ${i + 1} — Technologies: "${invalid.trim()}" contains invalid characters.\nUse letters, numbers, and common symbols only.`,
        "builderForm"
      );
      return false;
    }
  }

  return true;
}

// ── Live CV preview ───────────────────────────────────────────────────────────

const previewCard   = document.getElementById("previewCard");
const previewFrame  = document.getElementById("previewFrame");
const previewStatus = document.getElementById("previewStatus");
let   _previewBlobUrl = null;

async function updateCvPreview() {
  if (!validateBuilderForm()) return;

  const candidate_profile = buildCandidateProfile();
  previewStatus.innerHTML = `<span class="status-spinner"></span><span class="preview-status-busy">Generating preview…</span>`;

  try {
    const res = await apiFetch(`${BACKEND_URL}/preview-cv`, {
      method:  "POST",
      headers: authHeaders(),
      body:    JSON.stringify({ candidate_profile }),
    });

    if (!res.ok) {
      let detail = "Preview failed.";
      try { detail = (await res.json()).detail || detail; } catch (_) {}
      previewStatus.innerHTML = `<span class="preview-status-err">Preview failed — ${escapeHtml(detail)}</span>`;
      return;
    }

    const blob = await res.blob();
    if (_previewBlobUrl) URL.revokeObjectURL(_previewBlobUrl);
    _previewBlobUrl = URL.createObjectURL(blob);
    previewFrame.src = _previewBlobUrl;
    // Show frame, hide placeholder
    const _ph = document.getElementById("previewPlaceholder");
    if (_ph) _ph.style.display = "none";
    previewFrame.classList.remove("hidden");
    previewStatus.innerHTML = `<span class="preview-status-ok">Preview updated</span>`;
  } catch (err) {
    console.error("Preview error:", err);
    previewStatus.innerHTML = `<span class="preview-status-err">Could not reach backend.</span>`;
  }
}

// ── Reset all builder fields ──────────────────────────────────────────────────

function resetBuilder() {
  // Clear skills state arrays
  technicalSkills.length = 0;
  toolsSkills.length     = 0;
  softSkills.length      = 0;
  renderSkillChips("technicalSkillsContainer", technicalSkills);
  renderSkillChips("toolsSkillsContainer",     toolsSkills);
  renderSkillChips("softSkillsContainer",      softSkills);

  // Reset the form itself
  if (builderForm) builderForm.reset();

  // Clear all dynamic entry containers
  ["linksEntries", "educationEntries", "experienceEntries",
   "projectEntries", "extracurricularEntries", "certificationEntries",
   "awardEntries", "languageEntries"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.innerHTML = "";
  });

  // Re-seed default entries
  try { addEducationEntry(); } catch (_) {}
  try { addLinkEntry();      } catch (_) {}

  // Reset status + results; keep previewCard visible with placeholder
  if (statusCard)   statusCard.classList.add("hidden");
  if (resultsCard)  resultsCard.classList.add("hidden");
  const _ph = document.getElementById("previewPlaceholder");
  if (_ph) _ph.style.display = "";
  if (previewFrame) { previewFrame.classList.add("hidden"); previewFrame.src = ""; }
  if (_previewBlobUrl) {
    URL.revokeObjectURL(_previewBlobUrl);
    _previewBlobUrl = null;
  }
  if (previewStatus) previewStatus.innerHTML = "";

  // Reset submit button label
  const buildBtn = document.getElementById("buildCvBtn");
  if (buildBtn) buildBtn.textContent = "Build CV & Preview";

  // Remove any inline error banners
  document.querySelectorAll(".form-error-banner").forEach(el => el.remove());
}

window.resetBuilder = resetBuilder;

// ── Builder form submit → fast preview ───────────────────────────────────────

if (builderForm) {
  builderForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    await updateCvPreview();
    // After first successful preview the button label stays; label is updated below
    const buildBtn = document.getElementById("buildCvBtn");
    if (buildBtn && previewFrame && !previewFrame.classList.contains("hidden")) {
      buildBtn.textContent = "↻ Update Preview";
    }
  });
}

// ── "Update Preview" button ───────────────────────────────────────────────────
document.getElementById("updatePreviewBtn")?.addEventListener("click", async () => {
  await updateCvPreview();
});

// ── "Find Matching Jobs" button ───────────────────────────────────────────────
document.getElementById("findJobsBtn")?.addEventListener("click", async () => {
  if (!validateBuilderForm()) return;

  const candidate_profile = buildCandidateProfile();

  // Collect location preferences
  const builderModes = Array.from(
    document.querySelectorAll('#builderPrefsSection input[type="checkbox"]:checked')
  ).map(cb => cb.value);
  const builderRelocationCodes = Array.from(
    document.getElementById("builderRelocationCountries")?.selectedOptions || []
  ).map(o => o.value);
  const preferences = { modes: builderModes, relocation_locations: builderRelocationCodes };

  statusCard.classList.remove("hidden");
  resultsCard.classList.add("hidden");
  setStatusLoading("Submitting CV for job matching…");

  try {
    const res = await apiFetch(`${BACKEND_URL}/build-cv`, {
      method:  "POST",
      headers: authHeaders(),
      body:    JSON.stringify({ candidate_profile, preferences }),
    });

    const data = await res.json();

    if (!res.ok) {
      const detail = data.detail;
      let msg = "Submission failed.";
      if (Array.isArray(detail) && detail.length) {
        msg = "• " + detail.join("\n• ");
      } else if (typeof detail === "string") {
        msg = detail;
      }
      statusCard.classList.add("hidden");
      showFormError(msg, "builderForm");
      return;
    }

    pollResults(data.job_id);
  } catch (err) {
    console.error("Builder error:", err);
    setStatusFailed("Build CV request failed. Is the backend running?");
  }
});

// ── Reset button ──────────────────────────────────────────────────────────────
document.getElementById("resetBuilderBtn")?.addEventListener("click", () => {
  resetBuilder();
});

function setStatusLoading(msg) {
  statusCard.classList.remove("status-done", "status-failed");
  statusCard.classList.add("status-processing");
  statusText.innerHTML = `<span class="status-spinner"></span>${escapeHtml(msg)}`;
}

function setStatusDone(msg) {
  statusCard.classList.remove("status-processing", "status-failed");
  statusCard.classList.add("status-done");
  statusText.textContent = msg;
}

function setStatusFailed(msg) {
  statusCard.classList.remove("status-processing", "status-done");
  statusCard.classList.add("status-failed");
  statusText.textContent = msg;
}

let _activeJobId = null;
let _activeInterval = null;

window.stopAnalysis = function() {
  if (_activeInterval) { clearInterval(_activeInterval); _activeInterval = null; }
  const btn = document.getElementById("stopAnalysisBtn");
  if (btn) btn.style.display = "none";
  if (_activeJobId) {
    apiFetch(`${BACKEND_URL}/job/${_activeJobId}/cancel`, {
      method: "POST", headers: authHeaders()
    }).catch(() => {});
    _activeJobId = null;
  }
  setStatusFailed("Analysis stopped.");
};

async function pollResults(jobId) {
  setStatusLoading("Processing…");
  _activeJobId = jobId;
  const stopBtn = document.getElementById("stopAnalysisBtn");
  if (stopBtn) stopBtn.style.display = "";

  // Send heartbeats every 5s so the worker knows the browser is still open
  const heartbeatInterval = setInterval(() => {
    apiFetch(`${BACKEND_URL}/job/${jobId}/heartbeat`, {
      method: "POST", headers: authHeaders(),
    }).catch(() => {});
  }, 5000);

  // Send the first heartbeat immediately
  apiFetch(`${BACKEND_URL}/job/${jobId}/heartbeat`, {
    method: "POST", headers: authHeaders(),
  }).catch(() => {});

  function stopAll() {
    clearInterval(interval);
    clearInterval(heartbeatInterval);
    _activeInterval = null;
    if (stopBtn) stopBtn.style.display = "none";
  }

  const interval = setInterval(async () => {
    try {
      const res = await apiFetch(`${BACKEND_URL}/results/${jobId}`, {
        headers: { "Authorization": `Bearer ${getToken()}` },
      });
      if (!res.ok) throw new Error(`Results fetch failed with status ${res.status}`);
      const data = await res.json();

      if (["pending", "processing", "pending_matching"].includes(data.status)) {
        setStatusLoading(data.status_message || `Status: ${data.status.replaceAll("_", " ")}…`);
        return;
      }

      stopAll();

      if (data.status === "failed" || data.status.startsWith("failed")) {
        setStatusFailed(data.status_message || "Processing failed. Please try again.");
        return;
      }

      setStatusDone(data.status_message || `Status: ${data.status.replaceAll("_", " ")}`);
      renderResults(data, jobId);
    } catch (err) {
      stopAll();
      console.error("Polling error:", err);
      setStatusFailed("Error while fetching results. Please try again.");
    }
  }, 3000);
  _activeInterval = interval;
}

function renderResults(data, jobId) {
  resultsCard.classList.remove("hidden");

  const ai   = data.ai_structured_data || {};
  const jobs = data.matched_jobs || [];

  candidateInfo.innerHTML = `
    <p><strong>Name:</strong> ${escapeHtml(ai.full_name || "—")}</p>
    <p><strong>Email:</strong> ${escapeHtml(ai.email || "—")}</p>
    <p><strong>Phone:</strong> ${escapeHtml(ai.phone || "—")}</p>
    <p><strong>Location:</strong> ${escapeHtml(ai.location || "—")}</p>
  `;

  // ── Builder flow: cv_generated → show download + approve ──
  if (data.status === "cv_generated") {
    const hasPdf = !!data.generated_pdf_path;
    matchedJobs.innerHTML = `
      <div style="padding:20px;background:#ECFDF5;border:1.5px solid #A7F3D0;border-radius:12px;margin-top:8px;">
        <p style="margin:0 0 14px;font-weight:600;color:#059669;">Your CV has been generated successfully.</p>
        <div style="display:flex;gap:10px;flex-wrap:wrap;">
          ${hasPdf
            ? `<button onclick="downloadCv('${escapeHtml(jobId)}')" style="background:#059669;">Download CV (PDF)</button>`
            : `<p style="color:#94A3B8;font-size:13px;margin:0;">PDF not ready — pdflatex may not be installed.</p>`
          }
          <button onclick="approveCv('${escapeHtml(jobId)}')" style="background:#4F46E5;">
            Approve &amp; Find Matching Jobs
          </button>
        </div>
      </div>
    `;
    return;
  }

  // ── Matching results ──
  if (!jobs.length) {
    matchedJobs.innerHTML = `
      <div style="padding:18px 20px;background:#FFFBEB;border:1.5px solid #FDE68A;border-radius:12px;margin-top:8px;">
        <strong style="color:#D97706;">No jobs found yet.</strong>
        <p style="margin:6px 0 0;font-size:13px;color:#475569;">
          The job board may not have current listings that match your profile closely.
          Try again later or adjust your preferences.
        </p>
      </div>`;
    return;
  }

  const hasBestEffort = jobs.some(j => j.best_effort);
  const bannerHtml = hasBestEffort
    ? `<div style="padding:14px 18px;background:#FFFBEB;border:1.5px solid #FDE68A;border-radius:10px;margin-bottom:16px;font-size:13px;">
         <strong style="color:#D97706;">No strong matches found.</strong>
         <span style="color:#475569;"> Showing the closest available listings instead — review them carefully.</span>
       </div>`
    : "";

  matchedJobs.innerHTML = bannerHtml + jobs.map((job) => {
    const matchPct   = Math.round(((job.match_score ?? 0) / 500) * 100);
    const scoreColor = matchPct >= 70 ? "#059669" : matchPct >= 40 ? "#D97706" : "#DC2626";
    const scoreBg    = matchPct >= 70 ? "#ECFDF5" : matchPct >= 40 ? "#FFFBEB" : "#FEF2F2";
    const sb = job.score_breakdown || {};

    const scoreItems = [
      { label: "Skills",     value: sb.skills_score },
      { label: "Role Fit",   value: sb.role_relevance_score },
      { label: "Location",   value: sb.location_score },
      { label: "Experience", value: sb.experience_score },
      { label: "Stage Fit",  value: sb.grad_student_fit_score },
    ];

    const breakdownHtml = scoreItems.map(({ label, value }) => {
      const pct = value ?? 0;
      const barColor = pct >= 70 ? "#059669" : pct >= 40 ? "#D97706" : "#DC2626";
      return `
        <div>
          <div style="display:flex;justify-content:space-between;font-size:11px;color:#64748B;margin-bottom:3px;font-weight:600;">
            <span>${label}</span>
            <strong style="color:#0F172A;">${value !== undefined ? value + "%" : "—"}</strong>
          </div>
          <div style="height:5px;background:#E2E8F0;border-radius:999px;overflow:hidden;">
            <div style="height:100%;width:${pct}%;background:${barColor};border-radius:999px;"></div>
          </div>
        </div>`;
    }).join("");

    return `
    <div class="job">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px;">
        <div style="flex:1;min-width:0;">
          <h4 style="margin:0 0 5px;font-size:15px;font-weight:700;color:#0F172A;">${escapeHtml(job.title)}</h4>
          <p style="margin:0;font-size:13px;color:#64748B;font-weight:500;">
            ${escapeHtml(job.company)}<span style="margin:0 6px;color:#CBD5E1;">&bull;</span>${escapeHtml(job.location || "Location not specified")}
          </p>
        </div>
        <div style="display:flex;flex-direction:column;align-items:center;flex-shrink:0;min-width:60px;">
          <div style="width:56px;height:56px;border-radius:50%;border:3px solid ${scoreColor};background:${scoreBg};display:flex;align-items:center;justify-content:center;">
            <span style="font-size:13px;font-weight:700;color:${scoreColor};">${matchPct}%</span>
          </div>
          <span style="font-size:10px;color:#94A3B8;margin-top:4px;text-transform:uppercase;letter-spacing:0.06em;font-weight:700;">Match</span>
        </div>
      </div>

      <details style="margin-top:14px;">
        <summary style="cursor:pointer;font-size:11px;color:#64748B;font-weight:700;text-transform:uppercase;letter-spacing:0.07em;list-style:none;user-select:none;">
          Score Breakdown
        </summary>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:12px;">
          ${breakdownHtml}
        </div>
      </details>

      ${(job.matched_skills || []).length ? `
        <div style="margin-top:14px;">
          <span style="font-size:10px;font-weight:700;color:#64748B;text-transform:uppercase;letter-spacing:0.07em;display:block;margin-bottom:6px;">Matched Skills</span>
          ${job.matched_skills.map(s => `<span class="badge">${escapeHtml(s)}</span>`).join("")}
        </div>` : ""}

      ${(job.missing_skills || []).length ? `
        <div style="margin-top:10px;">
          <span style="font-size:10px;font-weight:700;color:#64748B;text-transform:uppercase;letter-spacing:0.07em;display:block;margin-bottom:6px;">Skills to Develop</span>
          ${job.missing_skills.slice(0, 6).map(s => `<span class="badge" style="background:#FEF2F2;color:#DC2626;border-color:#FECACA;">${escapeHtml(s)}</span>`).join("")}
        </div>` : ""}

      <div style="margin-top:14px;padding-top:12px;border-top:1px solid #E2E8F0;display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;">
        ${job.url ? `
          <a href="${escapeHtml(job.url)}" target="_blank" rel="noopener noreferrer"
             style="display:inline-flex;align-items:center;gap:6px;font-size:13px;font-weight:600;color:#4F46E5;text-decoration:none;">
            View Job Listing &#8594;
          </a>` : "<span></span>"}
        <button type="button"
          data-job-url="${escapeHtml(job.url || "")}"
          data-job-title="${escapeHtml(job.title || "")}"
          data-job-company="${escapeHtml(job.company || "")}"
          data-job-location="${escapeHtml(job.location || "")}"
          data-match-score="${matchPct}"
          onclick="toggleLikeJob(this)"
          style="background:none;border:1.5px solid #E2E8F0;border-radius:20px;padding:5px 14px;font-size:12px;font-weight:600;color:#64748B;cursor:pointer;display:inline-flex;align-items:center;gap:5px;transition:all 0.15s;"
          class="like-btn">
          &#9825; Save Job
        </button>
      </div>
    </div>`;
  }).join("");
}

// Track liked URLs in memory so the heart stays filled within the session
const _likedUrls = new Set();

window.toggleLikeJob = async function (btn) {
  const url     = btn.dataset.jobUrl;
  const title   = btn.dataset.jobTitle;
  const company = btn.dataset.jobCompany;
  const location= btn.dataset.jobLocation;
  const score   = btn.dataset.matchScore;
  const liked   = _likedUrls.has(url);

  try {
    if (liked) {
      await apiFetch(`${BACKEND_URL}/like-job`, {
        method: "DELETE",
        headers: authHeaders(),
        body: JSON.stringify({ job_url: url }),
      });
      _likedUrls.delete(url);
      btn.innerHTML = "&#9825; Save Job";
      btn.style.color = "#64748B";
      btn.style.borderColor = "#E2E8F0";
      btn.style.background = "none";
    } else {
      await apiFetch(`${BACKEND_URL}/like-job`, {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({ job_url: url, job_title: title, job_company: company, job_location: location, match_score: score }),
      });
      _likedUrls.add(url);
      btn.innerHTML = "&#9829; Saved";
      btn.style.color = "#DC2626";
      btn.style.borderColor = "#FECACA";
      btn.style.background = "#FEF2F2";
    }
  } catch (_) {}
};

window.downloadCv = async function (jobId) {
  try {
    const res = await apiFetch(`${BACKEND_URL}/download-cv/${jobId}`, {
      headers: { "Authorization": `Bearer ${getToken()}` },
    });
    if (!res.ok) throw new Error("Download failed");
    const blob = await res.blob();
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement("a");
    a.href     = url;
    a.download = "cv.pdf";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  } catch (err) {
    setStatusFailed("Download failed — please try again.");
  }
};

window.approveCv = async function (jobId) {
  try {
    const res = await apiFetch(`${BACKEND_URL}/approve-cv/${jobId}`, {
      method:  "POST",
      headers: authHeaders(),
    });
    if (!res.ok) throw new Error("Approval failed");
    setStatusLoading("CV approved — finding matching jobs…");
    matchedJobs.innerHTML = `
      <div style="padding:14px 18px;background:var(--bg);border-radius:10px;border:1px solid var(--border);font-size:13px;color:var(--text-secondary);display:flex;align-items:center;gap:8px;">
        <span class="status-spinner"></span> Running job matching, please wait…
      </div>`;
    pollResults(jobId);
  } catch (err) {
    setStatusFailed("Approval failed — please try again.");
  }
};

document.getElementById("addLinkBtn")?.addEventListener("click", addLinkEntry);
document.getElementById("addEducationBtn")?.addEventListener("click", addEducationEntry);
document.getElementById("addExperienceBtn")?.addEventListener("click", addExperienceEntry);
document.getElementById("addProjectBtn")?.addEventListener("click", addProjectEntry);
document.getElementById("addExtracurricularBtn")?.addEventListener("click", addExtracurricularEntry);
document.getElementById("addCertificationBtn")?.addEventListener("click", addCertificationEntry);
document.getElementById("addAwardBtn")?.addEventListener("click", addAwardEntry);
document.getElementById("addLanguageBtn")?.addEventListener("click", addLanguageEntry);

try {
  setupSkillInput("technicalSkillInput", "technicalSkillsContainer", technicalSkills);
  setupSkillInput("toolsSkillInput", "toolsSkillsContainer", toolsSkills);
  setupSkillInput("softSkillInput", "softSkillsContainer", softSkills);
  addEducationEntry();
  addLinkEntry();
} catch (err) {
  console.error("Init error (non-fatal, builder sections may not be ready):", err);
}

// ── Forgot password flow ───────────────────────────────────────────────────────

const authFormEl       = document.getElementById("authForm");
const authToggleFooter = document.getElementById("authToggleFooter");
const forgotPassLink   = document.getElementById("forgotPasswordLink");
const forgotFormEl     = document.getElementById("forgotForm");
const resetPassFormEl  = document.getElementById("resetPassForm");

function showForgotPasswordState() {
  if (authFormEl)       authFormEl.style.display       = "none";
  if (authToggleFooter) authToggleFooter.style.display  = "none";
  if (forgotPassLink)   forgotPassLink.style.display    = "none";
  if (forgotFormEl)     forgotFormEl.classList.add("visible");
  if (authTitle)        authTitle.style.display         = "none";
  const sub = document.querySelector(".auth-subtitle");
  if (sub) sub.style.display = "none";
}

function showLoginState() {
  if (authFormEl)       authFormEl.style.display       = "";
  if (authToggleFooter) authToggleFooter.style.display  = "";
  if (forgotPassLink)   forgotPassLink.style.display    = "";
  if (forgotFormEl)     forgotFormEl.classList.remove("visible");
  if (resetPassFormEl)  resetPassFormEl.classList.remove("visible");
  if (authTitle)        authTitle.style.display         = "";
  const sub = document.querySelector(".auth-subtitle");
  if (sub) sub.style.display = "";
}

document.getElementById("showForgotBtn")?.addEventListener("click", showForgotPasswordState);
document.getElementById("backToLoginBtn")?.addEventListener("click", showLoginState);
document.getElementById("backFromResetBtn")?.addEventListener("click", showLoginState);

document.getElementById("forgotSubmitBtn")?.addEventListener("click", async () => {
  const email = sanitizeInput(document.getElementById("forgotEmail")?.value || "");
  const msgEl = document.getElementById("forgotMsg");
  if (!email) { if (msgEl) { msgEl.textContent = "Please enter your email."; msgEl.className = "msg-error"; } return; }
  if (msgEl) { msgEl.textContent = "Sending…"; msgEl.className = ""; }
  try {
    await fetch(`${BACKEND_URL}/forgot-password`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email }),
    });
    if (msgEl) { msgEl.textContent = "If that email is registered, a reset link has been sent."; msgEl.className = "msg-success"; }
  } catch (_) {
    if (msgEl) { msgEl.textContent = "Cannot reach the server."; msgEl.className = "msg-error"; }
  }
});

// Live rules checker for reset password form
document.getElementById("resetPassInput")?.addEventListener("input", () => {
  const p = document.getElementById("resetPassInput").value;
  checkRule("reset-rule-length",  p.length >= 8);
  checkRule("reset-rule-upper",   /[A-Z]/.test(p));
  checkRule("reset-rule-lower",   /[a-z]/.test(p));
  checkRule("reset-rule-digit",   /\d/.test(p));
  checkRule("reset-rule-special", /[!@#$%^&*()_+\-=\[\]{};':"\\|,.<>\/?]/.test(p));
  const confirm = document.getElementById("resetPassConfirm")?.value || "";
  const errEl = document.getElementById("resetConfirmError");
  if (confirm) errEl.textContent = p !== confirm ? "Passwords do not match." : "";
});

document.getElementById("resetPassConfirm")?.addEventListener("input", () => {
  const p = document.getElementById("resetPassInput")?.value || "";
  const confirm = document.getElementById("resetPassConfirm").value;
  document.getElementById("resetConfirmError").textContent = p !== confirm ? "Passwords do not match." : "";
});

document.getElementById("resetPassSubmitBtn")?.addEventListener("click", async () => {
  const newPassword = document.getElementById("resetPassInput")?.value || "";
  const confirm     = document.getElementById("resetPassConfirm")?.value || "";
  const msgEl = document.getElementById("resetPassMsg");
  const params = new URLSearchParams(window.location.search);
  const token = params.get("token");
  if (!token) { if (msgEl) { msgEl.textContent = "Invalid or missing reset token."; msgEl.className = "msg-error"; } return; }
  if (!newPassword) { if (msgEl) { msgEl.textContent = "Please enter a password."; msgEl.className = "msg-error"; } return; }
  if (newPassword !== confirm) { if (msgEl) { msgEl.textContent = "Passwords do not match."; msgEl.className = "msg-error"; } return; }
  if (msgEl) { msgEl.textContent = "Setting password…"; msgEl.className = ""; }
  try {
    const res = await fetch(`${BACKEND_URL}/reset-password`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token, new_password: newPassword }),
    });
    const data = await res.json();
    if (!res.ok) {
      if (msgEl) { msgEl.textContent = data.detail || "Reset failed."; msgEl.className = "msg-error"; }
      return;
    }
    if (msgEl) { msgEl.textContent = "Password updated! You can now log in."; msgEl.className = "msg-success"; }
    setTimeout(showLoginState, 2000);
    // Clean URL
    history.replaceState({}, "", window.location.pathname);
  } catch (_) {
    if (msgEl) { msgEl.textContent = "Cannot reach the server."; msgEl.className = "msg-error"; }
  }
});

// Check for ?token= on page load → show reset-password form
(function checkResetToken() {
  const params = new URLSearchParams(window.location.search);
  if (params.has("token") && !getToken()) {
    if (authFormEl)       authFormEl.style.display       = "none";
    if (authToggleFooter) authToggleFooter.style.display  = "none";
    if (forgotPassLink)   forgotPassLink.style.display    = "none";
    if (authTitle)        authTitle.style.display         = "none";
    const sub = document.querySelector(".auth-subtitle");
    if (sub) sub.style.display = "none";
    if (resetPassFormEl) resetPassFormEl.classList.add("visible");
  }
})();

// ── Profile screen ─────────────────────────────────────────────────────────────

window.showProfileView = function () {
  document.getElementById("mainView").style.display    = "none";
  document.getElementById("profileScreen").classList.add("visible");

  // Populate avatar and name
  const name  = getEmail() || "User";
  const email = getEmail() || "";
  const av = document.getElementById("profileAvatar");
  const nm = document.getElementById("profileName");
  const em = document.getElementById("profileEmail");
  if (av) av.textContent = name.charAt(0).toUpperCase();
  if (nm) nm.textContent = name;
  if (em) em.textContent = email;

  loadProfileData();
};

window.showMainView = function () {
  document.getElementById("mainView").style.display    = "";
  document.getElementById("profileScreen").classList.remove("visible");
};

window.switchTab = function (tab, btn) {
  document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
  document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
  btn.classList.add("active");
  const panelId = tab === "cvs" ? "tabCvs" : tab === "matches" ? "tabMatches" : "tabLiked";
  document.getElementById(panelId).classList.add("active");
};

async function loadProfileData() {
  const cvsList     = document.getElementById("profileCvsList");
  const matchesList = document.getElementById("profileMatchesList");
  const likedList   = document.getElementById("profileLikedList");
  if (cvsList)     cvsList.innerHTML     = "<p class='profile-empty'>Loading…</p>";
  if (matchesList) matchesList.innerHTML = "<p class='profile-empty'>Loading…</p>";
  if (likedList)   likedList.innerHTML   = "<p class='profile-empty'>Loading…</p>";

  // CVs + matches
  try {
    const res = await apiFetch(`${BACKEND_URL}/my-jobs`, { headers: authHeaders() });
    if (!res.ok) throw new Error("Failed to load");
    const data = await res.json();
    const jobs = data.jobs || data;

    if (!jobs.length) {
      if (cvsList)     cvsList.innerHTML     = "<div class='profile-empty'><div class='profile-empty-icon'>CV</div><strong>No CVs yet</strong><p>Build or upload a CV to get started.</p></div>";
      if (matchesList) matchesList.innerHTML = "<div class='profile-empty'><div class='profile-empty-icon'>Jobs</div><strong>No job matches yet</strong><p>Run job matching on a built CV to see results here.</p></div>";
    } else {
      if (cvsList) {
        cvsList.innerHTML = jobs.map(j => {
          const date = j.created_at ? new Date(j.created_at).toLocaleDateString() : "—";
          const name = escapeHtml(j.display_name || j.filename || "Untitled CV");
          const isUpload = j.cv_type === "upload";
          const typeLabel = isUpload ? "Uploaded" : "Built";
          const typeClass = isUpload ? "upload" : "";
          const statusClass = j.status === "done" || j.status === "cv_generated" ? "done" : j.status?.startsWith("fail") ? "failed" : "processing";
          const statusLabel = j.status === "cv_generated" ? "Ready" : j.status === "done" ? "Matched" : j.status || "—";

          const dlPdfBtn   = j.has_pdf    ? `<button class="btn-ghost-sm" onclick="downloadCvPdf('${j.job_id}')">PDF</button>` : "";
          const dlUploadBtn = j.has_upload ? `<button class="btn-ghost-sm" onclick="downloadUploadedCv('${j.job_id}')">Original</button>` : "";
          const editBtn    = !isUpload    ? `<button class="btn-ghost-sm" onclick="editBuilderCv('${j.job_id}')">Edit</button>` : "";

          return `<div class="saved-cv-card" id="cv-card-${j.job_id}">
            <div class="saved-cv-icon">${isUpload ? "UP" : "CV"}</div>
            <div class="saved-cv-info">
              <div class="cv-name-wrap">
                <strong id="cv-name-${j.job_id}">${name}</strong>
                <span class="cv-type-badge ${typeClass}">${typeLabel}</span>
              </div>
              <span>${date} · <span class="saved-cv-badge ${statusClass}" style="display:inline;padding:1px 6px;font-size:10px;">${statusLabel}</span></span>
            </div>
            <div class="cv-card-actions">
              ${dlPdfBtn}${dlUploadBtn}${editBtn}
              <button class="btn-ghost-sm" onclick="startRename('${j.job_id}', this)" title="Rename">✎</button>
              <button class="btn-danger-sm" onclick="deleteCv('${j.job_id}')">✕</button>
            </div>
          </div>`;
        }).join("");
      }
      if (matchesList) {
        const withMatches = jobs.filter(j => j.match_count > 0);
        if (!withMatches.length) {
          matchesList.innerHTML = "<div class='profile-empty'><div class='profile-empty-icon'>Jobs</div><strong>No job matches yet</strong><p>Run 'Find Jobs' on a built CV to see results here.</p></div>";
        } else {
          matchesList.innerHTML = withMatches.map(j => {
            const date = j.created_at ? new Date(j.created_at).toLocaleDateString() : "—";
            const top = j.top_match;
            if (!top) return "";
            return `<div class="saved-cv-card">
              <div class="saved-cv-icon">Job</div>
              <div class="saved-cv-info">
                <strong>${escapeHtml(top.title || "Job")}</strong>
                <span>${escapeHtml(top.company || "")} · ${date}</span>
              </div>
              <span class="saved-cv-badge done">${top.match_score ? Math.round((top.match_score / 500) * 100) + "%" : "—"}</span>
            </div>`;
          }).join("");
        }
      }
    }
  } catch (err) {
    if (cvsList)     cvsList.innerHTML     = "<div class='profile-empty'><p>Failed to load data.</p></div>";
    if (matchesList) matchesList.innerHTML = "<div class='profile-empty'><p>Failed to load data.</p></div>";
  }

  // Liked jobs
  try {
    const res = await apiFetch(`${BACKEND_URL}/liked-jobs`, { headers: authHeaders() });
    if (!res.ok) throw new Error();
    const liked = await res.json();
    if (!likedList) return;
    if (!liked.length) {
      likedList.innerHTML = "<div class='profile-empty'><div class='profile-empty-icon'>&#9825;</div><strong>No saved jobs yet</strong><p>Hit the save button on any job result to save it here.</p></div>";
    } else {
      likedList.innerHTML = liked.map(l => `
        <div class="saved-cv-card">
          <div class="saved-cv-icon" style="color:#DC2626;">&#9829;</div>
          <div class="saved-cv-info">
            <strong>${escapeHtml(l.job_title || "Job")}</strong>
            <span>${escapeHtml(l.job_company || "")} · ${escapeHtml(l.job_location || "")}</span>
          </div>
          ${l.job_url ? `<a href="${escapeHtml(l.job_url)}" target="_blank" rel="noopener noreferrer"
            style="font-size:12px;font-weight:600;color:#4F46E5;text-decoration:none;flex-shrink:0;">View &#8594;</a>` : ""}
        </div>`).join("");
    }
  } catch (_) {
    if (likedList) likedList.innerHTML = "<div class='profile-empty'><p>Failed to load saved jobs.</p></div>";
  }
}

// ── CV Management Handlers ────────────────────────────────────────────────────

window.startRename = function (jobId, btn) {
  const nameEl = document.getElementById(`cv-name-${jobId}`);
  if (!nameEl) return;

  // If already renaming, cancel
  const existing = nameEl.parentElement.querySelector(".cv-rename-input");
  if (existing) {
    existing.remove();
    nameEl.style.display = "";
    btn.textContent = "✎";
    btn.title = "Rename";
    return;
  }

  // Show inline input
  const currentName = nameEl.textContent.trim();
  nameEl.style.display = "none";

  const input = document.createElement("input");
  input.className = "cv-rename-input";
  input.value = currentName;
  input.maxLength = 100;
  nameEl.parentElement.insertBefore(input, nameEl);
  input.focus();
  input.select();

  btn.textContent = "✓";
  btn.title = "Save";

  async function saveRename() {
    const newName = input.value.trim();
    if (!newName || newName === currentName) {
      input.remove();
      nameEl.style.display = "";
      btn.textContent = "✎";
      btn.title = "Rename";
      return;
    }
    try {
      const res = await apiFetch(`${BACKEND_URL}/job/${jobId}/rename`, {
        method: "PATCH",
        headers: authHeaders(),
        body: JSON.stringify({ display_name: newName }),
      });
      if (!res.ok) throw new Error();
      nameEl.textContent = newName;
    } catch (_) {
      alert("Failed to rename. Please try again.");
    }
    input.remove();
    nameEl.style.display = "";
    btn.textContent = "✎";
    btn.title = "Rename";
  }

  btn.onclick = saveRename;
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") saveRename();
    if (e.key === "Escape") {
      input.remove();
      nameEl.style.display = "";
      btn.textContent = "✎";
      btn.title = "Rename";
      btn.onclick = () => window.startRename(jobId, btn);
    }
  });
};

window.deleteCv = async function (jobId) {
  if (!confirm("Delete this CV? This cannot be undone.")) return;
  try {
    const res = await apiFetch(`${BACKEND_URL}/job/${jobId}`, {
      method: "DELETE",
      headers: authHeaders(),
    });
    if (!res.ok) throw new Error();
    document.getElementById(`cv-card-${jobId}`)?.remove();
    const cvsList = document.getElementById("profileCvsList");
    if (cvsList && cvsList.children.length === 0) {
      cvsList.innerHTML = "<div class='profile-empty'><div class='profile-empty-icon'>CV</div><strong>No CVs yet</strong><p>Build or upload a CV to get started.</p></div>";
    }
  } catch (_) {
    alert("Failed to delete. Please try again.");
  }
};

window.downloadCvPdf = async function (jobId) {
  try {
    const res = await apiFetch(`${BACKEND_URL}/download-cv/${jobId}`, { headers: authHeaders() });
    if (!res.ok) throw new Error();
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `cv-${jobId}.pdf`;
    a.click();
    URL.revokeObjectURL(url);
  } catch (_) {
    alert("Failed to download PDF. Please try again.");
  }
};

window.downloadUploadedCv = async function (jobId) {
  try {
    const res = await apiFetch(`${BACKEND_URL}/download-upload/${jobId}`, { headers: authHeaders() });
    if (!res.ok) throw new Error();
    const cd = res.headers.get("Content-Disposition") || "";
    const match = cd.match(/filename="?([^"]+)"?/);
    const filename = match ? match[1] : `uploaded-cv-${jobId}.pdf`;
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  } catch (_) {
    alert("Failed to download original CV. Please try again.");
  }
};

window.editBuilderCv = async function (jobId) {
  try {
    const res = await apiFetch(`${BACKEND_URL}/my-jobs`, { headers: authHeaders() });
    if (!res.ok) throw new Error();
    const data = await res.json();
    const jobs = data.jobs || data;
    const job = jobs.find(j => j.job_id === jobId);
    if (!job || !job.candidate_profile) {
      alert("Could not load CV data.");
      return;
    }

    const p = typeof job.candidate_profile === "string"
      ? JSON.parse(job.candidate_profile)
      : job.candidate_profile;

    // Switch to builder mode and navigate to main view
    document.getElementById("profileScreen")?.classList.remove("visible");
    document.getElementById("mainView")?.removeAttribute("style");
    window.showBuilder();

    // Reset first so we start clean
    resetBuilder();

    // Personal info
    const setVal = (id, val) => { const el = document.getElementById(id); if (el) el.value = val || ""; };
    setVal("fullName", p.full_name);
    setVal("email", p.email);
    setVal("phone", p.phone);
    setVal("location", p.location);
    setVal("summary", p.summary);

    // Setup section
    if (p.setup) {
      setVal("userType", p.setup.user_type);
      setVal("fields", (p.setup.target_fields || []).join(", "));
      setVal("level", p.setup.application_level);
    }

    // Skills
    technicalSkills.length = 0;
    toolsSkills.length = 0;
    softSkills.length = 0;
    if (p.skill_groups) {
      (p.skill_groups.technical || []).forEach(s => technicalSkills.push(s));
      (p.skill_groups.tools     || []).forEach(s => toolsSkills.push(s));
      (p.skill_groups.soft      || []).forEach(s => softSkills.push(s));
    } else {
      (p.skills || []).forEach(s => technicalSkills.push(s));
    }
    renderSkillChips("technicalSkillsContainer", technicalSkills);
    renderSkillChips("toolsSkillsContainer", toolsSkills);
    renderSkillChips("softSkillsContainer", softSkills);

    // Links — clear default entry first
    document.getElementById("linksEntries").innerHTML = "";
    (p.links || []).forEach(l => {
      addLinkEntry();
      const card = document.querySelector("#linksEntries .entry-card:last-child");
      if (!card) return;
      const sel = card.querySelector(".link-type-select");
      if (sel) {
        const opt = Array.from(sel.options).find(o => o.value === l.type);
        if (opt) { sel.value = l.type; }
        else { sel.value = "other"; sel.dispatchEvent(new Event("change")); card.querySelector(".link-type-custom").value = l.type || ""; }
      }
      const urlEl = card.querySelector(".link-url");
      if (urlEl) urlEl.value = l.url || "";
      const dispEl = card.querySelector(".link-display");
      if (dispEl) dispEl.value = l.display || "";
    });

    // Education — clear default entry first
    document.getElementById("educationEntries").innerHTML = "";
    (p.education || []).forEach(edu => {
      addEducationEntry();
      const card = document.querySelector("#educationEntries .entry-card:last-child");
      if (!card) return;
      card.querySelector(".education-institution").value = edu.institution || "";
      const degSel = card.querySelector(".education-degree-select");
      const degOther = card.querySelector(".education-degree-other");
      const degOpt = Array.from(degSel.options).find(o => o.value === edu.degree);
      if (degOpt) { degSel.value = edu.degree; }
      else { degSel.value = "Other"; degSel.dispatchEvent(new Event("change")); degOther.value = edu.degree || ""; }
      card.querySelector(".education-field").value = edu.field_of_study || "";
      card.querySelector(".education-start").value = edu.start_date || "";
      if (!edu.end_date) {
        card.querySelector(".education-currently-enrolled").checked = true;
        card.querySelector(".education-end").disabled = true;
      } else {
        card.querySelector(".education-end").value = edu.end_date || "";
      }
      // Grade
      if (edu.gpa) {
        const gradeType = card.querySelector(".education-grade-type");
        if (edu.gpa.startsWith("GPA:")) {
          gradeType.value = "gpa"; gradeType.dispatchEvent(new Event("change"));
          card.querySelector(".education-gpa-number").value = edu.gpa.replace("GPA:", "").trim();
        } else if (edu.gpa.endsWith("%")) {
          gradeType.value = "percentage"; gradeType.dispatchEvent(new Event("change"));
          card.querySelector(".education-pct-number").value = edu.gpa.replace("%", "").trim();
        } else {
          gradeType.value = "letter"; gradeType.dispatchEvent(new Event("change"));
          card.querySelector(".education-letter-select").value = edu.gpa;
        }
      }
    });

    // Work experience
    document.getElementById("experienceEntries").innerHTML = "";
    (p.work_experience || []).forEach(exp => {
      addExperienceEntry();
      const card = document.querySelector("#experienceEntries .entry-card:last-child");
      if (!card) return;
      card.querySelector(".experience-organization").value = exp.organization || "";
      card.querySelector(".experience-position").value = exp.position || "";
      card.querySelector(".experience-start").value = exp.start_date || "";
      if (!exp.end_date) {
        card.querySelector(".experience-currently-working").checked = true;
        card.querySelector(".experience-end").disabled = true;
      } else {
        card.querySelector(".experience-end").value = exp.end_date || "";
      }
      card.querySelector(".experience-location").value = exp.location || "";
      card.querySelector(".experience-description").value = (exp.description || []).join("\n");
    });

    // Projects
    document.getElementById("projectEntries").innerHTML = "";
    (p.projects || []).forEach(proj => {
      addProjectEntry();
      const card = document.querySelector("#projectEntries .entry-card:last-child");
      if (!card) return;
      card.querySelector(".project-title").value = proj.title || "";
      card.querySelector(".project-role").value = proj.role || "";
      card.querySelector(".project-technologies").value = (proj.technologies || []).join(", ");
      if (proj.link) {
        const addBtn = card.querySelector(".add-url-btn");
        const urlRow = card.querySelector(".url-row");
        if (addBtn && urlRow) { addBtn.style.display = "none"; urlRow.style.display = ""; }
        card.querySelector(".project-link").value = proj.link;
      }
      card.querySelector(".project-description").value = (proj.description || []).join("\n");
    });

    // Extracurriculars
    document.getElementById("extracurricularEntries").innerHTML = "";
    (p.extracurriculars || []).forEach(ex => {
      addExtracurricularEntry();
      const card = document.querySelector("#extracurricularEntries .entry-card:last-child");
      if (!card) return;
      card.querySelector(".extracurricular-title").value = ex.title || "";
      card.querySelector(".extracurricular-role").value = ex.role || "";
      card.querySelector(".extracurricular-organization").value = ex.organization || "";
      card.querySelector(".extracurricular-date").value = ex.date || "";
      card.querySelector(".extracurricular-description").value = (ex.description || []).join("\n");
    });

    // Certifications
    document.getElementById("certificationEntries").innerHTML = "";
    (p.certifications || []).forEach(cert => {
      addCertificationEntry();
      const card = document.querySelector("#certificationEntries .entry-card:last-child");
      if (!card) return;
      card.querySelector(".certification-title").value = cert.title || "";
      card.querySelector(".certification-date").value = cert.date || "";
      card.querySelector(".certification-organization").value = cert.organization || "";
    });

    // Languages
    document.getElementById("languageEntries").innerHTML = "";
    (p.languages || []).forEach(lang => {
      if (typeof addLanguageEntry === "function") {
        addLanguageEntry();
        const card = document.querySelector("#languageEntries .entry-card:last-child");
        if (!card) return;
        card.querySelector(".language-name").value = lang.language || "";
        const lvl = card.querySelector(".language-level");
        if (lvl) lvl.value = lang.proficiency || "";
      }
    });

    // Scroll to top of builder
    document.getElementById("builderSection")?.scrollIntoView({ behavior: "smooth", block: "start" });

  } catch (err) {
    alert("Failed to load CV for editing. Please try again.");
  }
};

// ── In-page confirm modal ─────────────────────────────────────────────────────
function showConfirmModal(title, body) {
  return new Promise((resolve) => {
    const modal  = document.getElementById("confirmModal");
    const titleEl = document.getElementById("confirmModalTitle");
    const bodyEl  = document.getElementById("confirmModalBody");
    const okBtn   = document.getElementById("confirmModalOk");
    const cancelBtn = document.getElementById("confirmModalCancel");

    titleEl.textContent = title;
    bodyEl.textContent  = body;
    modal.style.display = "flex";

    function cleanup(result) {
      modal.style.display = "none";
      okBtn.removeEventListener("click", onOk);
      cancelBtn.removeEventListener("click", onCancel);
      modal.removeEventListener("click", onBackdrop);
      resolve(result);
    }
    const onOk      = () => cleanup(true);
    const onCancel  = () => cleanup(false);
    const onBackdrop = (e) => { if (e.target === modal) cleanup(false); };

    okBtn.addEventListener("click", onOk);
    cancelBtn.addEventListener("click", onCancel);
    modal.addEventListener("click", onBackdrop);
  });
}

// ── File input: enable Analyze button + validate size before upload ──────────
document.getElementById("cv")?.addEventListener("change", function () {
  const file = this.files[0];
  const btn  = document.getElementById("analyzeCvBtn");
  const err  = document.getElementById("fileSizeError");
  if (!file) {
    if (btn) btn.disabled = true;
    if (err) err.textContent = "";
    return;
  }
  if (file.size > 10 * 1024 * 1024) {
    if (btn) btn.disabled = true;
    if (err) err.textContent = "File is too large (max 10 MB). Please choose a smaller PDF.";
  } else {
    if (btn) btn.disabled = false;
    if (err) err.textContent = "";
  }
});

window.deleteAccount = async function() {
  const confirmed = confirm("Are you sure you want to delete your account?\n\nThis will permanently delete all your uploaded CVs and saved jobs. Your account ID will be retained but your data will be removed.");
  if (!confirmed) return;
  try {
    const res = await apiFetch(`${BACKEND_URL}/delete-account`, {
      method: "DELETE",
      headers: authHeaders(),
    });
    if (!res.ok) throw new Error();
    clearSession();
    showAuthScreen();
    authError.textContent = "Your account has been deleted.";
  } catch (_) {
    alert("Failed to delete account. Please try again.");
  }
};