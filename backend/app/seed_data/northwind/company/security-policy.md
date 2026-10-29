# Information security policy

**Owner:** Security team · **Last reviewed:** July 2026

## Scope

This policy applies to every employee, contractor and system at Northwind Labs. Exceptions require written approval from the Head of Security and expire after 90 days.

## Access control

- Access to production is granted through Teleport and expires after 8 hours. Standing access to production databases is not allowed for anyone.
- Access to customer data requires a ticket linked to a support case or incident. All sessions are recorded.
- Quarterly access reviews are run by each team lead; access that is not re-confirmed is revoked automatically.
- When someone leaves Northwind, all access is revoked within one hour of their final working time.

## Devices

Company laptops must have full-disk encryption, automatic screen lock after 5 minutes, and the endpoint agent installed. Personal devices may be used for Slack and email only, through the managed mobile apps.

## Passwords and authentication

1Password is mandatory for all work credentials. Hardware security keys (YubiKey) are required for Google Workspace, GitHub, AWS and Teleport. SMS-based two-factor authentication is not permitted for any work account.

## Data classification

| Class | Examples | Handling |
|---|---|---|
| Restricted | Customer event payloads, credentials, encryption keys | Never leaves production; never in tickets or Slack |
| Confidential | Financials, contracts, employee data | Shared on a need-to-know basis |
| Internal | Roadmaps, handbooks, this policy | Shared freely inside Northwind |
| Public | Marketing site, public docs | No restrictions |

## Reporting

Report suspected incidents, phishing, or lost devices immediately in `#security-help` or by emailing security@northwind.example. You will never be penalized for reporting in good faith, even if it turns out to be nothing.
