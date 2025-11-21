const cProjectInput = document.getElementById("cProject");
const cQueryInput = document.getElementById("cQuery");
const askBtn = document.getElementById("askBtn");
const chatLog = document.getElementById("chatLog");
const chatStatus = document.getElementById("chatStatus");
const presetButtons = document.querySelectorAll(".preset-btn");

function appendMessage(role, text, meta = null) {
  const wrap = document.createElement("div");
  wrap.className = "mb-3";

  const badge =
    role === "user"
      ? '<span class="badge bg-info me-2">You</span>'
      : '<span class="badge bg-success me-2">Assistant</span>';

  let metaHtml = "";
  if (meta && meta.reminders_triggered) {
    metaHtml = `<div class="small text-warning mt-1">
      System: reminders triggered for <strong>${meta.reminders_triggered}</strong> resources in the latest month.
    </div>`;
  }

  wrap.innerHTML = `${badge}<span>${(text || "")
    .toString()
    .replace(/\n/g, "<br>")}</span>${metaHtml}`;
  chatLog.appendChild(wrap);
  chatLog.scrollTop = chatLog.scrollHeight;
}

async function ask() {
  const query = cQueryInput.value.trim();
  if (!query) return;
  const project = cProjectInput.value.trim() || null;

  appendMessage("user", query);
  cQueryInput.value = "";
  chatStatus.textContent = "Thinking on top of live billing & timesheets...";

  const payload = { query };
  if (project) payload.project_code = project;

  try {
    const res = await fetch("/api/chatbot", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) {
      appendMessage(
        "assistant",
        `Error: ${data.detail || "LLM call failed"}`
      );
    } else {
      appendMessage("assistant", data.answer || "(no answer)", {
        reminders_triggered: data.reminders_triggered || 0,
      });
    }
  } catch (e) {
    appendMessage("assistant", "Error calling chatbot API.");
  } finally {
    chatStatus.textContent = "";
  }
}

askBtn.addEventListener("click", ask);
cQueryInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    ask();
  }
});

presetButtons.forEach((btn) => {
  btn.addEventListener("click", () => {
    const q = btn.getAttribute("data-question") || "";
    cQueryInput.value = q;
    cQueryInput.focus();
  });
});
