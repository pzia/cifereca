# Browser Recorder — Chromium CDN cancellation/retry acceptance (#2628)

## CI fixture (opt-in, isolated only)

`tests/test_core_p0_2628_real_cdn_cancel_retry_e2e.py` exercises **the
official Python/Node Playwright installer** through the Core API. It is
disabled unless `CIFERECA_REAL_CDN_CANCEL_E2E=1` is explicitly set.

The dedicated `Browser Recorder · synthetic Chromium E2E` GitHub Actions
job provisions Playwright Python in a temporary venv and uses a **fresh,
disposable HOME**. Its steps:

1. Confirm no existing Chromium cache and no implicit installation.
2. Call `install_chromium(consent=True)` and cancel while its real
   Playwright installer process is running.
3. Verify the process has exited, no launchable browser remains and the
   Core lock is released.
4. Without deleting the incomplete cache, explicitly retry the official
   installer, then confirm version-coupled Chromium can launch.

**Scope:** tests a real CLI cancellation and recovery, not proof that
downloaded CDN bytes had already arrived when cancellation was issued.
A fully reproducible mid-payload disconnection still requires its own
fixture. The Linux GTK workflow separately tests physical XTEST consent
and cancellation; this test does not represent a human UX review.

**Privacy:** no provider, real credentials, browser profile, HAR, archive,
screenshots or private session are used. Playwright stdout and stderr are
discarded. The test does not touch the developer's ordinary browser cache.
Do not enable this fixture against a real user HOME or unreviewed mirror.
