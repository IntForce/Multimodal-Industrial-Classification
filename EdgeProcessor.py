import torch
from torch import nn
import torchvision.models as models

# https://pytorch.org/hub/pytorch_vision_resnet/

class EdgeProcessor(nn.Module):
    """
    This module implements a convolutional neural network (CNN) where the classifier head is stripped off leaving the remaining backbone as a feature extractor.
    It is implemented using a pretrained ResNet18 model where it will perform feature extraction on visual data. It converts
    input images of edge contours into a 512-dimensional feature tensor.
    """
    def __init__(self):
        super(EdgeProcessor, self).__init__()
        resnet18 = models.resnet18(pretrained=True)
        self._layers = nn.Sequential(*(list(resnet18.children())[:-1]))
    
    def forward(self, x):
        """
        This executes the forward pass of the EdgeProcessor module (e.g. ResNet18).

        Args:
            x (torch.Tensor): Input argument is a batch of edge contour images in form of a tesnor with the shape (batchSize, channels, height, width)

        Returns:
            torch.Tensor: A flattened tensor of the visual features processed by the pretrained ResNet18 model. The shape is (batchSize, 512)
        """
        x = self._layers(x)
        x = torch.flatten(input=x, start_dim=1)
        return x # output of size [1, 512]