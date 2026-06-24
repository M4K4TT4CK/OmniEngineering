# Security Checklist

- Do not read `.env`, keys, certificates, local databases, logs, or credentials.
- Do not print secrets or private tokens.
- Do not weaken authentication, authorization, validation, or encryption.
- Do not make destructive data changes without explicit approval.
- Treat prompt-level exclusions as guardrails, not real access control.
- Report unresolved security uncertainty clearly.
