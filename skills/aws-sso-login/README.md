# aws-sso-login

Gets AWS access through the SSO device-code flow the user approves in their own browser; afterwards `aws` and
`kubectl` work in the session.

**Needs:** `systems.aws_sso`; facts `aws.profile` (the SSO profile names), `aws.cluster` (the EKS clusters those
profiles reach) and `aws.account` (the account ids, for the verify step). All from the env store — never typed
into the skill.

**Without it:** `aws-sso-login: not applicable here — aws_sso is false`, one line, stop. A failing `aws`
call on such a machine is reported as is.

**Example:**

> **user:** `kubectl get pods` fails with "SSO session … expired or is otherwise invalid"
>
> **claude:** starts `aws sso login --no-browser` in the background, hands over the URL and the device code,
> waits for the approval, verifies with `aws sts get-caller-identity`, then re-runs the command.
