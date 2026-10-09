"""Infrastructure-as-code probes: Terraform and plan JSON, Kubernetes,
CloudFormation, encryption by key reference, the TLS rules.
"""
from __future__ import annotations

import pytest
from helpers import LEGACY, ROOT, _scan_text

from arbiter.engine import run_scan
from arbiter.graph import parse_terraform
from arbiter.policy import load_config


def test_terraform_parses_nested_blocks():
    resources = parse_terraform(LEGACY / "infra" / "main.tf", "infra/main.tf", "root")
    by_addr = {r.address: r for r in resources}
    assert "aws_s3_bucket.artifacts" in by_addr
    assert by_addr["aws_s3_bucket.artifacts"].kind == "object_store"
    audit = by_addr["aws_s3_bucket.audit_archive"]
    assert audit.get("server_side_encryption_configuration") is not None
    sg = by_addr["aws_security_group.web"]
    assert sg.get("ingress.cidr_blocks") == ["0.0.0.0/0"]


def test_egress_to_anywhere_is_not_flagged(legacy_report):
    """The fixture has an open egress rule. Flagging it would be a false positive."""
    ingress = [f for f in legacy_report.findings if f.rule_id.endswith("unrestricted-ingress")]
    assert len(ingress) == 1


def test_encrypted_bucket_is_not_flagged(legacy_report):
    bad = [
        f for f in legacy_report.findings
        if f.location.logical == "aws_s3_bucket.audit_archive"
        and "encryption" in f.rule_id
    ]
    assert not bad, "the correctly configured bucket must stay clean"


# --------------------------------------------------------------------------
# P1/P2 additions
# --------------------------------------------------------------------------

def test_sibling_resources_fold_into_the_parent(tmp_path):
    """The loudest known false positive: modern Terraform configures a bucket
    through separate resources, and a naive scan calls it unencrypted."""
    (tmp_path / "main.tf").write_text("""
resource "aws_s3_bucket" "data" { bucket = "d" }
resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  bucket = aws_s3_bucket.data.id
  rule { apply_server_side_encryption_by_default { sse_algorithm = "aws:kms" } }
}
resource "aws_s3_bucket_public_access_block" "data" {
  bucket            = aws_s3_bucket.data.id
  block_public_acls = true
}
resource "aws_s3_bucket_acl" "data" {
  bucket = aws_s3_bucket.data.id
  acl    = "public-read"
}
""")
    rep = run_scan([str(tmp_path)], load_config(None), only=["resource_policy"])
    ids = {f.rule_id for f in rep.active()}
    assert "arbiter/resource.unencrypted-object-store" not in ids, "encryption sibling not folded in"
    assert "arbiter/resource.public-object-store" not in ids, "public access block should compensate"


def test_bucket_without_siblings_is_still_flagged(tmp_path):
    (tmp_path / "main.tf").write_text(
        'resource "aws_s3_bucket" "x" { bucket = "x"\n  acl = "public-read"\n}\n'
    )
    rep = run_scan([str(tmp_path)], load_config(None), only=["resource_policy"])
    ids = {f.rule_id for f in rep.active()}
    assert "arbiter/resource.public-object-store" in ids


def test_example_infrastructure_is_downgraded(tmp_path):
    found = _scan_text(tmp_path, "examples/basic/main.tf",
                       'resource "aws_db_instance" "x" { engine = "postgres" }\n',
                       ["resource_policy"])
    enc = [f for f in found if "unencrypted-database" in f.rule_id]
    assert enc and enc[0].severity == "medium" and "example code" in enc[0].title


def test_production_infrastructure_is_not_downgraded(tmp_path):
    found = _scan_text(tmp_path, "infra/main.tf",
                       'resource "aws_db_instance" "x" { engine = "postgres" }\n',
                       ["resource_policy"])
    enc = [f for f in found if "unencrypted-database" in f.rule_id]
    assert enc and enc[0].severity == "high"


# --------------------------------------------------------------------------
# Terraform plan JSON
# --------------------------------------------------------------------------

TFPLAN = ROOT / "fixtures" / "tfplan"


