# OpenPnP Startup Script — Auto-run test job then exit.
# Jython 2.7 — runs inside OpenPnP's embedded Jython interpreter.
#
# Drives the PnP job processor directly via initialize() + next() loop.
# The GUI's jobStart() doesn't work reliably in headless/test mode, so we
# call the processor API directly on the EDT.
#
# IMPORTANT: The next() loop runs on the EDT. Job.Placement.Complete events
# are fired synchronously during next() calls, so event scripts execute
# inline (including their HTTP calls to dubIS). invokeAndWait ensures the
# background thread blocks until the job finishes before calling System.exit.

from java.io import File
from java.lang import Runnable, System
from javax.swing import SwingUtilities
import time
import traceback


# Set by run_job() so delayed_start() can verify the job actually ran.
_job_ran = False
_job_steps = 0
_last_step_time = [0]  # mutable container so watchdog can read it


def log(msg):
    System.out.println("STARTUP: " + msg)
    System.out.flush()


def dismiss_welcome_dialog():
    """Find and dismiss the Welcome to OpenPnP dialog if present."""
    from java.awt import Window
    from javax.swing import JDialog
    try:
        for w in Window.getWindows():
            if isinstance(w, JDialog):
                title = w.getTitle()
                if title and "Welcome" in title:
                    log("Dismissing Welcome dialog")
                    w.dispose()
                    return True
        return False
    except Exception as e:
        log("Error dismissing Welcome dialog: %s" % str(e))
        return False


def run_job():
    """Load and run the job on the EDT using direct processor API."""
    global _job_ran, _job_steps
    try:
        dismiss_welcome_dialog()

        import os
        job_path = os.environ.get("OPENPNP_TEST_JOB")
        if not job_path:
            log("No OPENPNP_TEST_JOB set, skipping auto-run")
            return

        log("Loading job from %s" % job_path)
        job_file = File(job_path)
        if not job_file.exists():
            log("Job file not found: %s" % job_path)
            System.exit(1)
            return

        # Enable the machine (connect to NullDriver)
        if not machine.isEnabled():
            log("Enabling machine...")
            machine.setEnabled(True)
            log("Machine enabled")

        # Home the machine
        log("Homing machine...")
        machine.home()
        log("Machine homed")

        # Load the job and set it on the job tab
        job = config.loadJob(job_file)
        log("Job loaded")
        job_tab = gui.getJobTab()
        job_tab.setJob(job)

        # Drive the processor directly: initialize + step loop
        proc = machine.getPnpJobProcessor()
        proc.initialize(job)
        log("Processor initialized, running job...")

        _last_step_time[0] = time.time()
        step_count = 0
        max_steps = 500  # safety limit
        while step_count < max_steps:
            step_count += 1
            _last_step_time[0] = time.time()
            has_more = proc.next()
            if not has_more:
                break

        _job_steps = step_count
        _job_ran = True
        log("Job complete: %d steps" % step_count)

    except Exception as e:
        log("Error running job: %s" % str(e))
        traceback.print_exc(file=System.out)
        System.exit(1)


class JobRunner(Runnable):
    def run(self):
        run_job()


log("Startup.py loaded")

import threading

WATCHDOG_STALL_SECONDS = 30  # Kill if proc.next() blocks for this long


def _watchdog():
    """Kill the process if proc.next() stalls (macOS NullDriver hang workaround)."""
    while not _job_ran:
        time.sleep(5)
        last = _last_step_time[0]
        if last > 0 and (time.time() - last) > WATCHDOG_STALL_SECONDS:
            log("WATCHDOG: proc.next() stalled for %ds, forcing exit (code 2)"
                % int(time.time() - last))
            System.exit(2)


def delayed_start():
    log("Waiting 10s for OpenPnP initialization...")
    time.sleep(10)
    dismiss_welcome_dialog()
    time.sleep(1)

    # invokeAndWait blocks until run_job() finishes on the EDT.
    # Previously invokeLater was used, which caused a race: System.exit(0)
    # could fire before the EDT picked up the job, producing 0 decreases.
    log("Starting job on EDT (invokeAndWait)...")
    SwingUtilities.invokeAndWait(JobRunner())
    log("Job finished on EDT (ran=%s, steps=%d)" % (_job_ran, _job_steps))

    if not _job_ran:
        log("ERROR: Job did not run — exiting with code 1")
        System.exit(1)

    # Brief grace period for any async cleanup
    time.sleep(2)
    log("Exiting with code 0")
    System.exit(0)

t = threading.Thread(target=delayed_start)
t.daemon = True
t.start()

wd = threading.Thread(target=_watchdog)
wd.daemon = True
wd.start()
