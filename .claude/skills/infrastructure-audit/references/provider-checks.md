# Provider-specific checks

Use repository-configured tooling first. Read the section for the provider(s) actually present.

## AWS

- IAM: no wildcard `Action` or `Resource` in a new or modified policy without a stated justification; prefer resource-scoped ARNs over account-wide grants.
- Networking: security groups and NACLs default-deny; a `0.0.0.0/0` ingress rule on anything but a public load balancer is a finding.
- Data: S3 buckets encrypted and not public unless the repository states a documented public-asset use case; RDS/EBS encryption at rest enabled.
- Secrets: credentials come from Secrets Manager, SSM Parameter Store, or an equivalent -- not plaintext in Terraform variables, CloudFormation parameters, or environment blocks.
- CI/CD: OIDC federation preferred over long-lived access keys stored as CI secrets.

## GCP

- IAM: primitive roles (`Owner`, `Editor`, `Viewer`) on a service account are a finding; prefer predefined or custom roles scoped to the resource.
- Networking: firewall rules default-deny; check `0.0.0.0/0` source ranges the same way as AWS security groups.
- Data: Cloud Storage bucket IAM and Cloud SQL are checked for public access and encryption the same way as their AWS equivalents.
- Secrets: Secret Manager, not plaintext in Terraform variables or Deployment Manager templates.
- CI/CD: Workload Identity Federation preferred over exported service-account JSON keys.

## Azure

- IAM: role assignments scoped to the resource group or resource, not the subscription, unless the task requires subscription-wide access.
- Networking: NSG rules default-deny; flag any rule permitting `Any` source to a management port (22, 3389, database ports).
- Data: Storage accounts and databases checked for public network access and encryption at rest.
- Secrets: Key Vault, not plaintext in ARM/Bicep parameters or pipeline variables.
- CI/CD: Workload identity federation (OIDC) preferred over a stored service-principal secret.

## Kubernetes

- RBAC: no `ClusterRole` bound cluster-wide when a namespaced `Role` would do; flag `cluster-admin` bindings.
- Pod security: containers not running as root without justification; no privileged containers or host namespace access without an explicit, documented reason.
- Secrets: mounted from a Secret or an external secrets operator, not baked into an image or a ConfigMap.
- Network policy: a namespace with no NetworkPolicy defaults to allow-all pod-to-pod traffic -- flag as a gap, not a pass, unless the repository states this is intentional.
- Supply chain: image tags pinned to a digest or immutable tag, not `:latest`, for anything deployed by CI.

## CI/CD (provider-neutral)

- Third-party actions and reusable workflows pinned to a commit SHA, not a mutable tag, for anything with write access to secrets or the repository.
- A pipeline triggered by a fork or an external pull request does not have access to repository secrets or a privileged deployment step.
- Branch protection and required reviews cover the branch a pipeline deploys from.
- A failed or partially-applied deployment step has a defined rollback, not just a re-run.
