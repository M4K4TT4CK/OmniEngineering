# Plan-reading fixture

`tfplan.json` is the output of:

```
terraform plan -out=tfplan.bin
terraform show -json tfplan.bin > tfplan.json
```

It exercises what source HCL cannot express: `for_each` expanded into two bucket
instances with different ACLs, a module boundary, a resource whose encryption is
unknown until apply, and a resource being destroyed.
