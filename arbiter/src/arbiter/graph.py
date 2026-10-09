"""Provider-neutral resource graph.

Terraform HCL, CloudFormation / CDK-synthesized templates and Kubernetes
manifests all normalize into one Resource shape, so a policy is written once
against a normalized `kind` and fires on every provider.

The HCL reader is a deliberate subset: brace-matched blocks and scalar
assignments. It is not a full HCL parser and does not pretend to be — it
extracts enough structure for property assertions, and anything it cannot
parse is reported rather than silently skipped.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .core import Location

# ---------------------------------------------------------------------------
# Normalized kinds. The left side is provider-specific, the right side is what
# policies are written against.
# ---------------------------------------------------------------------------
TF_KINDS = {
    "aws_s3_bucket": "object_store",
    "aws_s3_bucket_public_access_block": "access_control",
    "google_storage_bucket": "object_store",
    "azurerm_storage_account": "object_store",
    "aws_db_instance": "database",
    "aws_rds_cluster": "database",
    "aws_dynamodb_table": "database",
    "azurerm_postgresql_server": "database",
    "google_sql_database_instance": "database",
    "aws_sqs_queue": "queue",
    "aws_sns_topic": "topic",
    "aws_ebs_volume": "block_store",
    "aws_efs_file_system": "filesystem",
    "aws_security_group": "firewall",
    "aws_security_group_rule": "firewall_rule",
    "aws_iam_policy": "policy",
    "aws_iam_role": "identity",
    "aws_iam_role_policy": "policy",
    "aws_kms_key": "key",
    "aws_lambda_function": "function",
    "aws_instance": "compute",
    "aws_ecs_service": "compute",
    "aws_lb": "load_balancer",
    "aws_lb_listener": "listener",
    # Classic ELB carries its listeners as inline blocks rather than separate
    # resources, so it is both. The rules that read `listener` properties find
    # them under `listener.*` on this resource.
    "aws_elb": "load_balancer",
    "aws_alb": "load_balancer",
    "aws_alb_listener": "listener",
    "aws_api_gateway_domain_name": "listener",
    "aws_apigatewayv2_api": "listener",
    "aws_cloudtrail": "audit_log",
    "aws_cloudwatch_log_group": "log_group",
    "aws_opensearch_domain": "search",
    "aws_elasticsearch_domain": "search",

    # ---- Azure ----------------------------------------------------------
    # A normalized kind means an existing rule fires here, so each of these
    # needs its provider's property names added to that rule's `any_of`. The
    # Kubernetes PersistentVolumeClaim episode is the warning: a kind mapping
    # without the matching property vocabulary produces a rule that is
    # confidently wrong on an entire cloud.
    "azurerm_storage_container": "object_store",
    "azurerm_mssql_server": "database",
    "azurerm_mssql_database": "database",
    "azurerm_postgresql_flexible_server": "database",
    "azurerm_mysql_server": "database",
    "azurerm_mysql_flexible_server": "database",
    "azurerm_cosmosdb_account": "database",
    "azurerm_managed_disk": "block_store",
    "azurerm_storage_share": "filesystem",
    "azurerm_network_security_group": "firewall",
    "azurerm_network_security_rule": "firewall_rule",
    "azurerm_key_vault_key": "key",
    "azurerm_key_vault": "key_store",
    "azurerm_role_definition": "policy",
    "azurerm_user_assigned_identity": "identity",
    "azurerm_linux_function_app": "function",
    "azurerm_windows_function_app": "function",
    "azurerm_linux_virtual_machine": "compute",
    "azurerm_windows_virtual_machine": "compute",
    "azurerm_kubernetes_cluster": "compute",
    "azurerm_app_service": "compute",
    "azurerm_linux_web_app": "compute",
    "azurerm_windows_web_app": "compute",
    "azurerm_function_app": "compute",
    "azurerm_lb": "load_balancer",
    "azurerm_servicebus_queue": "queue",
    "azurerm_servicebus_topic": "topic",
    "azurerm_eventhub": "topic",
    "azurerm_log_analytics_workspace": "log_group",
    "azurerm_monitor_diagnostic_setting": "audit_log",
    "azurerm_search_service": "search",

    # ---- Google Cloud ---------------------------------------------------
    "google_sql_database": "database",
    "google_bigtable_instance": "database",
    "google_spanner_database": "database",
    "google_firestore_database": "database",
    "google_compute_disk": "block_store",
    "google_filestore_instance": "filesystem",
    "google_compute_firewall": "firewall",
    "google_kms_crypto_key": "key",
    "google_kms_key_ring": "key_store",
    "google_project_iam_custom_role": "policy",
    "google_service_account": "identity",
    "google_cloudfunctions_function": "function",
    "google_cloudfunctions2_function": "function",
    "google_compute_instance": "compute",
    "google_container_cluster": "compute",
    "google_cloud_run_v2_service": "compute",
    "google_compute_target_http_proxy": "listener",
    "google_app_engine_standard_app_version": "compute",
    "google_compute_forwarding_rule": "load_balancer",
    "google_pubsub_topic": "topic",
    "google_pubsub_subscription": "queue",
    "google_logging_project_sink": "audit_log",
}

CFN_KINDS = {
    "AWS::S3::Bucket": "object_store",
    "AWS::RDS::DBInstance": "database",
    "AWS::RDS::DBCluster": "database",
    "AWS::DynamoDB::Table": "database",
    "AWS::SQS::Queue": "queue",
    "AWS::SNS::Topic": "topic",
    "AWS::EC2::Volume": "block_store",
    "AWS::EFS::FileSystem": "filesystem",
    "AWS::EC2::SecurityGroup": "firewall",
    "AWS::IAM::Policy": "policy",
    "AWS::IAM::Role": "identity",
    "AWS::KMS::Key": "key",
    "AWS::Lambda::Function": "function",
    "AWS::ECS::Service": "compute",
    "AWS::ElasticLoadBalancingV2::LoadBalancer": "load_balancer",
    "AWS::ElasticLoadBalancingV2::Listener": "listener",
    "AWS::CloudTrail::Trail": "audit_log",
    "AWS::Logs::LogGroup": "log_group",
    "AWS::OpenSearchService::Domain": "search",
    "AWS::Elasticsearch::Domain": "search",
}

K8S_KINDS = {
    "PersistentVolumeClaim": "block_store",
    "PersistentVolume": "block_store",
    "Secret": "secret",
    "ConfigMap": "config",
    # Every shape that ultimately runs a container is compute. Omitting Job,
    # CronJob and Pod meant a third of injected workload defects were invisible
    # because the resource never matched a rule.
    "Deployment": "compute",
    "StatefulSet": "compute",
    "DaemonSet": "compute",
    "ReplicaSet": "compute",
    "ReplicationController": "compute",
    "Pod": "compute",
    "Job": "compute",
    "CronJob": "compute",
    "Service": "load_balancer",
    "Ingress": "listener",
    "Route": "listener",
    "NetworkPolicy": "firewall",
    "Role": "policy",
    "ClusterRole": "policy",
    "RoleBinding": "identity",
    "ClusterRoleBinding": "identity",
    "ServiceAccount": "identity",
}


@dataclass
class Resource:
    address: str
    kind: str
    provider: str
    native: str
    properties: dict = field(default_factory=dict)
    relations: list[dict] = field(default_factory=list)
    repo_id: str = "root"
    origin: Location = field(default_factory=Location)
    # Where these values came from. A plan has resolved variables, expanded
    # for_each and flattened modules; source is a best-effort literal read.
    source: str = "hcl"          # hcl | plan | state | cfn | k8s | compose
    # Dotted property paths the plan could not determine before apply. These
    # are NOT absent — treating them as absent is how a scanner reports an
    # encrypted bucket as unencrypted.
    unknown: list[str] = field(default_factory=list)
    module: str = ""             # module address, empty at the root
    config_address: str = ""     # module-relative address, no instance index
    index: str = ""              # for_each key or count index, as written
    # Addresses this resource's configuration refers to. Taken from the plan's
    # `configuration` section, because by the time a value reaches `after` it
    # has been resolved to a string or marked unknown — the reference is gone.
    references: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["origin"] = self.origin.to_dict()
        return d

    def is_unknown(self, dotted: str) -> bool:
        """True when the plan could not determine this property, or a parent of it."""
        if not self.unknown:
            return False
        for path in self.unknown:
            if path == dotted or dotted.startswith(path + ".") or path.startswith(dotted + "."):
                return True
        return False

    def get(self, dotted: str, default: Any = None) -> Any:
        """Read a nested property with a dotted path, tolerant of missing keys."""
        cur: Any = self.properties
        for part in dotted.split("."):
            if isinstance(cur, dict):
                # case-insensitive fallback helps CFN vs TF naming
                if part in cur:
                    cur = cur[part]
                    continue
                lowered = {k.lower(): v for k, v in cur.items()}
                if part.lower() in lowered:
                    cur = lowered[part.lower()]
                    continue
                return default
            elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
                cur = cur[int(part)]
            else:
                return default
        return cur

    def flat_text(self) -> str:
        try:
            return json.dumps(self.properties, default=str).lower()
        except (TypeError, ValueError):
            return str(self.properties).lower()


# ---------------------------------------------------------------------------
# HCL subset reader
# ---------------------------------------------------------------------------

_BLOCK_RE = re.compile(r'(resource|data|module|provider)\s+"([^"]+)"(?:\s+"([^"]+)")?\s*\{')


def _match_brace(text: str, open_idx: int) -> int:
    """Index just past the matching close brace, skipping strings and comments."""
    depth = 0
    i = open_idx
    n = len(text)
    while i < n:
        c = text[i]
        if c == '"':
            i += 1
            while i < n and text[i] != '"':
                if text[i] == "\\":
                    i += 1
                i += 1
        elif c == "#" or (c == "/" and i + 1 < n and text[i + 1] == "/"):
            while i < n and text[i] != "\n":
                i += 1
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


_ASSIGN_RE = re.compile(r'^\s*([A-Za-z_][\w.-]*)\s*=\s*(.+?)\s*$')
# The trailing `$` used to be mandatory, which silently dropped any block
# written on one line: `disk_encryption_key { kms_key_self_link = ... }` was
# not an assignment and not a sub-block, so it vanished, and a disk that WAS
# encrypted got reported as unencrypted. _match_brace already handles nesting,
# so the end of the block is found the same way either way.
_SUBBLOCK_RE = re.compile(r'^\s*([A-Za-z_][\w-]*)\s*(?:=\s*)?\{')


def _coerce(raw: str) -> Any:
    raw = raw.strip().rstrip(",")
    if raw.startswith('"') and raw.endswith('"') and len(raw) >= 2:
        return raw[1:-1]
    if raw in ("true", "false"):
        return raw == "true"
    if re.fullmatch(r"-?\d+", raw):
        return int(raw)
    if re.fullmatch(r"-?\d*\.\d+", raw):
        return float(raw)
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        if not inner:
            return []
        return [_coerce(p) for p in re.split(r",(?![^\[]*\])", inner)]
    return raw


def _split_top_level(text: str, sep: str = ",") -> list[str]:
    """Split on `sep`, ignoring separators inside quotes, braces or brackets."""
    parts, buf, depth, quote = [], [], 0, ""
    i = 0
    while i < len(text):
        c = text[i]
        if quote:
            if c == "\\":
                buf.append(c)
                i += 1
                if i < len(text):
                    buf.append(text[i])
                    i += 1
                continue
            if c == quote:
                quote = ""
            buf.append(c)
        elif c in "\"'":
            quote = c
            buf.append(c)
        elif c in "{[(":
            depth += 1
            buf.append(c)
        elif c in "}])":
            depth -= 1
            buf.append(c)
        elif c == sep and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(c)
        i += 1
    parts.append("".join(buf))
    return [p for p in (x.strip() for x in parts) if p]


def _parse_hcl_body(body: str) -> dict:
    """Parse a brace-matched HCL body into a dict. Repeated blocks become lists."""
    out: dict[str, Any] = {}
    # An inline object writes its entries on one line separated by commas:
    # `tags = { Name = "x", Env = "prod" }`. Splitting on newlines alone reads
    # that as a single assignment whose value is the rest of the line, so the
    # second and later keys are swallowed into the first one's value.
    if "\n" not in body.strip() and "," in body:
        pieces = _split_top_level(body)
        if len(pieces) > 1 and all("=" in p for p in pieces):
            body = "\n".join(pieces)
    lines = body.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("//"):
            i += 1
            continue
        m = _SUBBLOCK_RE.match(line)
        if m:
            # find the matching close by re-joining
            rest = "\n".join(lines[i:])
            open_idx = rest.index("{")
            end = _match_brace(rest, open_idx)
            sub_body = rest[open_idx + 1:end - 1]
            key = m.group(1)
            parsed = _parse_hcl_body(sub_body)
            if key in out:
                if isinstance(out[key], list):
                    out[key].append(parsed)
                else:
                    out[key] = [out[key], parsed]
            else:
                out[key] = parsed
            consumed = rest[:end].count("\n")
            i += consumed + 1
            continue
        m = _ASSIGN_RE.match(line)
        if m:
            key, raw = m.group(1), m.group(2)
            if raw.strip() in ("{", "["):
                rest = "\n".join(lines[i:])
                opener = "{" if raw.strip() == "{" else "["
                if opener == "{":
                    open_idx = rest.index("{")
                    end = _match_brace(rest, open_idx)
                    out[key] = _parse_hcl_body(rest[open_idx + 1:end - 1])
                    i += rest[:end].count("\n") + 1
                    continue
                # multi-line list: gather until closing bracket
                buf = [raw]
                j = i + 1
                depth = raw.count("[") - raw.count("]")
                while j < len(lines) and depth > 0:
                    buf.append(lines[j])
                    depth += lines[j].count("[") - lines[j].count("]")
                    j += 1
                out[key] = _coerce(" ".join(x.strip() for x in buf))
                i = j
                continue
            out[key] = _coerce(raw)
        i += 1
    return out


def parse_terraform(path: Path, rel: str, repo_id: str) -> list[Resource]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out: list[Resource] = []
    for m in _BLOCK_RE.finditer(text):
        block_type, a, b = m.group(1), m.group(2), m.group(3)
        if block_type != "resource":
            continue
        open_idx = text.index("{", m.end() - 1)
        end = _match_brace(text, open_idx)
        body = text[open_idx + 1:end - 1]
        props = _parse_hcl_body(body)
        native = a
        name = b or "unnamed"
        provider = native.split("_", 1)[0] if "_" in native else "unknown"
        provider = {"aws": "aws", "google": "gcp", "azurerm": "azure"}.get(provider, provider)
        out.append(
            Resource(
                address=f"{native}.{name}",
                kind=TF_KINDS.get(native, "other"),
                provider=provider,
                native=native,
                properties=props,
                repo_id=repo_id,
                origin=Location(path=rel, start_line=text[:m.start()].count("\n") + 1, logical=f"{native}.{name}"),
            )
        )
    return out


# ---------------------------------------------------------------------------
# Terraform plan JSON — the authoritative reader
#
# `terraform show -json` gives resolved variables, expanded for_each and count,
# flattened modules and provider defaults. Source HCL gives none of that, so a
# plan supersedes it wherever both exist.
#
# Arbiter never produces the plan itself: `terraform init` downloads and
# executes provider code, which is exactly what section 01 of the spec forbids.
# The operator generates it; Arbiter reads it.
# ---------------------------------------------------------------------------

_INDEX_SUFFIX = re.compile(r'\[(?:"[^"]*"|\'[^\']*\'|\d+)\]$')


def is_plan_document(doc: Any) -> bool:
    if not isinstance(doc, dict):
        return False
    if "planned_values" in doc or "resource_changes" in doc:
        return True
    # `terraform show -json` of state, rather than of a plan
    return "format_version" in doc and isinstance(doc.get("values"), dict)


def base_address(address: str) -> str:
    """Strip module prefixes and instance indexes.

    `module.storage["eu"].aws_s3_bucket.data[0]` -> `aws_s3_bucket.data`

    This is the join key back to the HCL block that declared the resource, so
    a plan finding can still cite a file and line.
    """
    addr = address
    while addr.startswith("module."):
        parts = addr.split(".", 2)
        if len(parts) < 3:
            break
        addr = parts[2]
    addr = _INDEX_SUFFIX.sub("", addr)
    segments = [_INDEX_SUFFIX.sub("", s) for s in addr.split(".")]
    return ".".join(segments[-2:]) if len(segments) >= 2 else addr


def module_of(address: str) -> str:
    parts = address.split(".")
    out: list[str] = []
    i = 0
    while i + 1 < len(parts) and parts[i] == "module":
        out.extend(parts[i:i + 2])
        i += 2
    return ".".join(out)


def _flatten_unknown(node: Any, prefix: str = "", acc: list[str] | None = None) -> list[str]:
    """Turn Terraform's `after_unknown` tree into dotted paths."""
    acc = acc if acc is not None else []
    if node is True:
        if prefix:
            acc.append(prefix)
    elif isinstance(node, dict):
        for k, v in node.items():
            _flatten_unknown(v, f"{prefix}.{k}" if prefix else str(k), acc)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _flatten_unknown(v, f"{prefix}.{i}" if prefix else str(i), acc)
    return acc


