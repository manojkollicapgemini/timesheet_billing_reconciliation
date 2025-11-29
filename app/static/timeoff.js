const yearSelect = document.getElementById("yearSelect");
const filterAllBtn = document.getElementById("filterAll");
const filterPendingBtn = document.getElementById("filterPending");
const filterApprovedBtn = document.getElementById("filterApproved");
const filterRejectedBtn = document.getElementById("filterRejected");

const totalApprovedDaysEl = document.getElementById("totalApprovedDays");
const totalPendingEl = document.getElementById("totalPending");
const topConsumersEl = document.getElementById("topConsumers");
const leaveSummaryBody = document.getElementById("leaveSummaryBody");
const timeoffBody = document.getElementById("timeoffBody");

const toCitiEmail = document.getElementById("toCitiEmail");
const toStart = document.getElementById("toStart");
const toEnd = document.getElementById("toEnd");
const toType = document.getElementById("toType");
const toReason = document.getElementById("toReason");
const toCreateBtn = document.getElementById("toCreateBtn");
const toMsg = document.getElementById("toMsg");

let currentStatusFilter = null; // null = all

function initYearSelect() {
  const now = new Date();
  const currentYear = now.getFullYear();
  const years = [currentYear - 1, currentYear, currentYear + 1];

  yearSelect.innerHTML = "";
  years.forEach((y) => {
    const opt = document.createElement("option");
    opt.value = y;
    opt.textContent = y;
    if (y === currentYear) opt.selected = true;
    yearSelect.appendChild(opt);
  });
}

async function loadSummary() {
  const year = yearSelect.value;
  const res = await fetch(`/api/timeoff/summary?year=${year}`);
  const data = await res.json();

  totalApprovedDaysEl.textContent = (data.total_approved_days || 0).toFixed(1);
  totalPendingEl.textContent = data.total_pending_requests || 0;

  // summary table
  leaveSummaryBody.innerHTML = "";
  data.items.forEach((item) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${item.name || "-"}</td>
      <td>${item.citi_email || "-"}</td>
      <td>${item.allowance ?? "-"}</td>
      <td>${(item.used || 0).toFixed(1)}</td>
      <td>${(item.remaining || 0).toFixed(1)}</td>
    `;
    leaveSummaryBody.appendChild(tr);
  });

  // top consumers (first 5 from sorted list)
  topConsumersEl.innerHTML = "";
  data.items.slice(0, 5).forEach((item) => {
    const li = document.createElement("li");
    li.textContent = `${item.name || item.citi_email} — ${(
      item.used || 0
    ).toFixed(1)} days`;
    topConsumersEl.appendChild(li);
  });
}

function statusBadge(status) {
  if (status === "Approved") {
    return '<span class="badge bg-success">Approved</span>';
  }
  if (status === "Pending") {
    return '<span class="badge bg-warning text-dark">Pending</span>';
  }
  if (status === "Rejected") {
    return '<span class="badge bg-danger">Rejected</span>';
  }
  return `<span class="badge bg-secondary">${status || "Unknown"}</span>`;
}

async function loadTimeoffList() {
  const year = yearSelect.value;
  let url = `/api/timeoff?year=${year}`;
  if (currentStatusFilter) {
    url += `&status=${currentStatusFilter}`;
  }

  const res = await fetch(url);
  const data = await res.json();

  timeoffBody.innerHTML = "";
  data.forEach((t) => {
    const tr = document.createElement("tr");
    const dateRange = t.start_date && t.end_date
      ? `${t.start_date} → ${t.end_date}`
      : "-";

    let actions = "";
    if (t.status === "Pending") {
      actions = `
        <button class="btn btn-sm btn-outline-success btn-pill me-1" data-approve="${t.id}">Approve</button>
        <button class="btn btn-sm btn-outline-danger btn-pill" data-reject="${t.id}">Reject</button>
      `;
    }

    tr.innerHTML = `
      <td>${t.employee_name || "-"}</td>
      <td>${t.citi_email || "-"}</td>
      <td>${t.leave_type || "-"}</td>
      <td>${dateRange}</td>
      <td>${(t.days || 0).toFixed(1)}</td>
      <td>${statusBadge(t.status)}</td>
      <td class="text-end">${actions}</td>
    `;
    timeoffBody.appendChild(tr);
  });
}

async function createTimeoff() {
  const citiEmail = toCitiEmail.value.trim();
  const start = toStart.value;
  const end = toEnd.value;
  const type = toType.value;
  const reason = toReason.value.trim();

  if (!citiEmail || !start || !end) {
    toMsg.textContent = "Citi email, start date and end date are required.";
    toMsg.classList.add("text-danger");
    return;
  }

  toMsg.textContent = "";
  toMsg.classList.remove("text-danger");

  const payload = {
    citi_email: citiEmail,
    start_date: start,
    end_date: end,
    leave_type: type,
    reason: reason,
  };

  const res = await fetch("/api/timeoff", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  const data = await res.json();
  if (!res.ok) {
    toMsg.textContent = data.detail || "Failed to create time off request.";
    toMsg.classList.add("text-danger");
    return;
  }

  toMsg.textContent = `Request captured (${data.days.toFixed(
    1
  )} working days, status ${data.status}).`;
  toMsg.classList.remove("text-danger");
  toMsg.classList.add("text-secondary");

  toCitiEmail.value = "";
  toStart.value = "";
  toEnd.value = "";
  toReason.value = "";

  await loadSummary();
  await loadTimeoffList();
}

async function updateTimeoffStatus(id, status) {
  const res = await fetch(`/api/timeoff/${id}/status`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status }),
  });
  await res.json();
  await loadSummary();
  await loadTimeoffList();
}

timeoffBody.addEventListener("click", (e) => {
  const approveBtn = e.target.closest("button[data-approve]");
  const rejectBtn = e.target.closest("button[data-reject]");

  if (approveBtn) {
    const id = approveBtn.getAttribute("data-approve");
    updateTimeoffStatus(id, "Approved");
  } else if (rejectBtn) {
    const id = rejectBtn.getAttribute("data-reject");
    updateTimeoffStatus(id, "Rejected");
  }
});

filterAllBtn.addEventListener("click", () => {
  currentStatusFilter = null;
  loadTimeoffList();
});
filterPendingBtn.addEventListener("click", () => {
  currentStatusFilter = "Pending";
  loadTimeoffList();
});
filterApprovedBtn.addEventListener("click", () => {
  currentStatusFilter = "Approved";
  loadTimeoffList();
});
filterRejectedBtn.addEventListener("click", () => {
  currentStatusFilter = "Rejected";
  loadTimeoffList();
});

yearSelect.addEventListener("change", () => {
  loadSummary();
  loadTimeoffList();
});

toCreateBtn.addEventListener("click", createTimeoff);

window.addEventListener("DOMContentLoaded", () => {
  initYearSelect();
  loadSummary();
  loadTimeoffList();
});
