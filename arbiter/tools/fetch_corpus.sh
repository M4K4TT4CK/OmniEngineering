#!/usr/bin/env bash
#
# Downloads the practice repositories used for training.
#
# Three groups, on purpose:
#   - repositories built to be insecure, where finding a lot is the right answer
#   - repositories written carefully by people who know the tools, where almost
#     anything found is the rule's fault, not the code's
#   - reference templates and teaching samples, which are neither: they are
#     short on purpose, so findings there are correct about the file and say
#     nothing about how noisy a rule is
#
# Without the second group there is no way to tell "this rule works" apart from
# "this rule fires on everything". Without the third kept separate, the second
# group's numbers are wrong -- teaching samples were four fifths of what looked
# like noise.
#
# Every stack needs a repository in the first two groups. A rule covering a
# language with no broken counterpart cannot be measured at all.
#
set -uo pipefail
DEST="${1:-/tmp/corpus}"
mkdir -p "$DEST"

# How much history to keep. Scanning needs none of it -- a single commit is
# enough to read files from -- but fix-pair mining needs it, and that is the one
# source of ground truth in the whole system that Arbiter did not generate
# itself: a commit where a maintainer changed code a rule fired on, after which
# it stopped firing. At depth 1 `tools/fixpairs.py` skips every repository as
# shallow, which is what it did on every run until 2026-09-13. 300 is the figure
# its own message recommends, and it buys enough history for the message filter
# to find candidates in repositories that commit often.
DEPTH="${DEPTH:-300}"

# Catch up a clone made when the depth was smaller, including one restored from
# a cache. Recorded rather than measured, because a repository with fewer than
# DEPTH commits in total is already as deep as it can get and would otherwise be
# re-fetched every night for nothing.
deepen() {
  local repo="$1" marker="$1/.git/arbiter-depth"
  [ -d "$repo/.git" ] || return 0
  [ -f "$marker" ] && [ "$(cat "$marker" 2>/dev/null)" = "$DEPTH" ] && return 0
  if [ -f "$repo/.git/shallow" ]; then
    git -C "$repo" fetch --quiet --deepen="$DEPTH" 2>/dev/null || true
  fi
  printf '%s' "$DEPTH" > "$marker" 2>/dev/null || true
}

get() {
  local name="$1" url="$2"
  if [ -d "$DEST/$name" ]; then
    deepen "$DEST/$name"
    printf '  have %s\n' "$name"
    return
  fi
  if git clone --depth "$DEPTH" -q "$url" "$DEST/$name" 2>/dev/null; then
    printf '%s' "$DEPTH" > "$DEST/$name/.git/arbiter-depth" 2>/dev/null || true
    printf '  got  %s\n' "$name"
  else
    printf '  FAILED %s (skipping)\n' "$name"
  fi
}

echo "Built to be insecure:"
get terragoat        https://github.com/bridgecrewio/terragoat.git
get cfngoat          https://github.com/bridgecrewio/cfngoat.git
get nodegoat         https://github.com/OWASP/NodeGoat.git
get kustomizegoat    https://github.com/bridgecrewio/kustomizegoat.git
get nodejs-goof      https://github.com/snyk-labs/nodejs-goof.git
get kubernetes-goat  https://github.com/madhuakula/kubernetes-goat.git
get sadcloud         https://github.com/nccgroup/sadcloud.git
get cdkgoat          https://github.com/bridgecrewio/cdkgoat.git
get pygoat           https://github.com/adeyosemanputra/pygoat.git
get webgoat          https://github.com/WebGoat/WebGoat.git
get railsgoat        https://github.com/OWASP/railsgoat.git
get dvwa             https://github.com/digininja/DVWA.git
get juice-shop       https://github.com/juice-shop/juice-shop.git
get vulhub           https://github.com/vulhub/vulhub.git
get govwa            https://github.com/0c34/govwa.git
get go-test-bench    https://github.com/Contrast-Security-OSS/go-test-bench.git

echo "Carefully maintained — infrastructure in production use:"
get tf-aws-s3-bucket https://github.com/terraform-aws-modules/terraform-aws-s3-bucket.git
get tf-aws-vpc       https://github.com/terraform-aws-modules/terraform-aws-vpc.git
get tf-aws-iam       https://github.com/terraform-aws-modules/terraform-aws-iam.git
get k8s-metrics-srv  https://github.com/kubernetes-sigs/metrics-server.git
get argo-cd          https://github.com/argoproj/argo-cd.git
get traefik          https://github.com/traefik/traefik.git

echo "Teaching material and reference templates — reported, never scored:"
get cfn-templates    https://github.com/aws-cloudformation/aws-cloudformation-templates.git
get cdk-examples     https://github.com/aws-samples/aws-cdk-examples.git
get k8s-examples     https://github.com/kubernetes/examples.git
get helm-charts      https://github.com/prometheus-community/helm-charts.git
get compose-awesome  https://github.com/docker/awesome-compose.git

echo "Carefully maintained — one per language:"
get requests         https://github.com/psf/requests.git
get flask            https://github.com/pallets/flask.git
get pipx             https://github.com/pypa/pipx.git
get express          https://github.com/expressjs/express.git
get ts-zod           https://github.com/colinhacks/zod.git
get go-cobra         https://github.com/spf13/cobra.git
get tf-provider-rand https://github.com/hashicorp/terraform-provider-random.git
get rust-ripgrep     https://github.com/BurntSushi/ripgrep.git
get cpp-json         https://github.com/nlohmann/json.git
get java-gson        https://github.com/google/gson.git
get ruby-sinatra     https://github.com/sinatra/sinatra.git
get php-guzzle       https://github.com/guzzle/guzzle.git
get r-stringr        https://github.com/tidyverse/stringr.git
get shell-nvm        https://github.com/nvm-sh/nvm.git

echo
echo "Downloaded to $DEST ($(du -sh "$DEST" 2>/dev/null | cut -f1))"
