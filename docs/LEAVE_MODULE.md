# Leave Module

Lets **any employee** apply for leave after logging in. Two-step **Manager → HR**
approval (mirrors self-attendance). Leave **types** only — no balance tracking in
this phase. It's a new, self-contained module and does not affect attendance
capture (kiosk or self).

## Who / what

- **Apply:** every logged-in employee sees **Apply Leave** in the top nav and can
  file for themselves. Admin / managers can file on behalf of any employee.
- **Types:** Casual, Sick, Earned, Unpaid.
- **Half-day:** a single-date request counting as 0.5 day. Multi-day requests
  count inclusive days automatically.
- **Approval chain:** the employee's line **Manager** (via `worker.manager_code`)
  approves first, then **HR** gives the final sign-off. Employees with no manager
  on file skip straight to HR (manager step shown as `NA`).

## Screens

- **Apply Leave** (`/leave/apply`) — the request form + the employee's own leave
  history with live status.
- **Team Leave Approvals** (`/leave/approvals`) — managers approve/decline their
  direct reports (ProcamRep + Admin).
- **Leave HR Approvals** (`/leave/hr-approvals`) — HR final sign-off (Admin).
- **Leave Register** (`/leave/register`) — all employees, all statuses, with
  filters (status, type, employee, date range), summary counts, and **Excel
  export** (`/leave/register.xlsx`).

## Data

New table **`leave_requests`** (created automatically by `db.create_all()` on
boot — no manual migration). Overall `status` is derived from the two steps via
`LeaveRequest.recompute_status()` and kept in sync on each decision.

## Files

- `models/__init__.py` — `LeaveRequest` model + leave-type/status constants.
- `routes/leave.py` — apply, manager/HR approvals, register, Excel export.
- `templates/leave/apply.html`, `register.html`, `approvals.html`.
- `app.py` — blueprint registered at `/leave`.
- `templates/_partials/topnav.html` — Apply Leave (all roles) + management links.

## Tests

Covered end-to-end (15 checks): apply (single/multi/half-day), manager→HR chain,
no-manager path, manager authorisation (reports only), register filters, and
Excel export.
