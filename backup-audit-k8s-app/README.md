# Kubernetes Audit Logger

A comprehensive Kubernetes audit logging system that monitors and tracks changes to Kubernetes resources in real-time, storing them in Git for version control and historical tracking.

## Architecture Overview

This application operates as a Kubernetes admission webhook with a modular architecture:

### Entry Points
- **`src/api.py`** - Main HTTP server that handles admission webhook requests
- **`src/scheduler.py`** - Background scheduler for periodic sync and cleanup operations

### Core Components

#### Configuration & Models (`src/core/`)
- **`config.py`** - Configuration management (environment variables, excluded namespaces, Git settings)
- **`models.py`** - Data models and structures
- **`audit_logger.py`** - Main orchestrator that coordinates all audit logging operations

#### Integration Layer (`src/integrations/`)
- **`git_manager.py`** - Git operations (clone, commit, push, SSH/HTTPS authentication)
- **`kubernetes_manager.py`** - Kubernetes API client and Helm release management

#### Management Layer (`src/managers/`)
- **`storage_manager.py`** - Local file system operations and directory management
- **`archive_manager.py`** - Archiving of deleted resources for historical tracking
- **`sync_manager.py`** - Orchestrates synchronization and cleanup operations

#### Processing Layer (`src/processors/`)
- **`event_processor.py`** - Central webhook event processing (CREATE/UPDATE/DELETE)
- **`helm_processor.py`** - Specialized Helm release processing and values.yaml extraction
- **`resource_processor.py`** - General Kubernetes resource cleaning and storage

## How It Works

The audit-logger is a Kubernetes admission webhook that tracks all changes to Helm-managed applications. It intercepts resource operations (CREATE, UPDATE, DELETE) for Helm releases, snapshots their state, and saves them in a Git repository. Each Helm release gets its own folder with a `values.yaml` and a `resources/` subfolder containing all relevant resource YAMLs. Every change is versioned in Git, providing a complete, auditable history of your cluster's Helm-managed apps.

## First-Time Run: Initial Sync

On the first run, the audit-logger performs a full snapshot of all existing Helm releases in your cluster. It discovers every Helm-managed app, collects their current resource states and values.yaml, and commits them to the Git repository using the same folder structure described below. This ensures your Git repo starts with a complete, auditable baseline of your cluster's Helm-managed state.

Subsequent changes (CREATE, UPDATE, DELETE) are tracked incrementally as they happen.

---

## Git Repository Structure

```
/app/logs/
├── .git/                          # Git repository metadata
├── archived/                      # Removed namespaces and their contents
├── <namespace>/                   # Each namespace with Helm releases
│   ├── archived                   # Removed resources inside the namespaceß
│   └── <release>/                 # Each Helm release (app) as a folder
│       ├── values.yaml            # The Helm values.yaml for the release
│       └── resources/             # All tracked resource YAMLs for this release
│           ├── pod-<name>.yaml
│           ├── service-<name>.yaml
│           ├── configmap-<name>.yaml
│           └── ...                # Other resource types
│       └── deployment-info.yaml    # Chart metadata, repository, deploy command, and values
└── README.md                      # Repo info
```

- **Only Helm releases (apps) are tracked.**
- All resource YAMLs for a release are stored in the `resources/` subfolder under that release.
- The `values.yaml` for each release is stored at the root of the release folder.
- No Helm history or unrelated files are saved.
- No resource YAMLs are saved at the namespace root—only release folders.

---

## Environment Variables

The application requires several environment variables to function properly:

### Required Variables
- **`GIT_REMOTE_URL`** - Git repository URL for storing audit logs (supports SSH/HTTPS)
- **`GIT_BRANCH`** - Git branch to use (default: `master`)

### Git Authentication
For SSH repositories:
- **`GIT_SSH_KEY_PATH`** - Path to SSH key file - default: `/home/audit/.ssh/id_rsa`
- **`GIT_SSH_HOST`** - SSH host for Git operations - default: `git.storiette.ro`
- **`GIT_SSH_HOSTNAME`** - SSH hostname - default: `git.storiette.ro`
- **`GIT_SSH_PORT`** - SSH port - default: `1022`
- **`GIT_SSH_USER`** - SSH user - default: `git`

For HTTPS repositories with authentication:
- **`GIT_HTTPS_TOKEN`** - HTTPS authentication token

### Optional Configuration
- **`LOG_LEVEL`** - Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) - default: `INFO`
- **`PORT`** - HTTP server port - default: `8080`
- **`HELM_TRACKING`** - Enable/disable Helm tracking - default: `true`
- **`EXCLUDED_NAMESPACES`** - Comma-separated list of namespaces to exclude
- **`RELEASE_NAMESPACE`** - Current release namespace (auto-excluded)
- **`CLEANUP_INTERVAL`** - Cleanup interval in seconds - default: `120`
- **`GIT_USER_NAME`** - Git commit author name - default: `audit-logger`
- **`GIT_USER_EMAIL`** - Git commit author email - default: `audit-logger@kubernetes.local`

## Quick Start

1. **Build the Docker image:**
   ```sh
   docker build -t audit-logger .
   ```

2. **Set required environment variables:**
   ```sh
   export GIT_REMOTE_URL="git@github.com:yourorg/audit-logs.git"
   export GIT_BRANCH="master"
   ```

3. **Run the container with SSH key mounted:**
   ```sh
   docker run -d \
     -e GIT_REMOTE_URL="$GIT_REMOTE_URL" \
     -e GIT_BRANCH="$GIT_BRANCH" \
     -v ~/.ssh:/home/audit/.ssh:ro \
     -p 8080:8080 \
     audit-logger
   ```

4. **For Kubernetes deployment:**
   - Configure the webhook and RBAC using the separate Helm chart in `../helm-chart/`
   - Mount Kubernetes service account tokens for cluster access

---

## Example

```
/app/logs/
└── my-namespace/
    └── my-app-release/
        ├── values.yaml
        └── resources/
            ├── pod-my-app.yaml
            ├── service-my-app.yaml
            └── configmap-my-app-config.yaml
```

---

For advanced configuration, see `values.yaml` and the Helm chart documentation.

## Latest Change Tracking

For each namespace, the audit logger creates or updates a `latest-changes.json` file in `/app/logs/<namespace>/`. This file contains information about the most recent change detected by the webhook, including:

- `timestamp`: When the change was processed
- `operation`: The type of operation (e.g., CREATE, UPDATE, DELETE)
- `resource_kind`: The kind of Kubernetes resource affected
- `resource_name`: The name of the resource
- `user`: The username of the user who triggered the change (from the webhook event)
- `namespace`: The namespace where the change occurred
- `release`: The Helm release name (if applicable)

This allows you to quickly see the latest change for any namespace, including who made it.