def _expression_references(expressions: Any, acc: set[str] | None = None) -> set[str]:
    """Collect `references` from anywhere in a configuration expression tree."""
    acc = acc if acc is not None else set()
    if isinstance(expressions, dict):
        for key, val in expressions.items():
            if key == "references" and isinstance(val, list):
                for ref in val:
                    if isinstance(ref, str) and not ref.startswith(("each.", "count.", "var.", "local.")):
                        acc.add(base_address(ref))
            else:
                _expression_references(val, acc)
    elif isinstance(expressions, list):
        for val in expressions:
            _expression_references(val, acc)
    return acc


def parse_plan_config(doc: dict) -> dict[tuple[str, str], list[str]]:
    """Map (module address, config address) -> referenced config addresses."""
    out: dict[tuple[str, str], list[str]] = {}

    def walk(module: dict, module_addr: str) -> None:
        for res in module.get("resources") or []:
            if not isinstance(res, dict):
                continue
            addr = res.get("address", "")
            refs = _expression_references(res.get("expressions"))
            if addr:
                out[(module_addr, addr)] = sorted(refs)
        for name, call in (module.get("module_calls") or {}).items():
            if not isinstance(call, dict):
                continue
            child = call.get("module")
            if isinstance(child, dict):
                nested = f"{module_addr}.module.{name}" if module_addr else f"module.{name}"
                walk(child, nested)

    root = (doc.get("configuration") or {}).get("root_module")
    if isinstance(root, dict):
        walk(root, "")
    return out


