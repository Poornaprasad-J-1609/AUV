import sys

# Load the camera-detection compute backend BEFORE Qt. On Windows, importing it
# after PySide6 can fail with "DLL initialization routine failed" (c10.dll).
# Optional: the rest of the ground station works without it.
try:
    import torch  # noqa: F401
except Exception:
    pass

from PySide6.QtWidgets import QApplication
from src.app import MarineGroundStation

def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion') # Use clean Fusion style as baseline for our custom theme
    
    window = MarineGroundStation()
    # The cockpit dashboard is designed as a three-column operational view.
    # Start maximized so it uses the full 1920x1080 workspace instead of
    # opening in the previous 1100x650 window and requiring horizontal scroll.
    window.showMaximized()
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
