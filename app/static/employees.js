const empTableBody = document.querySelector("#empTable tbody");
const activeCountEl = document.getElementById("activeCount");
const inactiveCountEl = document.getElementById("inactiveCount");
const filterActiveBtn = document.getElementById("filterActive");
const filterInactiveBtn = document.getElementById("filterInactive");
const empMsg = document.getElementById("empMsg");

const empEmployeeId = document.getElementById("empEmployeeId");
const empName = document.getElementById("empName");
const empManager = document.getElementById("empManager");
const empCgEmail = document.getElementById("empCgEmail");
const empCitiEmail = document.getElementById("empCitiEmail");
const empRole = document.getElementById("empRole");
const empRegionCode = document.getElementById("empRegionCode");
const empRegionName = document.getElementById("empRegionName");
const empProjectCode = document.getElementById("empProjectCode");
const empRate = document.getElementById("empRate");
const empStartDate = document.getElementById("empStartDate");
const createEmpBtn = document.getElementById("createEmpBtn");

let currentStatusFilter = null; // null = all, 'Active', 'Inactive'

function statusBadge(status) {
  if (status === "Active") {
    return '<span class="badge bg-success">Active</span>';
  }
  if (status === "Inactive") {
    return '<span class="badge bg-secondary">Inactive</span>';
  }
  return `<span class="badge bg-dark">${status || "Unknown"}</span>`;
}

async function loadEmployees() {
  const url =
    currentStatusFilter && (currentStatusFilter === "Active" || currentStatusFilter === "Inactive")
      ? `/api/employees?status=${currentStatusFilter}`
      : "/api/employees";

  const res = await fetch(url);
  const data = await res.json();

  let active = 0;
  let inactive = 0;

  empTableBody.innerHTML = "";
  data.forEach((e) => {
    if (e.status === "Active") active++;
    if (e.status === "Inactive") inactive++;

    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${e.employee_id || "-"}</td>
      <td>${e.name || "-"}</td>
      <td>${e.cg_email || "-"}</td>
      <td>${e.citi_email || "-"}</td>
      <td>${e.region_code || ""} ${e.region_name || ""}</td>
      <td>${e.default_project_code || "-"}</td>
      <td>${e.billing_rate ?? 0}</td>
      <td>${statusBadge(e.status)}</td>
      <td>${e.start_date || "-"}</td>
      <td>${e.end_date || "-"}</td>
      <td class="text-end">
        ${
          e.status === "Active"
            ? `<button class="btn btn-sm btn-outline-warning btn-pill" data-deboard="${e.id}">Deboard</button>`
            : `<button class="btn btn-sm btn-outline-success btn-pill" data-onboard="${e.id}">Onboard</button>`
        }
      </td>
    `;
    empTableBody.appendChild(tr);
  });

  activeCountEl.textContent = active;
  inactiveCountEl.textContent = inactive;
}

async function createEmployee() {
  const payload = {
    employee_id: empEmployeeId.value.trim() || null,
    name: empName.value.trim() || null,
    manager: empManager.value.trim() || null,
    cg_email: empCgEmail.value.trim() || null,
    citi_email: empCitiEmail.value.trim() || null,
    role: empRole.value.trim() || null,
    region_code: empRegionCode.value.trim() || null,
    region_name: empRegionName.value.trim() || null,
    default_project_code: empProjectCode.value.trim() || null,
    billing_rate: empRate.value || null,
    start_date: empStartDate.value || null,
    status: "Active",
  };

  const res = await fetch("/api/employees", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await res.json();
  if (!res.ok) {
    empMsg.textContent = data.detail || "Failed to onboard employee.";
    empMsg.classList.remove("text-secondary");
    empMsg.classList.add("text-danger");
    return;
  }

  empMsg.textContent = "Employee onboarded successfully.";
  empMsg.classList.remove("text-danger");
  empMsg.classList.add("text-secondary");

  // Clear form
  [
    empEmployeeId,
    empName,
    empManager,
    empCgEmail,
    empCitiEmail,
    empRole,
    empRegionCode,
    empRegionName,
    empProjectCode,
    empRate,
    empStartDate,
  ].forEach((el) => (el.value = ""));

  await loadEmployees();
}

async function postAction(url) {
  const res = await fetch(url, { method: "POST" });
  const data = await res.json();
  if (!res.ok) {
    console.error("Action failed", data);
  }
  await loadEmployees();
}

// click handlers
empTableBody.addEventListener("click", (e) => {
  const btnDeboard = e.target.closest("button[data-deboard]");
  const btnOnboard = e.target.closest("button[data-onboard]");

  if (btnDeboard) {
    const id = btnDeboard.getAttribute("data-deboard");
    postAction(`/api/employees/${id}/deboard`);
  } else if (btnOnboard) {
    const id = btnOnboard.getAttribute("data-onboard");
    postAction(`/api/employees/${id}/onboard`);
  }
});

createEmpBtn.addEventListener("click", createEmployee);

filterActiveBtn.addEventListener("click", () => {
  currentStatusFilter = "Active";
  loadEmployees();
});
filterInactiveBtn.addEventListener("click", () => {
  currentStatusFilter = "Inactive";
  loadEmployees();
});

window.addEventListener("DOMContentLoaded", () => {
  loadEmployees();
});
