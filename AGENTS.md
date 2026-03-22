This repository is small, script-driven, and intentionally low-dependency.
Agentic coding work should stay conservative: preserve existing behavior, keep changes focused, and prefer direct reasoning over framework-heavy rewrites.

## Repository Overview

- `widevine_patch.py`: standalone Python 3 ELF patcher for `libwidevinecdm.so`.
- `install.sh`: POSIX shell installer for Chromium, Widevine extraction, and system integration.
- `uninstall.sh`: inverse of the installer; removes files and restores package state.
- `tests/unit/`: pytest-based unit tests for the Python patcher.
- `tests/unit/fixtures/minimal_elf.py`: synthetic ELF fixture generator used by unit tests.
- `tests/integration/test_installer.bats`: Bats integration tests for the installer.
- `tests/e2e/test_playback.py`: Playwright-based smoke tests for real hardware/browser playback.

## Rules Sources

- No `.cursorrules` file exists in the repository.
- No `.cursor/rules/` directory exists in the repository.
- No `.github/copilot-instructions.md` file exists in the repository.
- Therefore, this file is the primary agent instruction source inside the repo.

## Project Shape

- Language/runtime: Python 3 and POSIX `sh`.
- Test stack: `pytest`, `bats`, and `playwright`.
- Build system: none; package manager metadata is absent.
- Prefer minimal patches and preserve the current single-file utility architecture.
- Avoid new dependencies unless clearly necessary.
- Treat `install.sh` and `uninstall.sh` as system-touching code: explicit, reversible, careful.

## Build, Run, And Test Commands

There is no compile/build step in the conventional sense. The main workflows are direct script execution and tests.

### Primary Script Execution

- Run patcher help path: `python3 widevine_patch.py`
- Run patcher on a binary: `python3 widevine_patch.py input.so output.so`
- Run patcher with debug logging: `python3 widevine_patch.py --debug input.so output.so`
- Run installer: `sudo ./install.sh`
- Run installer in verbose mode: `sudo ./install.sh -v`
- Run uninstaller: `sudo ./uninstall.sh`
- Run uninstaller in verbose mode: `sudo ./uninstall.sh -v`

### Unit Tests

- Run all unit tests: `pytest tests/unit/ -q`
- Run one test file: `pytest tests/unit/test_widevine_patch.py -q`
- Run one test class: `pytest tests/unit/test_widevine_patch.py::TestAtomicRelocs -q`
- Run one specific test: `pytest tests/unit/test_widevine_patch.py::TestAtomicRelocs::test_both_atomic_relocs_rewritten -q`
- Run tests matching a substring: `pytest tests/unit/test_widevine_patch.py -k relr -q`
- Show stdout while debugging: `pytest tests/unit/test_widevine_patch.py -q -s`

### Integration Tests

- Run installer integration suite: `bats tests/integration/test_installer.bats`
- Run a filtered Bats test: `bats tests/integration/test_installer.bats --filter 'fails with non-zero exit on non-aarch64 architecture'`
- Useful when iterating on installer behavior: run the full Bats file after any `install.sh` change.

### End-To-End Tests

- Install Playwright first if needed: `pip install playwright`
- Install browser support: `playwright install chromium`
- Run all E2E tests: `pytest tests/e2e/ -q`
- Run headed E2E tests: `pytest tests/e2e/ -q --headed`
- Run one E2E test: `pytest tests/e2e/test_playback.py::test_spotify_loads_without_drm_error -q -s`

### Suggested Validation Matrix

- If you change `widevine_patch.py`, run at least `pytest tests/unit/test_widevine_patch.py -q`.
- If you change `install.sh`, run `bats tests/integration/test_installer.bats`.
- If you change browser-installation, playback, or user-facing verification guidance, consider `pytest tests/e2e/ -q` on appropriate hardware.

## Formatting And Linting

- No formal linter or formatter is configured in the repo.
- Do not invent a repo-wide formatting sweep.
- Match the surrounding file's style instead of enforcing a new one.

