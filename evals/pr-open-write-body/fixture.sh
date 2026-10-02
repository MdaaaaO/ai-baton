#!/bin/sh
# scaffold_script (case.yaml context.scaffold_script, --scaffold only): runs before Claude starts, outside the
# sandbox, in the empty scratch workspace. Writes the one file prompt.md tells the agent to read: a small,
# self-contained unified diff standing in for "the change on my current branch" the prompt describes, so a
# response can be judged on whether it actually engages with that diff rather than on generic PR-opening advice.
set -eu

cat > change.diff <<'DIFF'
--- a/webhook_client.py
+++ b/webhook_client.py
@@ -1,13 +1,29 @@
 import time
 import requests


 class WebhookClient:
-    def __init__(self, url):
+    def __init__(self, url, max_retries=3, backoff_base=0.5):
         self.url = url
+        self.max_retries = max_retries
+        self.backoff_base = backoff_base

     def send(self, payload):
-        response = requests.post(self.url, json=payload)
-        response.raise_for_status()
-        return response
+        attempt = 0
+        while True:
+            try:
+                response = requests.post(self.url, json=payload)
+                response.raise_for_status()
+                return response
+            except requests.RequestException:
+                attempt += 1
+                if attempt > self.max_retries:
+                    raise
+                time.sleep(self.backoff_base * (2 ** (attempt - 1)))
DIFF
