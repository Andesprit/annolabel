# Security policy

Security fixes target the latest published release. Older versions do not receive separate backports; upgrade to the latest patch release in the supported minor series, or follow migration instructions when a fix requires a new minor series.

Use [GitHub private vulnerability reporting](https://github.com/Andesprit/annolabel/security/advisories/new) for suspected vulnerabilities. Include affected versions, reproduction steps, and impact. Do not attach private research images or credentials. Maintainers triage reports as availability allows; no response-time SLA is offered.

AnnoLabel runs locally and opens files with the permissions of its caller. It is not a sandbox for agents or untrusted image decoders. Agent providers have their own data policies. Dataset provenance files can contain original source paths; inspect them before sharing.

Ordinary crashes, labeling quality problems, and feature requests belong in the public issue tracker unless they expose a security issue.
