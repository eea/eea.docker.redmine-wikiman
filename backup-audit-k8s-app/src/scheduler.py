#!/usr/bin/env python3

import os
import logging
import threading
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

# Set up logging
logger = logging.getLogger(__name__)


class AuditScheduler:
    def __init__(
        self,
        storage_path: str,
        config,
        git_manager,
        k8s_manager,
        storage_manager,
        archive_manager,
        resource_processor,
        helm_processor,
    ):
        """Schedule sync/cleanup jobs against the already-constructed, shared
        managers (built once in api.py) rather than creating a second,
        independent set. Sharing the same GitManager/ArchiveManager instances
        (and their locks) with the webhook event path is what keeps git
        operations serialized instead of racing on the same working tree."""
        self.storage_path = storage_path
        self.scheduler = BackgroundScheduler()
        self.scheduler.start()
        self.initial_sync_complete = False
        self.initial_sync_thread = None
        self.sync_lock = threading.Lock()  # Add sync lock

        from managers.sync_manager import SyncManager

        self.config = config
        self.git_manager = git_manager
        self.k8s_manager = k8s_manager
        self.storage_manager = storage_manager
        self.archive_manager = archive_manager
        self.resource_processor = resource_processor
        self.helm_processor = helm_processor

        # Initialize sync manager
        self.sync_manager = SyncManager(
            self.k8s_manager,
            self.resource_processor,
            self.helm_processor,
            self.archive_manager,
            self.storage_manager,
            self.config,
        )

    def start_initial_sync(self):
        """Start initial sync immediately with proper locking"""
        if self.initial_sync_thread and self.initial_sync_thread.is_alive():
            logger.info("Initial sync already running")
            return

        def run_initial_sync():
            try:
                with self.sync_lock:  # Lock during entire sync process
                    logger.info("Starting initial sync...")

                    # Step 1: Git pull with lock
                    logger.info("Pulling latest changes from Git...")
                    self.git_manager.setup_git_repository()

                    # Step 2: Write current state to storage
                    logger.info("Writing current Kubernetes state to storage...")
                    self.sync_manager.run_sync()

                    # Step 3: Archive missing namespaces and releases
                    logger.info("Archiving missing namespaces and releases...")
                    self.sync_manager.run_cleanup()

                    self.initial_sync_complete = True
                    logger.info("Initial sync completed successfully")

                    # Step 4: Force git commit and push with better error handling
                    logger.info("Committing initial sync changes to git...")
                    try:
                        self.git_manager.commit_changes()
                        logger.info("Git commit completed successfully")
                    except Exception as e:
                        logger.error(f"Git commit failed: {e}")

                    try:
                        self.git_manager.push_to_remote()
                        logger.info("Git push completed successfully")
                    except Exception as e:
                        logger.error(f"Git push failed: {e}")
                        # Don't let git push failure crash the application

                    logger.info("Initial sync git operations completed")

            except Exception as e:
                logger.error(f"Initial sync failed: {e}")
                # Mark as complete even if there's an error to prevent infinite retries
                self.initial_sync_complete = True

        self.initial_sync_thread = threading.Thread(
            target=run_initial_sync, daemon=True
        )
        self.initial_sync_thread.start()
        logger.info("Initial sync started in background")

    def start_cleanup_job(self):
        """Schedule cleanup job"""
        try:
            cleanup_interval = int(os.getenv("CLEANUP_INTERVAL", "120"))

            def run_cleanup_job():
                # Only run cleanup if initial sync is complete
                if self.initial_sync_complete:
                    try:
                        # Hold sync_lock (same lock start_initial_sync and
                        # run_sync_job use) so cleanup's archive/move/delete
                        # of a namespace or release directory can't overlap
                        # with a sync job actively writing resources into it.
                        with self.sync_lock:
                            # Archive missing items
                            self.sync_manager.run_cleanup()
                            # Commit and Push changes to upstream
                            logger.info("Committing changes to git...")
                            try:
                                self.git_manager.commit_changes()
                                logger.info("Git commit completed successfully")
                            except Exception as e:
                                logger.error(f"Git commit failed: {e}")

                            try:
                                self.git_manager.push_to_remote()
                                logger.info("Git push completed successfully")
                            except Exception as e:
                                logger.error(f"Git push failed: {e}")
                    except Exception as e:
                        logger.error(f"Cleanup job failed: {e}")
                        # Don't let cleanup failures crash the application

            self.scheduler.add_job(
                func=run_cleanup_job,
                trigger=IntervalTrigger(seconds=cleanup_interval),
                id="cleanup_job",
                name="Cleanup deleted resources",
                replace_existing=True,
            )
            logger.info(f"Cleanup job scheduled (every {cleanup_interval} seconds)")
        except Exception as e:
            logger.error(f"Failed to schedule cleanup job: {e}")

    def start_sync_job(self):
        """Schedule sync job if enabled"""
        if not self.config.enable_sync_job:
            logger.info("Sync job disabled by ENABLE_SYNC_JOB configuration")
            return
            
        try:
            sync_interval = self.config.sync_interval

            def run_sync_job():
                # Only run sync if initial sync is complete and no other sync is in progress
                if self.initial_sync_complete and not self.is_sync_in_progress():
                    try:
                        with self.sync_lock:  # Lock during sync process
                            logger.info("Running scheduled sync...")
                            
                            # Pull latest changes from Git
                            self.git_manager.setup_git_repository()
                            
                            # Run sync to update storage with current state
                            self.sync_manager.run_sync()
                            
                            # Commit and push changes
                            logger.info("Committing sync changes to git...")
                            try:
                                self.git_manager.commit_changes()
                                logger.info("Git commit completed successfully")
                            except Exception as e:
                                logger.error(f"Git commit failed: {e}")

                            try:
                                self.git_manager.push_to_remote()
                                logger.info("Git push completed successfully")
                            except Exception as e:
                                logger.error(f"Git push failed: {e}")
                                
                            logger.info("Scheduled sync completed successfully")
                    except Exception as e:
                        logger.error(f"Scheduled sync failed: {e}")
                        # Don't let sync failures crash the application

            self.scheduler.add_job(
                func=run_sync_job,
                trigger=IntervalTrigger(seconds=sync_interval),
                id="sync_job",
                name="Sync Kubernetes state",
                replace_existing=True,
            )
            logger.info(f"Sync job scheduled (every {sync_interval} seconds)")
        except Exception as e:
            logger.error(f"Failed to schedule sync job: {e}")

    def stop(self):
        """Stop the scheduler"""
        self.scheduler.shutdown()
        logger.info("Scheduler stopped")

    def is_initial_sync_complete(self):
        """Check if initial sync is complete"""
        return self.initial_sync_complete

    def shutdown(self):
        """Shutdown the scheduler"""
        self.scheduler.shutdown()

    def is_sync_in_progress(self):
        """Check if sync is currently in progress"""
        return self.sync_lock.locked()
