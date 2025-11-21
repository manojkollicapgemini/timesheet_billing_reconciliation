let statusChart;
const monthInput = document.getElementById("month");
const fileInput = document.getElementById("fileInput");
const uploadBtn = document.getElementById("uploadBtn");
const useSampleBtn = document.getElementById("useSampleBtn");
const loadBtn = document.getElementById("loadBtn");
const notifyBtn = document.getElementById("notifyBtn");
const statusEl = document.getElementById("status");
const toastEl = document.getElementById("appToast");
const toastBody = document.getElementById("toastBody");
const toast = toastEl ? new bootstrap.Toast(toastEl) : null;

const totalCount = document.getElementById("totalCount");
const completedCount = document.getElementById("completedCount");
const partialCount = document.getElementById("partialCount");
const otherCount = document.getElementById("otherCount");

function showToast(msg) {
  if (!toast) return;
  toastBody.textContent = msg;
  toast.show();
}

function getYM() {
  const v = monthInput.value;
  if (!v) return null;
  const [y, m] = v.split("-").map(Number);
  return { year: y, month: m };
}

function statusClass(s) {
  if (s === "Completed") return "status-completed";
  if (s === "Partial") return "status-partial";
  if (s === "Mismatch") return "status-mismatch";
  return "status-notcompleted";
}

async function uploadWorkbook() {
  const f = fileInput.files?.[0];
  if (!f) {
    showToast("Choose a workbook first");
    return;
  }
  const fd = new FormData();
  fd.append("file", f);
  const res = await fetch("/api/upload", { method: "POST", body: fd });
  const data = await res.json();
  showToast(res.ok ? "Workbook uploaded" : data.detail || "Upload failed");
}

async function useSample() {
  const res = await fetch("/api/use-sample", { method: "POST" });
  const data = await res.json();
  if (res.ok) {
    if (data.latest_year && data.latest_month) {
      monthInput.value = `${data.latest_year}-${String(
        data.latest_month
      ).padStart(2, "0")}`;
    }
    showToast("Sample data loaded");
    await loadReport();
  } else {
    showToast(data.detail || "Failed to load sample");
  }
}

async function loadReport() {
  const ym = getYM();
  if (!ym) {
    statusEl.textContent = "Pick a month";
    return;
  }
  statusEl.textContent = "Loading...";
  const res = await fetch(
    `/api/report?year=${ym.year}&month=${ym.month}`
  );
  const data = await res.json();

  totalCount.textContent = data.summary.total;
  completedCount.textContent = data.summary.completed;
  partialCount.textContent = data.summary.partial;
  otherCount.textContent =
    data.summary.mismatch + data.summary.not_completed;

  const tbody = document.querySelector("#reportTable tbody");
  tbody.innerHTML = "";
  data.records.forEach((r) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${r.employee_id || ""}</td>
      <td>${r.name || ""}</td>
      <td>${r.email || ""}</td>
      <td>${r.project_code || ""}</td>
      <td>${r.total_hours ?? "-"}</td>
      <td>${r.submitted_hours_cg ?? "-"}</td>
      <td>${r.submitted_hours_citi ?? "-"}</td>
      <td>${r.submitted_on ?? "-"}</td>
      <td class="${statusClass(r.status_cg)}">${r.status_cg}</td>
      <td class="${statusClass(r.status_citi)}">${r.status_citi}</td>
      <td class="${statusClass(r.reconciled_status)}">${r.reconciled_status}</td>
      <td>${r.reminders ?? 0}</td>
      <td class="text-end text-nowrap">
        <button class="btn btn-sm btn-outline-warning btn-pill me-1" data-remind="${
          r.employee_id
        }" data-citi="${r.citi_email}">Send Reminder</button>
        <button class="btn btn-sm btn-outline-info btn-pill" data-daily="${
          r.citi_email
        }" data-name="${r.name}">Daily</button>
      </td>
    `;
    tbody.appendChild(tr);
  });

  statusEl.textContent = `Report loaded for ${
    data.year
  }-${String(data.month).padStart(2, "0")}.`;
}

async function sendReminder(ids) {
  const ym = getYM();
  if (!ym) {
    showToast("Pick a month first");
    return;
  }
  const payload = { year: ym.year, month: ym.month };
  if (ids?.length) payload.employee_ids = ids;
  const res = await fetch("/api/send-reminder", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await res.json();
  if (res.ok) {
    showToast(`Reminders recorded: ${data.count}`);
    await loadReport();
  } else {
    showToast("Failed to send reminders");
  }
}

async function openDaily(citiEmail, name) {
  const ym = getYM();
  if (!ym) {
    showToast("Pick a month first");
    return;
  }
  const res = await fetch(
    `/api/daily?citi_email=${encodeURIComponent(
      citiEmail
    )}&year=${ym.year}&month=${ym.month}`
  );
  const data = await res.json();
  document.getElementById(
    "dailyTitle"
  ).textContent = `Daily Timesheets — ${name} (${citiEmail})`;
  const tbody = document.querySelector("#dailyTable tbody");
  tbody.innerHTML = "";
  data.items.forEach((it) => {
    const tr = document.createElement("tr");
    const diffClass = it.diff === 0 ? "" : "text-warning";
    tr.innerHTML = `
      <td>${it.date}</td>
      <td>${it.hours_cg}</td>
      <td>${it.hours_citi}</td>
      <td class="${diffClass}">${it.diff}</td>
    `;
    tbody.appendChild(tr);
  });
  new bootstrap.Modal(document.getElementById("dailyModal")).show();
}

document
  .querySelector("#reportTable tbody")
  .addEventListener("click", (e) => {
    const btnRem = e.target.closest("button[data-remind]");
    if (btnRem) {
      const id =
        btnRem.getAttribute("data-remind") ||
        btnRem.getAttribute("data-citi");
      sendReminder([id]);
      return;
    }
    const btnDaily = e.target.closest("button[data-daily]");
    if (btnDaily) {
      openDaily(
        btnDaily.getAttribute("data-daily"),
        btnDaily.getAttribute("data-name")
      );
    }
  });

uploadBtn.addEventListener("click", uploadWorkbook);
useSampleBtn.addEventListener("click", useSample);
loadBtn.addEventListener("click", loadReport);
notifyBtn.addEventListener("click", () => sendReminder(null));

window.addEventListener("DOMContentLoaded", () => {
  const now = new Date();
  monthInput.value = `${now.getFullYear()}-${String(
    now.getMonth() + 1
  ).padStart(2, "0")}`;
});