def _walk_module(module: dict, out: list[dict]) -> None:
    for res in module.get("resources") or []:
        if isinstance(res, dict):
            out.append(res)
    for child in module.get("child_modules") or []:
        if isinstance(child, dict):
            _walk_module(child, out)


def _tf_provider(provider_name: str, native: str) -> str:
    name = (provider_name or "").rsplit("/", 1)[-1]
    return {"aws": "aws", "google": "gcp", "azurerm": "azure",
            "kubernetes": "k8s"}.get(name, name or native.split("_", 1)[0])


def parse_tfplan(path: Path, rel: str, repo_id: str) -> list[Resource]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return []
    if not is_plan_document(doc):
        return []

    # resource_changes carries after_unknown, so prefer it and fall back to
    # planned_values (or state values) for anything it does not cover.
    unknown_by_address: dict[str, list[str]] = {}
    values_by_address: dict[str, dict] = {}
    meta_by_address: dict[str, dict] = {}

    for change in doc.get("resource_changes") or []:
        if not isinstance(change, dict) or change.get("mode") == "data":
            continue
        actions = ((change.get("change") or {}).get("actions")) or []
        if actions == ["delete"]:
            continue  # evaluating something the plan removes is pointless
        addr = change.get("address", "")
        after = (change.get("change") or {}).get("after")
        if isinstance(after, dict):
            values_by_address[addr] = after
        unk = (change.get("change") or {}).get("after_unknown")
        if unk:
            unknown_by_address[addr] = _flatten_unknown(unk)
        meta_by_address[addr] = change

    planned: list[dict] = []
    root = (doc.get("planned_values") or {}).get("root_module")
    if isinstance(root, dict):
        _walk_module(root, planned)
    state_root = (doc.get("values") or {}).get("root_module")
    if isinstance(state_root, dict):
        _walk_module(state_root, planned)
    for res in planned:
        addr = res.get("address", "")
        meta_by_address.setdefault(addr, res)
        if addr not in values_by_address and isinstance(res.get("values"), dict):
            values_by_address[addr] = res["values"]

    config_refs = parse_plan_config(doc)
    source = "state" if "planned_values" not in doc and "values" in doc else "plan"

    out: list[Resource] = []
    for addr, meta in meta_by_address.items():
        if meta.get("mode") == "data":
            continue
        native = meta.get("type", "")
        if not native:
            continue
        props = values_by_address.get(addr) or {}
        module = module_of(addr)
        config_address = base_address(addr)
        index = meta.get("index")
        if index is None:
            m = _INDEX_SUFFIX.search(addr)
            index = m.group(0)[1:-1].strip('"\'') if m else ""
        out.append(Resource(
            address=addr,
            kind=TF_KINDS.get(native, "other"),
            provider=_tf_provider(meta.get("provider_name", ""), native),
            native=native,
            properties=props if isinstance(props, dict) else {},
            repo_id=repo_id,
            origin=Location(path=rel, logical=addr),
            source=source,
            unknown=sorted(set(unknown_by_address.get(addr, []))),
            module=module,
            config_address=config_address,
            index=str(index),
            references=config_refs.get((module, config_address), []),
        ))
    return out


