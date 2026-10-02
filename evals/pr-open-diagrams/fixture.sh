#!/bin/sh
# scaffold_script (case.yaml context.scaffold_script, --scaffold only): runs before Claude starts, outside the
# sandbox, in the empty scratch workspace. Writes the one file prompt.md tells the agent to read: a small,
# self-contained unified diff standing in for "the diff" the prompt asks for a data-flow diagram of, so a
# response can be judged on whether its diagram actually reflects this diff rather than on generic advice.
set -eu

cat > change.diff <<'DIFF'
--- a/ingest.py
+++ b/ingest.py
@@ -1,8 +1,8 @@
-from worker import process_record
+from queue_client import publish


 def ingest(record):
-    process_record(record)
+    publish("records.process", record)
--- a/worker.py
+++ b/worker.py
@@ -1,2 +1,7 @@
+def handle_message(message):
+    record = message.body
+    store(record)
+
+
 def process_record(record):
     store(record)
DIFF
