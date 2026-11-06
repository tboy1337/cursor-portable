"""Paths and names shared by the package and the install scripts."""

from __future__ import annotations

REPO = "tboy1337/cursor-portable"
RAW_BRANCH = "refs/heads/main"
STICK_DIR_NAME = "CursorPortable"
HOST_DIR_NAME = "cursor-portable"
RUNTIME_DIR_NAME = "runtime"
SESSION_FILE_NAME = "session.txt"
SHORTCUT_SNAPSHOT_NAME = "shortcuts.txt"
SHORTCUT_DISPLAY_NAME = "Cursor Portable"
DESKTOP_FILE_NAME = "cursor-portable.desktop"
MANAGED_MARK = "cursor-portable-managed"
PATH_BLOCK_START = "# >>> cursor-portable >>>"
PATH_BLOCK_END = "# <<< cursor-portable <<<"
HOME_BACKUP_SUFFIX = ".host-backup-cursor-portable"
MIN_SETUP_BYTES = 500_000
MIN_CURSOR_BYTES = 40_000_000
GITHUB_API = "https://api.github.com"
CURSOR_DOWNLOAD_API = "https://cursor.com/api/download"
CURSOR_CLI_INSTALL = "https://cursor.com/install"

UNINSTALLER_NAMES = frozenset({"unins000.exe", "unins000.dat", "unins000.msg"})

USER_DATA_DIRECTORIES = frozenset(
    {
        "User",
        "Partitions",
        "Local Storage",
        "Session Storage",
        "IndexedDB",
        "WebStorage",
        "Network",
        "Dictionaries",
        "Shared Dictionary",
        "blob_storage",
        "CachedProfilesData",
        "agent-harness",
    }
)

USER_DATA_FILES = frozenset(
    {
        "Local State",
        "Preferences",
        "languagepacks.json",
        "machineid",
    }
)

CHAT_FILES = frozenset(
    {
        "state.vscdb",
        "state.vscdb-wal",
        "state.vscdb-shm",
        "state.vscdb.backup",
        "conversation-search.db",
        "conversation-search.db-wal",
        "conversation-search.db-shm",
    }
)

CHAT_DIRECTORIES = frozenset({"History", "workspaceStorage"})

HOME_PROFILE_NAMES = frozenset(
    {
        "mcp.json",
        "ide_state.json",
        "agents",
        "skills-cursor",
        "skills",
        "rules",
        "commands",
        "plans",
        "plugins",
        "projects",
        ".gitignore",
    }
)

LOCAL_CACHE_DIR_NAMES = frozenset(
    {
        "Cache",
        "CachedData",
        "CachedExtensions",
        "CachedExtensionVSIXs",
        "Code Cache",
        "GPUCache",
        "logs",
        "Crashpad",
    }
)
