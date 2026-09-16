"""Keep the test suite out of production state.

Measured 2026-08-31: running `pytest` appended `ok:false reason:not_connected`
rows to the LIVE data/rotation_runs.jsonl, because trader.notify resolves its
paths relative to the module and the tests import it directly. The rotation
verifier then read those rows, judged the day FAILED and fired a real alert
about runs that never happened.

Redirect the ledger and alert log to a per-session temp directory. Set here at
import time, before any test imports trader.notify, and read by notify at CALL
time so import order cannot defeat it.
"""
import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="stock-trader-tests-")
os.environ["ROTATION_LEDGER_PATH"] = os.path.join(_TMP, "rotation_runs.jsonl")
os.environ["ROTATION_ALERT_LOG"] = os.path.join(_TMP, "rotation_alerts.jsonl")
# The cron log too. Without this, test_execution_quality drives _complete_fills
# straight into data/rotation_cron.log, and those lines are indistinguishable
# from a real run when read back weeks later.
os.environ["ROTATION_CRON_LOG"] = os.path.join(_TMP, "rotation_cron.log")
