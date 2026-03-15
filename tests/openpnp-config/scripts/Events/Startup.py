# OpenPnP Startup Script — Auto-run test job then exit.
# Jython 2.7 — runs inside OpenPnP's embedded Jython interpreter.
#
# Drives the PnP job processor directly via initialize() + next() loop.
# The GUI's jobStart() doesn't work reliably in headless/test mode, so we
# call the processor API directly on the EDT.
#
# IMPORTANT: The next() loop runs on the EDT. Job.Placement.Complete events
# are fired synchronously during next() calls, so event scripts execute
# inline. The background thread's wait_and_exit() gives time for any
# async HTTP calls from event scripts before calling System.exit(0).

from java.io import File
from java.lang import Runnable, System
from javax.swing import SwingUtilities
import time
import traceback


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

        step_count = 0
        max_steps = 500  # safety limit
        while step_count < max_steps:
            step_count += 1
            has_more = proc.next()
            if not has_more:
                break

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

def delayed_start():
    log("Waiting 10s for OpenPnP initialization...")
    time.sleep(10)
    dismiss_welcome_dialog()
    time.sleep(1)
    log("Starting job on EDT...")
    SwingUtilities.invokeLater(JobRunner())
    # Wait for event scripts (HTTP calls to dubIS) to complete
    log("Waiting 10s for event processing...")
    time.sleep(10)
    log("Exiting with code 0")
    System.exit(0)

t = threading.Thread(target=delayed_start)
t.daemon = True
t.start()
