# Backend & Database Structure
Departmental Payment/Contribution System — Team Visionary Coders

## Backend (what each part does)
| App | Responsibility |
|---|---|
| users | Login, signup, student profile (matric no., department, level, role) |
| contributions | Create/list dues & fees, track who's paid |
| payments | Start payment, verify via gateway webhook, history, receipts; each department's own NGN bank account (BMONI) |
| notifications | Auto-alerts on payment success/failure, new contribution |

Flow: `Frontend → Backend API → Database`, and `Backend ↔ Payment Gateway` for the money part.
Full endpoint list: see `API_CONTRACT.md`.

## Database (8 tables)
| Table | Key fields | Links to |
|---|---|---|
| Department | name, faculty | — |
| User (Student) | username, matric_no, level, role, phone | belongs to a Department |
| Contribution | title, amount, deadline, target_level | belongs to a Department; created by a User |
| Payment | amount, status, gateway_reference | belongs to a User + a Contribution |
| Transaction | raw_payload (gateway's proof), received_at | belongs to a Payment |
| DepartmentBMONIWallet | account_name, account_number, bank_name, status | belongs to a Department — its own NGN bank account |
| BMONIWebhookEvent | event_id, event_type, raw_payload, processed | belongs to a DepartmentBMONIWallet (when BMONI's payload says which) |
| Notification | type, message, is_read | belongs to a User |

**In one line:** a Department has Students and Contributions → a Student Pays a Contribution →
every Payment is backed up by a Transaction record → success/failure sends a Notification.

*("Links to" = that table stores the other table's ID, not a copy of its data — one source of truth per fact.)*
