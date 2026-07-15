from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar

from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from google.protobuf.internal import containers as _containers

DESCRIPTOR: _descriptor.FileDescriptor

class ApmEventMessage(_message.Message):
    __slots__ = ("event_id", "extras", "fields", "foreground", "global_context", "kind", "module", "name", "priority", "process_name", "scene", "severity", "thread_name", "timestamp")
    class FieldsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: str | None = ..., value: str | None = ...) -> None: ...
    class GlobalContextEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: str | None = ..., value: str | None = ...) -> None: ...
    class ExtrasEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: str | None = ..., value: str | None = ...) -> None: ...
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    MODULE_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    SEVERITY_FIELD_NUMBER: _ClassVar[int]
    PROCESS_NAME_FIELD_NUMBER: _ClassVar[int]
    THREAD_NAME_FIELD_NUMBER: _ClassVar[int]
    SCENE_FIELD_NUMBER: _ClassVar[int]
    FOREGROUND_FIELD_NUMBER: _ClassVar[int]
    FIELDS_FIELD_NUMBER: _ClassVar[int]
    GLOBAL_CONTEXT_FIELD_NUMBER: _ClassVar[int]
    EXTRAS_FIELD_NUMBER: _ClassVar[int]
    PRIORITY_FIELD_NUMBER: _ClassVar[int]
    EVENT_ID_FIELD_NUMBER: _ClassVar[int]
    timestamp: int
    module: str
    name: str
    kind: str
    severity: str
    process_name: str
    thread_name: str
    scene: str
    foreground: bool
    fields: _containers.ScalarMap[str, str]
    global_context: _containers.ScalarMap[str, str]
    extras: _containers.ScalarMap[str, str]
    priority: str
    event_id: str
    def __init__(self, timestamp: int | None = ..., module: str | None = ..., name: str | None = ..., kind: str | None = ..., severity: str | None = ..., process_name: str | None = ..., thread_name: str | None = ..., scene: str | None = ..., foreground: bool | None = ..., fields: _Mapping[str, str] | None = ..., global_context: _Mapping[str, str] | None = ..., extras: _Mapping[str, str] | None = ..., priority: str | None = ..., event_id: str | None = ...) -> None: ...