def test_plan_expands_for_each_into_instances(plan_report):
    from arbiter.graph import build_graph
    from arbiter.inventory import acquire_one, build_inventory
    info, _ = acquire_one(str(TFPLAN))
    graph = build_graph(build_inventory([info]))
    buckets = [r for r in graph if r.native == "aws_s3_bucket"]
    assert len(buckets) == 2, "for_each must yield one resource per instance"
    assert {b.index for b in buckets} == {"eu", "us"}
    assert all(b.source == "plan" for b in buckets)


def test_plan_finds_what_source_cannot(plan_report, source_only_report):
    """The ACL is a conditional expression, so a literal read cannot see that
    one of the two buckets is public. This is a false negative, not noise."""
    plan_ids = {f.rule_id for f in plan_report.active()}
    src_ids = {f.rule_id for f in source_only_report.active()}
    assert "arbiter/resource.public-object-store" in plan_ids
    assert "arbiter/resource.public-object-store" not in src_ids


def test_plan_avoids_a_false_positive_source_produces(plan_report, source_only_report):
    """`encrypted = var.encrypt_volumes` reads literally as a truthy string;
    the plan marks it unknown until apply."""
    src = [f for f in source_only_report.active()
           if f.rule_id == "arbiter/resource.unencrypted-volume"]
    planned = [f for f in plan_report.active()
               if f.rule_id == "arbiter/resource.unencrypted-volume"]
    assert src, "the literal reader asserts something it cannot know"
    assert not planned, "the plan must not assert an undetermined value"


def test_unknown_is_reported_as_not_assessed(plan_report):
    notes = [f for f in plan_report.active() if f.rule_id.endswith(".not-assessed")]
    assert notes, "an unevaluatable high-severity check must be surfaced"
    assert all(f.severity == "info" for f in notes)
    assert any("unknown" in f.evidence for f in notes)


def test_unknown_never_becomes_a_pass_or_a_finding():
    from arbiter.graph import Resource
    from arbiter.probes import SATISFIED, UNKNOWN, VIOLATED, _eval_assert
    rule = {"id": "x", "assert": "property_truthy", "any_of": ["encrypted"], "severity": "high"}
    assert _eval_assert(Resource("a", "block_store", "aws", "aws_ebs_volume",
                                 {"encrypted": True}), rule) == SATISFIED
    assert _eval_assert(Resource("a", "block_store", "aws", "aws_ebs_volume",
                                 {"encrypted": False}), rule) == VIOLATED
    assert _eval_assert(Resource("a", "block_store", "aws", "aws_ebs_volume",
                                 {}, unknown=["encrypted"]), rule) == UNKNOWN


def test_siblings_link_through_plan_configuration(plan_report):
    """In a real plan `bucket = aws_s3_bucket.data.id` has been resolved away,
    so the parent link comes from the configuration section's references."""
    enc = [f for f in plan_report.active()
           if f.rule_id == "arbiter/resource.unencrypted-object-store"]
    assert len(enc) == 1, "only the bucket without an encryption sibling should fire"
    assert "data[\"us\"]" in enc[0].location.logical


def test_destroyed_resources_are_ignored(plan_report):
    ingress = [f for f in plan_report.active() if "unrestricted-ingress" in f.rule_id]
    assert not ingress, "a security group the plan destroys is not a finding"


def test_plan_findings_cite_source_lines(plan_report):
    findings = [f for f in plan_report.active() if f.severity in ("critical", "high")]
    assert findings
    for f in findings:
        assert f.location.path.endswith(".tf"), "a plan finding must point at code"
        assert f.location.start_line > 0


def test_source_only_declares_its_own_weakness(source_only_report):
    note = [f for f in source_only_report.active()
            if f.rule_id == "arbiter/resource.source-is-literal-hcl"]
    assert note and note[0].severity == "info"


def test_base_address_strips_modules_and_indexes():
    from arbiter.graph import base_address, module_of
    assert base_address('module.storage.aws_s3_bucket.data["eu"]') == "aws_s3_bucket.data"
    assert base_address("aws_db_instance.primary") == "aws_db_instance.primary"
    assert base_address("module.a.module.b.aws_s3_bucket.x[0]") == "aws_s3_bucket.x"
    assert module_of('module.storage.aws_s3_bucket.data["eu"]') == "module.storage"
    assert module_of("aws_db_instance.primary") == ""