def merge_plan_over_source(resources: list[Resource]) -> list[Resource]:
    """Where a plan and the source both describe a resource, the plan wins.

    The source parse is still useful for one thing the plan does not carry:
    a file and a line number. Findings should point at code a person can open,
    so matched resources keep the plan's values and the source's origin.
    """
    by_repo_base: dict[tuple[str, str], list[Resource]] = {}
    for r in resources:
        if r.source in ("hcl",):
            by_repo_base.setdefault((r.repo_id, r.address), []).append(r)

    kept: list[Resource] = []
    superseded: set[int] = set()
    for r in resources:
        if r.source not in ("plan", "state"):
            continue
        base = base_address(r.address)
        matches = by_repo_base.get((r.repo_id, base)) or []
        if matches:
            src = matches[0]
            r.origin = Location(
                path=src.origin.path,
                start_line=src.origin.start_line,
                logical=r.address,
                repo_id=src.origin.repo_id,
            )
            for m in matches:
                superseded.add(id(m))

    for r in resources:
        if id(r) in superseded:
            continue
        kept.append(r)
    return kept


def parse_cfn(path: Path, rel: str, repo_id: str) -> list[Resource]:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    doc: Any = None
    if rel.endswith(".json"):
        try:
            doc = json.loads(raw)
        except ValueError:
            return []
    else:
        import yaml
        try:
            doc = yaml.safe_load(raw)
        except yaml.YAMLError:
            return []
    if not isinstance(doc, dict) or not isinstance(doc.get("Resources"), dict):
        return []
    resources_block = doc["Resources"]
    # Aurora/Neptune cluster members (AWS::RDS::DBInstance, AWS::Neptune::DBInstance)
    # never carry StorageEncrypted/KmsKeyId/DeletionProtection themselves -- those
    # are cluster-level settings in the CFN schema. Evaluating a member instance in
    # isolation reports the cluster's own encryption and deletion protection as
    # missing on every instance, even when the parent DBCluster has both set.
    cluster_props: dict[str, dict] = {}
    for logical_id, block in resources_block.items():
        if isinstance(block, dict) and block.get("Type", "").endswith("::DBCluster"):
            props = block.get("Properties")
            if isinstance(props, dict):
                cluster_props[logical_id] = props
    out: list[Resource] = []
    for logical_id, block in resources_block.items():
        if not isinstance(block, dict):
            continue
        native = block.get("Type", "")
        props = block.get("Properties") or {}
        props = props if isinstance(props, dict) else {}
        if native.endswith("::DBInstance"):
            cluster_ref = props.get("DBClusterIdentifier")
            cluster_id = cluster_ref.get("Ref") if isinstance(cluster_ref, dict) else None
            parent = cluster_props.get(cluster_id) if cluster_id else None
            if parent:
                inherited = dict(props)
                for key in ("StorageEncrypted", "KmsKeyId", "DeletionProtection"):
                    if key not in inherited and key in parent:
                        inherited[key] = parent[key]
                props = inherited
        out.append(
            Resource(
                address=logical_id,
                kind=CFN_KINDS.get(native, "other"),
                provider="aws",
                native=native,
                properties=props,
                repo_id=repo_id,
                origin=Location(path=rel, logical=logical_id),
                source="cfn",
            )
        )
    return out


