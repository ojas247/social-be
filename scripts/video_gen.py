import pandas as pd
import matplotlib
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation, FFMpegWriter
matplotlib.rcParams['animation.ffmpeg_path'] = r"C:\Users\Ojas\Tech\Software\ffmpeg\bin\ffmpeg.exe"

# ------------------------
# LOAD DATA
# ------------------------
data = pd.DataFrame({
    "Airport": ["Delhi", "Mumbai", "Bengaluru", "Hyderabad"],
    "Flights": [41000, 28500, 24500, 18600],
    "Passengers": [6500000, 4600000, 3700000, 2600000],
    "PPF": [159, 162, 152, 139]
})

# ------------------------
# CHART SETUP
# ------------------------
fig, ax = plt.subplots(figsize=(10, 6))
plt.tight_layout()

metrics = ["Flights", "Passengers", "PPF"]
titles = {
    "Flights": "Number of Flights (Oct 2025)",
    "Passengers": "Number of Passengers (Oct 2025)",
    "PPF": "Passengers per Flight (Oct 2025)"
}

# ------------------------
# ANIMATION FUNCTION
# ------------------------
def update(frame):
    ax.clear()
    metric = metrics[frame]

    ax.barh(data["Airport"], data[metric], color="#4682B4")
    ax.set_title(titles[metric], fontsize=16, pad=15)
    ax.set_xlabel(metric)
    ax.grid(axis="x", linestyle="--", alpha=0.4)

# ------------------------
# CREATE ANIMATION
# ------------------------
anim = FuncAnimation(fig, update, frames=len(metrics), interval=2000, repeat=True)

writer = FFMpegWriter(fps=1)
anim.save("airport_stats_oct25.mp4", writer=writer)

# anim.save("airport_stats_oct25.mp4", writer="ffmpeg", fps=1)

print("Video generated successfully: airport_stats_oct25.mp4")
