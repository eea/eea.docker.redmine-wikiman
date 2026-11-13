#!/usr/bin/env python3

"""Process memory self-visibility and page-cache hygiene.

The 2026-10-19 oom-kill of this app (#309775) happened with only ~140Mi of
anonymous memory: the rest of the 1Gi memcg was file-backed (page cache +
dirty writeback pages) from our own file writes (one YAML per resource,
values.yaml per release, git objects and packs). Two things follow:

1. We must be able to see our own memory, so the next incident is not blind:
   read_memory() parses /proc/self/status (VmRSS/VmHWM/VmData).
2. Files we write and immediately re-read by git are a transient cache for
   us - drop them from the page cache with posix_fadvise(DONTNEED) so they
   stop counting against the container memory limit as soon as we are done.

Both are best-effort: anything non-Linux or transiently unreadable degrades
to zeros / no-op and never raises.
"""

import logging
import os

logger = logging.getLogger(__name__)

_PROC_SELF_STATUS = "/proc/self/status"


def _read_kb(field: str) -> float:
    try:
        with open(_PROC_SELF_STATUS) as f:
            for line in f:
                if line.startswith(field + ":"):
                    return float(line.split()[1]) / 1024.0
    except (OSError, ValueError, IndexError):
        pass
    return 0.0


def read_memory() -> dict:
    """Current process memory in MiB: rss (now), hwm (high watermark), data."""
    return {
        "rss_mib": round(_read_kb("VmRSS"), 1),
        "hwm_mib": round(_read_kb("VmHWM"), 1),
        "data_mib": round(_read_kb("VmData"), 1),
    }


def memory_summary() -> str:
    m = read_memory()
    return f"rss={m['rss_mib']}Mi hwm={m['hwm_mib']}Mi data={m['data_mib']}Mi"


def drop_file_cache(path: str) -> None:
    """Ask the kernel to drop the page cache for a file we just wrote.

    Best-effort: silently no-ops where unsupported (non-Linux) or on any
    error. Opening read-only and closing does not modify the file.
    """
    if not hasattr(os, "posix_fadvise") or not os.path.exists(path):
        return
    try:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
        finally:
            os.close(fd)
    except OSError:
        pass


def drop_git_pack_cache(storage_path: str) -> None:
    """Drop page cache on the local clone's pack files after a successful push.

    The packs exist only to be shipped to the remote; keeping them warm in the
    page cache just spends the container's memory limit on data we will never
    read again (see #309775). Loose objects are small; packs are the bulk."""
    pack_dir = os.path.join(storage_path, ".git", "objects", "pack")
    if not os.path.isdir(pack_dir):
        return
    try:
        for entry in os.listdir(pack_dir):
            if entry.endswith(".pack"):
                drop_file_cache(os.path.join(pack_dir, entry))
    except OSError:
        pass
