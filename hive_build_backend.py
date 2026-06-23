"""PEP 517 wrapper that writes Hive's Git-derived fallback before packaging."""

from __future__ import annotations

from hatchling import build as hatchling_build

from hive.version import FALLBACK_PATH, write_fallback


def _with_fallback(build):
    previous = FALLBACK_PATH.read_text() if FALLBACK_PATH.exists() else None
    write_fallback()
    try:
        return build()
    finally:
        if previous is None:
            FALLBACK_PATH.unlink(missing_ok=True)
        else:
            FALLBACK_PATH.write_text(previous)


def get_requires_for_build_wheel(config_settings=None):
    return hatchling_build.get_requires_for_build_wheel(config_settings)


def get_requires_for_build_sdist(config_settings=None):
    return hatchling_build.get_requires_for_build_sdist(config_settings)


def get_requires_for_build_editable(config_settings=None):
    return hatchling_build.get_requires_for_build_editable(config_settings)


def prepare_metadata_for_build_wheel(metadata_directory: str, config_settings=None):
    return _with_fallback(
        lambda: hatchling_build.prepare_metadata_for_build_wheel(metadata_directory, config_settings)
    )


def prepare_metadata_for_build_editable(metadata_directory: str, config_settings=None):
    return _with_fallback(
        lambda: hatchling_build.prepare_metadata_for_build_editable(metadata_directory, config_settings)
    )


def build_wheel(wheel_directory: str, config_settings=None, metadata_directory: str | None = None):
    return _with_fallback(
        lambda: hatchling_build.build_wheel(wheel_directory, config_settings, metadata_directory)
    )


def build_sdist(sdist_directory: str, config_settings=None):
    return _with_fallback(lambda: hatchling_build.build_sdist(sdist_directory, config_settings))


def build_editable(wheel_directory: str, config_settings=None, metadata_directory: str | None = None):
    return _with_fallback(
        lambda: hatchling_build.build_editable(wheel_directory, config_settings, metadata_directory)
    )
