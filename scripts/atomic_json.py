"""Resilient sibling-file publication for local song metadata and telemetry.

Each call owns a unique temporary file. A failed replacement never removes the
previous target. Only Windows access/share/lock violations are retried; final
metadata writes raise after the bounded retries. Callers may mark disposable
progress updates best_effort so a temporarily locked status file cannot abort
an expensive conversion.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import tempfile
import time

logger = logging.getLogger(__name__)
_TRANSIENT_WINDOWS_ERRORS = frozenset({5, 32, 33})


def _transient(error: OSError) -> bool:
    return getattr(error, 'winerror', None) in _TRANSIENT_WINDOWS_ERRORS


def write_json_atomic(path, data, *, attempts=6, retry_delay=.05, best_effort=False) -> bool:
    """Publish a complete JSON file, retaining its old contents on any failure.

    Returns True after replacement. With best_effort=True an OSError returns
    False with a sanitized warning; it never converts a failed final metadata
    write into success unless the caller explicitly classifies it as telemetry.
    Invalid/non-finite JSON remains a programming error and always raises.
    """
    if type(attempts) is not int or not 1 <= attempts <= 16:
        raise ValueError('Atomic JSON attempts must be between 1 and 16.')
    if type(retry_delay) not in (int, float) or not 0 <= retry_delay <= .25:
        raise ValueError('Atomic JSON retry delay must be between 0 and 0.25 seconds.')
    # Serialize before touching disk: malformed state cannot truncate a valid
    # target or leave a half-written file bearing the published name.
    encoded = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    target = Path(path)
    temporary = None
    try:
        for attempt in range(attempts):
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                # Retry the same complete temporary file if only replacement
                # failed. A failed create/write gets a fresh unique sibling.
                if temporary is None:
                    descriptor, name = tempfile.mkstemp(prefix='.' + target.name + '.', suffix='.tmp', dir=target.parent)
                    temporary = Path(name)
                    try:
                        stream = os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n')
                        descriptor = None  # stream now owns and closes the descriptor
                        with stream:
                            stream.write(encoded)
                            stream.flush()
                    except BaseException:
                        if descriptor is not None:
                            os.close(descriptor)
                        _cleanup(temporary)
                        temporary = None
                        raise
                # Closing our own file first is essential on Windows.
                os.replace(temporary, target)
                temporary = None
                return True
            except OSError as error:
                if attempt + 1 == attempts or not _transient(error):
                    raise
                time.sleep(min(.25, retry_delay * (attempt + 1)))
    except OSError as error:
        if not best_effort:
            raise
        logger.warning('Song progress file could not be published (%s); computation continues', type(error).__name__)
        return False
    finally:
        if temporary is not None:
            _cleanup(temporary)


def _cleanup(path):
    """Remove only the exact temporary file created by this call."""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        # Antivirus may still hold this unique temp. Never remove the target or
        # another writer's file to force cleanup/publication.
        logger.warning('An owned song-state temporary file is still locked; published metadata is unchanged')
