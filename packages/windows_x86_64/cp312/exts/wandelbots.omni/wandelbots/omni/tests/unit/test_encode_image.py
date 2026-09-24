"""The formats a capture can be returned in.

`encode_image` is the one place that decides bytes and media type, so the
router's six capture endpoints cannot drift apart on what "jpeg" means.
"""

import io
import random

import omni.kit.test
from PIL import Image, ImageFilter

from wandelbots.omni.periphery.camera_configuration import (
    IMAGE_RESULT_TYPES,
    encode_image,
)


def a_picture():
    return Image.new("RGB", (4, 4), (10, 20, 30))


class TestEncodeImage(omni.kit.test.AsyncTestCase):
    async def test_png_is_lossless_and_announced_as_png(self):
        payload, media_type = encode_image(a_picture(), "rgb_png")
        self.assertEqual(media_type, "image/png")
        self.assertEqual(Image.open(io.BytesIO(payload)).format, "PNG")

    async def test_jpeg_is_announced_as_jpeg(self):
        payload, media_type = encode_image(a_picture(), "jpeg")
        self.assertEqual(media_type, "image/jpeg")
        self.assertEqual(Image.open(io.BytesIO(payload)).format, "JPEG")

    async def test_jpeg_is_the_smaller_one_on_a_rendered_frame(self):
        """Why jpeg is offered at all: these frames get polled, not sampled."""
        random.seed(0)
        noisy = Image.new("RGB", (256, 256))
        noisy.putdata(
            [
                (
                    random.randint(0, 255),
                    random.randint(0, 255),
                    random.randint(0, 255),
                )
                for _ in range(256 * 256)
            ]
        )
        frame = noisy.filter(ImageFilter.GaussianBlur(8))

        self.assertLess(
            len(encode_image(frame, "jpeg")[0]),
            len(encode_image(frame, "rgb_png")[0]),
        )

    async def test_an_image_with_alpha_still_encodes_as_jpeg(self):
        """The annotators hand back RGBA; jpeg has no alpha to put it in."""
        payload, _ = encode_image(Image.new("RGBA", (4, 4), (1, 2, 3, 128)), "jpeg")
        self.assertEqual(Image.open(io.BytesIO(payload)).format, "JPEG")

    async def test_json_is_not_an_image_format(self):
        with self.assertRaises(ValueError):
            encode_image(a_picture(), "json")

    async def test_the_advertised_set_is_what_encode_accepts(self):
        self.assertEqual(IMAGE_RESULT_TYPES, ("rgb_png", "jpeg"))
        for result_type in IMAGE_RESULT_TYPES:
            self.assertTrue(encode_image(a_picture(), result_type))