def test_explicit_plan_path_is_used(tmp_path):
    import shutil
    dst = tmp_path / "repo"
    shutil.copytree(TFPLAN, dst)
    plan = dst / "tfplan.json"
    moved = tmp_path / "elsewhere.json"
    shutil.move(str(plan), str(moved))
    rep = run_scan([str(dst)], load_config(None), only=["resource_policy"],
                   plan_paths=[str(moved)])
    assert "arbiter/resource.public-object-store" in {f.rule_id for f in rep.active()}


def test_a_bad_plan_path_fails_loudly(tmp_path):
    """A document that is not a plan raises the engine's RuntimeError, not
    whatever happens to go wrong first (FAIL-047: `raises(Exception)` would
    have passed on a typo in the call as readily as on the refusal)."""
    (tmp_path / "notaplan.json").write_text('{"hello": "world"}')
    with pytest.raises(RuntimeError, match="not a Terraform plan"):
        run_scan([str(TFPLAN)], load_config(None), only=["resource_policy"],
                 plan_paths=[str(tmp_path / "notaplan.json")])


def test_every_container_shape_is_a_workload():
    """Job, CronJob and Pod were not classified as compute, so a third of
    injected Kubernetes defects matched no rule at all."""
    from arbiter.graph import K8S_KINDS
    for kind in ("Deployment", "StatefulSet", "DaemonSet", "ReplicaSet",
                 "Pod", "Job", "CronJob", "ReplicationController"):
        assert K8S_KINDS.get(kind) == "compute", f"{kind} must be compute"


def test_kubernetes_claims_are_not_scored_by_aws_properties(tmp_path):
    """A PVC is a block_store, but encryption lives on its StorageClass. This
    rule fired on every PersistentVolumeClaim in Kubernetes' own examples."""
    found = _scan_text(tmp_path, "pvc.yaml",
                       "apiVersion: v1\nkind: PersistentVolumeClaim\nmetadata:\n  name: c\n"
                       "spec:\n  resources:\n    requests:\n      storage: 5Gi\n",
                       ["resource_policy"])
    assert not [f for f in found if "unencrypted-volume" in f.rule_id]


def test_unhardened_workload_is_reported(tmp_path):
    """Kubernetes defaults are the insecure ones, so the finding is about what
    is absent. Without these rules a deliberately vulnerable Kubernetes
    repository produced two findings."""
    found = _scan_text(tmp_path, "deploy.yaml",
                       "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: w\n"
                       "spec:\n  template:\n    spec:\n      containers:\n"
                       "      - name: app\n        image: app:1.0\n",
                       ["resource_policy"])
    rules = {f.rule_id.split(".")[-1] for f in found}
    assert "k8s-no-security-context" in rules
    assert "k8s-not-run-as-non-root" in rules


def test_hardened_workload_is_quiet(tmp_path):
    found = _scan_text(tmp_path, "deploy.yaml",
                       "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: w\n"
                       "spec:\n  template:\n    spec:\n      containers:\n"
                       "      - name: app\n        image: app:1.2.3\n"
                       "        securityContext:\n          allowPrivilegeEscalation: false\n"
                       "          runAsNonRoot: true\n          readOnlyRootFilesystem: true\n"
                       "          capabilities:\n            drop: [\"ALL\"]\n"
                       "        resources:\n          limits:\n            cpu: \"1\"\n",
                       ["resource_policy"])
    assert not found, [f.rule_id for f in found]


def test_provider_scoping_is_honoured():
    from arbiter.graph import Resource
    from arbiter.probes import SATISFIED, _eval_assert
    rule = {"id": "x", "assert": "text_present", "values": ['"securitycontext"'],
            "match_providers": ["k8s"]}
    # the assertion itself is provider-blind; scoping happens in the probe,
    # so this only checks the assertion still evaluates predictably
    hardened = Resource("d", "compute", "k8s", "Deployment", {"securityContext": {"runAsNonRoot": True}})
    assert _eval_assert(hardened, rule) == SATISFIED


