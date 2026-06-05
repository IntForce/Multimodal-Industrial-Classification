import numpy as np
from EdgeExtractor import EdgeExtractor
# from MetricExtractor import MetricExtractor

class FeatureExtractor:
    """
    The FeatureExtractor module is a interface for the EdgeExtractor (EE). Multiple extracotrs may be added later on
    such as a HOGExtractor (histogram of gradients extractor). This interface should then have acces to that class implementation aswell.
    Right now, this is a high-level interface that is designed to orchestrate the visual data extraction process.
    """
    def __init__(self):
        self._image = None
        self._metricVector = None
        self.EE = EdgeExtractor()
        # self.ME = MetricExtractor()
    
    def extractEdges(self, images_path: str, edges_path: str):
        """
        Initializes the EdgeExtractor module.

        Args:
            images_path (str): Absolute path for the directory of the source images to be processed.
            edges_path (str): Absolute path of the directory that will contain the extracted edge contours after processing.
        Returns:
            None.
        """
        try:
            self.EE.setImageDirectory(images_path)
            self.EE.setEdgeDirectory(edges_path)
            self.EE.generateEdgeLibrary()
        except Exception as ex:
            print(f"[FeatureExtracture] > Error caught when extracting edges with exception: {ex}")

FT = FeatureExtractor()
# FT.extractEdges(r"D:\Aarhus Universitet - Lokal\BP\Traceparts\gallery", r"D:\Aarhus Universitet - Lokal\BP\Traceparts\edgeContours")
FT.extractEdges(r"D:\Aarhus Universitet - Lokal\BP\SolidLetters\step_gallery_original", r"D:\Aarhus Universitet - Lokal\BP\SolidLetters\solidlettersCanny")
