# OpenPnP Startup Script — Auto-run test job then exit.
# Jython 2.7 — runs inside OpenPnP's embedded Jython interpreter.

from java.io import File
from javax.swing import SwingUtilities
import time
import traceback

def run_job():
    """Load and run the test job, then exit."""
    try:
        import os
        job_path = os.environ.get("OPENPNP_TEST_JOB")
        if not job_path:
            print("STARTUP: No OPENPNP_TEST_JOB set, skipping auto-run")
            return

        print("STARTUP: Loading job from %s" % job_path)
        job_file = File(job_path)
        if not job_file.exists():
            print("STARTUP: Job file not found: %s" % job_path)
            from java.lang import System
            System.exit(1)
            return

        # Load the job
        job = config.loadJob(job_file)
        print("STARTUP: Job loaded, starting run...")

        # Get the job panel from the GUI and start the job
        job_tab = gui.getJobTab()
        job_tab.setJob(job)
        time.sleep(1)  # Let GUI settle

        # Start the job
        job_tab.jobStart()

        # Poll for job completion
        processor = machine.getPnpJobProcessor()
        for _ in range(300):  # 5 minute timeout
            time.sleep(1)
            if not processor.isRunning():
                break

        print("STARTUP: Job finished, exiting")
        time.sleep(2)  # Let any final events fire

        from java.lang import System
        System.exit(0)

    except Exception:
        print("STARTUP: Error running job:")
        traceback.print_exc()
        from java.lang import System
        System.exit(1)

# Run on the Swing EDT after a delay to let OpenPnP fully initialize
class JobRunner(object):
    def run(self):
        run_job()

# Delay to let OpenPnP finish initialization
import threading
def delayed_start():
    time.sleep(5)
    SwingUtilities.invokeLater(JobRunner())

t = threading.Thread(target=delayed_start)
t.daemon = True
t.start()
