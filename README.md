# MICS: Multimodal-Industrial-Classification
This repository contains all relevant files for the Multimodal Industrial Classification (MICS) project. MICS intends to implement a multimodal architecture using visual image and tabular data for performing classification. The architecture implements ResNet18 along with a multilayer perceptron in a combined architecure.

[![Docs](https://img.shields.io/badge/docs-GitHub_Pages-blue.svg)](https://intforce.github.io/Multimodal-Industrial-Classification/)
[![Python](https://img.shields.io/badge/python-3.x-blue.svg)](#)
[![PyTorch](https://img.shields.io/badge/PyTorch-%23EE4C2C.svg?logo=PyTorch&logoColor=white)](#)

---

## Overview

This repository contains the multimodal architecture and data processing pipelines used in the MICS project.
The full documentation for all modules is generated automatically and hosted via GitHub Pages.
The project utilized the SolidLetters and TraceParts for benchmarking and evaluating model metrics.

📖 **[Documentation](https://intforce.github.io/Multimodal-Industrial-Classification/)**


🔗 **[SolidLetters](https://github.com/AutodeskAILab/UV-Net)**, **[TraceParts](https://www.traceparts.com/en)**, **[STEP-categorizer](https://gitlab.au.dk/maleci/aimo/step_categorizer)**

## Key Components

* **`Orchestrator.py`**: Main entry point mediating data between system components. The orchestrator integrates the training and testing process of the multimodal fusion architecture.
* **`DataLoader.py`**: Handles the I/O and pre-processing of different datasets. Contains custom functions used for loading SolidLetters and TraceParts.
* **`FeatureExtractor.py`**: Implements an interface for `EdgeExtractor.py`.
* **`FeatureProcessor.py`**: Handles the fusion of different modalities.
* **`EdgeProcessor.py`**: Implements a pretrained ResNet18 model for feature extraction. Strips off the classification layer for the intermediate fusion process.
* **`MetricProcessor.py`**: Implements a multilayer perceptron (MLP) for learning patterns inherent in tabular data.
* **`FeatureClassifier.py`**: Implements a classification layer consisting of a single PyTorch linear layer.
* **`EdgeExtractor.py`**: Sub-component of `FeatureExtractor.py`. Implements the Canny algorithm to extract and save image contours.
* **`step_categorizer-master`**: A modified clone of the [STEP-categorizer](https://gitlab.au.dk/maleci/aimo/step_categorizer/-/tree/ba722267cdf0d0a89f6550c7e408c37721bc9a39/) utility. Extends program functionality allowing the removal of gradient background and axis-display.

## Installation

1. Clone the repository:
```bash
git clone https://github.com/IntForce/Multimodal-Industrial-Classification.git
cd [Multimodal-Industrial-Classification]
```

2. Install the required dependencies:

**Anaconda**

Use the `anaconda_env.yml` environment file if you are planning to execute the project using Anaconda.
```bash
conda env create -f anaconda_env.yml
```

**Pip (Windows)**

Install and use [Python v3.9.15](https://www.python.org/downloads/).

Create and activate the pip environment
```bash
py -3.9 -m venv mics_env
```
```bash
mics_env\Scripts\activate
```

Use the `pip_env.txt` environment file if you are planning to execute the project using pip.
```bash
pip install -r pip_env.txt
```


## Usage

To execute the pipeline first open the STEP-categorizer follow these steps.
1. Uncheck the 'Display Axis' and check the 'No background (disables AA)' checkbox.
2. Select the location of where the STEP files are located in the 'step' dropdown menu.
3. Select where you want to save the generated thumbnails of the STEP files.
4. Open your code editor of choice and navigate to the root directory of the repository.
5. Open the `FeatureExtractor.py` interface and specify the absolute directory of both the image thumbnails and where you plan to save the contours.
6. After the contours are successfully generated open the `Orchestrator.py` file.
7. Adjust the hyperparameters as you please and specify the paths corresponding to what dataset you are using e.g. SolidLetters or TraceParts. Furthermore, adjust the corresponding paths for the image thumbnails and contours respectively.
8. Make sure to execute either the `classifySolidLettersLib` or `classifyTracePartsLib` depending on the dataset you are using. Make sure to specify the meta data .csv file path if you are using TraceParts e.g. the `categories.csv` file.
9. Scroll down to the bottom of the `Orchestrator.py` module where the `main` function is located and specify whether you want to load or train the model.
10. Execute the `Orchestrator.py` file using the previous environment.

## Project Structure

```bash
├── docs/                   # Generated pdoc HTML documentation
├── outputs/                # Directory for generated model weights (.pth)
├── Orchestrator.py         # Pipeline mediator
├── DataLoader.py           # Data handling
├── EdgeExtractor.py        # Edge detection logic
├── EdgeProcessor.py        # Convolutional neural network using ResNet18
├── MetricProcessor.py      # Multilayer perceptron (ML)
├── FeatureProcessor.py     # Feature fusion
└── FeatureClassifier.py    # Final classification head
```