def parse_k8s(path: Path, rel: str, repo_id: str) -> list[Resource]:
    import yaml
    try:
        docs = list(yaml.safe_load_all(path.read_text(encoding="utf-8", errors="replace")))
    except (OSError, yaml.YAMLError):
        return []
    out: list[Resource] = []
    for doc in docs:
        if not isinstance(doc, dict) or "kind" not in doc or "apiVersion" not in doc:
            continue
        kind = doc.get("kind", "")
        name = (doc.get("metadata") or {}).get("name", "unnamed")
        out.append(
            Resource(
                address=f"{kind}/{name}",
                kind=K8S_KINDS.get(kind, "other"),
                provider="k8s",
                native=kind,
                properties=doc.get("spec") or doc,
                repo_id=repo_id,
                origin=Location(path=rel, logical=f"{kind}/{name}"),
                source="k8s",
            )
        )
    return out


# ---------------------------------------------------------------------------
# Sibling resolution
#
# Modern Terraform splits one logical resource across several: a bucket's
# encryption, ACL, logging and public-access settings each live in their own
# resource pointing back at the parent. Evaluating the parent in isolation
# reports an encrypted bucket as unencrypted, which is the single loudest
# false positive a naive IaC scanner produces.
# ---------------------------------------------------------------------------

CHILD_RULES: list[dict] = [
    {"child": "aws_s3_bucket_server_side_encryption_configuration",
     "parent": "aws_s3_bucket", "mode": "under", "key": "server_side_encryption_configuration"},
    {"child": "aws_s3_bucket_public_access_block",
     "parent": "aws_s3_bucket", "mode": "under", "key": "public_access_block"},
    {"child": "aws_s3_bucket_versioning",
     "parent": "aws_s3_bucket", "mode": "under", "key": "versioning"},
    {"child": "aws_s3_bucket_logging",
     "parent": "aws_s3_bucket", "mode": "under", "key": "logging"},
    {"child": "aws_s3_bucket_lifecycle_configuration",
     "parent": "aws_s3_bucket", "mode": "under", "key": "lifecycle_rule"},
    {"child": "aws_s3_bucket_policy",
     "parent": "aws_s3_bucket", "mode": "under", "key": "bucket_policy"},
    {"child": "aws_s3_bucket_acl",
     "parent": "aws_s3_bucket", "mode": "promote", "keys": ["acl"]},
    {"child": "aws_security_group_rule",
     "parent": "aws_security_group", "mode": "append_typed", "key": "type"},
    {"child": "aws_ecs_task_definition",
     "parent": "aws_ecs_service", "mode": "under", "key": "task_definition_body"},
    {"child": "aws_rds_cluster_instance",
     "parent": "aws_rds_cluster", "mode": "under", "key": "cluster_instance"},
    {"child": "google_storage_bucket_iam_binding",
     "parent": "google_storage_bucket", "mode": "under", "key": "iam_binding"},
]

