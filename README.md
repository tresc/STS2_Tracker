<center><img width="1300" height="613" alt="Screenshot 2026-04-02 200925" src="https://github.com/user-attachments/assets/f0235a14-f085-4b0a-b3b5-6d48f168777d" /></center>


# ☠️ SPIRE-METRICS TERMINAL (v1.0)

A lightweight harvester and data visualizer for **Slay the Spire 2** career stats. 
This tool parses your local `.run` files and generates a terminal dashboard 
to analyze your pathing, card pick rates, and "Asset Lifecycle."

---

## 💾 INSTALLATION & USAGE

1. **Download**: Go to the [Releases] tab and download `SpireHarvester.exe`.
2. **Run**: Double-click the executable.
3. **View**: The program will generate `spire_metrics.html` on your Desktop.
4. **Analyze**: Open the HTML file in any browser to view your autopsy.

> **NOTE**: This tool currently looks for Steam data in the default Windows directory. 
> Ensure your game is installed on your `C:` drive.

---

## 🔍 FEATURES

* **Run Autopsy**: Deep-dive into your latest run's "Pathing & Velocity."
* **HP Timeline**: SVG-rendered health tracking across every floor.
* **Career Ledger**: Global win rates, Ascension scaling, and "Elite Lethality" stats.
* **90s Aesthetic**: Zero-dependency, monochromatic CSS for that classic terminal feel.

---

## 🛠️ TECHNICAL SPECS

* **Language**: Python 3.x
* **Data Source**: Steam `userdata` history files (.run)
* **Output**: Standalone HTML/CSS (No JavaScript required)

---

## 📜 LICENSE
Distributed under the MIT License. Use it, break it, fix it.
