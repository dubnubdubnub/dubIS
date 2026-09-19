// @ts-check
/* setup.mjs — global fast-check configuration for the `property` vitest project.
 *
 * The JS twin of the Hypothesis profiles registered in tests/python/conftest.py,
 * and it makes the same trade for the same reasons:
 *
 *   local   randomly seeded. Over many runs the repo explores far more of the
 *           input space than any fixed seed would, and a local flake costs one
 *           rerun.
 *   CI      seeded. `FC_SEED` pins the run so a red CI job reproduces
 *           byte-for-byte, and a green commit never turns red on a re-run of
 *           the same code.
 *   nightly deeper and random, for finding new counterexamples on purpose.
 *
 * Every knob is an env var so a failure is reproducible without editing a file:
 *
 *   FC_NUM_RUNS=5000 npx vitest run --project property
 *   FC_SEED=1758240000 FC_PATH=12:3:4 npx vitest run --project property
 *
 * fast-check prints both `seed` and `path` with every counterexample; passing
 * them back replays that exact shrink path. See docs/property-testing.md.
 *
 * No timeout/`interruptAfterTimeLimit` is configured on purpose: a per-example
 * time limit measures the runner, not the code, and would fail correct tests on
 * a loaded self-hosted box. Vitest's own test timeout still bounds the suite.
 */

import fc from 'fast-check';

const isCI = Boolean(process.env.CI);

/** The seed CI pins to. Any fixed integer works; this one is just a date. */
const CI_SEED = 20260919;

const numRuns = Number(process.env.FC_NUM_RUNS ?? (isCI ? 200 : 100));
const seed = process.env.FC_SEED !== undefined
  ? Number(process.env.FC_SEED)
  : (isCI ? CI_SEED : undefined);

fc.configureGlobal({
  numRuns,
  ...(seed === undefined ? {} : { seed }),
  ...(process.env.FC_PATH ? { path: process.env.FC_PATH } : {}),
  // Report the whole shrink history, not just the final counterexample: knowing
  // what the search walked through is most of the diagnosis.
  verbose: fc.VerbosityLevel.Verbose,
  // Fail rather than silently thinning the search when a `.filter()` rejects too
  // much -- a property that quietly ran 3 of its 100 examples is worse than red.
  ignoreEqualValues: false,
});
