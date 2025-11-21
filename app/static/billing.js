let perProjectChart, trendChart;
const bMonthInput = document.getElementById("bMonth");
const projectSelect = document.getElementById("projectCode");
const loadBillingBtn = document.getElementById("loadBillingBtn");
const monthlyTotalEl = document.getElementById("monthlyTotal");
const annualProjectionEl = document.getElementById("annualProjection");

function getYM() {
  const v = bMonthInput.value;
  if (!v) return null;
  const [y, m] = v.split("-").map(Number);
  return { year: y, month: m };
}

async function refreshProjects() {
  const ym = getYM();
  if (!ym) return;
  const res = await fetch(
    `/api/projects?year=${ym.year}&month=${ym.month}`
  );
  const data = await res.json();
  projectSelect.innerHTML =
    '<option value="">All Projects</option>' +
    data.projects
      .map((p) => `<option value="${p}">${p}</option>`)
      .join("");
}

async function loadBilling() {
  const ym = getYM();
  if (!ym) return;
  const pc = projectSelect.value || "";
  const res = await fetch(
    `/api/billing?year=${ym.year}&month=${ym.month}&project_code=${encodeURIComponent(
      pc
    )}`
  );
  if (res.status === 401) {
    window.location.href = "/login";
    return;
  }
  const data = await res.json();
  monthlyTotalEl.textContent = data.monthly_total ?? 0;
  annualProjectionEl.textContent = data.annual_projection ?? 0;

  const labels = Object.keys(data.per_project);
  const values = Object.values(data.per_project);

  const ctx = document.getElementById("perProjectChart");
  if (perProjectChart) perProjectChart.destroy();
  perProjectChart = new Chart(ctx, {
    type: "bar",
    data: {
      labels,
      datasets: [
        {
          label: "Billing (selected month)",
          data: values,
        },
      ],
    },
    options: {
      scales: {
        y: { beginAtZero: true },
      },
    },
  });

  const tbody = document.querySelector("#billingTable tbody");
  tbody.innerHTML = "";
  data.detail.forEach((r) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${r.name}</td>
      <td>${r.email}</td>
      <td>${r.project_code || ""}</td>
      <td>${r.reconciled_hours || 0}</td>
      <td>${r.rate || 0}</td>
      <td>${r.billing || 0}</td>
    `;
    tbody.appendChild(tr);
  });

  const tctx = document.getElementById("trendChart");
  if (trendChart) trendChart.destroy();
  trendChart = new Chart(tctx, {
    type: "line",
    data: {
      labels: data.trend_labels,
      datasets: [
        {
          label: "Monthly Billing",
          data: data.trend_values,
          tension: 0.3,
        },
      ],
    },
    options: {
      scales: {
        y: { beginAtZero: true },
      },
    },
  });
}

loadBillingBtn.addEventListener("click", loadBilling);
projectSelect.addEventListener("change", loadBilling);

window.addEventListener("DOMContentLoaded", async () => {
  const now = new Date();
  bMonthInput.value = `${now.getFullYear()}-${String(
    now.getMonth() + 1
  ).padStart(2, "0")}`;
  await refreshProjects();
  await loadBilling();
});
