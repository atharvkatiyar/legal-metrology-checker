from __future__ import annotations

import logging
from typing import Optional

from geopy.exc import GeocoderServiceError, GeocoderTimedOut, GeocoderUnavailable
from geopy.geocoders import Nominatim

logger = logging.getLogger(__name__)

_GEOLOCATOR = Nominatim(user_agent="lmcs_legal_metrology_sih2026", timeout=5)


def reverse_geocode(latitude: float, longitude: float) -> Optional[str]:
    """
    Reverse-geocode a (lat, lon) pair into a human-readable address string.

    Synchronous (geopy has no first-class async client). Callers on an
    asyncio event loop should invoke this via asyncio.to_thread() to
    avoid blocking. Never raises: any network failure, timeout, or
    unexpected error degrades to None so PDF generation can proceed
    with an "address unavailable" fallback rather than crashing.
    """
    if latitude is None or longitude is None:
        return None

    try:
        location = _GEOLOCATOR.reverse(
            (latitude, longitude),
            exactly_one=True,
            language="en",
        )
    except (GeocoderTimedOut, GeocoderUnavailable, GeocoderServiceError):
        logger.warning(
            "Reverse geocoding unavailable/timed out for (%s, %s)",
            latitude,
            longitude,
        )
        return None
    except Exception:
        logger.exception(
            "Unexpected reverse geocoding failure for (%s, %s)",
            latitude,
            longitude,
        )
        return None

    if location is None or not getattr(location, "address", None):
        return None

    return str(location.address)


__all__ = ["reverse_geocode"]