## Python Style Guidelines

- Target plain Python 3 without external dependencies.
- Keep `widevine_patch.py` self-contained.
- Prefer top-level helper functions over classes unless state management becomes unavoidable.
- Use descriptive snake_case names for functions and variables.
- Use ALL_CAPS for ELF constants, offsets, and other protocol-level constants.
- Keep low-level binary helpers short and obvious.
- Prefer explicit integer math and offsets over clever abstractions.
- Favor readability over DRY when manipulating binary layouts; duplicated clarity is acceptable here.
- Preserve existing script-style CLI flow in `widevine_patch.py` unless the user asks for modularization.

## Python Imports

- Group imports as: standard library, third-party, then local test-only imports.
- Keep imports simple and static when possible.
- In tests, the existing `sys.path.insert(...)` fixture import pattern is acceptable because the fixture is not packaged.
- Avoid adding optional imports to production code unless they are guarded and necessary.

## Python Formatting Conventions

- Follow the local file's quote style; do not normalize unrelated strings.
- Keep lines readable; wrap long assertions/messages with parentheses or continued expressions.
- Use blank lines to separate phases and helper sections, as the current files do.
- Retain the current section-divider comment style if editing files that already use it.

## Types And Data Handling

- Existing production code is untyped; do not add type hints only for style.
- Be explicit about byte/offset/value domains.
- Distinguish carefully between file offsets, virtual addresses, sizes, and counts.
- Name transformed values clearly, for example `*_off`, `*_va`, `*_sz`, `*_file`.

## Assertions, Errors, And Exit Behavior

- In `widevine_patch.py`, use `assert` for structural invariants and impossible states discovered while parsing.
- Use explicit `print(...)` plus `sys.exit(1)` for CLI usage errors or final failure paths.
- Keep failure messages actionable and specific to the broken invariant.
- Do not silently ignore corruption or malformed ELF structures.
- In shell scripts, fail fast with `set -e` and explicit preflight checks.
- In shell scripts, print user-facing `ERROR:` messages before exiting.
- Preserve warnings when behavior may still succeed but is unsupported or risky.

## Testing Style Guidelines

- Add or update tests with behavior changes whenever practical.
- Prefer narrow, behavior-focused pytest tests over giant end-to-end unit assertions.
- Keep helper functions in tests local to the file unless they are truly shared.
- Maintain the current layered testing model: unit for ELF rewriting, Bats for installer flow, E2E for real playback smoke checks.

## Shell Script Guidelines

- Write POSIX `sh`, not Bash-specific syntax, unless the file is explicitly Bash.
- Quote variable expansions unless unquoted behavior is required.
- Use uppercase names for exported/defaulted environment variables and script-level configuration.
- Use lowercase names for short-lived local shell variables.
- Keep install/uninstall steps linear and well-separated by section headers.
- Prefer explicit checks and clear messages over compact but opaque shell tricks.
- Preserve the current `Q`/`log` helpers when extending installer scripts.
- Any new filesystem or package-manager action in `install.sh` should have a corresponding uninstall consideration.

## Naming Conventions

- Python functions/variables: `snake_case`.
- Python constants: `UPPER_SNAKE_CASE`.
- Shell environment/config variables: `UPPER_SNAKE_CASE`.
- Shell helper/local variables: short, descriptive, lowercase.
- Test names should read like expected behavior, not implementation details.

## Documentation Expectations

- Update `README.md` when installation flow, requirements, version pins, or verification steps change.
- Keep command examples copy-pasteable.
- If behavior differs between README and tests, treat that as a bug to resolve.
- Document new environment overrides in both code comments and README when user-facing.

## Change Safety Notes

- Be careful with anything that touches package installation, symlinks, `/usr`, `/etc`, or `/var/lib` paths.
- Avoid making the installer more stateful unless removal logic is updated too.
- Do not assume x86_64 support; the project is intentionally aarch64-specific.
- Do not remove the mocked/testable seams in installer code without replacing test coverage.
