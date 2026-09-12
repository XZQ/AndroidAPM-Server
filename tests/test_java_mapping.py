"""Cover qualified, kept and generated R8 names with bounded synthetic mappings."""

from pathlib import Path

import pytest

from androidapm_server.artifacts import validate_java_mapping
from androidapm_server.errors import ApiError


@pytest.mark.parametrize(
    "target",
    [
        "a",
        "com.example.a",
        "com.example.Real",
        "com.example.Outer$Inner",
        "pkg.-$$Lambda$Real$0",
        "包.类",
    ],
)
async def test_accepts_jvm_mapping_class_names(tmp_path: Path, target: str) -> None:
    path = tmp_path / "mapping.txt"
    path.write_text(
        '# {"id":"com.android.tools.r8.mapping","version":"2.2"}\n'
        f"com.example.Real -> {target}:\n    1:2:void run():10:11 -> a\n",
        encoding="utf-8",
    )
    await validate_java_mapping(path)


@pytest.mark.parametrize("target", ["", ".a", "a.", "com..a", "a/b", "a b", "a;", "[La;", "a\x00b"])
async def test_rejects_malformed_mapping_names(tmp_path: Path, target: str) -> None:
    path = tmp_path / "mapping.txt"
    path.write_text(f"com.example.Real -> {target}:\n    void run() -> a\n", encoding="utf-8")
    with pytest.raises(ApiError) as error:
        await validate_java_mapping(path)
    assert error.value.code == "invalid_artifact"
