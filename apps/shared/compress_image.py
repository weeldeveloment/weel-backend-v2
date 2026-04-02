from io import BytesIO

from PIL import Image, ImageOps
from PIL.Image import Resampling


from django.core.files import File
from django.utils.translation import gettext_lazy as _

from core import settings


def utils_image(image):
    extension = image.name.split(".")[-1].lower()
    if extension not in settings.ALLOWED_PHOTO_EXTENSION:
        raise ValueError(
            _("Invalid image format, allowed are: jpg, jpeg, png, heif, heic")
        )
    img = Image.open(image)
    img = ImageOps.exif_transpose(img)
    if img.mode != "RGB":
        img = img.convert("RGB")
    return img, extension


def compress_image(image, ratio, quality=None):
    img, extension = utils_image(image)

    width, height = img.size
    ratio = min(ratio / width, ratio / height)

    img = img.resize((int(ratio * width), int(ratio * height)), Resampling.LANCZOS)
    img_io = BytesIO()

    if extension in settings.ALLOWED_PHOTO_EXTENSION:
        extension = "jpeg"

    if quality is None:
        img.save(fp=img_io, format="jpeg", quality=100)
    else:
        img.save(fp=img_io, format="jpeg", quality=quality)

    new_image = File(img_io, name=f"{image.name}.{extension}")
    return new_image


def property_image_compress(image):
    new_image = compress_image(image, 640)
    return new_image

