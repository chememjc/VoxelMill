# Handoff — VoxelMill portable alpha (2026-09-18)

Power was lost mid-session. **Do not tag a release until the new artifacts
below have been retested.** Tree is clean on `master`.

## Goal (from the user)

1. Finish the portable packaging plan (Linux AppImage + Mac DMGs + Windows zip on GitHub Actions).
2. Download and test **Linux AppImage** (this machine) and **macOS Intel DMG** (iMac). Do not compile on the iMac.
3. If those work, make an **alpha release**. User originally asked to call it **`v5.0.0`**. Later message said “v0.5.0”; the committed package version is **`5.0.0`**. Confirm the tag name before tagging. **Do not tag until the 5fe0edc artifacts are smoke-tested.**
4. Attach binaries to the GitHub Release with install instructions for Linux / macOS / Windows (already drafted).
5. Explain GitHub Actions usage limits and how to see current usage.
6. Linux AppImage must be the GitHub-built one, not only the local 0.3.0 image.
7. Explain how to sign the Mac app so Gatekeeper stops complaining.

## Repo / machine

| | |
|---|---|
| Repo | `/home3/voxelmill` on **wopr** (`192.168.1.104` wifi, also `192.168.0.2` eth) |
| GitHub | public `chememjc/VoxelMill`, default branch **master** |
| HEAD | `5fe0edca06072fcc2b028afa643ba9c4553db637` (pushed) |
| Package version | `5.0.0` in `pyproject.toml` and `src/voxelmill/__init__.py` |
| Existing tags | `v0.3.0`, `v0.4.0` only — **no v5 tag yet** |
| gh CLI | `/usr/bin/gh` 2.4.0, logged in as `chememjc` |
| iMac | `mcurtis@192.168.1.195` (`Lorraines-iMac.local`), **Intel x86_64**, macOS 26.7. SSH key already authorized. Sleeps and drops TCP; ping may work while port 22 is dead — wake it first. |

Working tree is clean. Uncommitted `todo.md` edits from earlier were included in `88dd632`.

## Do this next (in order)

The **correct** artifacts are from the green run on HEAD, **not** the earlier successful-but-buggy run.

```sh
# 1. Download from run 35303910671 (HEAD 5fe0edc — AppImage pth + Darwin RLIMIT_AS fixes)
mkdir -p /tmp/vm-v5b/{linux,mac}
gh run download 35303910671 --repo chememjc/VoxelMill \
  --name VoxelMill-linux-x86_64-AppImage --dir /tmp/vm-v5b/linux
gh run download 35303910671 --repo chememjc/VoxelMill \
  --name VoxelMill-macos-x86_64-dmg --dir /tmp/vm-v5b/mac

# 2. Linux smoke (this machine)
chmod +x /tmp/vm-v5b/linux/VoxelMill-x86_64.AppImage
APPIMAGE_EXTRACT_AND_RUN=1 /tmp/vm-v5b/linux/VoxelMill-x86_64.AppImage --version
# expect: VoxelMill 5.0.0   (must NOT mention /home/runner/work/...)
APPIMAGE_EXTRACT_AND_RUN=1 /tmp/vm-v5b/linux/VoxelMill-x86_64.AppImage --help
APPIMAGE_EXTRACT_AND_RUN=1 /tmp/vm-v5b/linux/VoxelMill-x86_64.AppImage \
  prepare /home3/voxelmill/fixtures/shapes/cube.stl \
  --max-passes 1 --allow-unresolved --output /tmp/vm-v5b/linux/cube-prepared.stl

# 3. Intel Mac smoke (iMac). Wake it if SSH times out.
scp /tmp/vm-v5b/mac/VoxelMill-macos-x86_64.dmg \
    /home3/voxelmill/fixtures/shapes/cube.stl \
    mcurtis@192.168.1.195:VoxelMill-alpha/
ssh mcurtis@192.168.1.195
# then:
#   hdiutil attach ~/VoxelMill-alpha/VoxelMill-macos-x86_64.dmg -nobrowse -mountpoint /tmp/VoxelMillDMG
#   rm -rf ~/VoxelMill-alpha/VoxelMill.app
#   cp -R /tmp/VoxelMillDMG/VoxelMill.app ~/VoxelMill-alpha/
#   hdiutil detach /tmp/VoxelMillDMG
#   xattr -dr com.apple.quarantine ~/VoxelMill-alpha/VoxelMill.app
#   ~/VoxelMill-alpha/VoxelMill.app/Contents/MacOS/VoxelMill --version
#   ~/VoxelMill-alpha/VoxelMill.app/Contents/MacOS/VoxelMill inspect ~/VoxelMill-alpha/in/cube.stl
#   ~/VoxelMill-alpha/VoxelMill.app/Contents/MacOS/VoxelMill prepare ~/VoxelMill-alpha/in/cube.stl \
#     --max-passes 1 --allow-unresolved --output ~/VoxelMill-alpha/out/cube-prepared.stl
```

