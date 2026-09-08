"""Команды — единственный разрешённый способ изменить документ."""

from sfstudio.core.commands.actor_cmds import (
    AddActor,
    AssignActor,
    RemoveActor,
    RenameActor,
    UpdateActor,
)
from sfstudio.core.commands.base import Command, CompositeCommand
from sfstudio.core.commands.position import (
    ClearPosition,
    SetAlignment,
    SetOverrideTag,
    SetOverrideTags,
    SetPosition,
    SetRotation,
)
from sfstudio.core.commands.structure import (
    DeleteEvents,
    DuplicateEvents,
    InsertEvent,
    MergeEvents,
    SplitEvent,
)
from sfstudio.core.commands.style_cmds import (
    ApplyStyleToEvents,
    CreateStyle,
    DeleteStyle,
    RenameStyle,
    UpdateStyle,
)
from sfstudio.core.commands.text import (
    SetActor,
    SetMargins,
    SetStyle,
    SetText,
    ToggleComment,
)
from sfstudio.core.commands.timing import (
    ApplyTimings,
    LinearSync,
    SetTiming,
    ShiftTimes,
    SyncError,
    SyncPoints,
)
from sfstudio.core.commands.track_cmds import (
    AddTrack,
    MoveEventsToLayer,
    RemoveTrack,
    SetTrackFlags,
    UpdateTrack,
)

__all__ = [
    "AddActor",
    "AddTrack",
    "ApplyStyleToEvents",
    "ApplyTimings",
    "AssignActor",
    "ClearPosition",
    "Command",
    "CompositeCommand",
    "CreateStyle",
    "DeleteEvents",
    "DeleteStyle",
    "DuplicateEvents",
    "InsertEvent",
    "LinearSync",
    "MergeEvents",
    "MoveEventsToLayer",
    "RemoveActor",
    "RemoveTrack",
    "RenameActor",
    "RenameStyle",
    "SetActor",
    "SetAlignment",
    "SetMargins",
    "SetOverrideTag",
    "SetOverrideTags",
    "SetPosition",
    "SetRotation",
    "SetStyle",
    "SetText",
    "SetTiming",
    "SetTrackFlags",
    "ShiftTimes",
    "SplitEvent",
    "SyncError",
    "SyncPoints",
    "ToggleComment",
    "UpdateActor",
    "UpdateStyle",
    "UpdateTrack",
]
