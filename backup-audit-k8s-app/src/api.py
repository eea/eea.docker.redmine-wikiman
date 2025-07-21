#!/usr/bin/env python3

import os
import sys
import json
import logging
import ssl
import queue
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
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
webhook_queue = queue.Queue()
startup_lock = threading.Lock()

# Initialize components directly
event_processor = None
scheduler = None


def initialize_components():
    """Initialize event processor and scheduler directly"""
    global event_processor, scheduler

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
    archive_manager = ArchiveManager(storage_path, git_manager, archive_lock, config)

    # Initialize processors
    resource_processor = ResourceProcessor(storage_path, config)
    helm_processor = HelmProcessor(k8s_manager, storage_path)

    # Initialize event processor directly with git_manager
    event_processor = EventProcessor(
        resource_processor,
        helm_processor,
        archive_manager,
        storage_manager,
        git_manager,
    )

    # Initialize scheduler
    scheduler = AuditScheduler(storage_path)

    logger.info("Components initialized successfully")


class AuditWebhookHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Don't log health check requests
        if self.path != "/health":
            logger.info(f'HTTP: "{format % args}"')

    def do_GET(self):
        """Handle health check requests"""
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            response = {"status": "healthy", "timestamp": time.time()}
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

            # Always enqueue — queue guarantees order
            webhook_queue.put(request_data)

        except Exception as e:
            logger.error(f"Error handling audit request: {e}", exc_info=True)
            # Send error response
            self.send_response(500)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            response = {"error": str(e)}
            self.wfile.write(json.dumps(response).encode())


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
    global startup_complete, scheduler

    try:
        logger.info("Starting startup sequence...")

        # Start scheduler and initial sync (this will handle git operations)
        logger.info("Starting audit scheduler...")
        if scheduler:
            scheduler.start_initial_sync()
            scheduler.start_cleanup_job()
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

    # Setup HTTPS server with SSL certificates
    server = HTTPServer(("0.0.0.0", port), AuditWebhookHandler)

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
        if scheduler:
            scheduler.shutdown()


if __name__ == "__main__":
    main()
