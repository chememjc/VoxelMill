# Security Policy

## Supported versions

VoxelMill is alpha software. Only the most recent release gets fixes; there are
no maintenance branches. Check your version with `voxelmill --version` and
compare it against the [latest
release](https://github.com/chememjc/VoxelMill/releases) before reporting.

## Reporting a vulnerability

Report privately through [GitHub security
advisories](https://github.com/chememjc/VoxelMill/security/advisories/new).
Please do not open a public issue for a vulnerability.

Include the version, the platform, the input file or settings that trigger it,
and what an attacker would gain. A crash on a malformed file is worth
reporting even if you cannot show more than a crash.

There is no bounty, and no guaranteed response time: this is a one-person
alpha project. You will get an acknowledgement, and the fix and its release
will be described in the advisory.

## What is actually exposed

VoxelMill is a desktop tool, not a service. It listens on no port. The
attack surface that matters is **file parsing**, because the files come from
elsewhere:

- STL (binary and ASCII), and STEP by way of FreeCAD,
- GOO and CTB, both written and re-opened for verification,
- `.voxmil` projects, and TOML printer/resin profiles,
- settings and reports as JSON.

Malformed input in any of those is in scope, including out-of-bounds reads,
unbounded allocation from an attacker-controlled length field, and anything
reachable in the pybind11-bound C++ in `voxelmill._native`, where memory
safety is not the language's problem to solve.

Also in scope: the `resources.post_slice_hook` setting runs a command, so a
project or profile that arrives from someone else can run code. Treat a
`.voxmil` or `.ptr` file from a stranger the way you would treat a script.

## What is out of scope

- The printer network client (`voxelmill monitor`) talks to a device on your
  LAN over the manufacturer's own protocol. Weaknesses in that protocol are
  the printer vendor's, not VoxelMill's, though we will document them.
- Release binaries are unsigned and say so. Gatekeeper and SmartScreen
  warnings are expected, not a vulnerability.
- Bundled third-party libraries: report those upstream. Tell us too, so the
  bundled version can be moved.
- "The validation report says the print passed and it failed to print."
  That is a correctness bug. Open a normal issue; see `CONTRIBUTING.md`.
