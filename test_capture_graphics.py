"""Optional monitor-capture probe. It reports only synthetic marker pixels."""

import json
import subprocess
import sys
from pathlib import Path


def sample_monitor(folder):
    from windows_capture import WindowsCapture

    coordinates = json.loads((folder / "coordinates.json").read_text(encoding="utf-8"))
    capture = WindowsCapture(cursor_capture=False)

    @capture.event
    def on_frame_arrived(frame, control):
        samples = []
        for x, y in coordinates:
            blue, green, red = map(int, frame.frame_buffer[y, x, :3])
            samples.append(red | green << 8 | blue << 16)
        (folder / "samples.json").write_text(json.dumps(samples), encoding="ascii")
        control.stop()

    @capture.event
    def on_closed():
        pass

    capture.start()


def receive_graphics_samples(folder):
    subprocess.run([sys.executable, str(Path(__file__).resolve()), "--receiver", str(folder)],
                   check=True, timeout=12, creationflags=subprocess.CREATE_NO_WINDOW)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--receiver":
        sample_monitor(Path(sys.argv[2]))
    elif len(sys.argv) == 2:
        from main import enable_dpi_awareness
        from self_test import run, verify_privacy

        enable_dpi_awareness()
        run(sys.argv[1], check=lambda: verify_privacy(receive_graphics_samples, "Windows Graphics Capture"))
    else:
        raise SystemExit("Usage: test_capture_graphics.py REPORT.json")