def test_cloudformation_and_kubernetes_are_not_labelled_terraform_source(tmp_path):
    """The `evaluated from source, not from a plan` note fired on repositories
    with no Terraform in them at all."""
    found = _scan_text(tmp_path, "deploy.yaml",
                       "apiVersion: v1\nkind: Service\nmetadata:\n  name: s\n"
                       "spec:\n  ports:\n  - port: 80\n", ["resource_policy"])
    assert not [f for f in found if "source-is-literal-hcl" in f.rule_id]


# ---------------------------------------------------------------------------
# Encryption by key reference.
#
# The worst false positive found so far: a volume encrypted with a
# customer-managed KMS key was reported HIGH as unencrypted, because the
# truthiness test only accepted the literal strings "true"/"yes"/"1" and a key
# ARN is none of those. It affected every provider including AWS, and it had
# survived every corpus run and every injection trial — the multi-cloud
# fixture is what made it visible, because writing the CORRECT half of a
# fixture is what exposes a rule that cannot recognise correctness.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tf,label", [
    ('resource "aws_ebs_volume" "v" {\n  size = 10\n'
     '  kms_key_id = aws_kms_key.main.arn\n}\n', "aws ebs + kms reference"),
    ('resource "aws_db_instance" "d" {\n  storage_encrypted = true\n'
     '  kms_key_id = "arn:aws-us-gov:kms:us-gov-west-1:1:key/abc"\n}\n', "aws rds + kms arn"),
    ('resource "azurerm_managed_disk" "d" {\n  disk_size_gb = 10\n'
     '  disk_encryption_set_id = azurerm_disk_encryption_set.m.id\n}\n', "azure disk"),
    ('resource "google_compute_disk" "d" {\n  size = 10\n'
     '  disk_encryption_key { kms_key_self_link = google_kms_crypto_key.m.id }\n}\n', "gcp disk"),
    ('resource "google_pubsub_topic" "t" {\n'
     '  kms_key_name = google_kms_crypto_key.m.id\n}\n', "gcp pubsub"),
])
def test_a_key_reference_counts_as_encryption(tmp_path, tf, label):
    (tmp_path / "main.tf").write_text(tf)
    found = [f for f in _scan_text(tmp_path, "x.txt", "", ["resource_policy"])
             if "unencrypted" in f.rule_id]
    assert not found, f"{label}: a wired-up key is what encryption looks like"


def test_an_empty_key_reference_is_not_encryption(tmp_path):
    """A reference to nothing wires nothing up."""
    for val in ('""', '"none"', "null"):
        d = tmp_path / val.strip('"')
        d.mkdir(exist_ok=True)
        (d / "main.tf").write_text(
            f'resource "aws_ebs_volume" "v" {{\n  size = 10\n  kms_key_id = {val}\n}}\n')
        found = [f for f in run_scan([str(d)], load_config(None), only=["resource_policy"],
                                     use_adapters=False).active()
                 if "unencrypted-volume" in f.rule_id]
        assert found, f"kms_key_id = {val} is not a key"


def test_a_boolean_from_a_variable_is_still_not_evidence(tmp_path):
    """The distinction the fix has to preserve. A key reference is presence
    evidence; a BOOLEAN read from a variable is not, because the variable may
    be false. This is the original tfplan false positive and it must stay
    fixed."""
    (tmp_path / "main.tf").write_text(
        'resource "aws_ebs_volume" "v" {\n  size = 10\n'
        '  encrypted = var.encrypt_volumes\n}\n')
    found = [f for f in run_scan([str(tmp_path)], load_config(None),
                                 only=["resource_policy"], use_adapters=False).active()
             if "unencrypted-volume" in f.rule_id]
    assert found, "a boolean from a variable must not read as true"


def test_multicloud_fixture_is_clean_where_it_should_be(tmp_path):
    """The fixture's whole purpose: the correctly-configured half must produce
    nothing. A kind mapping added without its provider's property vocabulary
    fires on everything, and every recall number still looks perfect."""
    rep = run_scan([str(ROOT / "fixtures" / "multicloud")], load_config(None),
                   only=["resource_policy"], use_adapters=False)
    wrong = [f for f in rep.active() if ".good" in f.location.logical]
    assert not wrong, f"false positives on correct config: " \
                      f"{[(f.location.logical, f.rule_id) for f in wrong]}"
    broken = [f for f in rep.active() if ".bad" in f.location.logical]
    assert len(broken) >= 8, "the deliberately-broken half must still be caught"
    assert {f.location.logical.split("_")[0] for f in broken} >= {"azurerm", "google"}


