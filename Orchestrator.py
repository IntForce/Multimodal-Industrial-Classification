import numpy as np
import torch
from torch import nn
import torchvision.models as models
import torchsummary
from torch.utils.data import Dataset, DataLoader
import matplotlib.pyplot as plt
from sklearn.decomposition import PCA
from torch.utils.tensorboard import SummaryWriter
from datetime import datetime
import random
import json
from pathlib import Path
import os
os.environ["CUBLAS_WORKSPACE_CONFIG"] = ":4096:8" # https://discuss.pytorch.org/t/userwarning-by-pytorch-cublas-workspace-config-can-not-be-parsed/182006/3

# modules
from DataLoader import DataLoader as CustomDataLoader # custom data loader for custom data loader since data loader is an in built lib for torch
from DataLoader import DataWrapper
from FeatureProcessor import FeatureProcessor
from FeatureClassifier import FeatureClassifier
from sklearn import metrics as sklMetrics



class Orchestrator():
    """
    The Orchestrator class is the engine running the multimodal model and pipeline.
    This class utilizes determenistic execution such that the results are as reproducable as possible, it orchestrates the data loading,
    initiates the model and the corresponding sub-modules, and manages model training, validation and testing. It generates important evaluation metrics and saves them as .svg plots or JSON files.
    """
    def __init__(self):
        """
        Initializes the orchestrator module. Random seeds are set for the packages: PyTorch, NumPy, and random, such that results are as reproducible as possible.
        Hyperparameters are initialized, and datasets loaded via a wrapper function interacting with the CustomDataLoader module.  
        """
        # seed and determanism: https://docs.pytorch.org/docs/2.12/notes/randomness.html
        torch.backends.cudnn.benchmark = False
        torch.use_deterministic_algorithms(True)
        torch.manual_seed(0)
        torch.cuda.manual_seed_all(0)
        random.seed(0)
        np.random.seed(0)
        self.gen = torch.Generator()
        self.gen.manual_seed(0)
        
        # hyper parameters
        self._epochs: int = 10
        self._learningRate: float = 1e-4 #learning rate
        self._batchSize: int = 10
        self._device = torch.device("cuda" if torch.cuda.is_available() else "cpu") # gpu if possible
        
        self.imagesPath = r"D:\Aarhus Universitet - Lokal\BP\Traceparts\gallery"
        self.edgesPath = r"D:\Aarhus Universitet - Lokal\BP\Traceparts\edgeContours"
        self.metricsPath = r"D:\OneDrive - Aarhus universitet\Aarhus Universitet\6. semester\Bachelorprojekt\Models\Model_Revised\step_categorizer-master\output"
        self.csvFileName = r"D:\OneDrive - Aarhus universitet\Aarhus Universitet\6. semester\Bachelorprojekt\Models\Model_Revised\step_categorizer-master\output\trace_parts_secondary_data.csv"

        # data split parameters
        self.trainRatio: float = 0.7
        self.validationRatio: float = 0.15
        self.testRatio: float = 0.15
        
        # file paths
        self.imagesPath: str
        self.edgesPath: str
        self.metricsPath: str
        self.csvFileName: str
        
        self._CDL = CustomDataLoader()
        self._CDL.load(imagesPath=self.imagesPath, edgesPath=self.edgesPath, metricsPath=self.metricsPath, csvFileName=self.csvFileName)
        print(f"[Orchestrator] > DEBUG: Size of self._data after load(): {len(self._CDL._data)}")

        self.dataset = "TraceParts"
        # self._CDL.classifySolidLettersLib()
        self._CDL.classifyTracePartsLib(r"D:\Aarhus Universitet - Lokal\BP\Traceparts\categories.csv")
        self._CDL.splitData(trainRatio=self.trainRatio, validationRatio=self.validationRatio, testRatio=self.testRatio)
        
        self.outRootDir = Path(__file__).resolve().parent / "outputs"
        self.outputDir = self.newOutputDir(datetime.now().strftime('%Y%m%d_%H%M%S'))
        
        # data splitting
        train = DataWrapper(self._CDL, split="train")
        validation = DataWrapper(self._CDL, split="validation")
        test = DataWrapper(self._CDL, split="test")
        
        numClasses = self._CDL.getNumOfClasses()
        
        # loaders
        numOfWorkers = 4
        self._trainLoader = DataLoader(train, batch_size=self._batchSize, shuffle=True, num_workers=numOfWorkers, generator=self.gen, worker_init_fn=self.seedWorker) # https://www.geeksforgeeks.org/deep-learning/pytorch-dataloader/
        self._validationLoader = DataLoader(validation, batch_size=self._batchSize, shuffle=False, num_workers=numOfWorkers, generator=self.gen, worker_init_fn=self.seedWorker)
        self._testLoader = DataLoader(test, batch_size=self._batchSize, shuffle=True, num_workers=numOfWorkers, generator=self.gen, worker_init_fn=self.seedWorker)

        # modules
        self._FP = FeatureProcessor().to(self._device)
        self._FC = FeatureClassifier(numClasses=numClasses).to(self._device)
        
        # optimizer and loss funcs
        self._criterion = nn.CrossEntropyLoss() #https://docs.pytorch.org/docs/stable/generated/torch.nn.CrossEntropyLoss.html
        self._optimizer = torch.optim.Adam(list(self._FP.parameters()) + list(self._FC.parameters()), lr=self._learningRate)
        
    
    #https://www.slingacademy.com/article/a-beginner-s-guide-to-pytorch-training-loops/
    # https://docs.pytorch.org/tutorials/beginner/introyt/trainingyt.html
    # https://www.geeksforgeeks.org/python/initialize-weights-in-pytorch/
    #https://discuss.pytorch.org/t/pre-trained-model-feature-fusion/67584/2
    #https://docs.pytorch.org/docs/main/notes/autograd.html
    # https://docs.pytorch.org/tutorials/beginner/blitz/autograd_tutorial.html
    # https://developers.redhat.com/articles/2026/03/03/optimize-pytorch-training-autograd-engine#what_is_autograd_
    
    def seedWorker(self, worker_id):
        """
        Helper function for adding determanism to the model. This function is used for seeding PyTorch's internal DataLoader class' worker threads.

        Args:
            worker_id (_type_): _description_
        """
        worker_seed = torch.initial_seed() % 2**32
        np.random.seed(worker_seed)
        random.seed(worker_seed)
    
    def train(self):
        """
        This function executes the training and validation of the model.
        It iterates over the provided dataset for the specified number of epochs while performing: forward passes, backpropagation, and optimizing model weights.
        Important metrics are tracked and saved such as: Accuracy, F1-score, precision, and recall. These metrics are measured using Scikit-Learn. The model best model
        weights are saved along with JSON summaries containing evaluation metrics.
        
        Returns:
            None.
        """
        #train featureprocessor, featureclassifier
        
        startTime = datetime.now()
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        writer = SummaryWriter('runs/fashion_trainer_{}'.format(timestamp))
        bestValidationLoss = 1_000_000

        # stats
        self.trainStats = {
            "library": self.dataset,
            "epochs": self._epochs,
            "history": [],
        }
        
        self._FP.train()
        self._FC.train()
        TrainLossHistory =[]
        ValidationLossHistory = []
        
        for epoch in range(self._epochs):
            runningLoss = 0.0
            
            for batchIndex, (contours, metrics, labels) in enumerate(self._trainLoader):
                self._optimizer.zero_grad(set_to_none=True) # init bp
                
                # fetch data
                contours = contours.to(self._device)
                metrics = metrics.to(self._device)
                labels = labels.to(self._device)
                
                
                # fw pass
                features = self._FP(contours, metrics)
                logits = self._FC(features)
                
                # loss
                loss = self._criterion(logits, labels)
                
                # bp
                loss.backward()
                self._optimizer.step()
                
                runningLoss += loss.item()
                
                if (batchIndex + 1) % 10 == 0:
                    total_batches = len(self._trainLoader)
                    print(f"   --> Processing batch {batchIndex + 1}/{total_batches}... current loss: {loss.item():.4f}")
            
            averageTrainLoss = runningLoss / len(self._trainLoader)
            TrainLossHistory.append(averageTrainLoss)
            
            self._FP.eval()
            self._FC.eval()

            validationRunningLoss = 0.0
            predictionsInEpoch = []
            labelsInEpoch = []
            
            with torch.no_grad():
                for batchIndex, (contours, metrics, labels) in enumerate(self._validationLoader):
                    contours = contours.to(self._device)
                    metrics = metrics.to(self._device)
                    labels = labels.to(self._device)

                    features = self._FP(contours, metrics)
                    logits = self._FC(features)
                    
                    loss = self._criterion(logits, labels)
                    validationRunningLoss += loss.item()
                    
                    predictions = torch.argmax(logits, dim=1)
                    predictionsInEpoch.extend(predictions.cpu().numpy())
                    labelsInEpoch.extend(labels.cpu().numpy())
            
            averageValidationLoss = validationRunningLoss / len(self._validationLoader)
            ValidationLossHistory.append(averageValidationLoss)
            
            #https://scikit-learn.org/stable/api/sklearn.metrics.html
            accuracy = sklMetrics.accuracy_score(labelsInEpoch, predictionsInEpoch)
            f1 = sklMetrics.f1_score(y_true=labelsInEpoch, y_pred=predictionsInEpoch, average="macro", zero_division=0)
            presicion = sklMetrics.precision_score(y_true=labelsInEpoch, y_pred=predictionsInEpoch, average="macro", zero_division=0)
            recall = sklMetrics.recall_score(y_true=labelsInEpoch, y_pred=predictionsInEpoch, average="macro", zero_division=0)

            modelStats = {
                "timestamp": datetime.now().strftime('%Y%m%d_%H%M%S'),
                "epoch": epoch+1,
                "average_train_loss": averageTrainLoss,
                "average_val_loss": averageValidationLoss,
                "val_accuracy": float(accuracy),
                "val_f1_macro": float(f1),
                "val_precision_macro": float(presicion),
                "val_recall_macro": float(recall)
            }
            self.trainStats["history"].append(modelStats)

            print('LOSS train {} valid {}'.format(averageTrainLoss, averageValidationLoss))
            writer.add_scalars('Training vs. Validation Loss', { 'Training' : averageTrainLoss, 'Validation' : averageValidationLoss }, epoch + 1)
            writer.flush()
            
            if averageValidationLoss < bestValidationLoss:
                bestValidationLoss = averageValidationLoss
                modelPath = self.outputDir / f"model_timestamp{timestamp}_epoch{epoch}.pth"

                model = {
                    "epoch": epoch,
                    "FP_state": self._FP.state_dict(),
                    "FC_state": self._FC.state_dict(),
                    "optimizer_state": self._optimizer.state_dict(),
                    "loss": bestValidationLoss,
                }
                
                torch.save(model, modelPath)
            
            self._FP.train()
            self._FC.train()
        
        self.trainStats["finished_at"] = datetime.now().strftime('%Y%m%d_%H%M%S')
        self.trainStats["duration_seconds"] = (datetime.now() - startTime).total_seconds()
        
        modelTrainSummary = self.outputDir / f"TrainSummary_{timestamp}.json"
        with open(modelTrainSummary, "w") as f:
            json.dump(self.trainStats, f, indent=4)
            
            # print(f'[Epoch {epoch + 1}] Train Loss: {runningLoss / len(self._trainLoader):.5f}')
        
        print(f"\n[Orchestrator] > Training Complete: Epoch Loss Summary --")
        for l, val in enumerate(TrainLossHistory):
            print(f"   # epoch {l + 1} loss: {val:.5f}")
        
        for l, val in enumerate(ValidationLossHistory):
            print(f"   # epoch {l + 1} loss: {val:.5f}")
            
        # torch.save(self._FP.state_dict(), "FeatureProcessor_Weights_SolidLettersLib_ClosedSet.pth")
        path = self.outputDir / f"FeatureProcessorWeights_{timestamp}.pth"
        model = {
            "epoch": self._epochs,
            "FP_state": self._FP.state_dict(),
            "FC_state": self._FC.state_dict(),
            "optimizer_state": self._optimizer.state_dict(),
            "loss": bestValidationLoss,
        }
        
        torch.save(model, path)
        
        print("[Orchestrator] > Model weights saved successfully.")
    
    def test(self, prefix: str):
        """
        This function evaluates the model using the isolated the isolated test split. Analysis on the model performance hereof is performed.
        This function generates a confusion matrix and PCA plots. Important results are stored and saved into a JSON file.

        Args:
            prefix (str): Prefix for denoting when the test function is executed, e.g. before or after training.
        
        Returns:
            None.
        """
        self._FP.eval()
        self._FC.eval()
        
        with torch.no_grad():
            contourFeatures = []
            metricFeatures = []
            features = []
            classes = []
            classPredictions = []
            
            for index, (contours, metrics, labels) in enumerate(self._testLoader):
                # fetch data
                contours = contours.to(self._device)
                metrics = metrics.to(self._device)
                labels = labels.to(self._device)

                # propagate data through to sub components
                fusion = self._FP(contours, metrics)

                # https://discuss.pytorch.org/t/softmax-cross-entropy-loss/125383/2
                # https://medium.com/@mariabalos16/pytorch-cross-entropy-implementation-a-data-scientists-nightmare-the-double-softmax-trap-9a935e6fc848
                logits = self._FC(fusion) # https://medium.com/@imdadul0202/why-softmax-is-used-instead-of-argmax-in-neural-network-training-23c9ef5d814c
                predictions = torch.argmax(logits, dim=1)
                
                contours = self._FP._EP(contours)
                metrics = self._FP._MP(metrics)
                
                # append to lists
                contourFeatures.append(contours.cpu().numpy())
                metricFeatures.append(metrics.cpu().numpy())
                features.append(fusion.cpu().numpy())
                classes.append(labels.cpu().numpy())
                classPredictions.append(predictions.cpu().numpy())
            
            contourFeatures = np.vstack(contourFeatures)
            metricFeatures = np.vstack(metricFeatures)
            features = np.vstack(features)
            classes = np.concatenate(classes)
            classPredictions = np.concatenate(classPredictions)

            # stats
            #https://datascience.stackexchange.com/questions/65839/macro-average-and-weighted-average-meaning-in-classification-report
            #https://stackoverflow.com/questions/62326735/metrics-f1-warning-zero-division
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            accuracy = sklMetrics.accuracy_score(classes, classPredictions)
            f1 = sklMetrics.f1_score(y_true=classes, y_pred=classPredictions, average="macro", zero_division=0)
            presicion = sklMetrics.precision_score(y_true=classes, y_pred=classPredictions, average="macro", zero_division=0)
            recall = sklMetrics.recall_score(y_true=classes, y_pred=classPredictions, average="macro", zero_division=0)
            
            self.testStats = {
                "library": self.dataset,
                "epochs": self._epochs,
                "history": [],
            }
            
            modelStats = {
                "timestamp": timestamp,
                "test_accuracy": float(accuracy),
                "test_f1_macro": float(f1),
                "test_precision_macro": float(presicion),
                "test_recall_macro": float(recall)
            }
            self.testStats["history"].append(modelStats)
            
            cleanPrefix = prefix.replace("[", "").replace("]", "").replace(" ", "_")
            modelTestSummary = self.outputDir / f"TestSummary_{cleanPrefix}_{timestamp}.json"
            with open(modelTestSummary, "w") as f:
                json.dump(self.testStats, f, indent=4)
            
            ### PLOTTING
            
            # conf matrix
            confusionMatrix = sklMetrics.confusion_matrix(y_pred=classPredictions, y_true=classes, labels=np.arange(len(self._CDL.getClassNames())))
            display = sklMetrics.ConfusionMatrixDisplay(confusion_matrix=confusionMatrix, display_labels=self._CDL.getClassNames())
            fig, ax = plt.subplots(figsize=(15,15))
            display.plot(ax=ax)
            plt.title(f"[{self.dataset}]{prefix}: Confusion Matrix")
            plotPath = self.outputDir / f"{cleanPrefix}_confMatrix.svg"
            plt.savefig(plotPath, format="svg", bbox_inches="tight")
            plt.close()
            
            # feature plots and pca
            pca = PCA(n_components=3, random_state=0)
            
            
            componentVarianceLogPath = self.outputDir / f"{cleanPrefix}_pca_variances.txt"
            with open(componentVarianceLogPath, "w", encoding="utf-8") as file:

                fusionPCA = pca.fit_transform(features)
                file.write(f"==== Variance Log for [{self.dataset}][{prefix}] ====\n")
                file.write(f"Multimodal Features PCA:\n")
                file.write(f"Explained variance: {pca.explained_variance_.tolist()}\n")
                file.write(f"Explained variance ratio: {pca.explained_variance_ratio_.tolist()}\n\n")
                print(f"[Orchestrator] > (Principal Component Analysis):features -- explained variance = {pca.explained_variance_}")
                
                self.scatterPlot3D("Component 1",
                                "Component 2",
                                "Component 3",
                                f"[{self.dataset}]{prefix}: 3D PCA Plot of Learned Multimodal Features",
                                fusionPCA,
                                classes,
                                source=self._CDL.getClassNames())
                
                contourPCA = pca.fit_transform(contourFeatures)
                file.write(f"Visual Features PCA:\n")
                file.write(f"Explained variance: {pca.explained_variance_.tolist()}\n")
                file.write(f"Explained variance ratio: {pca.explained_variance_ratio_.tolist()}\n\n")
                print(f"[Orchestrator] > (Principal Component Analysis):features -- explained variance = {pca.explained_variance_}")
                
                self.scatterPlot3D("Component 1",
                                "Component 2",
                                "Component 3",
                                f"[{self.dataset}]{prefix}: 3D PCA Plot of Learned Contours (ResNet18)",
                                contourPCA,
                                classes,
                                source=self._CDL.getClassNames())
                
                metricPCA = pca.fit_transform(metricFeatures)
                file.write(f"Tabular Features PCA:\n")
                file.write(f"Explained variance: {pca.explained_variance_.tolist()}\n")
                file.write(f"Explained variance ratio: {pca.explained_variance_ratio_.tolist()}\n\n")
                print(f"[Orchestrator] > (Principal Component Analysis):features -- explained variance = {pca.explained_variance_}")
                
                self.scatterPlot3D("Component 1",
                                "Component 2",
                                "Component 3",
                                f"[{self.dataset}]{prefix}: 3D PCA Plot of Learned Metrics (MLP)",
                                metricPCA,
                                classes,
                                source=self._CDL.getClassNames())
    
    def scatterPlot3D(self, xLabel: str, yLabel: str, zLabel: str, title: str, array: np.ndarray, classes: np.ndarray, source: list):
        """
        Creates and saves a 3-dimensional scatter plot of the PCA analysis of features in latent space.

        Args:
            xLabel (str): Label for x-axis (component1)
            yLabel (str): Label for y-axis (component2)
            zLabel (str): Label for z-axis (component3)
            title (str): Title of plot (used to determine filename for the plot)
            array (np.ndarray): Array of size (n, 3) which is of the PCA features
            classes (np.ndarray): Array of unique class labels, e.g. ground truths
            source (list): List of string class names to build legend
        Returns:
            None.
        """
        fig = plt.figure(figsize=(15,15))
        ax = fig.add_subplot(111, projection="3d")
        
        scatter = ax.scatter(
            array[:, 0],
            array[:, 1],
            array[:, 2],
            c=classes, edgecolor='none',
            alpha=0.5,
            cmap=plt.cm.get_cmap('rainbow', len(np.unique(classes)))
        )
        
        ax.set_xlabel(xLabel, labelpad=10)
        ax.set_ylabel(yLabel, labelpad=10)
        ax.set_zlabel(zLabel, labelpad=10)

        labels = {i: str(className) for i, className in enumerate(source)}
        
        handles, _ = scatter.legend_elements(num=len(np.unique(classes)))
        uniqueClasses = np.unique(classes)
        legendLabels = [labels[int(value)] for value in uniqueClasses]
        plt.legend(handles, legendLabels, title="Classes", bbox_to_anchor=(1.05, 1), loc="upper left")
        plt.title(title, pad=20)
        plotPath = self.outputDir / f"{self.dataset}_{title.replace('[', '').replace(']', '').replace(' ', '_').replace(':', '-')}.svg"
        plt.savefig(plotPath, format="svg", bbox_inches="tight")
        plt.close()
    
    def loadWeights(self, path):
        """
        Used for loading pre-trained PyTorch weights into the model, e.g. the FeatureProcessor (FP) and FeatureClassifier (FP) modules.

        Args:
            path (str): Absolute path for the .pth file to be laoded.
        Returns:
            None.
        """
        # self._FP.load_state_dict(torch.load("FeatureProcessor_Weights_SolidLettersLib_ClosedSet.pth", map_location=self._device))
        model = torch.load(path, map_location=self._device)
        self._FP.load_state_dict(model["FP_state"])
        self._FC.load_state_dict(model["FC_state"])
        print("[Orchestrator] > Model weights fetched.")
    
    def newOutputDir(self, name):
        """
        Function for creating new output directory for each run. Results and model weights are stored into this directory.
        This prevents overwriting previous learned weights and helps add transparency regarding how results have evolved when performing experiments.

        Args:
            name (str): Basename for directory (a timestamp is given by default).

        Returns:
            Path: `pathlib.Path` object pointing at the new directory that is created.
        """
        self.outRootDir.mkdir(exist_ok=True)
        while True:
            directory = self.outRootDir / f"{name}"
            if not directory.exists():
                directory.mkdir(parents=True)
                print(f"[Orchestrator] > Output directory created as {directory}")
                return directory
                
