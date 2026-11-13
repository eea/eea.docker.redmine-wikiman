#!/usr/bin/env python3

import os
import sys
import json
import logging
import ssl
import queue
import threading
import time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

# Add /app to Python path so we can import our modules
sys.path.insert(0, "/app")

# Set up logging
log_level = os.getenv("LOG_LEVEL", "INFO").upper()
if log_level not in ["DEBUG", "INFO", "WARNING", "ERROR"]:
    log_level = "INFO"

logging.basicConfig(
    level=getattr(logging, log_level),
    format="%(asctime)s %(levelname)s [%(filename)s:%(lineno)d][%(funcName)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("/app/audit-webhook.log"),
    ],
)

logger = logging.getLogger(__name__)

# Global variables
storage_path = "/app/logs"
startup_complete = False
# Bounded queue (M5): a webhook flood or a slow/stuck event worker must not
# grow memory without limit. Full queue -> drop the OLDEST event (logged);
# the periodic full sync resyncs the cluster, so a dropped event is
# self-healing.
WEBHOOK_QUEUE_MAXSIZE = max(1, int(os.getenv("WEBHOOK_QUEUE_MAXSIZE", "1000")))
webhook_queue = queue.Queue(maxsize=WEBHOOK_QUEUE_MAXSIZE)
webhook_dropped_count = 0
startup_lock = threading.Lock()
process_start_time = time.time()

# Initialize components directly
event_processor = None
scheduler = None
upgrade_notifier = None

# Readiness tuning (the old /health logic, now served on /ready): how long to
# give initial sync before readiness starts failing, and how many missed
# cleanup cycles of git push failures to tolerate (one blip shouldn't flip
# the probe, sustained failure should). Liveness (/health) is deliberately
# separate: it must stay green while the listener answers, because a
# dependency outage (GitLab) cannot be fixed by killing the container -
# that produced the 2026-11-09 restart storm (RESILIENCE_REVIEW.md).
HEALTH_STARTUP_GRACE_SECONDS = int(os.getenv("HEALTH_STARTUP_GRACE_SECONDS", "120"))
CLEANUP_INTERVAL = int(os.getenv("CLEANUP_INTERVAL", "120"))
HEALTH_PUSH_FAILURE_GRACE_CYCLES = 2


def get_liveness_status():
    """Liveness: is the process alive and able to serve HTTP?

    Only a truly dead listener should fail this. Dependency health (git
    push) and startup state live on /ready, where they can make the pod
    NotReady (no webhook traffic) without triggering a restart.
    Returns (alive, reason)."""
    if event_processor is None or scheduler is None:
        return True, "components not initialized (listener up)"
    return True, None


def get_readiness_status():
    """Determine whether the service is actually able to do its job, not
    just whether the HTTP listener is up. Returns (ready, reason)."""
    elapsed = time.time() - process_start_time

    if event_processor is None or scheduler is None:
        return False, "components not initialized"

    if not startup_complete:
        if elapsed < HEALTH_STARTUP_GRACE_SECONDS:
            return True, None  # still within startup grace period
        return False, f"startup did not complete within {HEALTH_STARTUP_GRACE_SECONDS}s"

    git_manager = getattr(scheduler, "git_manager", None)
    if git_manager is not None and git_manager.last_push_error:
        push_failure_threshold = CLEANUP_INTERVAL * HEALTH_PUSH_FAILURE_GRACE_CYCLES
        last_success = git_manager.last_push_success
        # F2: no successful push ever recorded? Measure the failure age from
        # process start, not from "forever" (inf). The old code returned 503
        # immediately for a fresh process whose remote was down - the
        # intended grace never applied at startup (2026-11-09 restart storm).
        origin = last_success.timestamp() if last_success else process_start_time
        seconds_since_success = time.time() - origin
        if seconds_since_success > push_failure_threshold:
            return False, (
                f"git push failing since "
                f"{last_success.isoformat() if last_success else 'startup'}: "
                f"{git_manager.last_push_error}"
            )

    return True, None


