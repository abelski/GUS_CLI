---
name: security-review
description: Analyse pending changes for security vulnerabilities
shell: git diff HEAD 2>&1
---
Perform a security review of the following diff. Check for:

- **Injection** — SQL, shell, LDAP, XPath, template injection
- **Authentication & authorisation** — missing checks, privilege escalation, insecure defaults
- **Sensitive data exposure** — hardcoded secrets, credentials in logs, unencrypted storage
- **Input validation** — unvalidated user input, path traversal, ReDoS
- **Dependency risks** — new packages added, known vulnerable patterns
- **Cryptography** — weak algorithms, improper key management
- **CSRF / SSRF / open redirect** — for web-facing code

Diff:

```
$SHELL_OUTPUT
```

For each finding: severity (Critical / High / Medium / Low), file and line, description, and recommended fix.
If no issues found, confirm the diff is clean with a one-line summary.

$ARGUMENTS