_REF_RE = re.compile(r"\b([a-z][a-z0-9_]*\.[A-Za-z0-9_-]+)(?:\.[A-Za-z0-9_]+)*\b")


def _referenced_addresses(props: Any, acc: set[str] | None = None) -> set[str]:
    """Collect `type.name` references from anywhere in a property tree."""
    acc = acc if acc is not None else set()
    if isinstance(props, dict):
        for v in props.values():
            _referenced_addresses(v, acc)
    elif isinstance(props, list):
        for v in props:
            _referenced_addresses(v, acc)
    elif isinstance(props, str):
        for m in _REF_RE.finditer(props):
            acc.add(m.group(1))
    return acc


def resolve_siblings(resources: list[Resource]) -> list[Resource]:
    """Fold child configuration resources into the parent they configure.

    Children stay in the graph — they are still real resources and rules may
    target them directly — but the parent gains their settings so a rule
    written against the parent sees the effective configuration.
    """
    by_address: dict[tuple[str, str], Resource] = {(r.repo_id, r.address): r for r in resources}
    rules_by_child: dict[str, list[dict]] = {}
    for rule in CHILD_RULES:
        rules_by_child.setdefault(rule["child"], []).append(rule)

    # Plan resources are matched through the configuration section's
    # references and the for_each key, not through property values: by the
    # time a value reaches `after` it is a resolved string or an unknown.
    plan_index: dict[tuple[str, str, str, str], Resource] = {}
    for r in resources:
        if r.source in ("plan", "state"):
            plan_index[(r.repo_id, r.module, r.config_address, r.index)] = r

    for child in resources:
        for rule in rules_by_child.get(child.native, []):
            parent_type = rule["parent"]
            parent: Resource | None = None

            if child.source in ("plan", "state"):
                for ref in child.references:
                    if not ref.startswith(parent_type + "."):
                        continue
                    parent = (
                        plan_index.get((child.repo_id, child.module, ref, child.index))
                        or plan_index.get((child.repo_id, child.module, ref, ""))
                    )
                    if parent is not None:
                        break
            else:
                for ref in _referenced_addresses(child.properties):
                    if ref.startswith(parent_type + "."):
                        parent = by_address.get((child.repo_id, ref))
                        if parent is not None:
                            break
            if parent is None:
                continue

            mode = rule["mode"]
            if mode == "under":
                parent.properties.setdefault(rule["key"], child.properties)
            elif mode == "promote":
                for key in rule.get("keys", []):
                    if key in child.properties:
                        parent.properties.setdefault(key, child.properties[key])
            elif mode == "append_typed":
                # security group rules carry their own ingress/egress marker
                bucket = "ingress" if str(child.properties.get("type", "")).lower() == "ingress" else "egress"
                existing = parent.properties.get(bucket)
                entry = dict(child.properties)
                if existing is None:
                    parent.properties[bucket] = entry
                elif isinstance(existing, list):
                    existing.append(entry)
                else:
                    parent.properties[bucket] = [existing, entry]

            parent.relations.append({"to": child.address, "type": "configured_by"})
            child.relations.append({"to": parent.address, "type": "configures"})
    return resources