def initialize_components():
    """Initialize event processor and scheduler with one shared set of
    managers. GitManager/ArchiveManager (and their locks) must be shared
    between the webhook event path and the scheduler's sync/cleanup jobs -
    building two independent sets here previously let both paths run git
    operations concurrently against the same working tree with no real
    mutual exclusion between them."""
    global event_processor, scheduler, upgrade_notifier

    from core.config import Config
    from integrations.git_manager import GitManager
    from integrations.kubernetes_manager import KubernetesManager
    from managers.storage_manager import StorageManager
    from managers.archive_manager import ArchiveManager
    from processors.resource_processor import ResourceProcessor
    from processors.helm_processor import HelmProcessor
    from processors.event_processor import EventProcessor
    from scheduler import AuditScheduler

    # Initialize configuration
    config = Config()
    archive_lock = threading.Lock()

    # Initialize managers
    git_manager = GitManager(storage_path)
    k8s_manager = KubernetesManager()
    storage_manager = StorageManager(storage_path)
    archive_manager = ArchiveManager(
        storage_path, git_manager, archive_lock, config, k8s_manager
    )

    # Initialize processors. ResourceProcessor shares archive_lock with
    # ArchiveManager so a resource write can't interleave with a concurrent
    # archive/move/delete of the same directory tree.
    resource_processor = ResourceProcessor(storage_path, config, archive_lock)
    helm_processor = HelmProcessor(k8s_manager, storage_path, archive_lock)

    # Initialize event processor directly with git_manager
    event_processor = EventProcessor(
        resource_processor,
        helm_processor,
        archive_manager,
        storage_manager,
        git_manager,
    )

    # Initialize scheduler against the same shared managers
    scheduler = AuditScheduler(
        storage_path,
        config,
        git_manager,
        k8s_manager,
        storage_manager,
        archive_manager,
        resource_processor,
        helm_processor,
    )

    # Upgrade notifier (#310015): best-effort background loop, out of the
    # admission hot path. Inert unless enabled AND a webhook URL is set.
    if config.upgrade_notify_enabled and config.upgrade_notify_webhook_url:
        from integrations.upgrade_notifier import UpgradeNotifier

        notes_resolver = None
        if config.upgrade_notify_release_notes:
            from integrations.release_notes import ReleaseNotesResolver

            notes_resolver = ReleaseNotesResolver(config.github_token)
        upgrade_notifier = UpgradeNotifier(
            k8s_manager,
            config,
            config.upgrade_notify_webhook_url,
            config.upgrade_notify_cluster,
            config.upgrade_notify_interval,
            config.upgrade_notify_cluster_url,
            notes_resolver=notes_resolver,
        )
        logger.info("Upgrade notifier enabled (starts after initial sync)")
    elif config.upgrade_notify_enabled:
        logger.warning(
            "UPGRADE_NOTIFY_ENABLED is true but UPGRADE_NOTIFY_WEBHOOK_URL is empty; "
            "upgrade notifications stay disabled"
        )

    logger.info("Components initialized successfully")


class AuditWebhookHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Don't log health check requests
        if self.path not in ("/health", "/ready"):
            logger.info(f'HTTP: "{format % args}"')

    def do_GET(self):
        """Handle health check requests.

        /health = liveness (listener alive; stays up during GitLab outages,
        slow syncs, startup). /ready = readiness (startup complete, git push
        healthy within grace). The chart wires livenessProbe to /health and
        readinessProbe to /ready. Both report the process memory (M1)."""
        from core.memory import read_memory

        if self.path in ("/health", "/ready"):
            if self.path == "/health":
                ok, reason = get_liveness_status()
                status = "alive" if ok else "dead"
            else:
                ok, reason = get_readiness_status()
                status = "ready" if ok else "not ready"
            self.send_response(200 if ok else 503)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            response = {
                "status": status,
                "timestamp": time.time(),
                "memory": read_memory(),
            }
            if reason:
                response["reason"] = reason
            self.wfile.write(json.dumps(response).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        """Handle admission webhook requests"""
        try:
            # Parse URL
            parsed_url = urlparse(self.path)
            path = parsed_url.path

            if path == "/audit":
                self._handle_audit_request()
            else:
                self.send_response(404)
                self.end_headers()

        except Exception as e:
            logger.error(f"Error handling POST request: {e}")
            self.send_response(500)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            response = {"error": str(e)}
            self.wfile.write(json.dumps(response).encode())

    def _handle_audit_request(self):
        """Handle audit webhook requests with queuing during sync"""
        try:
            # Read request body
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)

            # Parse JSON
            request_data = json.loads(body.decode("utf-8"))

            # Always send immediate response first
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()

            response = {
                "apiVersion": "admission.k8s.io/v1",
                "kind": "AdmissionReview",
                "response": {
                    "uid": request_data.get("request", {}).get("uid", ""),
                    "allowed": True,
                },
            }
            self.wfile.write(json.dumps(response).encode())

            # Always enqueue — queue guarantees order (bounded; M5)
            enqueue_webhook_event(request_data)

        except Exception as e:
            logger.error(f"Error handling audit request: {e}", exc_info=True)
            # Send error response
            self.send_response(500)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            response = {"error": str(e)}
            self.wfile.write(json.dumps(response).encode())


