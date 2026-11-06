"""Plan the temporary link from the host ``.cursor`` folder to the stick."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import logging
from pathlib import Path

from cursor_portable.constants import HOME_BACKUP_SUFFIX
from cursor_portable.failure import Failure
from cursor_portable.launcher.links import DirectoryLinker, link_target

LOGGER = logging.getLogger(__name__)


class HomeAction(Enum):
    """What to do with the host ``.cursor`` directory."""

    CREATE = "create"
    ALREADY = "already"
    HEAL = "heal"
    MISSING_TARGET = "missing-target"


@dataclass(frozen=True)
class HomePlan:
    """A home-folder change."""

    action: HomeAction
    link: Path
    target: Path
    backup: Path


def is_our_home_target(target: str) -> bool:
    """Return whether a link already points at a portable dot-cursor folder."""
    normalized = target.replace("\\", "/").casefold()
    if "cursorportable/data/dot-cursor" in normalized:
        return True
    return "cursor-portable/runtime/" in normalized and "data/dot-cursor" in normalized


def plan_home_link(
    link: Path,
    target: Path,
    *,
    current_target: str | None,
    link_exists: bool,
    backup_exists: bool,
) -> HomePlan:
    """Decide how to point the host profile at the stick for this session."""
    backup = Path(str(link) + HOME_BACKUP_SUFFIX)
    plan = HomePlan(action=HomeAction.CREATE, link=link, target=target, backup=backup)
    if not target.exists():
        return HomePlan(
            action=HomeAction.MISSING_TARGET,
            link=link,
            target=target,
            backup=backup,
        )
    if current_target is not None and is_our_home_target(current_target):
        if Path(current_target) == target:
            return HomePlan(
                action=HomeAction.ALREADY,
                link=link,
                target=target,
                backup=backup,
            )
        return HomePlan(action=HomeAction.HEAL, link=link, target=target, backup=backup)
    if link_exists and not backup_exists:
        return plan
    if link_exists and backup_exists:
        return HomePlan(action=HomeAction.HEAL, link=link, target=target, backup=backup)
    return plan


def apply_home_link(plan: HomePlan, linker: DirectoryLinker) -> Failure | None:
    """Move the host profile aside and link it to the stick."""
    if plan.action is HomeAction.ALREADY:
        return None
    if plan.action is HomeAction.MISSING_TARGET:
        plan.target.mkdir(parents=True, exist_ok=True)
    current = link_target(plan.link)
    if current is not None:
        failure = linker.remove(plan.link)
        if failure is not None:
            return failure
    elif plan.link.exists():
        moved = _move_aside(plan.link, plan.backup)
        if moved is not None:
            return moved
    plan.target.mkdir(parents=True, exist_ok=True)
    return linker.create(plan.link, plan.target)


def _move_aside(link: Path, backup: Path) -> Failure | None:
    """Rename the host profile. A folder Cursor has open stays where it is."""
    if backup.exists():
        return Failure(
            "home-backup",
            "The host .cursor folder and its backup both exist. Nothing was moved.",
        )
    try:
        link.rename(backup)
    except OSError as exc:
        LOGGER.error("Could not move %s aside: %s", link, exc)
        return Failure(
            "home-busy",
            (
                f"{link} is in use. Close Cursor on this computer "
                "and start the portable copy again."
            ),
        )
    return None
