import subprocess
import webbrowser
import os
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Absolute paths
LAND_HTML = os.path.join(BASE_DIR, "land.html")
THERAPIST_BOT = os.path.join(BASE_DIR, "old", "TherapistBot", "app.py")
ANON_CHAT = os.path.join(BASE_DIR, "old", "AnonChat", "app.py")

def open_land():
    """Open land.html in default web browser."""
    webbrowser.open_new_tab(f"file://{LAND_HTML}")

def run_therapist():
    """Run TherapistBot on port 5500."""
    return subprocess.Popen(
        ["python", THERAPIST_BOT, "--port", "5500"],
        cwd=os.path.dirname(THERAPIST_BOT)
    )

def run_anonchat():
    """Run AnonChat app.py."""
    return subprocess.Popen(
        ["python", ANON_CHAT],
        cwd=os.path.dirname(ANON_CHAT)
    )

if __name__ == "__main__":
    # Step 1: Open land.html
    open_land()

    # Step 2: Start TherapistBot
    print("Starting TherapistBot on port 5500...")
    therapist_proc = run_therapist()
    time.sleep(3)  # give it time to start

    # Step 3: Start AnonChat
    print("Starting AnonChat...")
    anonchat_proc = run_anonchat()

    print("Both apps are running. Press Ctrl+C to stop.")
    try:
        therapist_proc.wait()
        anonchat_proc.wait()
    except KeyboardInterrupt:
        print("\nShutting down...")
        therapist_proc.terminate()
        anonchat_proc.terminate()
