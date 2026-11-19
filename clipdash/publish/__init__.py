"""Publishing routes. One interface (Publisher), three routes per platform:
direct (official API with the user's own developer app), service (an audited third-party posting API),
assisted (reveal file, copy caption, open upload page, paste the link back)."""
from .base import Publisher, PublishResult, get_publisher, is_dry_run, route_info  # noqa: F401
