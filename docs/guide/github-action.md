# Repro in GitHub Actions

Repro's Action reproduces each pull request and puts its card on it: a check run named
`Repro`, one comment that each run updates, the job's summary, and, on a public repository, a
signed attestation of the card.

It is listed on GitHub Marketplace as
[Repro Reproduction Card](https://github.com/marketplace/actions/repro-reproduction-card).

## Use it
```yaml
name: Repro
on: pull_request
permissions:
  contents: read
  checks: write            # the check run
  pull-requests: write     # the comment
  id-token: write          # signing the card (public repositories)
  attestations: write
  artifact-metadata: write
jobs:
  repro:
    runs-on: ubuntu-latest
    steps:
      - uses: DuhItzAniket/Repro@<ref>
```

`<ref>` is a release tag from `v1.1.0` on, the first with the Action, or a commit's hash. A
hash is the safer pin: a tag can be moved, a hash cannot.

The job needs no `actions/checkout`: Repro fetches the pull request into a mirror of its own
and runs it from its git objects. It needs a Linux runner with Docker, which `ubuntu-latest`
has: the Action runs the pull request's code in containers only (L3), never in a contained
process on the runner, which would run as the job's user beside its token. With gVisor
installed on the runner, `docker-runtime: runsc` runs the target's containers under it (L4).

## What it does
1. **Plans**: is the pull request from this repository or a fork, may the card be posted, may it
   be signed.
2. **Reproduces** the pull request (`repro run --pr`): its head against where it left its base
   branch.
3. **Keeps the card** as the artifact `repro-card`: `card.json`, `card.md` and the attestation's
   predicate.
4. **Reports** it (`repro report`): the check run (VERIFIED `success`, FLAGGED `failure`,
   UNVERIFIABLE `neutral`), the comment, the job's summary. A rerun updates them.
5. **Signs** `card.json` with Repro's predicate through `actions/attest`, on a public repository
   when the card was posted. Artifact attestations are available in public repositories on
   every plan, and in private ones on GitHub Enterprise Cloud only [actions/attest README,
   checked 2026-10-17].

## Inputs and outputs
| Input | Default | What it does |
|---|---|---|
| `token` | `github.token` | Reads the pull request, and reports with |
| `post-on-forks` | `false` | Posts for a fork's pull request too (below) |
| `attest` | `true` | Signs the card (public repositories) |
| `agents` | `off` | `claude`, `groq` or `openai` judge the pull request's claims; their key comes from the job's environment |
| `docker-runtime` | none | `runsc`: the target's containers under gVisor (L4) |
| `analyse-only` | `false` | Only analyses the change, running none of its code |

Outputs: `status` (the card's), `run-id`, `card` (the path of `card.json`) and, when signed,
`attestation-url`.

## Pull requests from forks
Under `pull_request`, a fork's pull request gets a read-only token, so the Action cannot post to
it: the run still happens, and its card goes to the job's summary and the `repro-card` artifact.

Posting for forks is your choice. It takes `pull_request_target`, whose token can write even for a
fork's pull request [GitHub docs, "Workflow syntax", `permissions`, checked 2026-10-17], and
`post-on-forks: true`:

```yaml
on: pull_request_target
# ... the same permissions ...
    steps:
      - uses: DuhItzAniket/Repro@<hash>
        with:
          post-on-forks: true
          docker-runtime: runsc   # if the runner has gVisor
```

That job holds a write token while it runs the fork's code. Repro runs that code only in its
containers, never on the runner, and the token is in neither the containers' environment nor
Repro's copies of the code; an escape from a container would still reach the token. In that
workflow, add no `actions/checkout` of the pull request and no step that runs its code.

## Checking a signed card
Download the `repro-card` artifact, then:

```bash
gh attestation verify card.json --repo <owner>/<repo> --predicate-type https://github.com/DuhItzAniket/Repro/blob/main/docs/attestation/reproduction-card/v0.md
```

`gh attestation verify` accepts only SLSA provenance unless told the predicate type [GitHub CLI
manual, `gh attestation verify`, checked 2026-10-17]. The signed subject is `card.json`, the
card's canonical JSON, whose SHA-256 the predicate also records; the predicate holds the head
commit, the event log's digest and the evidence the card cites
([the predicate](../attestation/reproduction-card/v0.md)).

## Agents
To have the pull request's claims judged, set `agents` and give the job the backend's key from a
secret. The other settings are the job's environment, as for `repro run`
([the settings](../../engine/README.md#settings)). Claude, with every role on one model:

```yaml
      - uses: DuhItzAniket/Repro@<ref>
        with:
          agents: ${{ secrets.ANTHROPIC_API_KEY != '' && 'claude' || 'off' }}
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          REPRO_AGENT_MODEL: claude-haiku-5-5
```

Any OpenAI-compatible endpoint:

```yaml
      - uses: DuhItzAniket/Repro@<ref>
        with:
          agents: openai
        env:
          REPRO_AGENT_BASE_URL: https://api.openai.com/v1
          REPRO_AGENT_MODEL: <model>
          REPRO_AGENT_API_KEY_ENV: OPENAI_API_KEY
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
```

The first example turns the agents off when the secret is not set, so the job still runs. A
workflow triggered from a fork is given no secret but `GITHUB_TOKEN` [GitHub docs, "Using
secrets in GitHub Actions", checked 2026-10-17], so a fork's pull request runs without agents.

## Caching
Repro's mirror of the repository and its test maps are cached between runs, keyed by the pull
request's base commit, so a later run fetches less and maps base's tests only when base changed.