def test_azure_and_gcp_kinds_are_normalized():
    from arbiter.graph import TF_KINDS
    for native, kind in [("azurerm_managed_disk", "block_store"),
                         ("azurerm_mssql_database", "database"),
                         ("google_compute_disk", "block_store"),
                         ("google_sql_database_instance", "database"),
                         ("google_pubsub_topic", "topic")]:
        assert TF_KINDS.get(native) == kind, native


# ---------------------------------------------------------------------------
# HCL forms that used to be dropped silently.
#
# Both of these are the same failure shape as the key-reference bug: correct
# configuration written in a legal form the parser did not handle, discarded
# without a word, and then reported as a missing setting. A parser that drops
# input is worse than one that errors, because the result still looks like an
# answer.
# ---------------------------------------------------------------------------

def test_single_line_block_is_parsed(tmp_path):
    from arbiter.graph import parse_terraform
    (tmp_path / "main.tf").write_text(
        'resource "google_compute_disk" "d" {\n  size = 10\n'
        '  disk_encryption_key { kms_key_self_link = google_kms_crypto_key.m.id }\n}\n')
    r = parse_terraform(tmp_path / "main.tf", "main.tf", "root")[0]
    assert r.get("disk_encryption_key.kms_key_self_link") == "google_kms_crypto_key.m.id"


def test_inline_map_keeps_every_key(tmp_path):
    """`tags = { Name = "x", Env = "prod" }` read as one assignment swallowed
    every key after the first into the first one's value."""
    from arbiter.graph import parse_terraform
    (tmp_path / "main.tf").write_text(
        'resource "aws_s3_bucket" "b" {\n  tags = { Name = "x", Env = "prod" }\n}\n')
    r = parse_terraform(tmp_path / "main.tf", "main.tf", "root")[0]
    assert r.get("tags") == {"Name": "x", "Env": "prod"}


def test_a_comma_inside_a_string_does_not_split_it(tmp_path):
    from arbiter.graph import parse_terraform
    (tmp_path / "main.tf").write_text(
        'resource "aws_s3_bucket" "b" {\n  tags = { Name = "a,b", Env = "prod" }\n}\n')
    r = parse_terraform(tmp_path / "main.tf", "main.tf", "root")[0]
    assert r.get("tags") == {"Name": "a,b", "Env": "prod"}


def test_multi_line_blocks_and_lists_still_work(tmp_path):
    """The relaxed sub-block pattern must not disturb the forms that worked."""
    from arbiter.graph import parse_terraform
    (tmp_path / "main.tf").write_text(
        'resource "aws_security_group" "s" {\n'
        '  ids = ["sg-1", "sg-2"]\n'
        '  ingress {\n    from_port = 80\n  }\n'
        '  ingress {\n    from_port = 443\n  }\n}\n')
    r = parse_terraform(tmp_path / "main.tf", "main.tf", "root")[0]
    assert r.get("ids") == ["sg-1", "sg-2"]
    assert r.get("ingress") == [{"from_port": 80}, {"from_port": 443}]


