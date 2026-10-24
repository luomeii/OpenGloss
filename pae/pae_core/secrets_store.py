"""Create complete instance secrets atomically without replacing existing files."""
import json
import os
import tempfile


def load_or_create(path, field, generate, validate):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        fd, name = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump({field: generate()}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(name, path)  # publish complete file; never overwrite a competitor
            except FileExistsError:
                pass
        finally:
            os.unlink(name)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))[field]
        if not validate(value):
            raise ValueError()
        return value
    except (ValueError, KeyError, TypeError):
        raise ValueError("invalid instance secret: " + path.name) from None
