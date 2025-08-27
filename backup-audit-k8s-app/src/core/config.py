import os
from typing import Set, Dict


class Config:
    """Configuration management for audit logger"""

    def __init__(self):
        self.helm_tracking = os.getenv("HELM_TRACKING", "true").lower() == "true"
        self.log_level = os.getenv("LOG_LEVEL", "INFO").upper()
        self.port = int(os.getenv("PORT", "8080"))
        self.enable_sync_job = os.getenv("ENABLE_SYNC_JOB", "true").lower() == "true"
        self.sync_interval = int(os.getenv("SYNC_INTERVAL", "300"))

    def get_excluded_namespaces(self) -> Set[str]:
        """Get list of excluded namespaces"""
        excluded = set()

        # Environment variables
        excluded_env = os.environ.get("EXCLUDED_NAMESPACES", "")
        if excluded_env:
            excluded.update(excluded_env.split(","))

        release_namespace = os.environ.get("RELEASE_NAMESPACE", "")
        if release_namespace:
            excluded.add(release_namespace)

        # System namespaces
        system_namespaces = [
            "kube-system",
            "kube-public",
            "kube-node-lease",
            "default",
            "audit-system",
        ]
        excluded.update(system_namespaces)

        return excluded

    def get_git_config(self) -> Dict[str, str]:
        """Get Git configuration from environment"""
        return {
            "remote_url": os.environ.get("GIT_REMOTE_URL", ""),
            "branch": os.environ.get("GIT_BRANCH", "master"),
            "user_name": os.environ.get("GIT_USER_NAME", "audit-logger"),
            "user_email": os.environ.get(
                "GIT_USER_EMAIL", "audit-logger@kubernetes.local"
            ),
            "ssh_key_path": os.environ.get(
                "GIT_SSH_KEY_PATH", "/home/audit/.ssh/id_rsa"
            ),
            "ssh_host": os.environ.get("GIT_SSH_HOST", "git.storiette.ro"),
            "ssh_hostname": os.environ.get("GIT_SSH_HOSTNAME", "git.storiette.ro"),
            "ssh_port": os.environ.get("GIT_SSH_PORT", "1022"),
            "ssh_user": os.environ.get("GIT_SSH_USER", "git"),
        }
