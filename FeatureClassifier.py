from torch import nn

# https://www.youtube.com/watch?v=AmgBoQjEAZY
# https://www.geeksforgeeks.org/machine-learning/multi-dimensional-inputs-in-pytorch-linear-method-in-python/

class FeatureClassifier(nn.Module):
    """
    This module implements the FeatureClassifier which is the classification head of the multimodal model.
    It takes a 256-dimensional tensor (the fusion of visual and tabular data) as input and maps this tensor directly to the output classes.
    """
    def __init__(self, numClasses: int):
        """
        Initializes the FeatureClassifier module.

        Args:
            numClasses (int): The total number of unique categories which the model should be able to predict.
        """
        super(FeatureClassifier, self).__init__()
        _inputSize = 256 # outt size of FP
        
        self._head = nn.Linear(_inputSize, numClasses)
    
    def forward(self, features):
        """
        Executes the forward pass of the classifier, mapping the input fusion to the output classes.

        Args:
            features (torch.Tensor): The fused feature tensor with the shape (batchSize, 256).

        Returns:
            torch.Tensor: The final class predictions with shape (batchSize, numOfClasses).
        """
        x = self._head(features)
        
        return x