**Only if both Linux `--version`/`prepare` and Mac `--version`/`prepare` work**, tag and let Actions publish:

```sh
# Confirm tag name with the user if ambiguous (v5.0.0 vs v0.5.0).
git tag -a v5.0.0 -m 'VoxelMill 5.0.0 alpha'
git push origin v5.0.0
# workflow trigger is v5* (and leftover v0.4*). publish job creates a prerelease
# with packaging/release-notes.md and the four binaries renamed VoxelMill-5.0.0-*.
```

Do **not** `workflow_dispatch` after tagging unless the tag run fails; tagging retriggers the whole matrix.

## What was already tested (older artifacts — do not ship these)

From run **35302787040** (`e9ea58d`, **before** the last two packaging fixes):

| Vehicle | Result |
|---|---|
| Linux AppImage | **FAIL** `FileNotFoundError: /home/runner/work/VoxelMill/VoxelMill/src/voxelmill/__init__.py` — editable `_voxelmill_editable.pth` copied into the image. GHA `--version` smoke was a false green because that path exists on the runner. |
| macOS Intel DMG on iMac | Mounts; `Mach-O 64-bit executable x86_64`. `--version` → `VoxelMill 5.0.0`. `--help` OK. `inspect` on `cube.stl` OK (12 triangles, 8000 mm³). **`prepare` FAIL** `ValueError: current limit exceeds maximum limit` in `resources.execution_limits` `setrlimit(RLIMIT_AS)`. |
| macOS arm64 / Windows | Built on GHA; not smoke-tested on hardware. |

Those bugs are fixed in **5fe0edc**. Run **35303910671** is green and has new artifacts. **They have not been downloaded or retested** (power loss).

iMac already has `~/VoxelMill-alpha/` with the *old* DMG, `in/cube.stl`, and a copied `VoxelMill.app`. Replace the DMG/app from 35303910671 before retesting.

Local Linux AppImage `output/appimage/VoxelMill-x86_64.AppImage` is the **v0.3.0** Debian-python image (`--version` → `VoxelMill 0.3.0`). Not the GitHub vehicle.

## GitHub Actions history