# ---------------------------------------------------------------------------
# The TLS rules, written from what the coverage-gap analysis actually named.
#
# Ten checkov checks that Arbiter had no counterpart for, on four providers,
# reduced to three provider-neutral rules. The fixture's correct half is the
# half that matters: a rule that cannot recognise a properly configured
# resource fires on everything and still reads 1.0000 recall.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tf,should_fire,label", [
    ('resource "azurerm_postgresql_server" "d" {\n  sku_name = "GP_Gen5_2"\n}\n',
     True, "azure pg without enforcement"),
    ('resource "azurerm_postgresql_server" "d" {\n  sku_name = "GP_Gen5_2"\n'
     '  ssl_enforcement_enabled = true\n}\n', False, "azure pg enforcing"),
    ('resource "google_sql_database_instance" "d" {\n  settings {\n'
     '    ip_configuration {\n      require_ssl = true\n    }\n  }\n}\n',
     False, "gcp requiring ssl"),
    ('resource "google_sql_database_instance" "d" {\n  settings {\n'
     '    tier = "db-f1-micro"\n  }\n}\n', True, "gcp not requiring ssl"),
])
def test_database_plaintext_connection_rule(tmp_path, tf, should_fire, label):
    d = tmp_path / label.replace(" ", "_")
    d.mkdir()
    (d / "main.tf").write_text(tf)
    rep = run_scan([str(d)], load_config(None), only=["resource_policy"],
                   use_adapters=False)
    hits = [f for f in rep.active() if "database-allows-plaintext" in f.rule_id]
    assert bool(hits) == should_fire, label


def test_a_provider_that_cannot_express_the_control_is_excluded(tmp_path):
    """Azure SQL enforces TLS unconditionally and has no property saying so. A
    rule looking for one reports every Azure SQL database ever written — the
    PersistentVolumeClaim mistake, one level finer than provider."""
    (tmp_path / "main.tf").write_text(
        'resource "azurerm_mssql_database" "d" {\n  server_id = "x"\n}\n')
    rep = run_scan([str(tmp_path)], load_config(None), only=["resource_policy"],
                   use_adapters=False)
    assert not [f for f in rep.active() if "database-allows-plaintext" in f.rule_id]


@pytest.mark.parametrize("version,should_fire", [
    ("TLS1_0", True), ("TLS1_1", True), ("1.0", True),
    ("Policy-Min-TLS-1-0-2019-07", True),
    ("TLS1_2", False), ("1.3", False), ("Policy-Min-TLS-1-2-2019-07", False),
])
def test_weak_tls_version_rule(tmp_path, version, should_fire):
    d = tmp_path / version.replace(".", "_").replace("-", "_")
    d.mkdir()
    (d / "main.tf").write_text(
        f'resource "azurerm_storage_account" "s" {{\n'
        f'  customer_managed_key = k.id\n  min_tls_version = "{version}"\n}}\n')
    rep = run_scan([str(d)], load_config(None), only=["resource_policy"],
                   use_adapters=False)
    hits = [f for f in rep.active() if "weak-tls-version" in f.rule_id]
    assert bool(hits) == should_fire, version


def test_https_redirect_rule(tmp_path):
    (tmp_path / "bad.tf").write_text(
        'resource "azurerm_app_service" "b" {\n  name = "w"\n}\n')
    (tmp_path / "good.tf").write_text(
        'resource "azurerm_app_service" "g" {\n  name = "w"\n  https_only = true\n}\n')
    rep = run_scan([str(tmp_path)], load_config(None), only=["resource_policy"],
                   use_adapters=False)
    hits = {f.location.logical for f in rep.active()
            if "no-https-redirect" in f.rule_id}
    assert "azurerm_app_service.b" in hits
    assert "azurerm_app_service.g" not in hits


def test_exclude_native_scopes_finer_than_provider():
    from arbiter.probes import _load_resource_rules
    rules = {r["id"]: r for r in _load_resource_rules()}
    assert rules["database-allows-plaintext-connections"].get("exclude_native")
    # rules that over-applied to Azure until the multicloud fixture caught them
    assert "azure" in rules["no-deletion-protection"].get("exclude_providers", [])
    assert "azure" in rules["no-object-store-logging"].get("exclude_providers", [])


def test_the_multicloud_fixture_correct_half_stays_clean_for_every_rule(tmp_path):
    """A fixture whose correct half is only correct about the rules that
    existed when it was written quietly stops being able to catch the next
    false positive."""
    rep = run_scan([str(ROOT / "fixtures" / "multicloud")], load_config(None),
                   only=["resource_policy"], use_adapters=False)
    wrong = [f for f in rep.active() if "good" in f.location.logical]
    assert not wrong, [(f.location.logical, f.rule_id) for f in wrong]
    broken = [f for f in rep.active() if "bad" in f.location.logical]
    assert len(broken) >= 15
