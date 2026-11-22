import struct
import unittest
from unittest.mock import Mock

from audio_capture import PlaybackCapture
from realtime_audio import BoundedAudioQueue, StreamingPcmConverter


class StreamingPcmConverterTests(unittest.TestCase):
    def test_capture_callback_sends_audio_to_live_queue_without_blocking(self):
        capture = object.__new__(PlaybackCapture)
        capture.buffer = Mock()
        capture.on_chunk = Mock(return_value=True)
        capture.callback_errors = 0

        capture._capture(b"\x00\x00" * 12, 12, None, 0)

        capture.buffer.append.assert_called_once_with(b"\x00\x00" * 12)
        capture.on_chunk.assert_called_once_with(b"\x00\x00" * 12, 12)

    def test_converts_stereo_48khz_to_mono_16khz_in_100ms_chunks(self):
        converter = StreamingPcmConverter(sample_rate=48000, channels=2)
        pcm = struct.pack("<hh", 1200, 800) * 24000

        chunks = converter.feed(pcm) + converter.flush()

        self.assertEqual([len(chunk) for chunk in chunks], [3200] * 5)
        self.assertEqual(chunks[0][:2], struct.pack("<h", 1000))

    def test_converter_carries_resampler_state_across_audio_callbacks(self):
        converter = StreamingPcmConverter(sample_rate=48000, channels=1)
        one_second = struct.pack("<h", 500) * 48000
        chunks = []
        for offset in range(0, len(one_second), 4096):
            chunks.extend(converter.feed(one_second[offset:offset + 4096]))
        chunks.extend(converter.flush())

        self.assertEqual(sum(map(len, chunks)), 32000)

    def test_audio_queue_is_bounded_and_marks_dropped_samples(self):
        queue = BoundedAudioQueue(max_chunks=2)
        self.assertTrue(queue.put(b"first", frames=1600))
        self.assertTrue(queue.put(b"second", frames=1600))
        self.assertFalse(queue.put(b"dropped", frames=3200))
        queue.get_nowait()
        queue.get_nowait()
        self.assertTrue(queue.put(b"after gap", frames=1600))
        packet = queue.get_nowait()

        self.assertEqual(queue.dropped_frames, 3200)
        self.assertEqual(packet.gap_before_frames, 3200)
        self.assertEqual(packet.sequence, 4)


if __name__ == "__main__":
    unittest.main()