| Run | SHA | Result | Notes |
|---|---|---|---|
| [35300676209](https://github.com/chememjc/VoxelMill/actions/runs/35300676209) | `c0cb84f` tag `v0.4.0` | fail | PyInstaller `ValueError: too many values to unpack` — `Tree()` in `Analysis.datas`. Linux AppImage uploaded but broken (Debian stdlib + python.org binary → no `math`). |
| [35302321369](https://github.com/chememjc/VoxelMill/actions/runs/35302321369) | `88dd632` | fail | Mac arm+intel DMGs OK. Windows native: VS generator could not configure. Linux smoke: `math` (fixed earlier in `52b06f3`, not in this SHA). |
| [35302787040](https://github.com/chememjc/VoxelMill/actions/runs/35302787040) | `e9ea58d` | jobs green | All four artifacts. Linux AppImage still had editable `.pth`. Mac `prepare` still hit Darwin `RLIMIT_AS`. **Do not ship.** |
| [35303910671](https://github.com/chememjc/VoxelMill/actions/runs/35303910671) | **`5fe0edc` HEAD** | **jobs green** | linux 3m15, macos-arm 4m12, windows 6m31, macos-intel 12m42. `github-release` skipped (dispatch, not a tag). **Retest these.** |

Workflow: `.github/workflows/release.yml`

- Triggers: `workflow_dispatch` and tags `v5*` / `v0.4*`.
- Jobs: `linux-appimage` (`ubuntu-22.04`, CPython 3.10, **non-editable** `pip install '.[gui]'`), `macos-arm` (`macos-14`), `macos-intel` (`macos-15-intel`), `windows` (`windows-latest` + `vcvars64.bat` into `GITHUB_ENV`, Ninja). Mac/Win still use editable install + PyInstaller.
- Actions are Node 24 majors: `actions/checkout@v6`, `setup-python@v6`, `upload-artifact@v6`, `download-artifact@v6`. No `ilammy/msvc-dev-cmd`.
- `publish` job only on tags: downloads artifacts, names them `VoxelMill-${ver}-…`, `gh release create --prerelease --notes-file` from `packaging/release-notes.md`.

Dispatch (no tag): `gh workflow run release.yml --repo chememjc/VoxelMill --ref master`

## Commits on master this session

```
5fe0edc Keep the AppImage off the runner tree and skip Darwin RLIMIT_AS
e9ea58d Export MSVC via vcvars64 so Windows Ninja builds without Node 20
52b06f3 Stage the running CPython prefix into the AppImage
88dd632 Fix PyInstaller datas unpack and prepare 5.0.0 portables
```

Fixes inside those:

- `packaging/pyinstaller.spec` — `Analysis.datas` must be `(src, dest)` pairs, not `Tree()`.
- `scripts/build_appimage.py` — copy CPython stdlib/libpython from `sysconfig` of the **running** interpreter (not hardcoded Debian `/usr/lib/python3.10`); **do not copy `site-packages` as part of stdlib**; delete leftover `_voxelmill_editable.pth`; `_verify_staged_python` asserts `voxelmill.__file__` is under `PYTHONHOME`.
- `src/voxelmill/resources.py` — `setrlimit(RLIMIT_AS)` is **Linux-only** (Darwin raises `current limit exceeds maximum limit`).
- `src/voxelmill/pipeline.py` — `import resource` gated; `peak_rss_bytes` is bytes on Darwin, KB×1024 on Linux, `None` on Windows.
- AppImage GHA install is **not** `-e`.
- Version bump 0.4.0 → 5.0.0. README / `docs/packaging.md` / `platforms.md` / `packaging/release-notes.md` updated.

Local tests after 5fe0edc: `tests/test_appimage.py` + `tests/test_resources.py` → **6 passed**.

## GitHub Actions usage (answer for the user)

Repo is **public**. Standard GitHub-hosted runners are **free and unlimited** on public repos (Linux, Windows, and macOS). Included-minute quotas and OS multipliers **do not apply**.

If the repo were private (it is not):

| Plan | Included minutes / month | Storage |
|---|---|---|
| Free | 2,000 Linux-equivalent | 500 MB artifacts+Packages |
| Pro / Team | 3,000 | 1–2 GB |
| Enterprise | 50,000 | 50 GB |

Private-repo minutes are Linux-normalized: Windows ×2, macOS ×10. Per-minute list prices after quota: Linux 2-core **$0.006**, Windows 2-core **$0.010**, macOS 3/4-core **$0.062**. Jobs round up to the next whole minute. Larger runners never use the included quota.

This workflow is four standard jobs (one Linux, two Mac, one Windows), ~3–13 minutes each. On a public repo that costs **$0**.

**Where to see usage**

- Personal: [https://github.com/settings/billing](https://github.com/settings/billing) → product tabs / **Metered usage**. Filter product = Actions, group by SKU.
- Per run: Actions → run → **Usage** (billable minutes; multipliers are *not* in that view).
- API (needs billing scope; `gh api user/settings/billing/actions` 404’d on this token): [https://docs.github.com/en/billing/how-tos/products/view-productlicense-use](https://docs.github.com/en/billing/how-tos/products/view-productlicense-use)
- Email alerts at 90% / 100% of included usage if enabled under budgets.

## Signing the Mac binary so Gatekeeper stops complaining

Unsigned downloads get quarantine (`com.apple.quarantine`). Workaround we document:

```sh
xattr -dr com.apple.quarantine /Applications/VoxelMill.app
# or System Settings → Privacy & Security → Open Anyway
```

To actually sign so Finder double-click works after a browser download:

1. Enroll in the **Apple Developer Program** ($99/year) at [https://developer.apple.com](https://developer.apple.com).
2. In Keychain / developer.apple.com create a **Developer ID Application** certificate (not Apple Development). Keep the private key; GitHub Actions needs it as secrets.
3. **Hardened runtime** + entitlements (PyInstaller/Qt/VTK will need at least `com.apple.security.cs.allow-unsigned-executable-memory` and often `com.apple.security.cs.disable-library-validation` unless every dylib is signed).
4. Sign the `.app` (after PyInstaller, before DMG):

   ```sh
   codesign --deep --force --options runtime --timestamp \
     --entitlements packaging/macos.entitlements \
     --sign "Developer ID Application: Your Name (TEAMID)" \
     output/portable/VoxelMill.app
   codesign --verify --strict --verbose=2 output/portable/VoxelMill.app
   ```

5. **Notarize** (required for Gatekeeper on current macOS):

   ```sh
   ditto -c -k --keepParent VoxelMill.app VoxelMill.zip
   xcrun notarytool submit VoxelMill.zip \
     --apple-id YOU --team-id TEAMID --password APP_SPECIFIC_PASSWORD --wait
   xcrun stapler staple VoxelMill.app
   ```

   Prefer an API key (`notarytool --issuer --key --key-id`) stored as Actions secrets over an account password.

6. Then `hdiutil create` the DMG and staple the DMG too.

7. CI: put the `.p12` and notarization key in GitHub secrets; **never** commit them. Apple Silicon GHA runners do not have a static UDID; Intel `macos-15-intel` does if you need a development profile.

Ad-hoc `codesign -s -` does **not** help for files downloaded from the internet. Without Developer ID + notarization, `xattr` / Open Anyway is the supported path. This is follow-up work; the current alpha is unsigned on purpose.

Windows equivalent is an Authenticode cert (SmartScreen). Not started.

## Layout reminders

- Linux ship vehicle = AppImage (`scripts/make_appimage.sh` → `scripts/build_appimage.py`). **Do not** PyInstaller Linux for release.
- Mac = two thin DMGs (arm64 / x86_64), not universal2. VTK/PySide6 wheels are arch-specific.
- Windows = PyInstaller onedir zip, `VoxelMill.exe` with `console=True` so CLI subcommands work.
- CUDA is omitted (`CMAKE_ARGS=-DCMAKE_CUDA_COMPILER=`).
- iMac is Intel — test `VoxelMill-macos-x86_64.dmg`, not arm64.

## Out of scope / leftover ledger

`todo.md` still has Phase 1 `--timing`, Phase 2 items 5b/6–9, Phase 6 README architecture bump. Those are **not** this packaging task. Headline number remains 2.32 s / 350 MB.

## Contacts / commands cheat sheet

```sh
# iMac
ssh mcurtis@192.168.1.195

# latest green packaging run
gh run view 35303910671 --repo chememjc/VoxelMill

# dispatch another rehearsal (no tag)
gh workflow run release.yml --repo chememjc/VoxelMill --ref master
```
