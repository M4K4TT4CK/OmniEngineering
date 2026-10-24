#!/usr/bin/env bash
#
# Downloads the practice repositories used for training.
#
# Four groups, on purpose:
#   - repositories built to be insecure, where finding a lot is the right answer
#   - repositories written carefully by people who know the tools, where almost
#     anything found is the rule's fault, not the code's
#   - reference templates and teaching samples, which are neither: they are
#     short on purpose, so findings there are correct about the file and say
#     nothing about how noisy a rule is
#
#   - a gold standard: the best-run project per language by measurable signals
#     (OpenSSF Scorecard, Best Practices badge, foundation stewardship, CI),
#     each pinned to one commit so a benchmark number means the same thing on
#     every machine and every night. `tools/gold_benchmark.py` scores it.
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

# Gold standard: one exact commit, no history, nothing executed. Hooks are
# disabled, LFS is not smudged and submodules are never touched, because
# these trees are read and nothing else -- see tools/gold_benchmark.py.
# Fields: name url sha language. tools/gold_benchmark.py parses these lines, so
# this script stays the one place a pin is written down.
get_pinned() {
  local name="$1" url="$2" sha="$3" dir="$DEST/$1"
  if [ "$(git -C "$dir" rev-parse HEAD 2>/dev/null)" = "$sha" ]; then
    printf '  have %s @ %s\n' "$name" "${sha:0:12}"
    return
  fi
  rm -rf "$dir"
  mkdir -p "$dir"
  if git -C "$dir" -c init.defaultBranch=gold init -q \
     && git -C "$dir" remote add origin "$url" \
     && GIT_LFS_SKIP_SMUDGE=1 git -C "$dir" -c core.hooksPath=/dev/null \
          fetch --quiet --depth 1 --no-tags origin "$sha" 2>/dev/null \
     && GIT_LFS_SKIP_SMUDGE=1 git -C "$dir" -c core.hooksPath=/dev/null \
          -c advice.detachedHead=false checkout --quiet FETCH_HEAD; then
    printf '  got  %s @ %s\n' "$name" "${sha:0:12}"
  else
    rm -rf "$dir"
    printf '  FAILED %s @ %s (skipping)\n' "$name" "${sha:0:12}"
  fi
}

# Django applications, pinned like the gold group but not part of it: they
# are where `csrf_exempt`, sessions and JWTs are used, which the gold
# libraries (Django itself included) only define. Field-tested evidence for
# the Python CWE rules (ARB-062), triaged by hand; `tools/gold_benchmark.py`
# reads only `get_pinned` lines, so these are never budgeted.
# DJANGO_APPS_ONLY=1 fetches this group alone, into its own directory.
get_pinned_app() { get_pinned "$@"; }
if [ "${DJANGO_APPS_ONLY:-0}" = "1" ]; then
echo "Django applications (sessions, csrf_exempt) — pinned, not gold:"
get_pinned_app py-zulip           https://github.com/zulip/zulip.git 4bae1c54f50c6ef523d69f084b47aed1fcc57c4e python
get_pinned_app py-healthchecks    https://github.com/healthchecks/healthchecks.git 47415f03c8c6da20b1a7a57cdb86260ab23b47a6 python
get_pinned_app py-django-cms      https://github.com/django-cms/django-cms.git 827b45987cd14aab4b0ac3f4bd20dcf553eabadb python
echo
echo "Downloaded to $DEST ($(du -sh "$DEST" 2>/dev/null | cut -f1))"
exit 0
fi

# GOLD_ONLY=1 fetches the gold group alone, into its own directory.
if [ "${GOLD_ONLY:-0}" != "1" ]; then

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

# The language packs look for timeouts, retries, worker loops, locks and query
# building. A clean library that never does those things proves nothing, so
# these are well-run projects whose whole job is exactly that code.
echo "Carefully maintained — HTTP clients, job workers, retries, pools:"
get py-httpx         https://github.com/encode/httpx.git
get py-rq            https://github.com/rq/rq.git
get py-tenacity      https://github.com/jd/tenacity.git
get ts-got           https://github.com/sindresorhus/got.git
get ts-bullmq        https://github.com/taskforcesh/bullmq.git
get ts-fastify       https://github.com/fastify/fastify.git
get go-retryablehttp https://github.com/hashicorp/go-retryablehttp.git
get go-nsq           https://github.com/nsqio/nsq.git
get go-chi           https://github.com/go-chi/chi.git
get java-hikaricp    https://github.com/brettwooldridge/HikariCP.git
get java-resilience4j https://github.com/resilience4j/resilience4j.git
get ruby-sidekiq     https://github.com/sidekiq/sidekiq.git
get ruby-faraday     https://github.com/lostisland/faraday.git