def enqueue_webhook_event(request_data):
    """Enqueue an admission event; drop the OLDEST event when full (M5).

    The bounded queue guarantees a webhook flood (or a stuck worker) can
    never grow memory without limit. Drops are counted and logged; the
    periodic full sync resyncs the cluster, so a dropped event is
    self-healing."""
    global webhook_dropped_count
    try:
        webhook_queue.put_nowait(request_data)
        return
    except queue.Full:
        pass
    try:
        webhook_queue.get_nowait()
        webhook_dropped_count += 1
        logger.warning(
            f"Webhook queue full (maxsize={WEBHOOK_QUEUE_MAXSIZE}), "
            f"dropped oldest event (total dropped: {webhook_dropped_count})"
        )
    except queue.Empty:
        pass
    try:
        webhook_queue.put_nowait(request_data)
    except queue.Full:
        webhook_dropped_count += 1
        logger.error(
            f"Webhook queue still full after dropping oldest, "
            f"dropped new event (total dropped: {webhook_dropped_count})"
        )


def process_queued_requests():
    """Continuously process webhook_queue in strict order"""
    while True:
        request_data = webhook_queue.get()  # blocks until item is available

        try:
            if not event_processor:
                logger.warning("Event processor not ready, waiting...")
                # Put it back and wait
                webhook_queue.put(request_data)
                time.sleep(1)
                continue

            event_processor.process_admission_event(request_data)
        except Exception as e:
            logger.error(f"Failed to process admission event: {e}", exc_info=True)
        finally:
            webhook_queue.task_done()


def startup_sequence():
    """Perform startup sequence: git operations and initial sync"""
    global startup_complete, scheduler, upgrade_notifier

    try:
        logger.info("Starting startup sequence...")

        # Start scheduler and initial sync (this will handle git operations)
        logger.info("Starting audit scheduler...")
        if scheduler:
            scheduler.start_initial_sync()
            scheduler.start_cleanup_job()
            # Periodic full resync, gated by ENABLE_SYNC_JOB (default on).
            # Without this, a resource that disappears and reappears while
            # the service is running is only picked back up by a live
            # webhook event or a restart - this closes that gap.
            scheduler.start_sync_job()
        else:
            logger.error("Scheduler not initialized")
            return

        # Wait for initial sync to complete
        while not scheduler.is_initial_sync_complete():
            logger.info("Waiting for initial sync to complete...")
            time.sleep(5)

        # Process any queued requests
        threading.Thread(target=process_queued_requests, daemon=True).start()

        # Mark startup as complete
        startup_complete = True
        logger.info("Startup sequence completed successfully")

        # Start the upgrade notifier only once the initial sync is done, so
        # its cluster-wide scans don't stack API load on top of the sync.
        # Its first (seed) cycle is silent, so no notification replays on
        # restart.
        if upgrade_notifier:
            upgrade_notifier.start()

    except Exception as e:
        logger.error(f"Startup sequence failed: {e}")
        startup_complete = True  # Mark as complete to allow webhook to function


def main():
    # Initialize components BEFORE starting the server
    logger.info("Initializing components...")
    initialize_components()
    logger.info("Components initialized, starting server...")

    # Start the webhook server immediately
    port = int(os.getenv("PORT", "8080"))

    # Setup HTTPS server with SSL certificates. ThreadingHTTPServer (F3):
    # a slow request can never block /health or /ready behind it.
    server = ThreadingHTTPServer(("0.0.0.0", port), AuditWebhookHandler)

    # Load SSL certificate
    cert_file = "/etc/webhook/certs/tls.crt"
    key_file = "/etc/webhook/certs/tls.key"

    if os.path.exists(cert_file) and os.path.exists(key_file):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert_file, key_file)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        logger.info(f"Starting HTTPS audit webhook server on port {port}")
    else:
        logger.error(
            "SSL certificates not found at /etc/webhook/certs/tls.crt and /etc/webhook/certs/tls.key"
        )
        logger.error("Cannot start webhook server without SSL certificates")
        sys.exit(1)

    # Start startup sequence in background
    startup_thread = threading.Thread(target=startup_sequence, daemon=True)
    startup_thread.start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down server...")
        server.shutdown()
        if upgrade_notifier:
            upgrade_notifier.stop()
        if scheduler:
            scheduler.shutdown()


if __name__ == "__main__":
    main()
