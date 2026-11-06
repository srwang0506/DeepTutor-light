"""Bounded image evidence; resolve only attachments in the answer's session."""

import base64
import hashlib
from io import BytesIO
from urllib.parse import unquote, urlsplit

from deeptutor.learning.semantic_assessment import AssessmentUnavailable


def answer_image_parts(images, session_id):
    from PIL import Image

    from deeptutor.services.storage import get_attachment_store

    parts, total = [], 0
    for image in images:
        raw = None
        if image.get("base64"):
            if len(image["base64"]) > 6 * 1024 * 1024:
                raise AssessmentUnavailable("Answer image exceeds the size limit")
            raw = base64.b64decode(image["base64"], validate=True)
        elif image.get("url"):
            url = urlsplit(image["url"])
            segments = [unquote(p) for p in url.path.strip("/").split("/")]
            if (
                url.scheme
                or url.netloc
                or len(segments) != 5
                or segments[:2] != ["files", "attachments"]
                or segments[2] != session_id
            ):
                raise AssessmentUnavailable("Answer image must belong to this session")
            target = get_attachment_store().resolve_path(
                session_id=session_id, attachment_id=segments[3], filename=segments[4]
            )
            if target and target.is_file() and target.stat().st_size <= 4 * 1024 * 1024:
                raw = target.read_bytes()
        if not raw:
            raise AssessmentUnavailable("Answer image is unavailable")
        total += len(raw)
        if len(raw) > 4 * 1024 * 1024 or total > 12 * 1024 * 1024:
            raise AssessmentUnavailable("Answer images exceed the request budget")
        with Image.open(BytesIO(raw)) as img:
            if img.width * img.height > 25_000_000 or img.format not in {
                "PNG",
                "JPEG",
                "WEBP",
                "GIF",
            }:
                raise AssessmentUnavailable("Unsupported answer image")
            mime = Image.MIME[img.format]
            img.verify()
        parts.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{base64.b64encode(raw).decode()}"},
            }
        )
    return parts


async def persist_answer_images(parts, session_id):
    """Save validated evidence once, using content-addressed attachment IDs."""
    from deeptutor.services.storage import get_attachment_store

    references = []
    for part in parts:
        header, encoded = part["image_url"]["url"].split(",", 1)
        mime = header.removeprefix("data:").removesuffix(";base64")
        raw = base64.b64decode(encoded, validate=True)
        identifier = "quiz-" + hashlib.sha256(raw).hexdigest()
        extension = {
            "image/png": "png",
            "image/jpeg": "jpg",
            "image/webp": "webp",
            "image/gif": "gif",
        }[mime]
        filename = f"answer.{extension}"
        url = await get_attachment_store().put(
            session_id=session_id,
            attachment_id=identifier,
            filename=filename,
            data=raw,
            mime_type=mime,
        )
        references.append({"id": identifier, "url": url, "filename": filename, "mime_type": mime})
    return references
