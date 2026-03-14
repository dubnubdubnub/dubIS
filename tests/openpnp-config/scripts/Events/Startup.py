# OpenPnP Startup Script — Auto-run test job then exit.
# Jython 2.7 — runs inside OpenPnP's embedded Jython interpreter.

from java.io import File
from java.lang import Runnable, System
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

        # Wait for job completion. With a NullDriver the job finishes quickly,
        # but we poll the processor state to be safe.
        processor = machine.getPnpJobProcessor()
        for _ in range(120):  # 2 minute timeout
            time.sleep(1)
            try:
                # OpenPnP 2.6+
                if not processor.isRunning():
                    break
            except AttributeError:
                try:
                    # OpenPnP 2.4: check state enum
                    state = str(processor.getState())
                    if state in ("Stopped", "ERROR"):
                        break
                except Exception:
                    # Last resort: just wait 30s for NullDriver job to finish
                    time.sleep(29)
                    break

        print("STARTUP: Job finished, exiting")
        time.sleep(2)  # Let any final events fire
        System.exit(0)

    except Exception as e:
        import sys
        print("STARTUP: Error running job: %s" % str(e))
        try:
            import StringIO
            sio = StringIO.StringIO()
            traceback.print_exc(file=sio)
            print("STARTUP: Traceback: %s" % sio.getvalue())
        except Exception:
            traceback.print_exc(file=sys.stdout)
        System.exit(1)

# Explicitly implement Runnable for Jython compatibility
class JobRunner(Runnable):
    def run(self):
        run_job()

# Delay to let OpenPnP finish initialization.
# The test harness sends Enter key via xdotool to dismiss the first-run dialog.
# After that, the EDT is free and invokeLater will work.
import threading
def delayed_start():
    time.sleep(10)
    print("STARTUP: Scheduling job runner on EDT...")
    SwingUtilities.invokeLater(JobRunner())

t = threading.Thread(target=delayed_start)
t.daemon = True
t.start()
