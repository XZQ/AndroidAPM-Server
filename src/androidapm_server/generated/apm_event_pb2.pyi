from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class ApmEventMessage(_message.Message):
    __slots__ = ("timestamp", "module", "name", "kind", "severity", "process_name", "thread_name", "scene", "foreground", "fields", "global_context", "extras", "priority", "event_id", "typed_fields")
    class FieldsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    class GlobalContextEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    class ExtrasEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    class TypedFieldsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: ApmTypedValue
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[ApmTypedValue, _Mapping]] = ...) -> None: ...
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
    TYPED_FIELDS_FIELD_NUMBER: _ClassVar[int]
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
    typed_fields: _containers.MessageMap[str, ApmTypedValue]
    def __init__(self, timestamp: _Optional[int] = ..., module: _Optional[str] = ..., name: _Optional[str] = ..., kind: _Optional[str] = ..., severity: _Optional[str] = ..., process_name: _Optional[str] = ..., thread_name: _Optional[str] = ..., scene: _Optional[str] = ..., foreground: _Optional[bool] = ..., fields: _Optional[_Mapping[str, str]] = ..., global_context: _Optional[_Mapping[str, str]] = ..., extras: _Optional[_Mapping[str, str]] = ..., priority: _Optional[str] = ..., event_id: _Optional[str] = ..., typed_fields: _Optional[_Mapping[str, ApmTypedValue]] = ...) -> None: ...

class ApmTypedValue(_message.Message):
    __slots__ = ("type", "value")
    TYPE_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    type: str
    value: str
    def __init__(self, type: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...

class ApmResource(_message.Message):
    __slots__ = ("service_name", "service_version", "deployment_environment", "installation_id")
    SERVICE_NAME_FIELD_NUMBER: _ClassVar[int]
    SERVICE_VERSION_FIELD_NUMBER: _ClassVar[int]
    DEPLOYMENT_ENVIRONMENT_FIELD_NUMBER: _ClassVar[int]
    INSTALLATION_ID_FIELD_NUMBER: _ClassVar[int]
    service_name: str
    service_version: str
    deployment_environment: str
    installation_id: str
    def __init__(self, service_name: _Optional[str] = ..., service_version: _Optional[str] = ..., deployment_environment: _Optional[str] = ..., installation_id: _Optional[str] = ...) -> None: ...

class ApmBatchEnvelope(_message.Message):
    __slots__ = ("schema_version", "sdk_name", "sdk_version", "batch_id", "sent_at_ms", "resource", "events")
    SCHEMA_VERSION_FIELD_NUMBER: _ClassVar[int]
    SDK_NAME_FIELD_NUMBER: _ClassVar[int]
    SDK_VERSION_FIELD_NUMBER: _ClassVar[int]
    BATCH_ID_FIELD_NUMBER: _ClassVar[int]
    SENT_AT_MS_FIELD_NUMBER: _ClassVar[int]
    RESOURCE_FIELD_NUMBER: _ClassVar[int]
    EVENTS_FIELD_NUMBER: _ClassVar[int]
    schema_version: int
    sdk_name: str
    sdk_version: str
    batch_id: str
    sent_at_ms: int
    resource: ApmResource
    events: _containers.RepeatedCompositeFieldContainer[ApmEventMessage]
    def __init__(self, schema_version: _Optional[int] = ..., sdk_name: _Optional[str] = ..., sdk_version: _Optional[str] = ..., batch_id: _Optional[str] = ..., sent_at_ms: _Optional[int] = ..., resource: _Optional[_Union[ApmResource, _Mapping]] = ..., events: _Optional[_Iterable[_Union[ApmEventMessage, _Mapping]]] = ...) -> None: ...

class ApmBatchAck(_message.Message):
    __slots__ = ("schema_version", "batch_id", "accepted_event_count")
    SCHEMA_VERSION_FIELD_NUMBER: _ClassVar[int]
    BATCH_ID_FIELD_NUMBER: _ClassVar[int]
    ACCEPTED_EVENT_COUNT_FIELD_NUMBER: _ClassVar[int]
    schema_version: int
    batch_id: str
    accepted_event_count: int
    def __init__(self, schema_version: _Optional[int] = ..., batch_id: _Optional[str] = ..., accepted_event_count: _Optional[int] = ...) -> None: ...