fi

# The fourth group. Chosen by evidence recorded in tools/gold/repos.json, not
# by reputation; pinned so the benchmark is repeatable. Overlap with the groups
# above is deliberate only where noted there (none at present).
echo "Gold standard — exemplary code per language, pinned:"
get_pinned py-urllib3             https://github.com/urllib3/urllib3.git ef8b211bc9cc688a7d68242fbfb583f545793293 python
get_pinned py-celery              https://github.com/celery/celery.git d6d754e21e353629c22803edfc2093f3deeffe9d python
get_pinned py-django              https://github.com/django/django.git 2fff3e9f493a9c85fb8a2294358c6751575ee4bf python
get_pinned py-aiohttp             https://github.com/aio-libs/aiohttp.git 72a735c6a43fb6b846ad67e53a042b7bfb9556ab python
get_pinned py-cryptography        https://github.com/pyca/cryptography.git 1b01d635193fe41c697418d3be80fbb45bc3519d python
get_pinned ts-undici              https://github.com/nodejs/undici.git 25543f5bd5744ff673016ed89b4bb09455e4875a typescript
get_pinned ts-nest                https://github.com/nestjs/nest.git d5d7e2c89f46b7f0f76e1d588bb092b41b4b926e typescript
get_pinned ts-jose                https://github.com/panva/jose.git 2cddc59c0592a851876a717ae39c59d41751cd72 typescript
get_pinned ts-otel-js             https://github.com/open-telemetry/opentelemetry-js.git 12a5817ad147c186a0e45a6637abd5abf423727a typescript
get_pinned ts-axios               https://github.com/axios/axios.git fceeb10997ea71778ad8bc9671ad53e9195a707c typescript
get_pinned go-caddy               https://github.com/caddyserver/caddy.git bfdbeb4a1bf7e267b0b93070ed854b847beef581 go
get_pinned go-containerd          https://github.com/containerd/containerd.git 2c2a63f43253bda805d83b6f31c70443ffe783ec go
get_pinned go-coredns             https://github.com/coredns/coredns.git 3cbe6b5269be2eae6d4d2404d7371c9a24660f85 go
get_pinned go-grpc                https://github.com/grpc/grpc-go.git 679b224109d3373eb0e4d80b6e41aafc8d295b6d go
get_pinned go-etcd                https://github.com/etcd-io/etcd.git 7ff040cb84c9944d0c38a9f0dc9f12ed9c58223b go
get_pinned java-netty             https://github.com/netty/netty.git 154e87c6b8fdacd7510fbe01d45bbce7018bc604 java
get_pinned java-httpclient        https://github.com/apache/httpcomponents-client.git acb0cd33fd77d1f4164c4848ae607000b557ac8c java
get_pinned java-tomcat            https://github.com/apache/tomcat.git 6ba25c848c84f4b076220c5e0578a1cbb9063ed0 java
get_pinned java-spring-security   https://github.com/spring-projects/spring-security.git 2ad2ec631b52c2f6318c4f186e599b93cc711bee java
get_pinned java-keycloak          https://github.com/keycloak/keycloak.git d7b7d660babd29495322bca8c65f528872dad18b java
get_pinned ruby-rails             https://github.com/rails/rails.git 3f0b675158e8284241bb1213c00375bc1fb9c52f ruby
get_pinned ruby-rack              https://github.com/rack/rack.git 412b7e5250612e8b23095eee65ee3268addb4776 ruby
get_pinned ruby-puma              https://github.com/puma/puma.git d53529399bc250915374eebe3ae54dd9fd9a6cb0 ruby
get_pinned ruby-rubygems          https://github.com/rubygems/rubygems.git f033df93676007227d1ae0bd7326008e6396b3e9 ruby

# Ground truth for recall: test suites where every case is labelled vulnerable
# or not. Fetched, never redistributed (OWASP Benchmark is GPL), never built.
if [ "${GOLD_BENCHMARKS:-1}" = "1" ]; then
echo "Vulnerability benchmarks with ground truth — pinned:"
get_pinned owasp-benchmark-java   https://github.com/OWASP-Benchmark/BenchmarkJava.git a0f07b8451902de4e3ff37aa03e40f4aeb3b4dbe java
get_pinned owasp-benchmark-python https://github.com/OWASP-Benchmark/BenchmarkPython.git f1291485808b66e20ddb6b01b10dc71b3df8c8ba python
fi

echo
echo "Downloaded to $DEST ($(du -sh "$DEST" 2>/dev/null | cut -f1))"
