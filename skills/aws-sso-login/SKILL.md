---
name: aws-sso-login
description: Get AWS access via the SSO device-code flow: starts `aws sso login --no-browser` in the background, hands the user the URL and code to approve, verifies, then kubectl/aws work. Use whenever aws/kubectl fails with "SSO session … expired or is otherwise invalid" or before any stage/prod AWS or EKS check.
compatibility: "Designed for Claude Code; needs aws_sso (systems.*)"
metadata:
  version: "7"
  updated: "2026-09-26"
  reviewed: "2026-09-25"
  requires: "aws_sso"
  facts: "aws.profile,aws.cluster,aws.account"
---

# aws-sso-login — device-code login the user approves in their browser

Where the session's machine has no browser (a sandbox, a remote shell), `aws sso login --no-browser` runs in
device-code mode: the CLI prints a URL + code, the user approves it in their own browser, the CLI polls until
approved and writes the token cache. The same flow works on a machine with a browser. Environment background
(which role sees what) belongs in `.context/reference/tools-access.md`, never here.

## Profiles (legacy per-profile SSO style — keep it that way)

Profile names and account ids are env facts (`aws.md` in the store), never literals here. Resolve them first
(`K="python3 $BATON/context-db/bin/kb.py"`; `$K list aws` shows every row):

| fact | what it is | used by |
|---|---|---|
| `$K get aws.profile stage` | the stage role | stage SSM/S3 reads, the stage kube context |
| `$K get aws.profile prod` | the prod role (usually read-only) | the prod kube context |
| `$K get aws.profile <other>` | any further role the environment lists | whatever its `purpose` column says |
| `$K get aws.cluster stage` / `aws.cluster prod` | the kubectl context per account | `kubectl --context …` |
| `$K get aws.account stage` / `aws.account prod` | the account ids behind them | only when a check needs the id itself |

A missing fact (`$K get` exits 1): `/env-init aws.profile <name>` (then `aws.cluster <name>` / `aws.account <name>`,
which read the profile row) — the `aws` discovery manifest runs `aws configure list-profiles`, `aws sts get-caller-identity
--profile <p>` and `aws eks list-clusters`, verifies each candidate as it says, and writes the row back with
`--from tool:aws`; only "which profile is stage / prod" is asked, once, when several fit. Never type a profile or
context from memory into `$K set`.
Below, `<stage>` / `<prod>` stand for the resolved profile names and `<stage-ctx>` for the kube context.

When all profiles share one Identity Center start URL, **one device-code login (any profile) mints a token that serves every profile** — after the stage login, `aws sts get-caller-identity --profile <prod>` just works, no second code. The device code expires after ~10 min; an approval that lands after that fails with `invalid_grant: Invalid device code` and you must restart from step 1.

## Procedure (three tool calls)

1. **Start the login in the background, under a pty** — the CLI buffers everything when piped, so
   without `script` the code never appears:
   ```
   Bash(run_in_background=true, timeout=600000):
     cd <scratchpad> && script -qefc "aws sso login --profile <stage> --no-browser" /dev/null > sso-login.out 2>&1
   ```
2. **Read the code** (poll the file for a few seconds, strip `\r`):
   ```
   for i in $(seq 1 20); do grep -q 'user_code=' <scratchpad>/sso-login.out && break; sleep 1; done
   tr -d '\r' < <scratchpad>/sso-login.out | grep -E 'user_code=|^[A-Z]{4}-[A-Z]{4}$'
   ```
3. **Tell the user** in the reply, bold, nothing else needed from them:
   `Please approve the stage SSO login: <the device URL the CLI printed, with user_code=XXXX-XXXX> (code XXXX-XXXX, profile <stage>)`.
   The background task completes when they approve (notification arrives; the output ends with
   `Successfully logged into Start URL`). The code expires after ~10 min — if the task ends with an
   error, start over from step 1.

Then **verify before asserting anything**:
```
aws sts get-caller-identity --profile <stage> --query Arn --output text
kubectl --context <stage-ctx> -n <namespace> get pods
```

## Gotchas (all hit before)

- `[sso-session]` config style fails with `invalid_grant: Invalid device code` against some
  Identity Center setups — profiles must carry `sso_start_url`/`sso_region`/`sso_account_id`/`sso_role_name`
  directly. Don't "modernise" `~/.aws/config`.
- After an identity-provider group change: `rm -rf ~/.aws/sso/cache` then log in again — the cached token does
  not pick up new roles.
- The root `Makefile` target `aws_sso_login` uses `--sso-session aws-cli` and is for the user's own
  machine, not for a session.
- Never print SecureString values (SSM) into the transcript — list parameter names, compare in-shell.
- Install AWS CLI v2 with the official installer, never `pip` (system pythons are PEP-668-locked); a
  rebuilt machine needs it again — `command -v aws` before the first call.

## What each role can and cannot see

Namespaces, exec-able containers, resources a role is Forbidden to read (and how to infer state it cannot
read) are environment knowledge — keep them in `.context/reference/tools-access.md`, never here. Verify a role's reach with `kubectl auth can-i` before asserting it.
