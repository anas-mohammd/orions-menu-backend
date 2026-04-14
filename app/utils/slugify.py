from slugify import slugify


def generate_slug(name: str) -> str:
    """Convert a restaurant name to a URL-safe slug."""
    return slugify(name, max_length=80, word_boundary=True)
