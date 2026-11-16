"""Local Summary-card references and original image storage; no transport."""
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import stat
import tempfile


CARD_ID_PATTERN = r'(?:c_[a-fA-F0-9]{32}|oc_su_[a-fA-F0-9]{32}_i[0-9]+)'
_RELEVANT = re.compile(r'(?<!!)\[\]\(plaud://image\?type=summaryCard\b')
_CARD_LINK = re.compile(_RELEVANT.pattern + r'&id=(' + CARD_ID_PATTERN + r')\)')


def summary_card_ids(body):
    """Return distinct literal IDs; reject malformed relevant references."""
    result = []
    for relevant in _RELEVANT.finditer(body):
        match = _CARD_LINK.match(body, relevant.start())
        if match is None:
            raise ValueError('INVALID_SUMMARY_CARD_REF')
        if match[1] not in result:
            result.append(match[1])
    return result


MAX_IMAGE_BYTES = 20 * 1024 * 1024


def _image_extension(data, content_type):
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_IMAGE_BYTES:
        raise ValueError('INVALID_SUMMARY_IMAGE')
    png = (data.startswith(b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR')
           and len(data) >= 45 and data.endswith(b'\x00\x00\x00\x00IEND\xaeB`\x82'))
    jpeg = len(data) >= 4 and data.startswith(b'\xff\xd8\xff') and data.endswith(b'\xff\xd9')
    webp = (len(data) >= 20 and data[:4] == b'RIFF' and data[8:12] == b'WEBP'
            and data[12:16] in (b'VP8 ', b'VP8L', b'VP8X')
            and int.from_bytes(data[4:8], 'little') == len(data) - 8)
    for mime, valid, extension in [('image/png', png, 'png'), ('image/jpeg', jpeg, 'jpg'),
                                    ('image/webp', webp, 'webp')]:
        if content_type == mime and valid:
            return extension
    raise ValueError('INVALID_SUMMARY_IMAGE')


def _safe_path(vault, output, target):
    if (not vault.is_absolute() or not output.is_absolute()
            or '..' in vault.parts or '..' in output.parts
            or output == vault or not output.is_relative_to(vault)):
        raise RuntimeError('UNSAFE_SUMMARY_IMAGE_PATH')
    for part in [*reversed(target.parents), target]:
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            raise RuntimeError('UNSAFE_SUMMARY_IMAGE_PATH') from None
        # Cloud Files tags do not redirect paths; junction/symlink and unknown tags still fail.
        cloud = getattr(info, 'st_reparse_tag', 0) in {0x9000001A | (index << 12) for index in range(16)}
        if (stat.S_ISLNK(info.st_mode)
                or (getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT and not cloud)
                or (part != target and not stat.S_ISDIR(info.st_mode))
                or (part == target and not stat.S_ISREG(info.st_mode))):
            raise RuntimeError('UNSAFE_SUMMARY_IMAGE_PATH')
    if not vault.is_dir() or not target.resolve().is_relative_to(output.resolve()):
        raise RuntimeError('UNSAFE_SUMMARY_IMAGE_PATH')


def _write_image(vault, output, target, data, old):
    # shortcut: use the engine writer_lock; hostile external renames need OS handle-based I/O.
    target.parent.mkdir(parents=True, exist_ok=True)
    _safe_path(vault, output, target)
    fd, temporary = tempfile.mkstemp(prefix='.plaud-', dir=target.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        _safe_path(vault, output, target)
        if old is None:
            try:
                os.link(temporary, target)  # Atomic no-clobber publish on the same filesystem.
            except FileExistsError:
                raise RuntimeError('UNRELATED_DESTINATION_EXISTS') from None
        else:
            if (not target.exists() or target.stat().st_size != len(old)
                    or target.read_bytes() != old):
                raise RuntimeError('SUMMARY_IMAGE_OWNERSHIP_MISMATCH')
            os.replace(temporary, target)
        if target.read_bytes() != data:
            raise RuntimeError('OUTPUT_READBACK_FAILED')
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _summary_target(vault, output, record, card_id, extension):
    from .engine import record_id
    record_id(record.get('id'))
    if not isinstance(card_id, str) or not re.fullmatch(CARD_ID_PATTERN, card_id):
        raise ValueError('INVALID_SUMMARY_CARD_ID')
    vault, output = Path(vault), Path(output)
    value = record.get('start_at') or record.get('created_at')
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            date = datetime.fromtimestamp(value / 1000 if value > 100000000000 else value, timezone.utc)
        else:
            date = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        parts = (f'{date.year:04d}', f'{date.month:02d}')
    except (ValueError, TypeError, OverflowError, OSError):
        parts = ('날짜 미상',)
    target = output.joinpath('_attachments', *parts, record['id'], card_id + '.' + extension)
    _safe_path(vault, output, target)
    return target


def summary_image_receipt(vault, output, record, card_id, data, content_type):
    """Validate bytes/path and describe a pending publication without writing it."""
    content_type = content_type.split(';', 1)[0].strip().lower() if isinstance(content_type, str) else ''
    extension = _image_extension(data, content_type)
    target = _summary_target(vault, output, record, card_id, extension)
    return {'card_id': card_id, 'path': target.relative_to(Path(vault)).as_posix(),
            'sha256': hashlib.sha256(data).hexdigest(), 'byte_count': len(data), 'content_type': content_type}


def read_summary_image_receipt(vault, output, record, card_id, receipt):
    """Read only the exact bounded asset named by a trusted private receipt; None if missing."""
    if not isinstance(receipt, dict) or receipt.get('card_id') != card_id:
        raise RuntimeError('SUMMARY_IMAGE_OWNERSHIP_MISMATCH')
    mime = receipt.get('content_type')
    if not isinstance(mime, str):
        raise RuntimeError('SUMMARY_IMAGE_OWNERSHIP_MISMATCH')
    extension = {'image/png': 'png', 'image/jpeg': 'jpg', 'image/webp': 'webp'}.get(mime)
    if extension is None:
        raise RuntimeError('SUMMARY_IMAGE_OWNERSHIP_MISMATCH')
    target = _summary_target(vault, output, record, card_id, extension)
    if receipt.get('path') != target.relative_to(Path(vault)).as_posix():
        raise RuntimeError('SUMMARY_IMAGE_OWNERSHIP_MISMATCH')
    if not target.exists():
        return None
    if target.stat().st_size > MAX_IMAGE_BYTES:
        raise RuntimeError('SUMMARY_IMAGE_OWNERSHIP_MISMATCH')
    data = target.read_bytes()
    if summary_image_receipt(vault, output, record, card_id, data, mime) != receipt:
        raise RuntimeError('SUMMARY_IMAGE_OWNERSHIP_MISMATCH')
    return data


def store_summary_image(vault, output, record, card_id, data, content_type, previous=None):
    """Publish under the engine writer_lock; previous is a trusted private receipt."""
    receipt = summary_image_receipt(vault, output, record, card_id, data, content_type)
    content_type = receipt['content_type']
    vault, output = Path(vault), Path(output)
    target = vault / receipt['path']
    old = None
    if target.exists():
        if previous is None:
            raise RuntimeError('UNRELATED_DESTINATION_EXISTS')
        if not target.is_file() or target.stat().st_size > MAX_IMAGE_BYTES:
            raise RuntimeError('SUMMARY_IMAGE_OWNERSHIP_MISMATCH')
        old = target.read_bytes()
        expected = {**receipt, 'sha256': hashlib.sha256(old).hexdigest(), 'byte_count': len(old)}
        if previous != expected:
            raise RuntimeError('SUMMARY_IMAGE_OWNERSHIP_MISMATCH')
        _image_extension(old, content_type)
        if old == data:
            return receipt
    _write_image(vault, output, target, data, old)
    return receipt


_OWNED_BLOCK = re.compile(
    r'\r?\n<!-- hermes-plaud:summary-image ' + CARD_ID_PATTERN
    + r' -->\r?\n!\[\[[^\r\n\[\]]+\]\]\r?\n<!-- /hermes-plaud:summary-image -->')


def strip_summary_image_embeds(body):
    """Remove only the exact, single-image blocks generated by this module."""
    return _OWNED_BLOCK.sub('', body)


def embed_summary_images(body, receipts):
    """Keep PLAUD references; embed available cards at their first occurrence."""
    body = strip_summary_image_embeds(body)
    summary_card_ids(body)
    available = {}
    for receipt in receipts:
        if not isinstance(receipt, dict):
            raise ValueError('INVALID_SUMMARY_IMAGE_RECEIPT')
        card_id, path = receipt.get('card_id'), receipt.get('path')
        if (not isinstance(card_id, str) or not re.fullmatch(CARD_ID_PATTERN, card_id)
                or not isinstance(path, str) or re.search(r'[\\\\:\[\]|#^\x00-\x1f\x7f]', path)
                or any(part in ('', '.', '..') for part in path.split('/'))
                or path.split('/')[-1] not in [card_id + '.' + ext for ext in ('png', 'jpg', 'webp')]
                or (card_id in available and receipt != available[card_id])):
            raise ValueError('INVALID_SUMMARY_IMAGE_RECEIPT')
        available[card_id] = receipt
    seen = set()
    def insert(match):
        card_id = match[1]
        if card_id in seen or card_id not in available:
            return match[0]
        seen.add(card_id)
        return (match[0] + '\n<!-- hermes-plaud:summary-image ' + card_id + ' -->\n![['
                + available[card_id]['path'] + ']]\n<!-- /hermes-plaud:summary-image -->')
    return _CARD_LINK.sub(insert, body)
