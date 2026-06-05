import numpy as np
import torch
from torch import nn
# import torchvision.models as models
# import torchsummary

from EdgeProcessor import EdgeProcessor
from MetricProcessor import MetricProcessor

class FeatureProcessor(nn.Module):
    """
    This is the fusion module that fuses the visual and tabular features together into a combined tensor.
    The FeatureProcessor module instantiates the EdgeProcessor (EP) and MetricProcessor (MP) modules, and
    sends the respective feature modalities through each processing module in parrallel. The result is concatenated into a combined tensor with shape (1, 640).
    This tensor is then projected into a latent space of smaller dimensionality (e.g. with dimensions 256) to extract the most important features from both modules.
    This creates a multimodal representation of the visual and tabular features. 
    """
    def __init__(self):
        super(FeatureProcessor, self).__init__()
        self._EP = EdgeProcessor()
        self._MP = MetricProcessor()
        
        self._layers = nn.Sequential(
            nn.Linear(640, 256), # (EP+MP size, outSize)
            nn.ReLU(),
            nn.Dropout(p=0.2)
        )
    
    # https://discuss.pytorch.org/t/pre-trained-model-feature-fusion/67584/3
    
    def forward(self, rawEdges, rawMetrics):
        """
        This is the function that performs the forward pass in the multimodal architecture. This function takes the features given by the EdgeProcessor (EP),
        and MetricProcessor (EP), concatenates them and projects the fusion into a 256-dimensional latent space.

        Args:
            rawEdges (torch.Tensor): Batch of image edge contours.
            rawMetrics (torch.Tensor): Batch of tabular data metrics.

        Returns:
            torch.Tensor: A fused multimodal tensor with shape (batchSize, 256).
        """
        feature1 = self._EP(rawEdges)
        feature2 = self._MP(rawMetrics)

        x = torch.cat((feature1, feature2), dim=1)
        x = self._layers(x)
        
        return x
        
        