PLAN_MARKERS = ('"planned_values"', '"resource_changes"', '"terraform_version"')
PLAN_NAME_HINTS = ("tfplan", "plan.json", "terraform-plan", "tf-plan", "showplan")


def looks_like_plan(path: Path, rel: str) -> bool:
    """Cheap sniff before parsing. A repository can hold a lot of JSON."""
    base = rel.rsplit("/", 1)[-1].lower()
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:4096]
    except OSError:
        return False
    if not any(m in head for m in PLAN_MARKERS):
        return False
    return any(h in base for h in PLAN_NAME_HINTS) or '"planned_values"' in head or '"resource_changes"' in head


def build_graph(inv, plan_paths: list[str] | None = None) -> list[Resource]:
    """Walk the inventory and normalize everything infrastructure-shaped."""
    resources: list[Resource] = []

    # Explicitly supplied plans are read first and are never sniffed for.
    for spec in plan_paths or []:
        repo_id, _, raw = spec.partition("=")
        if not raw:
            repo_id, raw = (inv.files[0].repo_id if inv.files else "root"), spec
        p = Path(raw)
        if not p.is_file():
            raise RuntimeError(f"plan file not found: {raw}")
        found = parse_tfplan(p, p.name, repo_id)
        if not found:
            raise RuntimeError(f"{raw} is not a Terraform plan or state JSON document")
        resources.extend(found)
    explicit = {r.repo_id for r in resources}

    for f in inv.files:
        p = Path(f.abspath)
        # A real plan easily exceeds the size at which the inventory stops
        # reading files, so plan candidates get looked at regardless.
        plan_candidate = (
            f.language == "json"
            and f.repo_id not in explicit
            and any(h in f.path.rsplit("/", 1)[-1].lower() for h in PLAN_NAME_HINTS)
        )
        if f.binary and not plan_candidate:
            continue
        if f.path.endswith(".tf"):
            resources.extend(parse_terraform(p, f.path, f.repo_id))
        elif f.language == "json" and f.repo_id not in explicit and (
            plan_candidate or looks_like_plan(p, f.path)
        ):
            resources.extend(parse_tfplan(p, f.path, f.repo_id))
        elif f.path.endswith(".template.json") or (f.language == "json" and "cdk.out" in f.path):
            resources.extend(parse_cfn(p, f.path, f.repo_id))
        elif f.language == "yaml":
            head = ""
            try:
                head = p.read_text(encoding="utf-8", errors="replace")[:4000]
            except OSError:
                pass
            if "AWSTemplateFormatVersion" in head or re.search(r"^Resources:", head, re.M):
                resources.extend(parse_cfn(p, f.path, f.repo_id))
            elif re.search(r"^apiVersion:", head, re.M) and re.search(r"^kind:", head, re.M):
                resources.extend(parse_k8s(p, f.path, f.repo_id))

    resources = merge_plan_over_source(resources)
    return resolve_siblings(resources)
