from torch import nn

class MetricProcessor(nn.Module):
    """
    This module implements a multilayer perceptron (MLP) which is designed to process tabular CAD metric data.
    It accepts a 35-dimensional feature tensor representing the metrics of each CAD drawing. The tensor is projected into a 128-dimensional feature space,
    where multimodal intermediate fusion will be applied later on.
    """
    def __init__(self):
        super(MetricProcessor, self).__init__()

        # shape of input metrics is 36 excluding the filename in the csv
        _inSize = 35 # a vector of 1x35 excluding the category in dataloader class module <- husk i rapport
        _outSize = 128

        self._layers = nn.Sequential(
            nn.Flatten(),
            nn.Linear(_inSize, 48),
            nn.ReLU(),
            nn.Dropout(p=0.2),
            nn.Linear(48, 256),
            nn.ReLU(),
            nn.Dropout(p=0.2),
            nn.Linear(256, _outSize),
        )
    
    def forward(self, x):
        """
        This function runs the forward pass of the MetricProcessor module.

        Args:
            x (torch.Tensor): Accepts a specified batch of tabular metrics with size (batchSize, 35).

        Returns:
            torch.Tensor: A processed tensor representing the embeddings of the processed tabular data metrics. The size will be (batchSize, 128).
        """
        return self._layers(x)