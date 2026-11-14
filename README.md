# PFA Jar

This repository contains the source code for PFA Jar, a modified version of Mason Jar. The original source code and executable are preserved separately. For workspace paths, restoration history, and validation guidance, see the [Modified Source Setup Guide](docs/MC_SOURCE_SETUP.md).

![Licence](https://img.shields.io/github/license/Ileriayo/markdown-badges?style=for-the-badge) ![Electron.js](https://img.shields.io/badge/Electron-191970?style=for-the-badge&logo=Electron&logoColor=white) ![Windows](https://img.shields.io/badge/Windows-0078D6?style=for-the-badge&logo=windows&logoColor=white) ![Mac OS](https://img.shields.io/badge/mac%20os-000000?style=for-the-badge&logo=macos&logoColor=F0F0F0) ![Linux](https://img.shields.io/badge/Linux-FCC624?style=for-the-badge&logo=linux&logoColor=black)

# Introduction

PFA Jar is a fork of [Mason Jar](https://github.com/matsojr22/masonjar) for neurohistology analysis of the mouse brain.

# Compatibility

Legacy Bell Jar project bundles (`*.belljar`, `project.belljar`, and `.belljar/` metadata) open without conversion. PFA Jar stores its application environment under `~/.pfajar`, separate from Mason Jar's `~/.masonjar` and Bell Jar's `~/.belljar`. On first launch, existing model/atlas data can be copied into `~/.pfajar`; Python and its virtual environment are always installed independently. Project metadata directories and file formats remain unchanged.

# Usage

See `docs/belljar_guide.pdf` for workflow instructions and a guide to each tool. The guide retains upstream Bell Jar branding; PFA Jar behavior is the same unless noted in the release notes.

# Requirements

- At least 20GB of disk space
- 32GB of memory minimum (64GB recommended)
- Intel i5 / Apple Silicon / AMD Ryzen 4th gen
- GPU with at least 6GB of VRAM and CUDA 11 support

# Install from Release

Download the most recent release from [PFAJar releases](https://github.com/mirihara0523/PFAJar/releases).

Extract the archive and run PFA Jar (`PFA Jar.app` on macOS or `PFA Jar.exe` on Windows).

On some macOS systems you may need to authorize the app to run because code signing is not implemented. See [Apple's guide on running unsigned code](https://support.apple.com/en-us/HT202491).

# Install from Source

```text
git clone https://github.com/mirihara0523/PFAJar.git
cd PFAJar
npm install -g yarn   # if needed
yarn install
yarn compile
yarn start
```

First launch into a new `~/.pfajar` installs Python and its packages. If Mason Jar or Bell Jar model/atlas data is available, choose **Copy model/atlas data** to reuse a separate copy instead of downloading it again. The original application's files are not modified. Otherwise, the required data is downloaded. Initial setup needs internet access and additional disk space.

# How to work with annotations

Annotations can be loaded with Python's `pickle` library and NumPy. Each pixel is an Allen Atlas region ID. Region metadata is in `structure_graph.json` in the `csv` folder. PFA Jar uses the `id` field, not `atlas_id`.

```python
import pickle
import numpy as np

with open("Annotation_MyBrain_s001.pkl", "rb") as file:
    annotation = pickle.load(file)
```

# Attribution

PFA Jar is maintained by [mirihara0523](https://github.com/mirihara0523). It is derived from Mason Jar by Matt Jacobs and Alec Soronow at the Euiseok Kim Lab, used under the MIT License. See `pages/credits.html` for full attribution.