if __name__ == "__main__":
    print("[System] > Initializing Orchestrator...")
    orchestrator = Orchestrator()

    # orchestrator.imagesPath = r"D:\Aarhus Universitet - Lokal\BP\step_normals"
    # orchestrator.edgesPath = r"D:\Aarhus Universitet - Lokal\BP\step_edges"
    # orchestrator.metricsPath = r"D:\OneDrive - Aarhus universitet\Aarhus Universitet\6. semester\Bachelorprojekt\step_categorizer-master\output"
    # orchestrator.csvFileName = r"D:\OneDrive - Aarhus universitet\Aarhus Universitet\6. semester\Bachelorprojekt\step_categorizer-master\output\secondary_data.csv"
        
    
    print("[System] > Testing Classifier")
    orchestrator.test(prefix="[Before Training]")
    
    print("[System] > Initializing training phase...")
    # orchestrator.train()
    orchestrator.loadWeights(r"D:\OneDrive - Aarhus universitet\Aarhus Universitet\6. semester\Bachelorprojekt\Models\Model_Revised\outputs\20260525_113627\FeatureProcessorWeights_20260525_114000.pth")
    
    print("[System] > Testing Classifier")
    orchestrator.test(prefix="[After Training]")

    print("[System] > Temporary hold (waiting for user input)")
    input("Press any key to continue...")
    
    print("[System] > Execution complete.")