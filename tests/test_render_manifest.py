import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "tools/reel_pipeline/render_manifest.py"
spec = importlib.util.spec_from_file_location("render_manifest", MODULE)
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)


class RenderManifestTest(unittest.TestCase):
    def test_rejects_invalid_cut_map(self):
        for keeps in ([], [[0, 7]], [[3, 4], [2, 3]], [[1, 3], [2, 4]], [[1, 1]], [[float("nan"), 2]]):
            with self.assertRaises(ValueError):
                renderer.validate_keeps(keeps, 6)
        self.assertAlmostEqual(renderer.validate_keeps([[0.2, 2.2], [3.2, 4.7]], 6), 3.5)

    def test_cover_cut_and_audio_alignment(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            source = directory / "source.mp4"
            cover = directory / "cover.png"
            Image.new("RGB", (540, 960), "white").save(cover)
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=red:s=270x480:r=30:d=3",
                            "-f", "lavfi", "-i", "color=blue:s=270x480:r=30:d=3",
                            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=3",
                            "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000:duration=3",
                            "-filter_complex", "[0:v][2:a][1:v][3:a]concat=n=2:v=1:a=1[v][a]",
                            "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                            "-c:a", "aac", str(source)], check=True)
            manifest = directory / "manifest.json"
            manifest.write_text(json.dumps({"source": str(source),
                "font": str(MODULE.parents[2] / "assets/fonts/Baloo2.ttf"), "exports": [{"name": "test",
                "keeps": [[0.2, 2.2], [3.2, 4.7]], "cover": str(cover),
                "captions": [{"start": 0.1, "end": 1.1, "text": "प्लेन टी-शर्ट का स्टॉक"}],
                "output": str(directory / "result.mp4")}]}))
            renderer.render(manifest, "test", draft=True)
            result = directory / "result-draft.mp4"
            for time, channel in ((0.2, None), (0.8, 0), (2.8, 2)):
                frame = directory / f"frame-{time}.png"
                subprocess.run(["ffmpeg", "-v", "error", "-ss", str(time), "-i", str(result),
                                "-frames:v", "1", "-update", "1", str(frame)], check=True)
                pixel = Image.open(frame).getpixel((270, 480))
                if channel is None:
                    self.assertTrue(min(pixel) > 230, pixel)
                else:
                    self.assertGreater(pixel[channel], 220)
                    self.assertLess(pixel[2 if channel == 0 else 0], 25)
            import array
            for time, expected in ((0.05, 0), (0.8, 440), (2.8, 880)):
                data = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(time), "-i", str(result),
                                       "-t", "0.2", "-vn", "-ac", "1", "-ar", "48000",
                                       "-f", "s16le", "-"], check=True, capture_output=True).stdout
                samples = array.array("h", data)
                if expected == 0:
                    self.assertLess(max(abs(x) for x in samples), 30)
                else:
                    crossings = sum(a <= 0 < b for a, b in zip(samples, samples[1:]))
                    frequency = crossings / (len(samples) / 48000)
                    self.assertAlmostEqual(frequency, expected, delta=10)


if __name__ == "__main__":
    unittest.main()
