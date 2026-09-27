# One-time Kaggle account setup

Owner steps, once, ~10 minutes:

1. **Sign in** at kaggle.com (Google sign-in works).
2. **Phone verification** - Settings -> Phone Verification -> Verify. Without
   this the account gets **no free GPU and no internet access** in kernels,
   and both are required. This is the only step that needs the owner's phone
   (one OTP).
3. **Enable T4x2 once manually** - create any notebook, Settings ->
   Accelerator -> "GPU T4 x2", run one cell. Selecting T4x2 through the API
   alone is unreliable (known Kaggle issue); doing it once on the web makes
   the setting stick for later CLI-pushed kernels.
4. **API token** - Settings -> API -> "Create New Token" downloads
   `kaggle.json`. This file is a **secret**: store it in the vault, never in
   chat or in the repo. Machines that submit jobs place it at
   `~/.kaggle/kaggle.json` (mode 600).
5. **State dataset** (first submit does this automatically if missing):
   a private dataset `kagglecompute-state` holding the budget ledger.

Verify-at-first-session items (the runner logs them):
- exact accelerator ID string for T4x2 in kernel metadata
- whether T4x2 drains the weekly quota at 1x or 2x -> set
  `quota.drain_multiplier` in `config/farm.config.yaml` accordingly
