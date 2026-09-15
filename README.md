# Mason Jar

이 폴더는 **7.0.2-MC.1 수정본 전용 소스**입니다. 원본 소스와 원본 실행본은 별도로 보존합니다. 경로, 복원 내역과 검증 방법은 [수정본 소스 안내](docs/MC_SOURCE_SETUP.md)를 확인하세요.

![Licence](https://img.shields.io/github/license/Ileriayo/markdown-badges?style=for-the-badge) ![Electron.js](https://img.shields.io/badge/Electron-191970?style=for-the-badge&logo=Electron&logoColor=white) ![Windows](https://img.shields.io/badge/Windows-0078D6?style=for-the-badge&logo=windows&logoColor=white) ![Mac OS](https://img.shields.io/badge/mac%20os-000000?style=for-the-badge&logo=macos&logoColor=F0F0F0) ![Linux](https://img.shields.io/badge/Linux-FCC624?style=for-the-badge&logo=linux&logoColor=black)

# Introduction

Mason Jar is a fork of [Bell Jar](https://github.com/asoronow/belljar) for neurohistology analysis of the mouse brain. It supports new `.masonjar` project bundles designed to import data directly from CZI image files, while remaining compatible with legacy `.belljar` files inside bundles and the flat directory styles from the original publication.

# Compatibility

Mason Jar aims to run on any platform; release binaries are provided where tested. If you do not see your OS, build from source using the instructions below.

Legacy Bell Jar **project** bundles (`*.belljar`, `project.belljar`, `.belljar/` meta) open without conversion. Mason Jar **always** stores its app environment under `~/.masonjar` (embedded Python, venv, models, log). Bell Jar continues to use `~/.belljar`. On first launch, if only `~/.belljar` has an environment, Mason Jar offers to **copy** it into `~/.masonjar` (read-only on Bell Jar’s folder) or install fresh.

# Usage

See `docs/belljar_guide.pdf` in the repository for workflow instructions and a guide to each tool. The guide retains upstream Bell Jar branding; Mason Jar behavior is the same unless noted in release notes.

# Requirements

- At least 20GB of disk space
- 32GB of memory MINIMUM (64GB recommended)
- Intel i5 / Apple Silicon / AMD Ryzen 4th gen
- (REQUIRED) GPU with at least 6 GB of VRAM and CUDA 11 support

# Install from Release

Download the most recent release for your OS from [matsojr22/masonjar releases](https://github.com/matsojr22/masonjar/releases).

Extract the archive and run the Mason Jar executable (`Mason Jar.app` on macOS, `Mason Jar.exe` on Windows).

Note: On some macOS systems you may need to authorize the app to run since code signing is not implemented. See [Apple's guide on running unsigned code](https://support.apple.com/en-us/HT202491).

# Install from Source

Clone this repository and run:

```
git clone https://github.com/matsojr22/masonjar.git
cd masonjar
npm install -g yarn   # if needed
yarn install
yarn compile
yarn start
```

First launch into a new `~/.masonjar` downloads ~20GB of models and embeddings from the upstream Bell Jar CDN (`storage.googleapis.com/belljar_updates`). If you already use Bell Jar, choose **Copy from Bell Jar** to avoid re-downloading.

# How to work with annotations

Annotations can be loaded with Python's pickle library and numpy. Each pixel is an Allen Atlas region id. Region metadata is in `structure_graph.json` in the csv folder.

Note: Mason Jar uses the `id` field, not `atlas_id`.

```
import pickle
import numpy as np

with open("Annotation_MyBrain_s001.pkl", "rb") as file:
    annotation = pickle.load(file)
```

# Attribution

Mason Jar is maintained by [matsojr22](https://github.com/matsojr22). It is derived from Bell Jar by Alec Soronow and Matt Jacobs at the Euiseok Kim Lab, used under the MIT License. See `pages/credits.html` for full attribution.
