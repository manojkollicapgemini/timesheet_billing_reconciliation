const cProjectInput = document.getElementById("cProject");
const cQueryInput = document.getElementById("cQuery");
const askBtn = document.getElementById("askBtn");
const chatLog = document.getElementById("chatLog");
const chatStatus = document.getElementById("chatStatus");
const debugSql = document.getElementById("debugSql");
const presetButtons = document.querySelectorAll(".preset-btn");
const typingIndicator = document.getElementById("typingIndicator");

function createBubble(role, text) {
  const row = document.createElement("div");
  row.className =
    "chat-row " +
    (role === "user" ? "chat-row-user justify-content-end" : "chat-row-bot");

  const avatar = document.createElement("div");
  avatar.className =
    "chat-avatar " + (role === "user" ? "avatar-user" : "avatar-bot");
  avatar.textContent = role === "user" ? "You" : "AI";

  const bubble = document.createElement("div");
  bubble.className =
    "chat-bubble " +
    (role === "user" ? "chat-bubble-user" : "chat-bubble-bot") +
    " bubble-animate";
  bubble.innerHTML = (text || "").toString().replace(/\n/g, "<br>");

  if (role === "user") {
    row.appendChild(bubble);
    row.appendChild(avatar);
  } else {
    row.appendChild(avatar);
    row.appendChild(bubble);
  }

  chatLog.appendChild(row);
  chatLog.scrollTop = chatLog.scrollHeight;
}

function setTyping(active) {
  if (active) {
    typingIndicator.classList.remove("d-none");
  } else {
    typingIndicator.classList.add("d-none");
  }
}

async function ask() {
  const query = cQueryInput.value.trim();
  if (!query) return;
  const project = cProjectInput.value.trim() || null;

  createBubble("user", query);
  cQueryInput.value = "";
  chatStatus.textContent = "Querying live data and preparing an answer...";
  debugSql.textContent = "";
  setTyping(true);
  askBtn.disabled = true;

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
      createBubble(
        "bot",
        `Error: ${data.detail || "Chatbot API call failed. Check backend logs."}`
      );
    } else {
      createBubble("bot", data.answer || "(no answer)");
      if (data.sql) {
        debugSql.textContent = `SQL: ${data.sql}`;
      }
    }
  } catch (e) {
    createBubble("bot", "Error calling chatbot API.");
  } finally {
    setTyping(false);
    askBtn.disabled = false;
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

// Optional: initial welcome message
window.addEventListener("DOMContentLoaded", () => {
  createBubble(
    "bot",
    "Hi, I’m your CG × Citi portfolio assistant.\n\nYou can ask about:\n• Completed / mismatched timesheets\n• Billing trends by project\n• Leave balance and time off\n• Utilisation and risk signals"
  );
});
